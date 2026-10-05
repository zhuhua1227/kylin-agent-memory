#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_manifest.py —— 交付物 sha256 清单（R2-30，P-9/P-12b；R3-36 扩面 + 接线）

═══ 用途 ═══════════════════════════════════════════════════════════

`records/*.json` 是本项目全部数字主张的最终依据。这份清单本身**不能
证明记录没被伪造**（能改记录内容的人也能同步改这份清单，参见
可复现性说明.md 里反复强调的"JSON能编"这条边界声明）——它的价值和
`verify_reports.py` 一样，是防"自己不小心手改了一个数字忘了说明"，
是给读者一个"这份记录自冻结以来有没有被意外改动过"的快速核对点，
不是防造假机制。

═══ 第三轮独立复核 R3-36 的两处整改 ═════════════════════════════════

**① 没人跑就等于没有。** 这份清单此前**没有接进 `reproduce_L1.py`**——
它是唯一一个复核员一定会跑的入口，清单不在里面，就只能指望别人主动想起
来敲 `--check`，实际上等于不存在。现已接进去，一键复现开头就核。

**② 只盖了 `records/*.json`，盖不住真正会腐烂的东西。** 记录是产物，
产物之所以可信，是因为它由某份代码在某份输入上跑出来。只锁产物、不锁
产出它的脚本，恰恰漏掉了本项目最常见的那类失效——**脚本改了、记录没
重跑、报告里的数字于是变成"上一版代码的产物"**（这正是《代码产物落差
登记表》整张表在处理的问题）。现在把 `claims.json` 与各模块的关键脚本
一并纳入。

两类条目的失配后果不同，因此**判定强度也不同**：

  · `records/*.json`、`claims.json` 失配 = **失败**。这些是冻结的证据与
    主张，除了重新测量之外没有正当理由变动，变了就说明有人手改过。

  · 脚本失配 = **警告**，并明确指向"对应记录可能已过期，请重跑测量后
    重新生成清单"。开发期间改脚本是正常的，把它判成失败只会逼人麻木地
    重新生成清单、失去信号；判成警告并说清后果，才是这条检查真正的用处。

  · 旁边模块目录（评测集/、知识检索/、多源数据整合/）的脚本标记为
    **可选**——只拿到 `可复现性体系/` 一个文件夹时它们本来就不在，
    那是"没拿到那部分交付物"，不是"文件被删了"（同 R3-31 的处理口径）。

用法：
    python generate_manifest.py            # 重新生成 records/MANIFEST.sha256
    python generate_manifest.py --check    # 核对当前文件与清单是否一致
