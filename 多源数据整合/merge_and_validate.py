#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_and_validate.py —— 合并多源数据，做质量校验
（对应题目内容(1)"统一接入、数据清洗、格式标准化与质量检验机制"）

═══ 2026-08-03 第二版：从"两个来源"扩展到"三类异构数据" ═══════════════

首版只处理了**工具执行结果**这一类（Claude Code + OpenClaw 两个来源）。
严格说那是"同一类数据的两个来源"，而题目内容(1)原文要求的是三类：

    「支持**工具执行结果、用户行为数据、手动配置信息**等数据的统一接入」

第二版补齐后的四个来源、三种记录类型：

| 来源                  | record_type    | 说明                       |
|-----------------------|----------------|----------------------------|
| claude_code           | tool_execution | 工具调用+执行结果          |
| openclaw_lucy         | tool_execution | 同上，另一个系统           |
| claude_code_history   | user_input     | **用户行为数据**（新增）   |
| claude_code_config    | config_item    | **手动配置信息**（新增）   |

同时修掉了首版"已知限制"第 2 条：**时间戳格式不统一**。现已全部归一为
ISO8601 UTC（见 normalize_time.py），原始值保留在 timestamp_raw。
这一步从"可选优化"变成"硬前置"的原因见 normalize_time.py 文件头。

输入：dataset_*.jsonl（同目录）
输出：dataset/memory_source_records.jsonl（合并后的最终数据集）
      dataset/tool_execution_records.jsonl（仅工具执行类，保持向后兼容）
