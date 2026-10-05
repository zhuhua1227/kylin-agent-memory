#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_corpus_duplicates.py —— 语料内部近重复扫描（独立评审 E-6）

独立评审指出：scan_duplicates.py 只做 query↔query 比对，从未检查语料内部
（doc↔doc）本身有多少近重复。上一版实测 152 条语料里 153 对相似度≥0.70
（涉及 71 条，46.7%），其中 46 条被用作 gold——多个知识单元在语义上几乎
等价时，用单一 gold 判定召回率会低估真实检索质量：模型把语义等价的另一条
排在前面，也应该算"找对了"。

用同一份字符 bigram 相似度（与 vectors/docs.tsv 的固化语料一致），扫描
当前 95 条知识单元语料内部的近重复对，供 measure_recall_lenient.py 计算
宽松召回率（strict vs lenient 两套数字都报，不用宽松数字掩盖严格数字）。

用法：
    python scan_corpus_duplicates.py [--threshold 0.70]
"""
import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
VECTORS_DIR = os.path.join(os.path.dirname(BASE), "可复现性体系", "vectors")


def bigram_sim(a: str, b: str) -> float:
    """字符 bigram Dice 系数，纯 Python、不依赖任何模型，任何机器可跑。"""
    def bigrams(s):
        chars = [c for c in s if not c.isspace()]
        return set(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    return 2 * inter / (len(ba) + len(bb))


def load_docs():
    docs = []
    with open(os.path.join(VECTORS_DIR, "docs.tsv"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            text, _, _ = line.partition("\t")
            docs.append(text)
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.70)
    args = ap.parse_args()

    docs = load_docs()
    pairs = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            sim = bigram_sim(docs[i], docs[j])
            if sim >= args.threshold:
                pairs.append((i, j, sim))

    involved = set()
    for i, j, _ in pairs:
        involved.add(i)
        involved.add(j)

    print(f"语料总数：{len(docs)}，阈值：{args.threshold}")
    print(f"近重复对数：{len(pairs)}，涉及 {len(involved)} 条（{len(involved)/len(docs)*100:.1f}%）")
    for i, j, sim in sorted(pairs, key=lambda x: -x[2]):
        print(f"[{sim:.3f}] doc_id {i} vs {j}")
        print(f"    A: {docs[i][:70]}")
        print(f"    B: {docs[j][:70]}")

    out = {
        "threshold": args.threshold,
        "corpus_size": len(docs),
        "pair_count": len(pairs),
        "involved_count": len(involved),
        "involved_ratio": round(len(involved) / len(docs), 4),
        "pairs": [{"a": i, "b": j, "similarity": round(sim, 4)} for i, j, sim in pairs],
    }
    out_path = os.path.join(BASE, "corpus_duplicates.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n写入：{out_path}")


if __name__ == "__main__":
    main()
