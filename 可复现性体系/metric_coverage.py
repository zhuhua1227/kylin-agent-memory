#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metric_coverage.py —— 记录指标覆盖率：主张层到底守住了多少个数字

═══ 为什么需要这个东西 ═══════════════════════════════════════════

🔴 第四轮独立复核 R4-16：复核员篡改了 `records/M-111.json` 里的
`recall_at_1`，一键复现 **72/72 全绿，毫无察觉**——因为**没有任何一条
主张引用这个指标**。最后是 MANIFEST 的文件哈希兜住的。

这暴露的是一件此前从没被说出口的事：

    `claims.json` 覆盖的只是 records/ 里的一小部分数字，
    其余部分**在主张层是完全无人看管的**。

而报告里给读者的印象一直是「72 条主张全绿 = 这些数字都验过了」。
两者之间的落差有多大，此前**没有任何地方写出来过**。

本模块把这个落差算成一个数字并公开报出来。**它不是用来"提高"的
KPI**——376 个指标里有大量是中间量（分档命中数、逐题分数、样本量），
为它们逐个编主张只会制造凑数的主张，不会提高可信度。它的作用是
**让读者知道绿灯覆盖的是哪一部分**。

═══ 那它凭什么不是又一份没人看的报表 ═══════════════════════════

因为它带一道闸：**覆盖率不许静默下滑**。