"""

import json
import os
import sys
from collections import Counter

from normalize_time import apply_to_record

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_FILES = [
    os.path.join(BASE_DIR, "dataset_claude_code.jsonl"),
    os.path.join(BASE_DIR, "dataset_openclaw_lucy.jsonl"),
    os.path.join(BASE_DIR, "dataset_user_history.jsonl"),
    os.path.join(BASE_DIR, "dataset_manual_config.jsonl"),
]

OUTPUT_DIR = os.path.join(BASE_DIR, "dataset")
OUTPUT_ALL = os.path.join(OUTPUT_DIR, "memory_source_records.jsonl")
OUTPUT_TOOLS = os.path.join(OUTPUT_DIR, "tool_execution_records.jsonl")

SCHEMA_PATH = os.path.join(BASE_DIR, "schema.json")

# 独立评审D-5：此前质检只查字段是否存在/非空，没有类型与枚举校验（比如
# is_error 传成字符串"true"、record_type 传成schema里没有的值都不会被拦下）。
# 不引入 jsonschema 第三方库——保留 L1"纯Python、任何机器可跑"这条约束，
# 自己写个轻量校验器读 schema.json。
_PY_TYPES = {"str": str, "bool": bool, "dict": dict, "list": list,
             "int": int, "float": (int, float)}


def _type_ok(value, type_spec):
    if type_spec == "any":
        return True
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    # bool 是 int 的子类，先排除，否则 True/False 会被误判成合法的 int
    for t in types:
        py_t = _PY_TYPES[t]
        if t == "bool":
            if isinstance(value, bool):
                return True
        elif t == "int":
            if isinstance(value, int) and not isinstance(value, bool):
                return True
        elif isinstance(value, py_t):
            return True
    return False


# 不同记录类型有不同的必填字段——这正是"异构数据统一接入"要处理的核心问题：
# 强行要求所有类型都有 tool_name 是不合理的，但完全不校验又失去了质量把关的意义。
# 折中方案：公共字段全类型必填，类型专有字段按类型分别校验。
COMMON_REQUIRED = ["source", "record_type", "timestamp"]

TYPE_REQUIRED = {
    "tool_execution": ["session_id", "tool_name", "tool_input", "tool_output"],
    "user_input":     ["text"],
    "config_item":    ["config_path", "config_value"],
}


def classify_execution_status(rec: dict) -> str:
    """把 tool_execution 记录粗分成三态，而不是只看 is_error 布尔值。

    ═══ 为什么必须拆 ═══════════════════════════════════════════════════

    独立评审发现：实测 5,174 条 `is_error=True` 的记录里，**4,664 条
    （90.1%）的 `tool_output` 是"用户拒绝了这次工具调用"**——工具压根
    没真的执行过。把这类记录当成"执行失败"统计，会让"错误率"这个数字
    偏离真实值一个数量级（把它们算进去是 36.8%，排除后真实执行失败
    只有 3.6%），也会让下游的案例挖掘（`知识检索/mine_cases.py`）在这个
    被污染的池子里找"失败→修复"对，产出大量假案例。

    `mine_cases.py` 自己的 `classify_error()` 已经在挖掘阶段处理了这个
    问题（更细致的四分类：permission/infra/technical/unknown），但**本
    模块（数据源头）自己报告"执行成功/出错"比例时，用的还是原始的
    `is_error` 二值**，同样的误导在报告里独立出现了一次——这里补上，
    让"多源数据整合"这份源头报告自己的错误率表述也是准确的，不依赖
    下游模块替它纠正。

    三态定义（不是 mine_cases.py 那套四分类的替代，是更粗粒度的"这条
    记录是否真的执行过"判断，服务于本模块的统计口径）：
    """
    out = str(rec.get("tool_output", "") or "")
    if "tool use was rejected" in out or "doesn't want to proceed" in out:
        return "user_rejected"      # 未执行，不应计入错误率
    if "completed with no output" in out:
        return "no_output"          # 执行成功但无输出
    if rec.get("is_error"):
        return "execution_failed"   # 真实执行失败
    return "success"


_SCHEMA = json.load(open(SCHEMA_PATH, encoding="utf-8")) if os.path.exists(SCHEMA_PATH) else None


def validate_record(record):
    """质量校验。返回 (是否合法, 问题描述)。

    注意 is_error=False、config_value=False 这类**合法的假值**不能当"空"处理，
    所以判空只认 None 和空字符串，不能直接用 `if not value`。
    首版就是这么写的，这里保持一致。
    """
    rtype = record.get("record_type")

    # record_type 缺失时按工具执行处理——首版产出的记录没有这个字段，
    # 需要向后兼容，不能因为加了新字段就把已有的 14047 条判为不合法。
    if rtype is None:
        rtype = "tool_execution"
        record["record_type"] = rtype

    if rtype not in TYPE_REQUIRED:
        return False, f"未知记录类型: {rtype}"

    for field in COMMON_REQUIRED + TYPE_REQUIRED[rtype]:
        if field not in record:
            return False, f"[{rtype}] 缺少字段: {field}"
        value = record[field]
        if value is None or value == "":
            return False, f"[{rtype}] 字段为空: {field}"

    # 独立评审D-5：类型与枚举校验（schema.json缺失时跳过，不因此拒绝记录——
    # 结构性必填字段检查已经是硬门槛，schema只是加码，不是唯一防线）
    if _SCHEMA is not None:
        for field, spec in {**_SCHEMA["common"], **_SCHEMA.get(rtype, {})}.items():
            if field.startswith("_") or field not in record:
                continue
            value = record[field]
            if not _type_ok(value, spec["type"]):
                return False, f"[{rtype}] 字段 {field} 类型不符：期望 {spec['type']}，实际 {type(value).__name__}"
            if "enum" in spec and value not in spec["enum"]:
                return False, f"[{rtype}] 字段 {field} 值不在允许范围：{value!r} not in {spec['enum']}"

    return True, None


def dedup_key(rec: dict) -> str:
    """按 (会话, 工具, 输入, 输出) 四元组算去重签名。

    不用整条记录哈希：整条记录含 `timestamp`，同一操作被日志重复记录时
    时间戳可能有微小差异（毫秒级），整条哈希去不掉这类重复。四元组
    只认"这是不是同一次操作"，与记录到的时间无关。

    ⚠️ 锚点（每日固定 16 条）依赖「每天新开会话」来避开这条去重规则：
    同一会话内跑两遍、读数又没变的锚点会被当重复记录删掉，S1
    （`知识检索/mine_conflicts.py` 的 `mine_command_output_drift()`）就
    少一个数据点。若以后改成长会话跑多天，必须把 `timestamp[:10]` 加进
    这个四元组，否则同一天内的锚点会被去重掉，S1 的跨天/跨会话样本量
    会悄悄变小而不报错。
    """
    import hashlib
    payload = json.dumps(
        [rec.get("session_id"), rec.get("tool_name"),
         rec.get("tool_input"), rec.get("tool_output")],
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def has_encoding_damage(rec: dict) -> bool:
    """检测 U+FFFD 替换字符——GBK/UTF-8 转码失败留下的乱码标记。"""
    for key in ("tool_output", "text"):
        v = rec.get(key)
        if isinstance(v, str) and "�" in v:
            return True
    return False


def assert_inputs_not_newer_than_output(input_paths, output_path):
    """输入文件比输出新 → 输出是陈旧产物，本次合并的输出与当前输入对不上。

    第二轮独立复核 R2-06：复核员在纯净副本里重跑本脚本，得到的数字与
    交付的报告数字全部对不上（合并后记录数 6,927 vs 重跑 7,328，输入总数
    15,111 vs 15,502……）——根因是改完 extract 系脚本后没有重跑本脚本，
    `dataset_claude_code.jsonl` 的 mtime 比 `memory_source_records.jsonl`
    还晚 3 分钟。这类错误不报错、不崩溃，只有把两边的 mtime 放在一起看
    才会发现，容易一直没人注意到。

    ⚠️ 这个检查只在文件不存在时静默跳过（第一次运行，没有"陈旧"这个
    概念），存在时严格比较——不因为"可能只是本机时钟漂移几秒"就放宽，
    时间戳漂移几分钟以上足以说明真的没重跑。
    """
    if not os.path.exists(output_path):
        return
    out_mtime = os.path.getmtime(output_path)
    stale = [p for p in input_paths if os.path.exists(p) and os.path.getmtime(p) > out_mtime]
    if stale:
        raise SystemExit(
            f"以下输入文件比输出 {os.path.basename(output_path)} 更新：\n"
            + "\n".join(f"  {p}（mtime={os.path.getmtime(p)}）" for p in stale)
            + f"\n（输出 mtime={out_mtime}）\n"
            "说明提取脚本改动后没有重跑本脚本，输出是陈旧产物，报告数字"
            "会与重跑结果对不上。请先确认这是预期的（比如只是想看旧产物"
            "长什么样），否则重跑本脚本后再使用产物与报告数字。")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="合并多源数据并做质量校验")
    ap.add_argument("--allow-stale-inputs", action="store_true",
                     help="跳过输入新鲜度检查，强行使用已有输出（明确知道"
                          "输入比上次输出新、但不想重新合并时用，比如本项目"
                          "自身在使用过程中会不断产生新的工具调用记录，"
                          "刻意保留某个历史时刻冻结的数据集快照做交付基准）")
    args = ap.parse_args()

    if not args.allow_stale_inputs:
        assert_inputs_not_newer_than_output(INPUT_FILES, OUTPUT_ALL)

    all_records = []
    invalid_count = 0
    invalid_reasons = Counter()
    normalized_count = 0
    rejected = []   # 通过基础校验、但被后续质检规则剔除的记录，连同原因落盘供复查

    for path in INPUT_FILES:
        if not os.path.exists(path):
            print(f"[警告] 找不到输入文件，跳过：{os.path.basename(path)}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    invalid_count += 1
                    invalid_reasons["JSON解析失败"] += 1
                    continue

                # 时间戳归一化。首版产出的两个文件里时间戳格式不同，
                # 在这里统一处理，使全量数据可按时间排序、可按时间切分。
                if "timestamp_source" not in record:
                    apply_to_record(record)
                    normalized_count += 1

                ok, reason = validate_record(record)
                if not ok:
                    invalid_count += 1
                    invalid_reasons[reason] += 1
                    continue

                # ── 质检补强（独立评审 D-5 指出：此前只查字段存在与否，
                # 拒绝数恒为 0，无法证明校验器真的在工作）──────────────
                if has_encoding_damage(record):
                    invalid_count += 1
                    invalid_reasons["编码损坏(含U+FFFD)"] += 1
                    rejected.append({**record, "_reject_reason": "encoding_damaged"})
                    continue

                if record.get("record_type") == "tool_execution":
                    record["execution_status"] = classify_execution_status(record)

                all_records.append(record)

    # ── 内容级去重（独立评审 D-5 指出：整条完全重复的记录此前从未被
    # 检测过，实测占 33.1%）。放在字段校验之后、时间排序之前——去重
    # 依据的四元组字段（session_id/tool_name/tool_input/tool_output）
    # 此时已经确定不为空，不会因为脏数据产生假的"相同"判定。
    dedup_seen = {}
    deduped = []
    dup_count = 0
    for r in all_records:
        if r.get("record_type") != "tool_execution":
            deduped.append(r)
            continue
        key = dedup_key(r)
        if key in dedup_seen:
            dup_count += 1
            rejected.append({**r, "_reject_reason": "duplicate_of_earlier_record"})
            continue
        dedup_seen[key] = True
        deduped.append(r)
    all_records = deduped

    # 按时间排序：ISO8601 字符串的字典序即时间序，这正是选它的原因。
    # 时间戳无法识别的记录排在最后，不让它们干扰正常记录的顺序。
    all_records.sort(key=lambda r: (r.get("timestamp_source") == "unknown",
                                    r.get("timestamp") or ""))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_ALL, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 被剔除的记录（编码损坏/重复）连同原因落盘，供复查——不合法记录
    # 此前只计数后 continue 丢弃，没有留痕，无法审计具体是哪一条。
    rejected_path = os.path.join(OUTPUT_DIR, "rejected.jsonl")
    with open(rejected_path, "w", encoding="utf-8") as f:
        for r in rejected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 单独再出一份"仅工具执行结果"，保持与首版产物同名同格式，
    # 让已经依赖它的下游脚本（如知识检索的建索引）不必改动。
    tool_records = [r for r in all_records if r["record_type"] == "tool_execution"]
    with open(OUTPUT_TOOLS, "w", encoding="utf-8") as f:
        for r in tool_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------- 统计报告
    total_seen = len(all_records) + invalid_count + dup_count
    print("=" * 68)
    print("多源数据整合 —— 质量校验报告（第三版：补去重/编码检测/三态执行状态）")
    print("=" * 68)
    print(f"输入记录总数（去重前）：{total_seen}")
    print(f"合法记录总数：{len(all_records)}")
    print(f"通过率：{len(all_records) / total_seen * 100:.1f}%"
          if total_seen else "通过率：n/a")
    print(f"不合法/跳过记录数：{invalid_count}（原因分布见下）")
    print(f"内容级重复剔除：{dup_count}")
    if invalid_reasons:
        print("不合法原因分布：")
        for reason, cnt in invalid_reasons.most_common():
            print(f"  {reason}: {cnt}")
    print(f"⚠️ 「合法记录数」比首版口径更低是预期结果，不是缩水——首版没有去重"
          f"与编码检测，把这类噪声也算进了合法记录里。剔除的记录连同原因见 "
          f"{os.path.basename(rejected_path)}（{len(rejected)} 条，供复查）。")
    print(f"本次补做时间戳归一化的记录数：{normalized_count}")

    print("\n按记录类型分布：")
    for t, cnt in Counter(r["record_type"] for r in all_records).most_common():
        print(f"  {t}: {cnt} ({cnt / len(all_records) * 100:.1f}%)")

    print("\n按数据来源分布：")
    for src, cnt in Counter(r["source"] for r in all_records).most_common():
        print(f"  {src}: {cnt} ({cnt / len(all_records) * 100:.1f}%)")

    print("\n时间戳来源格式分布（验证归一化覆盖情况）：")
    for k, cnt in Counter(r.get("timestamp_source", "未标注")
                          for r in all_records).most_common():
        print(f"  {k}: {cnt}")

    stamps = [r["timestamp"] for r in all_records
              if r.get("timestamp") and r.get("timestamp_source") != "unknown"]
    if stamps:
        print(f"\n时间跨度（归一化后可直接比较）：\n  {min(stamps)}\n  → {max(stamps)}")

    if tool_records:
        print("\n工具类型分布（前12，仅 tool_execution）：")
        for name, cnt in Counter(r["tool_name"] for r in tool_records).most_common(12):
            print(f"  {name}: {cnt}")

        # 三态而非 is_error 二值：is_error=True 里混着大量"用户拒绝了工具
        # 调用"（工具从未真的执行过），直接按 is_error 报错误率会偏离真实值
        # 一个数量级（详见 classify_execution_status() 的文档字符串）。
        status_counts = Counter(r.get("execution_status", "success") for r in tool_records)
        n = len(tool_records)
        print(f"\n执行状态三态分布（共 {n} 条 tool_execution）：")
        print(f"  success（成功）：{status_counts.get('success', 0)} "
              f"({status_counts.get('success', 0)/n*100:.1f}%)")
        print(f"  no_output（成功但无输出）：{status_counts.get('no_output', 0)} "
              f"({status_counts.get('no_output', 0)/n*100:.1f}%)")
        print(f"  user_rejected（未执行，用户拒绝）：{status_counts.get('user_rejected', 0)} "
              f"({status_counts.get('user_rejected', 0)/n*100:.1f}%)")
        print(f"  execution_failed（真实执行失败）：{status_counts.get('execution_failed', 0)} "
              f"({status_counts.get('execution_failed', 0)/n*100:.1f}%)  ← 真实错误率")

    cfg = [r for r in all_records if r["record_type"] == "config_item"]
    if cfg:
        print("\n手动配置项按偏好类别分布（这些是偏好提取的交叉验证答案键）：")
        for c, cnt in Counter(r.get("preference_category", "其他")
                              for r in cfg).most_common():
            print(f"  {c}: {cnt}")

    print(f"\n最终数据集：{OUTPUT_ALL}")
    print(f"向后兼容子集：{OUTPUT_TOOLS}（{len(tool_records)} 条）")

    print("\n⚠️ 提醒：config_item 类记录带 is_answer_key 标记，")
    print("   做偏好提取实验时**必须把它们排除出训练输入**，否则等于拿答案当输入。")


if __name__ == "__main__":
    main()
