#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_conflicts.py —— 知识冲突挖掘器 + 规则裁决器

═══ 这个文件解决什么问题 ═══════════════════════════════════════════

赛题要求「新旧知识冲突处理」：同一件事，先说了 A，后来又说了 B，
系统要能识别"这是冲突"、判断"谁生效"、且**不删除旧知识**（版本化管理+回溯）。

═══ 数据来自哪两层（设计方案 §0.2）═══════════════════════════════════

冲突分两种来源，不在同一层：

  · **dataset 层**（T1事实过期/T3环境批量失效/T4局部修订）：
    `多源数据整合/dataset/memory_source_records.jsonl` 里对
    `memory/*.md` 的 Read/Write/Edit 工具调用记录。这些文件是本项目
    自己的记忆系统在跨天被反复修订，修订是真实发生的（自指声明见
    《知识冲突处理说明报告.md》）。

    `Edit` 记录的 `tool_input` 直接带 `old_string`/`new_string`——
    这就是段落级 diff 的两端，不需要自己实现跨天全文 diff。

  · **知识单元层**（T2方法推翻）：`知识检索/cases.jsonl`（`mine_cases.py`
    产出，已做过权限过滤/相似度过滤等五步清洗）。同一类错误签名下
    如果不同时间段给出了不同的修复方法，就是候选。

  · **S1（命令输出漂移，T1事实过期/T3环境批量失效）**：同样读
    `memory_source_records.jsonl`，但看的是 `record_type=="tool_execution"`
    的 **Bash** 记录本身（不限于 memory/*.md），比对"同一条命令跨天执行、
    输出是否变了"。这条矿脉不依赖任何自然语言措辞，是 T1/T3 唯一的客观
    读数来源，主要供给方是《每日锚点16条.md》。详见
    `mine_command_output_drift()` 与 `S1检测器-实施方案.md`。

═══ 稳定标识：为什么不用 doc_id（设计方案 §0.1）═══════════════════════

`doc_id` 是 `build_knowledge_index.py` 里 `enumerate(units)` 出来的整数
下标，任一源文件行数变化就整体重排（`freeze_guard.py` 存在正是为了防
这个）。冲突记录若直接存 `doc_id`，重挖一次全部指错。

因此冲突记录用**内容自然键** `stable_key()`：case 用
`(session_id, failed_at, tool_name)` 三元组，doc_revision 用
`(file_path, timestamp)`——两者都来自原始记录，不随挖掘器版本变化。
`doc_id_at_freeze` 仍会记一份，只作"当次索引位置"的参考，不作长期引用。

═══ 三层判定分工（设计方案 §三）══════════════════════════════════════

    L1 结构层（本文件，确定性、可复现、被 mutate.py 变异测试保护）
       同签名 + 跨会话 + 跨天 + diff 非空  →  候选池（mine()）
            ↓
    L2 语义层（人工/Claude 只排序解释，不产出 ground truth）
       候选池太大，先按启发式信号（否定词命中等）排序，帮助人工挑值得看的
            ↓
    L3 人工裁决（ground truth，见 review_and_freeze_conflicts.py）
       50~100 对正负例，冻结 + 哈希 + 外部锚定

`adjudicate()` 是 L1 里的**规则裁决器**（不是 LLM）：给一对已确认冲突的
知识，按 T1~T4 规则判断谁生效、旧知识的适用边界是什么。它的输出会被拿去
和 L3 人工裁决的评测集比对，算"裁决正确率"——**裁决器本身不能是评测集的
来源**，见 L3 那句话：这两条知识确实冲突，是事实断言，出题方绑架指标的
覆辙已经在评测集模块踩过一次（E-1），这里不能再踩。

用法：
    python mine_conflicts.py                # 挖掘 → 写 conflicts.jsonl
    python mine_conflicts.py --out 试.jsonl  # 实验，不碰冻结产物
"""

import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)

# mine_cases 与 freeze_guard 都假设从 知识检索/ 目录内 import——本文件必须
# 与它们同目录（勘探已确认 freeze_guard.py 用相对路径找 评测集/eval_set_frozen.json，
# 挪目录会打断这条相对路径）。
from mine_cases import (  # noqa: E402
    PERMISSION_PATTERNS, INFRA_FAILURE_PATTERNS, mine as mine_cases_mine,
)

DATASET = os.environ.get("RAG_DATASET") or os.path.join(
    RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")
CASES_PATH = os.path.join(BASE, "cases.jsonl")

# memory/*.md 文件路径识别：兼容正斜杠/反斜杠两种写法（数据集里 Windows
# 路径用反斜杠，但 Edit/Read 的 file_path 偶有正斜杠混用，见勘探样例第三条）。
_MEMORY_MD_RE = re.compile(r"[\\/]memory[\\/][^\\/]+\.md$", re.IGNORECASE)


def _is_memory_md(file_path) -> bool:
    return bool(file_path) and bool(_MEMORY_MD_RE.search(file_path))


# ═════════════════════════════════════════════════════ 稳定标识

def stable_key(unit_raw: dict, kind: str) -> str:
    """知识单元的稳定标识：与 doc_id 无关，重挖不变（设计方案 §0.1）。

    case 用 (session_id, failed_at, tool_name) 三元组——这三个字段来自
    cases.jsonl 的原始记录，不随挖掘器版本变化。doc_revision 用
    (file_path, timestamp)，timestamp 是这次编辑发生的时刻。
    """
    if kind == "case":
        return "case:%s:%s:%s" % (unit_raw["session_id"], unit_raw["failed_at"],
                                   unit_raw["tool_name"])
    if kind == "doc_revision":
        return "doc:%s:%s" % (unit_raw["file_path"], unit_raw["timestamp"])
    raise ValueError("未知 kind：%r（只支持 case / doc_revision）" % (kind,))


# ═════════════════════════════════════════════════════ 错误文本归一化

# ⚠️ mine_cases.py 里没有叫 normalize 的函数（勘探已确认，同名函数在
# normalize_time.py 做的是时间戳归一化，功能完全不同）。这个是新写的，
# 刻意加 _error_text 后缀避免与 normalize_time.normalize() 撞名混淆。
def normalize_error_text(text: str) -> str:
    """把错误文本归一化成可比较的签名原料：去掉随运行变化的部分
    （时间戳/PID/临时路径/内存地址/行号），保留错误类型与关键标识符。

    ⚠️ 签名粒度是本模块成败的关键（第二轮复核实测过的教训）：如果只用
    "exit code N" 当签名，6 个互不相关的问题会聚成一组。因此这里保留
    错误文本的实质内容，只剥离"每次运行都不同但无判别意义"的噪声。
    """
    t = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", "<TS>", text)
    t = re.sub(r"\bpid[= ]?\d+\b", "pid<N>", t, flags=re.IGNORECASE)
    t = re.sub(r"/tmp/[\w.-]+", "/tmp/<T>", t)
    t = re.sub(r"0x[0-9a-fA-F]{6,}", "<ADDR>", t)
    t = re.sub(r"\bline \d+\b", "line <N>", t)
    # ⚠️ 实测发现的第五类噪声（比设计方案预警的"exit code N 当整个签名"更
    # 隐蔽）：`Exit code N` 只是**前缀**，签名截断在它之后仍然可能落在
    # 「Traceback (most recent call last): File "<string>", line <N>, in
    # <module>」这类**所有 Python 一行式报错共享的样板文本**里——实测
    # 5 组按 60 字签名聚类的案例，人工核查后全部是误判：exit code
    # 143（超时）三次聚在一起，分别是三件毫不相关的长跑命令；两次
    # Traceback 聚在一起，分别是查 qBittorrent 配置和查 tailscale 网络，
    # 唯一共同点是都用了 `python3 -c "import json; d=json.load(...)"`
    # 这个惯用写法。剥掉退出码前缀本身**不够**，样板 Traceback 头也要剥。
    t = re.sub(r"^Exit code \d+\s*", "", t)
    t = re.sub(r'^Traceback \(most recent call last\):\s*File "[^"]*",\s*line <N>,\s*in \S+\s*',
               "", t)
    return re.sub(r"\s+", " ", t).strip()


def _text_overlap_ratio(a: str, b: str) -> float:
    """最长公共子串占全长较短者的比例——与 mine_cases.py 的
    `command_similarity()` 同款方法（那边用于区分"真修复"与"无关的下一步
    操作"），这里用于区分"签名分组恰好命中的两个真实同一问题"与"错误
    文本样板相似但实为不同事件的假阳性"。签名分组只是粗筛，真正判定
    还得看两条错误文本本身重叠多少实质内容。
    """
    import difflib
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = matcher.find_longest_match(0, len(a), 0, len(b))
    return match.size / min(len(a), len(b))


# ⚠️ 实测踩过一次坑：最初拿 error_text 本身算重叠度，结果四组假阳性全部
# 算出 1.000——因为"FATAL ERROR: Network error: Connection refused"这类
# 报错文本本身就是**完整包含**在彼此之中的通用短语（较短一条整段就是
# 较长一条的子串），文本相似度天然给满分，起不到区分作用。真正携带"是不是
# 同一件事"这个信息的是**失败命令**（failed_input）不是错误文本——换成
# 比较 failed_input 后，4 组已知假阳性重叠度全部落在 0.04~0.20（分别是
# ssh到device_b vs ssh到VPS:31879、写litellm测试文件 vs docker kill、
# 写litellm测试文件 vs 跑reproduce_L1.py、docker kill vs 跑reproduce_L1.py），
# 阈值取 0.3——比全部已知假阳性都高，且与 mine_cases.py 同款方法的
# 相似度过滤用的是同一量级判断标准。
CASE_SIGNATURE_OVERLAP_THRESHOLD = 0.3


def conflict_signature(unit_raw: dict, kind: str):
    """冲突判定的槽位签名。同签名 + 跨会话 + 跨时间 + 内容不同 = 冲突候选。

    ⚠️ kind 只有两类真实存在于本模块：case（知识单元层）/ doc_revision
    （dataset 层）。build_knowledge_index.py 里的知识单元 type 另有
    case/workflow/template 三类，是完全不同的分类维度，不要混淆。
    """
    if kind == "case":
        return ("case", unit_raw["tool_name"],
                normalize_error_text(unit_raw["error_text"])[:60])
    if kind == "doc_revision":
        return ("doc", unit_raw["file_path"])
    return None


# ═════════════════════════════════════════════════════ 否定/推翻信号（L2 排序辅助，不裁决）

# 这组正则**不是**冲突判定条件——它只用来给候选池排序、帮人工挑"更可能
# 是真冲突"的候选优先看。是否真的构成冲突，由 L3 人工裁决决定。命中/不
# 命中都会被如实记录进候选池（negation_hint 字段），供审计。
NEGATION_HINT_PATTERNS = re.compile(
    r"已经?不(存在|再是?|用了?)|不再是|已经?拔除|已经?下线|已经?作废|已经?废弃"
    r"|已经?停用|事实上停摆|停摆|判断有误|判断反了|理解反了|之前.{0,10}反了"
    r"|已经?迁移|已经?从.{0,12}迁移|改用|取消|已经?清除|不依赖|对着空气运行"
    r"|全部是.{0,6}missingFiles|不存在了|之前.{0,10}(错|误)|事实上",
)

# 环境重置信号（T3）：用户输入含这类表述时，该环境下一批知识可能同时失效。
ENV_RESET_PATTERNS = re.compile(
    r"重装了?.{0,4}系统|换了个?线|换了一?个?线|格式化了|重置了.{0,4}(系统|环境)"
    r"|换了个?(路由器|光猫|宽带)",
)

# T3（环境变更批量失效）在 doc_revision 类候选里的信号——与上面 S4 的
# ENV_RESET_PATTERNS 不同源（那个扫用户输入），这个扫**胜出一侧的正文**，
# 找"物理/环境条件变了导致旧结论前提消失"的措辞。取词依据：adjudicate()
# 用 18 条人工裁决样本首次测量时，3 条人工标 T3 的（做种盘拔除/tv-
# autostart用户决定停用/SATA接口不兼容改笔记本）规则侧全部误判成 T1，
# 补上这组关键词后重测，见 measure_L3.py 的 M-301 记录两版对比。
ENV_CHANGE_PATTERNS = re.compile(
    r"物理拔除|已拔除|断电|接口不兼容|插不进|不再需要|已停用|已经?停用"
    r"|不存在了|已经?下线|意外断电|SATA|USB口",
)


# ═════════════════════════════════════════════════════ dataset 层：memory/*.md 修订挖掘（T1/T3/T4）

def _extract_doc_edits(recs):
    """从 tool_execution 记录里抽出对 memory/*.md 的 Edit 事件（候选来源）
    与 Read/Write/Edit 的**全部内容快照**（起源查找用），按文件分组、
    按时间排序。

    候选只从 Edit 产出（`old_string`/`new_string` 是现成的段落级 diff，
    见文件头"意外之喜"）；但判断 old_text 是从什么时候开始存在的，需要
    在 Edit 之外**也**看 Read（首次观测到的文件全文）与 Write（整篇覆写）
    ——否则任何"第一次被 Edit 修改、此前只被 Read 过"的段落都会误判成
    "起源不明"，而 Read 的 `tool_output` 明明就带着它当时的原文（带行号
    前缀，需要先去掉）。
    """
    edits, timeline = [], []
    for r in recs:
        if r.get("record_type") != "tool_execution":
            continue
        tool = r.get("tool_name")
        if tool not in ("Read", "Write", "Edit"):
            continue
        ti = r.get("tool_input") or {}
        fp = ti.get("file_path") or ""
        if not _is_memory_md(fp):
            continue
        ts, sid = r.get("timestamp"), r.get("session_id")

        if tool == "Edit":
            old_text, new_text = ti.get("old_string"), ti.get("new_string")
            if not old_text or not new_text:
                continue
            edits.append({"file_path": fp, "timestamp": ts, "session_id": sid,
                           "old_text": old_text, "new_text": new_text})
            timeline.append({"file_path": fp, "timestamp": ts, "session_id": sid,
                              "content": new_text, "tool": "Edit"})
        elif tool == "Write":
            content = ti.get("content") or ""
            if content:
                timeline.append({"file_path": fp, "timestamp": ts, "session_id": sid,
                                  "content": content, "tool": "Write"})
        elif tool == "Read":
            # Read 的 tool_output 是「行号\t原文」逐行拼接，去掉行号前缀
            # 才能跟 old_string/new_string（不带行号）做子串匹配。
            raw = r.get("tool_output") or ""
            content = "\n".join(re.sub(r"^\d+\t", "", ln) for ln in raw.split("\n"))
            if content:
                timeline.append({"file_path": fp, "timestamp": ts, "session_id": sid,
                                  "content": content, "tool": "Read"})

    by_file_edits, by_file_timeline = {}, {}
    for e in edits:
        by_file_edits.setdefault(e["file_path"], []).append(e)
    for t in timeline:
        by_file_timeline.setdefault(t["file_path"], []).append(t)
    for fp in by_file_edits:
        by_file_edits[fp].sort(key=lambda e: e["timestamp"] or "")
    for fp in by_file_timeline:
        by_file_timeline[fp].sort(key=lambda t: t["timestamp"] or "")
    return by_file_edits, by_file_timeline


def _find_origin(timeline_before, old_text):
    """在这次编辑之前的同文件内容快照历史（Read/Write/Edit 全部纳入）里，
    倒序找最近一次"当时内容包含 old_text"的快照，即 old_text 最早被
    **本项目观测到**的时间点。

    ⚠️ 这是"被观测到"的时间，不一定是"被写下"的时间——如果这段文字在
    数据窗口开始前就已存在（该文件第一次被 Read/Write 时就已经带着它），
    这里给出的是数据窗口内已知的**最早下界**，仍然如实、仍然保守（不会
    把 old 判得比实际更晚），只是不能证明更早。找不到任何快照包含
    old_text，才是真正的"起源不明"（例如老快照因数据缺口未被记录）。

    ⚠️ 【2026-08-28 修复】两轮搜索：Read 是观测不是断言，只有在第一轮
    （Write/Edit，真正"写下"过这段内容的事件）完全找不到时，才退回第二轮
    把 Read 也算上——否则提示词自己规定的"先 Read 再 Edit"这个动作顺序，
    会让当天自己的 Read 快照把跨天的真实起源（前一天的 Write/Edit）挡在
    更早的位置，origin 被错误地判成"当天、同 session"（详见《冻结修订
    记录 #1》）。
    """
    for t in reversed(timeline_before):
        if t.get("tool") in ("Write", "Edit") and old_text in t["content"]:
            return t
    for t in reversed(timeline_before):
        if old_text in t["content"]:
            return t
    return None


def mine_doc_revisions(recs):
    """挖掘 memory/*.md 的段落级修订候选（T1/T3/T4 的 dataset 层来源）。

    返回 (candidates, stats)。candidates 里**每一次有起源可查的编辑**都会
    产出一条记录（无论最终是不是冲突候选），`structural_label` 标注 L1
    结构层的结论：`candidate`（跨会话/跨天，可能冲突）、`append_only`
    （纯追加）、`same_sitting`（同会话同天，多半是起草润色）。

    ⚠️ 这是刻意的设计，不是"顺手多输出点东西"：负例（append_only /
    same_sitting）要拿去和正例一起构成评测集，必须有**同样完整的原文
    证据**，不能只留一个计数器——不然人工/Claude 复核负例时无法核对
    "这条真的是纯追加"，评测集的负例就成了空口白牙。`unknown_origin`
    （旧文本起源查不到）不产出记录：没有 old 侧的时间/会话信息，连
    "结构上是什么"都说不清，输出了也没法用于评测。
    """
    stats = {
        "doc_edit_total": 0,
        "rejected_by_append_only": 0,
        "rejected_by_same_session": 0,
        "doc_revision_candidates": 0,
    }
    by_file_edits, by_file_timeline = _extract_doc_edits(recs)
    candidates = []

    def _record(structural_label, old_text, old_session, old_timestamp, new_text):
        hint = bool(NEGATION_HINT_PATTERNS.search(new_text))
        old_day, new_day = (old_timestamp or "")[:10], (e["timestamp"] or "")[:10]
        return {
            "candidate_id": None,   # main() 统一编号
            "source": "S2_doc_revision",
            "type_guess": "T1_or_T4" if structural_label == "candidate" else "NOT_CONFLICT",
            "structural_label": structural_label,
            "kind": "doc_revision",
            "old": {
                "stable_key": stable_key(
                    {"file_path": fp, "timestamp": old_timestamp}, "doc_revision"),
                "file_path": fp, "timestamp": old_timestamp,
                "session_id": old_session, "text": old_text,
            },
            "new": {
                "stable_key": stable_key(
                    {"file_path": fp, "timestamp": e["timestamp"]}, "doc_revision"),
                "file_path": fp, "timestamp": e["timestamp"],
                "session_id": e["session_id"], "text": new_text,
            },
            "cross_day": old_day != new_day,
            "negation_hint": hint,
        }

    for fp, edits in by_file_edits.items():
        stats["doc_edit_total"] += len(edits)
        file_timeline = by_file_timeline.get(fp, [])
        for e in edits:
            old_text, new_text = e["old_text"], e["new_text"]

            # 纯追加：旧文本整段原样保留在新文本里（不管是接在后面、还是
            # 前面插了新的一段）——没有替换/否定任何已有内容。
            #
            # ⚠️ 实测教训：最初只判"new_text 以 old_text 为前缀"
            # （只挡住"接着往后写"这一种），漏了 MEMORY.md 这类索引文件
            # 常见的"在前面插入一条新记录"模式——K-0001/K-0003 两条假阳性
            # 都是这样：old 是某一行索引条目，new 是"新插入的一行 + 原样
            # 保留的那一行"，旧文本一个字没改，只是不再是开头。改成
            # "old_text 整体是 new_text 的子串"才覆盖前插/后插/中间插三种。
            #
            # append_only 的 old 侧起源就是它自己（这次编辑之前，old_text
            # 一直存在于文件里——它没被替换，谈不上"起源"这个问题，此处
            # 直接用这次编辑的会话/时间当占位，供人工核对"确实是纯追加"）。
            if old_text in new_text:
                stats["rejected_by_append_only"] += 1
                candidates.append(_record("append_only", old_text,
                                           e["session_id"], e["timestamp"], new_text))
                continue

            before = [t for t in file_timeline if (t["timestamp"] or "") < (e["timestamp"] or "")]
            origin = _find_origin(before, old_text)
            if origin is not None:
                origin_known = True
                old_session = origin["session_id"]
                old_timestamp = origin["timestamp"]
            else:
                # 起源不明（多半来自文件创建时的 Write，本模块不处理全文
                # diff）。此时无法核实跨会话/跨天，保守地不产出候选，但
                # 仍计入 append_only 之外的"未知起源"统计，不静默丢弃。
                origin_known = False
                old_session = None
                old_timestamp = None

            if not origin_known:
                stats.setdefault("skipped_unknown_origin", 0)
                stats["skipped_unknown_origin"] += 1
                continue

            old_day = (old_timestamp or "")[:10]
            new_day = (e["timestamp"] or "")[:10]

            # 同一次"坐下来写"内的连续修订：多半是起草/润色，不是"旧知识
            # 被推翻"。
            #
            # ⚠️ 实测教训：判据不能只看 session_id 相等——本数据集里
            # Claude Code 会话可以跨天恢复（`--resume`/长驻会话），实测
            # session_id="1cd1ff9f" 一个会话横跨 **23 天**（07-13~08-13），
            # 若只按 session_id 判"同会话"，会把这 23 天里发生的、真正
            # 跨天的知识修订也一并当成"同一次调试的连续尝试"误杀——
            # pttime-seedbox.md 的大半修订史正是发生在这一个会话里。
            # 改成"同会话 **且** 同一天"（同一次坐下来写的操作时间窗）
            # 才是"连续尝试"这个概念真正对应的判据；同会话跨天的修订，
            # 即便技术上是"resume 了同一个 session_id"，语义上仍是
            # "过了一天回来重新审视"，应当放行为候选。
            if old_session == e["session_id"] and old_day == new_day:
                stats["rejected_by_same_session"] += 1
                candidates.append(_record("same_sitting", old_text, old_session,
                                           old_timestamp, new_text))
                continue

            candidates.append(_record("candidate", old_text, old_session, old_timestamp, new_text))
            stats["doc_revision_candidates"] += 1

    return candidates, stats


# ═════════════════════════════════════════════════════ 知识单元层：cases.jsonl 方法推翻挖掘（T2）

def mine_case_conflicts(recs):
    """从 tool_execution 原始记录里先复用 mine_cases.mine() 得到已清洗的
    案例（权限/探查动词/相似度过滤全部生效），再按错误签名分组找"同一
    问题、不同修复"的候选（T2 方法推翻的知识单元层来源）。

    复用 mine_cases 的清洗管线而不是自己重新实现失败→修复配对，理由：
    那套管线是四轮复核+多次人工抽样才收敛到当前精度的（85-90%），重新
    发明一遍既浪费又大概率引入新噪声。

    返回 (candidates, stats)。
    """
    stats = {
        "case_total": 0,
        "rejected_by_permission": 0,
        "rejected_by_infra": 0,
        "case_signature_groups": 0,
        "case_conflict_candidates": 0,
    }
    cases, _mine_cases_stats = mine_cases_mine(recs)
    stats["case_total"] = len(cases)

    groups = {}
    for c in cases:
        err = c.get("error_text") or ""
        # 防御性再过滤（设计方案要求）：即便 mine_cases 已经过滤过，冲突
        # 模块自己的签名分组逻辑不同，独立再挡一次，不依赖上游没有回归。
        if PERMISSION_PATTERNS.search(err):
            stats["rejected_by_permission"] += 1
            continue
        if INFRA_FAILURE_PATTERNS.search(err):
            stats["rejected_by_infra"] += 1
            continue
        sig = conflict_signature(c, "case")
        if sig is None:
            continue
        groups.setdefault(sig, []).append(c)

    candidates = []
    for sig, group in groups.items():
        if len(group) < 2:
            continue
        stats["case_signature_groups"] += 1
        group.sort(key=lambda c: c["failed_at"] or "")
        # 组内两两比较（通常组很小，O(n^2) 无妨）；只有不同 session 且
        # 修复命令确实不同的两条才构成候选——同会话内的多次尝试不算冲突
        # （debugging 过程中的连续试错，不是知识被推翻）。
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                # 同判据见 mine_doc_revisions：同 session_id 且同一天才算
                # "同一次调试的连续尝试"，不能只看 session_id（本数据集
                # 的会话可跨天恢复，实测最长一个会话横跨 23 天）。
                if a["session_id"] == b["session_id"] and \
                        (a["failed_at"] or "")[:10] == (b["failed_at"] or "")[:10]:
                    continue
                fix_a = json.dumps(a["fixed_input"], ensure_ascii=False, sort_keys=True)
                fix_b = json.dumps(b["fixed_input"], ensure_ascii=False, sort_keys=True)
                if fix_a == fix_b:
                    continue
                # 签名分组只是粗筛（同错误类型+同前缀），实测发现"退出码/
                # Traceback 样板相似但谈的是完全不同事件"的假阳性（见
                # normalize_error_text 头部与 CASE_SIGNATURE_OVERLAP_THRESHOLD
                # 处的实测记录），必须再用两条**失败命令**（不是错误文本——
                # 错误文本本身常是双方都通用的短语，测不出区分度）的重叠度
                # 把它们筛掉，不能只信分组结果。
                overlap = _text_overlap_ratio(
                    json.dumps(a["failed_input"], ensure_ascii=False),
                    json.dumps(b["failed_input"], ensure_ascii=False))
                if overlap < CASE_SIGNATURE_OVERLAP_THRESHOLD:
                    stats.setdefault("rejected_by_low_text_overlap", 0)
                    stats["rejected_by_low_text_overlap"] += 1
                    continue
                hint = bool(NEGATION_HINT_PATTERNS.search(
                    str(b.get("fixed_output", "")) + json.dumps(b["fixed_input"], ensure_ascii=False)))
                candidates.append({
                    "candidate_id": None,
                    "source": "S3_case_signature",
                    "type_guess": "T2",
                    "structural_label": "candidate",
                    "kind": "case",
                    "old": {
                        "stable_key": stable_key(a, "case"),
                        "session_id": a["session_id"], "failed_at": a["failed_at"],
                        "tool_name": a["tool_name"],
                        "text": "失败：%s | 修复：%s" % (
                            json.dumps(a["failed_input"], ensure_ascii=False),
                            json.dumps(a["fixed_input"], ensure_ascii=False)),
                    },
                    "new": {
                        "stable_key": stable_key(b, "case"),
                        "session_id": b["session_id"], "failed_at": b["failed_at"],
                        "tool_name": b["tool_name"],
                        "text": "失败：%s | 修复：%s" % (
                            json.dumps(b["failed_input"], ensure_ascii=False),
                            json.dumps(b["fixed_input"], ensure_ascii=False)),
                    },
                    "cross_day": (a["failed_at"] or "")[:10] != (b["failed_at"] or "")[:10],
                    "negation_hint": hint,
                })
                stats["case_conflict_candidates"] += 1

    return candidates, stats


# ═════════════════════════════════════════════════════ dataset 层：用户推翻信号（T3 补充矿脉）

def mine_user_reversal_signals(recs):
    """扫 user_input 记录里的环境重置表述（"我重装了一下系统"这类），
    标记该会话时间点之后一批知识需要重验——这是 T3（环境批量失效）唯一
    直接的信号来源。

    ⚠️ 与 S2/S3 不同，这类信号**不产出"旧claim vs 新claim"配对**（用户
    只是说了一句话，没有给出具体哪条知识失效），只能标记"这个时间点之后，
    这个会话里涉及的知识需要人工复核批量失效范围"。自动化置信度低，
    候选池里用 auto_confidence="low" 标注，供 L3 人工优先审阅，不直接
    产出可裁决的冲突对。
    """
    stats = {"user_reversal_signals": 0}
    signals = []
    for r in recs:
        if r.get("record_type") != "user_input":
            continue
        text = r.get("text") or ""
        if ENV_RESET_PATTERNS.search(text):
            signals.append({
                "candidate_id": None,
                "source": "S4_user_reversal",
                "type_guess": "T3",
                "kind": "user_signal",
                "session_id": r.get("session_id"),
                "timestamp": r.get("timestamp"),
                "text": text,
                "auto_confidence": "low",
            })
            stats["user_reversal_signals"] += 1
    return signals, stats


# ═════════════════════════════════════════════════════ dataset 层：命令输出漂移挖掘（S1 / T1+T3 客观来源）
#
# 与 S2/S3/S4 三条矿脉不同，S1 不看任何自然语言措辞——它只比对"同一条命令
# 跨天执行、输出是否变了"。这是 T1（事实过期）/T3（环境批量失效）唯一
# 不依赖措辞对齐的客观来源，供给方主要是《每日锚点16条.md》：13 项固定
# 读数每天跑一遍，其中第 13/14 两条（/usr 读写态、sshd 是否存在）在磐石
# 系统重启后必然翻转，是 T3 的客观真信号（详见 S1检测器-实施方案.md §一）。

_TMP_PATH_RE = re.compile(r"/tmp/[\w.-]+")
_CMD_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_OUT_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
_OUT_TIME_RE = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")
_OUT_PID_RE = re.compile(r"\bpid[= ]?\d+\b", re.IGNORECASE)


def normalize_cmd(c: str) -> str:
    """去掉随运行变化的部分，让「同一条命令」能被识别为同一个 key。"""
    c = _TMP_PATH_RE.sub("/tmp/<T>", c)
    c = _CMD_DATE_RE.sub("<DATE>", c)
    return re.sub(r"\s+", " ", c).strip()


def normalize_out(o: str) -> str:
    """去掉每次必然不同、但不代表知识变化的部分。
    不做这一步，同一条命令的输出永远「不同」，全是假阳性。"""
    o = _OUT_TS_RE.sub("<TS>", o)
    o = _OUT_TIME_RE.sub("<TIME>", o)
    o = _OUT_PID_RE.sub("pid<N>", o)
    o = _TMP_PATH_RE.sub("/tmp/<T>", o)
    return re.sub(r"\s+", " ", o).strip()


# 锚点里的两条环境探针，取值翻转 = 环境变更（判型靠命令本身，不靠措辞，
# 见 adjudicate() 里 kind=="cmd_drift" 分支与 S1检测器-实施方案.md §五）。
ENV_PROBE_COMMANDS = {
    "报告 /usr 当前是只读还是可写。",
    "报告 sshd 可执行文件当前是否存在。",
}


def mine_command_output_drift(records, min_days=2, sim_threshold=0.70):
    """S1 矿脉：同一条命令跨天执行、输出显著不同 → 事实过期候选。

    这是 T1/T3 的客观来源——不依赖任何自然语言措辞，只比对读数。
    锚点（每日固定 16 条）是它的主要供给方。

    四条判据（缺一不可，见设计方案 §3.1）：
      1. tool_name == "Bash"（最容易踩的坑——Write/Read 同路径不是"同一
         条命令跑两次"，见 §四.1 的 82 组假阳性教训）
      2. 归一化后命令字符串完全相同
      3. 至少跨 2 个不同日期 且 至少跨 2 个不同 session_id（同一天/同一
         会话内的重复执行是连续尝试，不是知识过期——长会话可跨 23 天，
         只判"跨天"会把连续调试误杀成冲突，见 §四.3）
      4. 首末两次输出的 difflib 相似度 < sim_threshold（读数没变的不算）

    返回 (candidates, stats)，与另三条矿脉的返回结构保持一致。
    """
    import difflib

    stats = {
        "cmd_drift_candidates": 0,
        "rejected_by_non_bash": 0,
        "rejected_by_permission": 0,
        "rejected_by_same_day": 0,
        "rejected_by_same_session": 0,
        "rejected_by_high_similarity": 0,
    }

    by_cmd = {}
    for r in records:
        if r.get("record_type") != "tool_execution":
            continue
        if r.get("tool_name") != "Bash":
            stats["rejected_by_non_bash"] += 1
            continue
        if r.get("is_error"):
            continue
        if r.get("execution_status") != "success":
            continue
        ti = r.get("tool_input") or {}
        cmd = ti.get("command") or ""
        if not cmd:
            continue
        out_text = r.get("tool_output") or ""
        if PERMISSION_PATTERNS.search(out_text):
            stats["rejected_by_permission"] += 1
            continue
        key = normalize_cmd(cmd)
        by_cmd.setdefault(key, []).append({
            "timestamp": r.get("timestamp"),
            "session_id": r.get("session_id"),
            "output": normalize_out(out_text),
        })

    candidates = []
    for cmd_key, runs in by_cmd.items():
        if len(runs) < 2:
            continue
        runs.sort(key=lambda x: x["timestamp"] or "")
        days = sorted(set((x["timestamp"] or "")[:10] for x in runs))
        sessions = sorted(set(x["session_id"] for x in runs))

        if len(days) < min_days:
            stats["rejected_by_same_day"] += 1
            continue
        if len(sessions) < 2:
            stats["rejected_by_same_session"] += 1
            continue

        old, new = runs[0], runs[-1]
        ratio = difflib.SequenceMatcher(None, old["output"], new["output"]).ratio()
        if ratio >= sim_threshold:
            stats["rejected_by_high_similarity"] += 1
            continue

        candidates.append({
            "candidate_id": None,   # main() 统一编号
            "kind": "cmd_drift",
            "source": "anchor",
            "command": cmd_key,
            "old": old,
            "new": new,
            "n_runs": len(runs),
            "n_days": len(days),
            "similarity": round(ratio, 4),
            "cross_day": True,
            "structural_label": "candidate",
            "type_guess": "T3" if cmd_key in ENV_PROBE_COMMANDS else "T1",
        })
        stats["cmd_drift_candidates"] += 1

    return candidates, stats


# ═════════════════════════════════════════════════════ 统一入口

def mine(recs):
    """挖掘全部冲突候选。返回 (conflicts, stats)。

    conflicts 里混着三种东西，靠 `structural_label`/`type_guess` 区分：
      · `structural_label="candidate"`（type_guess 是 T1_or_T4 / T2）——
        L1 结构层认为"可能是冲突"，交 L3 人工裁决；
      · `structural_label="append_only"|"same_sitting"`（type_guess 恒为
        NOT_CONFLICT）——L1 结构层认为"不是冲突"，但**带着和正例同等
        完整的原文证据**，是评测集负例的主要来源（见文件头设计说明）；
      · `kind="user_signal"`（S4）——T3 的低置信度信号，不是完整的
        新旧claim配对，不直接进裁决，只供人工复核批量失效范围。

    stats 汇总各阶段过滤计数，供 test_detectors.py 的阴性验证断言使用。
    """
    doc_candidates, doc_stats = mine_doc_revisions(recs)
    case_candidates, case_stats = mine_case_conflicts(recs)
    signals, signal_stats = mine_user_reversal_signals(recs)
    cmd_candidates, cmd_stats = mine_command_output_drift(recs)

    conflicts = doc_candidates + case_candidates + signals + cmd_candidates
    for i, c in enumerate(conflicts):
        c["candidate_id"] = "K-%04d" % (i + 1)

    stats = {}
    stats.update(doc_stats)
    stats.update(case_stats)
    stats.update(signal_stats)
    # ⚠️ S1（cmd_drift）与既有矿脉共用了两个 key 名——`rejected_by_
    # permission`（case 路径也用它）、`rejected_by_same_session`（doc 路径
    # 也用它），但两边挡的是完全不同的东西（doc 挡的是 memory/*.md 编辑，
    # cmd_drift 挡的是 Bash 输出漂移）。直接 dict.update() 会让先写入的
    # 那条矿脉的真实计数悄悄被覆盖（例如 doc 路径实测的 same_session=37
    # 会被 cmd_drift 的计数直接顶掉，报告里"同一次坐下来写"那条数字就
    # 变假了）。因此 S1 自己这两个重名计数器改用 `cmd_` 前缀单独暴露
    # （`cmd_rejected_by_permission` / `cmd_rejected_by_same_session`），
    # 不与既有 key 争位——`mine_command_output_drift()` **自身**返回的
    # stats 字典里仍然是设计方案 §3.5 写的原始 key 名，test_detectors.py
    # 的阴性验证直接调用那个函数拿到的就是未改名的原始版本，不受此影响。
    for k, v in cmd_stats.items():
        key = ("cmd_" + k) if k in ("rejected_by_permission", "rejected_by_same_session") else k
        stats[key] = v
    stats["conflict_candidates_total"] = len(conflicts)
    stats["positive_candidates_total"] = sum(
        1 for c in conflicts if c.get("structural_label") == "candidate"
        or c.get("kind") in ("case", "cmd_drift"))
    return conflicts, stats


# ═════════════════════════════════════════════════════ L1 规则裁决器（确定性，不调用 LLM）

# ⚠️ 这组正则是 NEGATION_HINT_PATTERNS 的**子集细分**：区分"旧结论本身
# 当时为假"（记录原文自己承认判断错了）与"前提消失"（环境/状态变了，旧
# 结论在它成立的那个时间窗内并没有错）。两者在设计方案 §四第 3 点被要求
# 分开标注——直接判"旧的错了"会丢掉信息，也不符合真实工程。
# 取词依据：本模块人工裁决 18 条正例时，实际写出"旧结论当时为假"的 9 条
# 里，原文全部含有下列某个措辞（"判断有误/判断反了/理解偏差/是错的/别用错/
# 拍脑袋/重大教训"），据此收敛出这份表，不是凭空猜的关键词。
SELF_CORRECTION_PATTERNS = re.compile(
    r"判断有误|判断反了|理解反了|理解偏差|是错的|错误结论|重大教训"
    r"|拍脑袋|别再信|别用错|亲自纠正|亲自改定",
)


def _ts_of(side: dict) -> str:
    return side.get("timestamp") or side.get("failed_at") or side.get("asserted_at") or ""


def is_conflict(pair: dict) -> bool:
    """判定器：给任意一对知识（不要求已经过 mine() 筛选），判断"值得进入
    裁决"这一步（对应设计方案 §六.1 的"判定准确率"子指标，与 adjudicate()
    的"裁决正确率"是两件事——那个只在**已认定冲突**的一对里判断谁生效）。

    规则与 mine_doc_revisions() 内部用的结构过滤三条完全一致（同文件、
    非纯追加、跨会话或跨天），这里抽成独立可调用函数：mine() 走的是
    "文件内逐条编辑历史"的场景，天然满足同文件；但 is_conflict() 要处理
    的是**任意给定的一对**（包括跨文件的无关配对），必须显式加"同一
    知识来源"这道门槛——两条来自不同文件/不同 case 签名的知识，字面上
    再怎么像，也不构成"新旧知识"关系（design §六.2 的"两条无关知识"负例
    正是测这一点）。

    ⚠️ 已知的真实局限（不是这次没做，是结构信号本身做不到）：K-0067
    （music-sweep 一句话精简掉"organize+lyrics"几个字）结构上满足全部
    三条（同文件、非纯追加、跨会话跨天），但语义上没有真实信息差异，
    人工复核后判定不算冲突——这条规则会把它误判为 True。这正是设计方案
    §三坚持"L1 只能召回候选、L2/L3 才能定案"的实证，不是可以简单打个
    补丁修掉的 bug（"这次编辑改变的字符数/词数是否足够多"这类阈值只会
    把误判换一个方向，未必更准，暂不在规则里加这类启发式，如实记录在
    《知识冲突处理说明报告.md》的局限声明里）。
    """
    old, new = pair["old"], pair["new"]
    if pair.get("kind") == "user_signal":
        return True   # T3 信号本身就是"值得复核"的判定，不走下面的结构规则

    if pair.get("kind") == "case":
        # case 类知识没有 file_path 概念，"同一来源"退化成"同一 stable_key
        # 前缀"（mine_case_conflicts 分组时已经保证了同错误签名），这里
        # 只需再确认不是同一会话内的连续尝试。
        return old.get("session_id") != new.get("session_id")

    # doc_revision：必须是同一文件——不同文件的两条知识，不构成"新旧"关系
    old_fp, new_fp = old.get("file_path"), new.get("file_path")
    if not old_fp or not new_fp or old_fp != new_fp:
        return False

    old_text = old.get("text") or old.get("claim") or ""
    new_text = new.get("text") or new.get("claim") or ""
    # 纯追加：旧文本整体被保留在新文本里，不构成冲突（与 mine_doc_
    # revisions 的 append_only 判据同一条规则，见那边的实测教训注释）。
    #
    # ⚠️ 评测集里的 claim 字段被截断到 300 字（见 review_and_freeze_
    # conflicts.py 的 _claim()），原文更长时这里的子串判断可能因为截断
    # 而失真——这是评测集摘要化本身带来的已知精度损失，不是这条规则
    # 的逻辑错误，同样记入报告的局限声明。
    if old_text and new_text and old_text in new_text:
        return False

    old_sid, new_sid = old.get("session_id"), new.get("session_id")
    old_day = (_ts_of(old))[:10]
    new_day = (_ts_of(new))[:10]
    if old_sid == new_sid and old_day == new_day:
        return False   # 同一次"坐下来写"，多半是起草润色

    return True


def adjudicate(pair: dict) -> dict:
    """规则裁决器：给一对已确认结构候选的知识，判断谁生效、旧知识的
    适用边界、reason 是"旧结论当时为假"还是"前提消失"。

    ⚠️ 确定性、不调用任何 LLM/网络请求——这是它能进 L1、能被
    mutate.py 变异测试保护、能被公开测量"裁决正确率"的前提。它的输出
    会拿去跟 `review_and_freeze_conflicts.py` 人工手写的 `conflicts_
    eval.jsonl` 比对算准确率，两者是完全独立的两条代码路径（前者是
    规则，后者是人工通读原文），不能自己验自己。

    ⚠️ 核心规则不信任输入字典里"old"/"new"这两个键的字面标签，只信
    时间戳——谁的断言时间更晚，谁的结论生效。这是"时间序反转则裁决
    反转"这条阴性验证能成立的原因：调用方把 old/new 的时间戳对调后，
    "更晚"这个身份会转移到另一侧，verdict 字符串必须跟着变
    （new_supersedes_old ⇄ old_supersedes_new），不能仍然写死
    "new_supersedes_old"（那样会让对调测试测不出任何东西）。
    """
    old, new = pair["old"], pair["new"]
    old_ts, new_ts = _ts_of(old), _ts_of(new)

    if new_ts >= old_ts:
        verdict, winner, loser = "new_supersedes_old", new, old
    else:
        verdict, winner, loser = "old_supersedes_new", old, new

    winner_text = winner.get("text") or winner.get("claim") or ""
    kind = pair.get("kind", "doc_revision")

    if kind == "user_signal":
        v_type = "T3"
    elif kind == "case":
        v_type = "T2"
    elif kind == "cmd_drift":
        # S1：判型靠命令本身，不靠措辞（cmd_drift 的 old/new 只有 output，
        # 没有 text/claim，winner_text 恒为空，走不到下面几条关键词分支，
        # 必须在这里单独接住）——环境探针翻转是一批知识同时失效，其余
        # 读数变化是单个事实槽位的值变了。见 S1检测器-实施方案.md §五。
        v_type = "T3" if pair["command"] in ENV_PROBE_COMMANDS else "T1"
    elif ENV_CHANGE_PATTERNS.search(winner_text):
        v_type = "T3"
    elif SELF_CORRECTION_PATTERNS.search(winner_text):
        v_type = "T2"
    elif NEGATION_HINT_PATTERNS.search(winner_text):
        v_type = "T1"
    else:
        v_type = "T4"

    reason_kind = ("old_was_false" if SELF_CORRECTION_PATTERNS.search(winner_text)
                   else "premise_disappeared")

    return {
        "type": v_type,
        "verdict": verdict,
        "scope_of_old": f"在 {_ts_of(loser)} 之前/期间成立（此后被 {_ts_of(winner)} 的记录取代）",
        "reason_kind": reason_kind,
        "retrievable": {"old": True, "new": True},
        "default_return": "new" if verdict == "new_supersedes_old" else "old",
        "adjudicated_by": "rule_based_L1",
    }


def _all_known_stable_keys(candidates=None) -> dict:
    """建一份 stable_key → 原始记录 的索引，供 retrieve_old() 回溯查找。
    索引源是 conflicts.jsonl（挖掘阶段见过的所有 old/new 快照）。
    """
    if candidates is None:
        candidates = []
        pool_path = os.path.join(BASE, "conflicts.jsonl")
        if os.path.exists(pool_path):
            with open(pool_path, encoding="utf-8") as f:
                candidates = [json.loads(line) for line in f if line.strip()]
    index = {}
    for c in candidates:
        for side_key in ("old", "new"):
            side = c.get(side_key)
            if side and side.get("stable_key"):
                index[side["stable_key"]] = side
    return index


def retrieve_old(stable_key: str, index: dict = None) -> dict:
    """按稳定标识回溯旧知识——赛题"版本化管理与回溯"的落地点。

    ⚠️ 查不到必须报错，不能悄悄返回 None：那样"这条知识被删了/从没
    存在过"和"只是这次没查到"在调用方看来长得一样，回溯能力名存实亡
    （设计方案 §六.4 第⑤条阴性验证专门盯的就是这个）。
    """
    if index is None:
        index = _all_known_stable_keys()
    if stable_key not in index:
        raise KeyError(
            f"回溯失败：stable_key={stable_key!r} 不在已知索引里"
            f"（可能从未挖掘到，或索引已被破坏——两种情况都不该返回 None）")
    return index[stable_key]


# ═════════════════════════════════════════════════════ 检索侧集成：supersedes/superseded_by

def build_supersede_edges(conflicts_eval_path=None) -> dict:
    """从 conflicts_eval.jsonl 里 label=="conflict" 的行构建 supersedes/
    superseded_by 边表，key 是 stable_key（设计方案 §0.1：不用 doc_id，
    重挖不失效）。

    ⚠️ 诚实的适用范围声明：这份边表对**任何**能算出 stable_key 的知识
    单元通用（case/workflow/template 皆可），但本项目实测的 18 条确认
    冲突**全部是 doc_revision 类型**（S3 case 签名分组的 5 组候选，
    见 CASE_SIGNATURE_OVERLAP_THRESHOLD 处的记录，全部被判定为不相关
    事件的假阳性，未产出任何 case 类正例）。而 doc_revision 指向的是
    memory/*.md 的段落快照，不是 case/workflow/template 索引里的条目
    ——所以下面 search_knowledge.py 里"按 supersede 边表过滤检索结果"
    这条机制，在当前真实数据上**是一个正确实现但触发不到任何真实
    过滤效果的 no-op**：真正能检索到的旧知识不在这批冲突记录覆盖的
    范围内。机制本身用合成数据验证过是对的（见 test_detectors.py），
    真要在 case/workflow 类知识上看到它生效，要等这类冲突被挖掘到
    （目前 S3 矿脉太浅）。这个边界写进《知识冲突处理说明报告.md》，
    不在代码注释里含糊带过。
    """
    if conflicts_eval_path is None:
        conflicts_eval_path = os.path.join(BASE, "conflicts_eval.jsonl")
    edges = {}
    if not os.path.exists(conflicts_eval_path):
        return edges
    with open(conflicts_eval_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            k = json.loads(line)
            if k.get("label") != "conflict":
                continue
            old_key, new_key = k["old"]["stable_key"], k["new"]["stable_key"]
            winner, loser = ((old_key, new_key) if k.get("verdict") == "old_supersedes_new"
                             else (new_key, old_key))
            edges.setdefault(winner, {"supersedes": [], "superseded_by": []})
            edges.setdefault(loser, {"supersedes": [], "superseded_by": []})
            edges[winner]["supersedes"].append(loser)
            edges[loser]["superseded_by"].append(winner)
    return edges


def main():
    import argparse
    from freeze_guard import add_arguments, guard, write_provenance

    args = add_arguments(argparse.ArgumentParser(
        description="知识冲突挖掘器")).parse_args()
    out_path = guard(os.path.join(BASE, "conflicts.jsonl"),
                      force=args.force, out_override=args.out)

    recs = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    conflicts, stats = mine(recs)

    print("=" * 70)
    print("知识冲突挖掘统计")
    print("=" * 70)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print()
    by_source = {}
    for c in conflicts:
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
    print(f"候选池合计 {len(conflicts)} 条，按来源：{by_source}")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for c in conflicts:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n已写：{out_path}")

    write_provenance(__file__, out_path, [DATASET])


if __name__ == "__main__":
    main()