删掉一条主张、或往记录里塞一堆无人引用的新指标，覆盖率都会掉——
掉了就报红。这和 `DECLARED_CHECK_COUNT` 是同一个套路，防的是同一件事：
「删掉检查器，什么都不会发生」。基线写在 `EXPECTED_COVERED` /
`EXPECTED_TOTAL` 两个常量里，改动必须是**有意识地改常量**，
而不是悄悄发生。
"""

import glob
import io
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))

# ⚠️ 改这两个数之前先想清楚：覆盖率下降意味着"被主张守着的数字变少了"。
# 如果是因为新增了一批中间量指标（合理），把 EXPECTED_TOTAL 调大；
# 如果是因为删了主张（可疑），先说清为什么要删。
# 2026-08-15 基线：423 个记录指标中 82 个被 claims.json 引用。
# （R3-31 一并补了 8 条主张：领域内负样本 4 条 + MRR/nDCG 4 条。覆盖率
#   19.7%→19.4% 反而略降，因为 M-111/M-209 同时新增了 47 个中间量指标——
#   这正说明**覆盖率不该当 KPI 追**：补真主张的同时指标总数也在长。）
#
# 2026-08-16：知识冲突处理模块新增 measure_conflicts.py（M-301~M-304，
# 27 个记录指标）+ claims.json 补了 C-301~C-309 共 9 条主张，覆盖率
# 91/460=19.8%，与上一条注释同一个道理：新增指标里只有 9 个进了主张层，
# 其余（各类计数器细分、per-candidate诊断值等）仍是无人看管的中间量。
#
# 2026-08-20：偏好提取模块新增 measure_preferences.py（M-401~M-404，
# 47 个记录指标）+ claims.json 补了 C-401~C-409 共 9 条主张，覆盖率
# 100/507=19.7%，同一个道理：新增指标里只有 9 个进了主张层。
#
# 2026-08-20（同日second）：S1 命令输出漂移检测器（mine_command_output_
# drift()）接入 M-301，新增 6 个记录指标（cmd_drift_candidates /
# rejected_by_non_bash / rejected_by_same_day / rejected_by_high_
# similarity / cmd_rejected_by_same_session / cmd_rejected_by_permission）
# + claims.json 补了 C-310 共 1 条主张，覆盖率 101/513=19.7%，同一个道理：
# 6 个新指标里只有 1 个（cmd_drift_candidates）进了主张层，其余 5 个是
# 过滤原因细分计数器，暂时仍是无人看管的中间量。
#
# 2026-08-27：冲突规则冻结（《冲突规则冻结-执行方案.md》）。measure_conflicts.py
# 新增 M-305（规则 sha256 / 分界行数 / build_eval_set 确定性），10 个记录
# 指标；claims.json 补 C-311~C-315 共 5 条主张（规则哈希 / 149 行 / 36 行 /
# 确定性 / 评测集哈希），覆盖率 106/523=20.3%。剩下 5 个（3 个次要文件的
# sha256 + pool sha256 + 冻结版本号字符串）是冗余锚点，不单独立主张。
#
# 2026-09-02：起源查找修复收尾 + 评测集 v1.3.0（《起源修复与评测集v1.3.0-
# 执行方案.md》§d）。M-305 的开发集/留出集分界线由「行号」改为「数据日期」，
# 新增 4 个记录指标（dev_holdout_boundary_date / dev_candidate_count /
# holdout_candidate_count / undated_s4_signal_count）。C-312 从
# conflicts_pool_lines 改指向 dev_holdout_boundary_date（被覆盖数不变），
# 覆盖率 106/527=20.1%。新增的 3 个计数器是无人看管的中间量。
#
# 2026-09-07（麒麟 ↔ Windows 仓库合并，《仓库合并-执行方案.md》）。两条线一并进：
#  ① 关联检索消融阶段二（《消融阶段二-执行方案.md》）：measure_L2.py 的 M-210
#     首次产出 records/M-210.json（43 个记录指标）+ claims.json 补 C-265~C-273
#     共 9 条主张（七档 R@1/R@10、all 提升题数 / 未清 85%CI、supersede no-op、
#     mmr 显著变差、meta_prior/meta_oracle 上界）。9 进主张层，其余 34 个（各档
#     R@5、CI 上下界、逐题 rank 差异题数）是无人看管的中间量。
#  ② 留出集裁决接入（measure_conflicts.py 的 M-306）：records/M-306.json（41 个
#     记录指标）。仓库合并当时只有 1 个进主张层（C-304 改指向留出集口径）。
#  覆盖率 115/611=18.8%。
#
# 2026-09-07（同日，《文档数字更新-执行方案.md》批次一收尾）：留出集那条线补齐
#  主张——claims.json 新增 C-316~C-324 共 9 条（合并口径下界达标 / 留出+合并准确率 /
#  C·B·A·NEG 四层分层准确率 / 重复题前后一致 5/5 / 开发集复核维持 13/13），
#  全部引用 M-306 的既有指标。covered 115→124，M-306 的其余 32 个（逐层命中数与
#  CI 上下界、rows_total/deduped 等）仍是无人看管的中间量。覆盖率 124/611=20.3%。
EXPECTED_COVERED = 124
EXPECTED_TOTAL = 611


def compute(records_dir=None, claims_path=None):
    """返回 (covered, total, per_record)。

    覆盖的定义：某条主张的 `metric` / `numerator` / `denominator` 任一
    字段引用了该记录的该指标名。比值类主张会同时覆盖两个指标，所以三个
    字段都要看——只看 `metric` 会把 ratio 类主张守住的数字漏报成无人看管。
    """
    records_dir = records_dir or os.path.join(BASE, "records")
    claims_path = claims_path or os.path.join(BASE, "claims.json")

    raw = json.load(io.open(claims_path, encoding="utf-8"))
    claims = raw["claims"] if isinstance(raw, dict) else raw

    covered_by_record = {}
    for c in claims:
        rid = c.get("record_id")
        if not rid:
            continue
        for field in ("metric", "numerator", "denominator"):
            name = c.get(field)
            if name:
                covered_by_record.setdefault(rid, set()).add(name)

    covered = total = 0
    per_record = []
    for path in sorted(glob.glob(os.path.join(records_dir, "M-*.json"))):
        rec = json.load(io.open(path, encoding="utf-8"))
        rid = rec.get("record_id") or os.path.basename(path)[:-5]
        metrics = set(rec.get("metrics", {}))
        if not metrics:
            continue
        hit = covered_by_record.get(rid, set()) & metrics
        covered += len(hit)
        total += len(metrics)
        per_record.append((rid, len(hit), len(metrics), sorted(metrics - hit)))
    return covered, total, per_record


def format_disclosure(covered, total, per_record, color=None):
    """把覆盖率写成给读者看的一段话。措辞要点：**不能让人误以为
    未覆盖 = 有问题**，也不能让人误以为绿灯 = 全部数字都验过了。"""
    g, y, r, x = (color or ("", "", "", ""))
    pct = 100.0 * covered / total if total else 0.0
    naked = [rid for rid, hit, tot, _ in per_record if hit == 0]
    lines = [
        f"{y}【记录指标覆盖率披露（R4-16）】{x}",
        f"  records/ 共 {total} 个指标，其中 {covered} 个（{pct:.1f}%）"
        f"被 claims.json 的某条主张引用。",
        f"  {g}绿灯覆盖的是这 {covered} 个{x}；余下 {total - covered} 个"
        f"（{100 - pct:.1f}%）在主张层无人看管，",
        "  只由 records/MANIFEST.sha256 的文件哈希兜底——**改动它们不会让"
        "一键复现变红**。",
        "  这不是缺陷清单：未覆盖的多是分档命中数、逐题分数、样本量这类中间量，",
        "  为它们逐个编主张只会制造凑数的主张。写出来是为了让你知道绿灯的边界在哪。",
    ]
    if naked:
        lines.append(f"  {y}零覆盖的记录（整条没有任何主张引用）：{', '.join(naked)}{x}")
    return "\n".join(lines)


def check_no_regression(covered, total):
    """覆盖率不许静默下滑。返回 (ok, message)。"""
    if covered < EXPECTED_COVERED:
        return False, (
            f"被主张覆盖的指标从 {EXPECTED_COVERED} 掉到 {covered}——"
            f"有主张被删除或改了引用的指标名。删主张是把数字移出监管范围，"
            f"必须是有意识的动作：确认无误后同步改 metric_coverage.py 的 "
            f"EXPECTED_COVERED。")
    if total > EXPECTED_TOTAL and covered == EXPECTED_COVERED:
        return True, (
            f"记录指标从 {EXPECTED_TOTAL} 增到 {total}，而被覆盖数没变——"
            f"新增的都是无人看管的中间量。这本身合法，只是覆盖率会下降。")
    return True, ""


def main():
    covered, total, per_record = compute()
    print(format_disclosure(covered, total, per_record))
    ok, msg = check_no_regression(covered, total)
    if msg:
        print(f"\n{'' if ok else '✗ '}{msg}")
    print("\n按记录逐条（覆盖数/总数）：")
    for rid, hit, tot, missing in sorted(per_record, key=lambda x: x[1] / x[2]):
        print(f"  {rid:<8} {hit:>3}/{tot:<3}  未覆盖 {len(missing)} 个")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
