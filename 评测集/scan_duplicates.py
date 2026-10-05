#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_duplicates.py —— 跨 doc_id 近似重复问题扫描（供人工复核用，不做自动裁决）

只在同一 difficulty 档内比较（close 之间、hard 之间），跨 doc_id 计算问句文本
相似度（difflib.SequenceMatcher.ratio）。这只是"提示哪些对值得人工看一眼"，
真正的去留判断必须逐对核查源头知识单元——见 review_and_freeze.py 顶部说明
与《说明报告.md》第一节记录的踩坑（阈值0.60下27对里近半是共享句式模板导致的
误判，必须逐对查源头）。

用法：
    python scan_duplicates.py --threshold 0.60
"""

import argparse
import difflib
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES_PATH = os.path.join(BASE, "eval_candidates.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.60)
    args = ap.parse_args()

    candidates = [json.loads(l) for l in open(CANDIDATES_PATH, "r", encoding="utf-8") if l.strip()]

    by_difficulty = {}
    for c in candidates:
        by_difficulty.setdefault(c["difficulty"], []).append(c)

    pairs = []
    for diff, items in by_difficulty.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a["doc_id"] == b["doc_id"]:
                    continue
                ratio = difflib.SequenceMatcher(None, a["query"], b["query"]).ratio()
                if ratio >= args.threshold:
                    pairs.append((ratio, diff, a, b))

    pairs.sort(key=lambda p: -p[0])

    lines = []
    lines.append(f"候选总数：{len(candidates)}，阈值：{args.threshold}")
    lines.append(f"命中对数：{len(pairs)}\n")
    for ratio, diff, a, b in pairs:
        lines.append(f"[{ratio:.2f}] difficulty={diff}  doc_id {a['doc_id']} vs {b['doc_id']}")
        lines.append(f"    A: {a['query']}")
        lines.append(f"    B: {b['query']}")
        lines.append("")

    text = "\n".join(lines)
    out_path = os.path.join(BASE, f"scan_{args.threshold:.2f}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"written: {out_path}")


if __name__ == "__main__":
    main()
