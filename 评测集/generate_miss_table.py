#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_miss_table.py —— 从逐题结果生成"完全未命中"表格（R2-09/R2-14）

═══ 为什么需要这个脚本 ═══════════════════════════════════════════════

《召回率实测报告.md》第四节"完全未命中分析"的两张表——按难度/类型统计的
未命中数、14道未命中题目的完整清单——此前是从 `recall_results_kylin.jsonl`
里手工摘抄进 markdown 的。第一轮独立评审就指出过这类手打表格容易和数据
脱节（重跑一次结果变了，表格忘了同步更新，报告和真实记录就此分岔），
给过"改成脚本生成"的方案，但当时没采纳，第二轮复核里同样的问题复发。

本脚本直接读 `recall_results_kylin.jsonl`，把两张表当场打印出来。报告
正文里的这两张表应该是这个脚本某次运行的输出原样粘贴，而不是手抄的——
往 recall_results_*.jsonl 里改任何一条记录，两张表下次重新生成就会自动
反映出来，不需要有人记得去改 markdown。

═══ 用法 ═══════════════════════════════════════════════════════════

    python generate_miss_table.py --backend kylin
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# Windows 控制台默认 GBK，本文件全是中文表格内容，不强制 UTF-8 会直接
# UnicodeEncodeError 崩掉——同 reproduce_L1.py 的做法
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="从逐题结果生成完全未命中表格")
    ap.add_argument("--backend", choices=("kylin", "numpy"), default="kylin")
    args = ap.parse_args()

    path = os.path.join(BASE, f"recall_results_{args.backend}.jsonl")
    if not os.path.exists(path):
        print(f"缺少 {path}，请先跑 measure_recall.py 产出逐题结果。")
        return 2

    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    misses = [r for r in rows if r.get("rank") is None]

    print(f"## 完全未命中分析（top-10 内 {len(misses)} 题，"
          f"{len(misses)/len(rows)*100:.1f}%）\n")

    print("| 维度 | 未命中数 |")
    print("|------|---------|")
    by_diff = {}
    for r in misses:
        by_diff.setdefault(r["difficulty"], 0)
        by_diff[r["difficulty"]] += 1
    print(f"| 按难度 | " + " / ".join(f"{k} {v} 题" for k, v in sorted(by_diff.items())) + " |")
    by_type = {}
    for r in misses:
        by_type.setdefault(r["knowledge_type"], 0)
        by_type[r["knowledge_type"]] += 1
    order = ["case", "workflow", "template"]
    type_parts = [f"{k} {by_type[k]} 题" for k in order if k in by_type]
    type_parts += [f"{k} {v} 题" for k, v in by_type.items() if k not in order]
    print(f"| 按类型 | " + " / ".join(type_parts) + " |\n")

    print("完整清单（`recall_results_{}.jsonl` 逐题结果里 `rank=null` 的记录，"
          "按 question_id 排序）：\n".format(args.backend))
    print("| 题号 | doc_id | 难度 | 类型 | 问题 |")
    print("|------|--------|------|------|------|")
    for r in sorted(misses, key=lambda x: x["question_id"]):
        q = r["query"].replace("|", "\\|")
        print(f"| {r['question_id']} | {r['doc_id']} | {r['difficulty']} | "
              f"{r['knowledge_type']} | {q} |")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