"""

import argparse
import hashlib
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RECORDS_DIR = os.path.join(BASE, "records")
MANIFEST_PATH = os.path.join(RECORDS_DIR, "MANIFEST.sha256")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 条目分类。KIND_* 决定失配时的判定强度（见文件头说明）。
KIND_EVIDENCE = "evidence"   # 失配 = 失败
KIND_SCRIPT = "script"       # 失配 = 警告（记录可能过期）

# 本目录内的关键脚本：产出记录的、核对记录的、以及核对"核对器"的
_LOCAL_SCRIPTS = [
    "measure.py", "measure_L1.py", "measure_B2.py",
    "reproduce_L1.py", "verify_reports.py",
    "test_detectors.py", "mutate.py", "generate_manifest.py",
]

# 旁边模块目录里的脚本：同样产出报告里的数字，但可能没随交付一起拿到
_SIBLING_SCRIPTS = [
    "../评测集/measure_recall.py",
    "../评测集/measure_corpus_size_effect.py",
    "../评测集/measure_recall_ci.py",
    "../知识检索/mine_cases.py",
    "../知识检索/mine_workflows.py",
    "../知识检索/mine_templates.py",
    "../知识检索/render_units.py",
    "../多源数据整合/anonymize.py",
]

# 冲突判定规则冻结锚点（《冲突规则冻结-执行方案.md》§三，2026-08-27）。
# 这几个文件按 **evidence** 处理——失配 = reproduce_L1 直接报红、退出码非零，
# 不是"脚本可能过期"的黄字警告。理由：报告里所有"留出集"措辞都建立在
# "规则先于 D2~D7 数据冻结"之上，规则文件被动过而没人发现，这条叙事就
# 塌了，必须当场看见。09-02 用 `reproduce_L1.py --check-only` 重算即证伪。
#   · mine_conflicts.py            —— is_conflict()/adjudicate() 判定+裁决规则
#   · review_and_freeze_conflicts.py —— ADJUDICATIONS 人工裁决 + 冻结逻辑
#   · measure_conflicts.py         —— M-301~305 的测量口径
#   · conflicts.jsonl              —— 候选池（149 行），留出集分界线
#   · conflicts_eval.jsonl         —— 36 对评测集，M-302~304 的 ground truth
#   · conflicts_eval_frozen.json   —— 版本/sha256/行数冻结元信息
# （mine_cases.py 是 mine_conflicts 的依赖，已在 _SIBLING_SCRIPTS 里，
#  其 S3 分支在本数据集上产出 0 正例，对冻结的评测集无影响，不重复登记。）
_FROZEN_RULES = [
    "../知识检索/mine_conflicts.py",
    "../知识检索/review_and_freeze_conflicts.py",
    "measure_conflicts.py",
    "../知识检索/conflicts.jsonl",
    "../知识检索/conflicts_eval.jsonl",
    "../知识检索/conflicts_eval_frozen.json",
]


def _rel(path):
    """统一成相对 BASE 的正斜杠路径，保证 Windows/Linux 生成的清单一致。"""
    return os.path.relpath(path, BASE).replace(os.sep, "/")


def manifest_entries():
    """返回 [(相对路径, 类别, 是否必需)]，顺序稳定。"""
    entries = []
    for root, _dirs, fs in os.walk(RECORDS_DIR):
        for fn in sorted(fs):
            if fn.endswith(".json") and fn != "MANIFEST.sha256":
                entries.append((_rel(os.path.join(root, fn)),
                                KIND_EVIDENCE, True))
    entries.sort()
    entries.append(("claims.json", KIND_EVIDENCE, True))
    entries += [(p, KIND_SCRIPT, True) for p in _LOCAL_SCRIPTS]
    entries += [(p, KIND_SCRIPT, False) for p in _SIBLING_SCRIPTS]
    # required=False：孤立打包（只带 可复现性体系/）时这些旁目录文件不在，
    # 属正常，不判失败——但只要文件**在**，哈希失配就是 evidence 级失败
    # （见 check() 里 KIND_EVIDENCE 分支）。这正是冻结要的语义：有就必须对得上。
    entries += [(p, KIND_EVIDENCE, False) for p in _FROZEN_RULES]
    return entries


def _sha256_of(path, normalize_eol=False):
    """默认算文件的**原始字节**——清单要锁的就是字节。

    `normalize_eol=True` 只在核对失败后用于**诊断**：把 CRLF 折成 LF 再算
    一次，用来区分"内容真的变了"和"只是换行符不同"。它绝不参与生成，
    也不作为通过条件，否则这份清单就锁不住"只改了行尾"的改动了。
    """
    with open(path, "rb") as f:
        data = f.read()
    if normalize_eol:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _parse_manifest():
    """读清单，返回 {相对路径: (sha256, 类别)}。老格式（无类别列）按证据处理。"""
    out = {}
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("  ")
            if len(parts) >= 3:
                h, kind, name = parts[0], parts[1], "  ".join(parts[2:])
            else:
                h, name, kind = parts[0], "  ".join(parts[1:]), KIND_EVIDENCE
            out[name] = (h, kind)
    return out


def check(verbose=True):
    """核对当前文件与清单。返回 (失败列表, 警告列表, 缺失的可选项列表)。

    抽成函数是为了让 `reproduce_L1.py` 直接调（R3-36 第①条），
    不必去解析另一个进程的 stdout。
    """
    if not os.path.exists(MANIFEST_PATH):
        return ([(_rel(MANIFEST_PATH), "清单文件本身不存在，无法核对")], [], [])

    expected = _parse_manifest()
    entries = manifest_entries()
    failures, warnings, absent_optional = [], [], []
    seen = set()

    for rel, kind, required in entries:
        seen.add(rel)
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            if required:
                failures.append((rel, "文件不存在"))
            else:
                absent_optional.append(rel)
            continue
        if rel not in expected:
            (failures if kind == KIND_EVIDENCE else warnings).append(
                (rel, "文件存在但清单里没有它——清单未随新增文件更新"))
            continue
        actual = _sha256_of(path)
        if expected[rel][0] != actual:
            msg = f"清单里是 {expected[rel][0][:16]}…，实际是 {actual[:16]}…"
            # 换行符差异要单独说清楚，绝不能让它以"证据被改动过"的措辞出现。
            # 仓库存 LF，而 Windows 上 git 默认 core.autocrlf=true 会在
            # checkout 时换成 CRLF——那样每一个文件都对不上，读者第一眼看到
            # 的却是"我们篡改了证据"。仓库里已用 .gitattributes 的 eol=lf
            # 钉死，这里是万一（比如从 zip 解包、或有人覆盖了属性）的兜底：
            # 把 CRLF 折成 LF 再算一次，对上了就说明只是换行符，不是内容。
            if _sha256_of(path, normalize_eol=True) == expected[rel][0]:
                # 仍然计为失败（清单锁的是字节，行尾变了就是变了），但措辞
                # 必须准确：说成"证据被改动过"会把一次环境问题描述成造假。
                eol_msg = ("内容逐字一致，只是行尾是 CRLF 而清单按 LF 计算——"
                           "**不是证据被改动**。仓库已用 .gitattributes 钉死 "
                           "eol=lf，出现这个提示多半是解包方式或本地 git 配置"
                           "覆盖了它；把文件转回 LF 即可，不必怀疑内容")
                (failures if kind == KIND_EVIDENCE else warnings).append(
                    (rel, eol_msg))
                continue
            if kind == KIND_EVIDENCE:
                failures.append((rel, msg + "（证据被改动过）"))
            else:
                warnings.append((rel, msg + "（脚本改过，对应 records/ 里的"
                                        "数字可能是上一版代码的产物）"))
        # 清单里有、文件也在、哈希一致 —— 无需报告

    for rel in set(expected) - seen:
        # 清单里有但本次没枚举到：文件被删了，或是不再属于清单范围
        path = os.path.join(BASE, rel)
        kind = expected[rel][1]
        if not os.path.exists(path):
            if kind == KIND_EVIDENCE:
                failures.append((rel, "清单里有记录，但文件已不存在"))
            else:
                absent_optional.append(rel)

    if verbose:
        n = len(entries) - len(absent_optional)
        if not failures and not warnings:
            print(f"MANIFEST 一致：{n} 个文件全部核对通过"
                  + (f"（另有 {len(absent_optional)} 个可选条目本机不存在，"
                     f"属正常）" if absent_optional else "。"))
        for rel, msg in failures:
            print(f"  ✗ {rel}：{msg}")
        for rel, msg in warnings:
            print(f"  ⚠ {rel}：{msg}")
        if warnings:
            print("    脚本类失配不算失败，但意味着「代码已改、记录未重跑」——"
                  "请重跑对应测量后 python generate_manifest.py 重新生成清单，"
                  "或在《代码产物落差登记表.md》里如实登记这处落差。")
        if failures:
            print(f"\n{len(failures)} 处证据类不一致。")
    return failures, warnings, absent_optional


def generate():
    entries = manifest_entries()
    lines = [
        "# 交付物 sha256 清单（generate_manifest.py 生成，勿手改）",
        "# 格式：<sha256>  <类别>  <相对 可复现性体系/ 的路径>",
        f"# 类别 {KIND_EVIDENCE}=证据（失配即失败）  "
        f"{KIND_SCRIPT}=脚本（失配为警告，说明记录可能过期）",
    ]
    written, absent = 0, []
    for rel, kind, required in entries:
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            if required:
                print(f"  ⚠ 必需条目缺失，未写入清单：{rel}")
            else:
                absent.append(rel)
            continue
        lines.append(f"{_sha256_of(path)}  {kind}  {rel}")
        written += 1
    # newline="\n"：不写就会在 Windows 上落成 CRLF，而仓库按 .gitattributes
    # 存 LF，于是这份清单自己成了唯一一个"工作区字节 ≠ 入库字节"的文件。
    # 一份用来核对字节的文件，自己的字节先不稳定，说不过去。
    with open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"已写入 {MANIFEST_PATH}（{written} 个文件）")
    if absent:
        print(f"  （{len(absent)} 个可选条目本机不存在，已略过："
              f"{', '.join(absent)}）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="不重新生成，核对当前文件与 MANIFEST.sha256 是否一致")
    args = ap.parse_args()
    if args.check:
        failures, _warnings, _absent = check()
        return 1 if failures else 0
    return generate()


if __name__ == "__main__":
    sys.exit(main())
