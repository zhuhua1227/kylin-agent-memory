#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_recall_ci.py —— 召回率的 Wilson 95% 置信区间（R2-09）

═══ 为什么需要这个 ═══════════════════════════════════════════════════

召回率是二项分布比例的点估计（169道题里命中多少道），n 不大时点估计的
抽样误差不可忽略。「recall@5=87.0%」这个单一数字掩盖了一件事：如果换
169道不同的题，这个数字本身会在多大范围内波动？

第二轮独立复核指出：总体 recall@5 的 Wilson 95% 置信区间下界只有
**81.1%**——够不着 85% 这条硬指标门槛。「recall@5 达标」这个说法，
在点估计层面成立，但在统计意义上并不稳固。**无需重新跑检索**，
用已经产出的逐题结果（`recall_results_kylin.jsonl`）就能算出这个区间，
所以本脚本不重新测量任何东西，纯粹是对已有结果做统计层面的补充分析。

═══ Wilson score interval，而不是正态近似 ═══════════════════════════

正态近似（p̂ ± z·√(p̂(1-p̂)/n)）在 p̂ 接近 0 或 1、或 n 较小时会给出
越界（<0 或 >1）或过窄的区间，本报告里 template 类 n=9 就是这种小样本
场景。Wilson score interval 在这些边界情况下表现更稳健，是二项比例
置信区间的标准选择之一。

═══ 用法 ═══════════════════════════════════════════════════════════

    python measure_recall_ci.py --backend kylin
    python measure_recall_ci.py --backend numpy
"""

import argparse
import json
import math
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
RECOMP_DIR = os.path.join(RAG, "可复现性体系")
sys.path.insert(0, RECOMP_DIR)

from measure import Measurement, wilson_ci  # noqa: E402

TOP_KS = (1, 3, 5, 10)
THRESHOLD = 0.85


def recall_hits(rows, k, field="rank"):
    return sum(1 for r in rows if r[field] is not None and r[field] <= k)


def report_group(name, rows, m):
    print(f"\n{name}（n={len(rows)}）")
    print(f"  {'K':<5}{'命中/n':<10}{'点估计':<10}{'Wilson 95% CI':<20}{'下界≥85%?':<10}")
    for k in TOP_KS:
        hits = recall_hits(rows, k)
        n = len(rows)
        p = hits / n if n else 0.0
        lo, hi = wilson_ci(hits, n)
        clears = "是" if lo >= THRESHOLD else "否"
        print(f"  {k:<5}{f'{hits}/{n}':<10}{p*100:>6.1f}%   "
              f"[{lo*100:5.1f}%, {hi*100:5.1f}%]   {clears}")
        safe_name = name.replace(" ", "_").replace("/", "_")
        m.set_value(f"{safe_name}_r{k}_hits", hits)
        m.set_value(f"{safe_name}_r{k}_n", n)
        m.set_value(f"{safe_name}_r{k}_ci_lo", round(lo, 4))
        m.set_value(f"{safe_name}_r{k}_ci_hi", round(hi, 4))


def m115(backend="kylin"):
    """🔴 第三轮独立复核 R3-15：本测量声明 layer="L1"（纯 Python、只吃已有的
    逐题结果、不调 embedding），却**没有接进 `reproduce_L1.py` 的一键重跑**，
    于是它产出的 3 条主张在结论区被算进"只能在真实麒麟环境上产生"那一档，
    重算占比因此从 78% 掉到 72%。抽成模块级函数并导出 ALL，就是为了让
    reproduce_L1.py 能像 measure_L1/measure_B2 一样直接调用。
    """
    results_path = os.path.join(BASE, f"recall_results_{backend}.jsonl")
    if not os.path.exists(results_path):
        print(f"缺少 {results_path}，请先跑 measure_recall.py 产出逐题结果。")
        return 2

    rows = [json.loads(line) for line in open(results_path, encoding="utf-8") if line.strip()]

    print("=" * 78)
    print(f"召回率 Wilson 95% 置信区间（{backend} 后端，n={len(rows)}，不重跑检索）")
    print("=" * 78)
    print("下界 < 85% 意味着：即使点估计过线，也不能 95% 确信真实召回率过线。")

    with Measurement(
        "M-115", f"召回率 Wilson 95% 置信区间（{backend} 后端，R2-09）", layer="L1",
        inputs=[results_path],
        command=f"python measure_recall_ci.py --backend {backend}",
    ) as m:
        report_group("总体", rows, m)
        for diff in ("close", "hard"):
            report_group(f"难度={diff}", [r for r in rows if r["difficulty"] == diff], m)
        for kt in ("case", "workflow", "template"):
            report_group(f"类型={kt}", [r for r in rows if r["knowledge_type"] == kt], m)

        m.note(
            "Wilson score interval，95%置信水平（z=1.96）。逐题结果来自已有的"
            f"recall_results_{backend}.jsonl，本脚本不重新调用embedding/检索，"
            "纯粹是对已有命中/未命中结果做统计层面的区间估计。小样本组（如"
            "template n=9）的区间会很宽，这本身就是「样本太小、数字不能太当真」"
            "的量化体现，不是脚本的缺陷。"
        )

    return 0


# 供 reproduce_L1.py 接线（R3-15）——与 measure_L1/measure_B2 同一约定
ALL = {"M-115": m115}


def check_preconditions():
    """R3-31：与 measure_L1/measure_B2 同一约定，缺文件返回人话、齐了返回 None。
    本模块只吃 recall_results_kylin.jsonl（已有逐题结果），不重跑检索。"""
    p = os.path.join(BASE, "recall_results_kylin.jsonl")
    if not os.path.exists(p):
        return (f"缺少逐题检索结果：{p}\n"
                "  本模块（measure_recall_ci.py）只对已有结果做区间估计，"
                "不自己跑检索。\n"
                "  该文件由 评测集/measure_recall.py 在麒麟环境上产出，"
                "随交付物一同提供；\n"
                "  只想核对已有记录的话用：python reproduce_L1.py --check-only")
    return None


def main():
    ap = argparse.ArgumentParser(description="召回率 Wilson 95% 置信区间（不重跑检索）")
    ap.add_argument("--backend", choices=("kylin", "numpy"), default="kylin")
    args = ap.parse_args()
    return m115(args.backend)


if __name__ == "__main__":
    sys.exit(main())
