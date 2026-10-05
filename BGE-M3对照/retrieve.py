#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retrieve.py —— 两侧共用的检索与计分（纯 numpy，评委任何机器可复现 / L1）。

数学与 知识检索/vector_store.py 的 numpy 后端一致：
  · docs、query 都在算余弦前显式 L2 归一化（不假设任何一侧已归一化）
  · score = docs_norm @ query_norm  （余弦）
  · 排序：np.argsort(-scores, kind="stable")  →  并列按 doc 行号（= doc_id）升序打破
    （vector_store 用 argpartition+argsort；float32 余弦精确并列概率≈0，
     两种方式结果一致，以已知答案自检为准）

用法：
  python retrieve.py --side kylin   # 读 docs_kylin.npy / queries_kylin.npy，出 R@k，核对锚定值
  python retrieve.py --side bge     # 读 docs_bge.npy / queries_bge.npy
  python retrieve.py --mcnemar      # 读两侧 per-query 命中，出配对不一致对
"""
import argparse
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = os.path.dirname(os.path.abspath(__file__))
TOP_KS = (1, 3, 5, 10)

# §2.3：只有 R@1 与 R@10 是 claims.json 里有独立主张、有 anchor 的
KYLIN_ANCHOR = {1: 0.5242, 10: 0.8634}   # C-265 / C-266（naive 档，227 题）


def l2norm(m):
    m = np.asarray(m, dtype=np.float64)
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


def rank_of_gold(doc_ids_sorted, gold):
    """gold 在排序结果里的名次（1-based）；不在前 max_k 内返回 None。"""
    w = np.where(doc_ids_sorted == gold)[0]
    return int(w[0]) + 1 if len(w) else None


def run_side(side, max_k=100):
    docs = np.load(os.path.join(OUT, f"docs_{side}.npy"))     # (86, D)
    qs = np.load(os.path.join(OUT, f"queries_{side}.npy"))    # (227, D)
    gold = np.load(os.path.join(OUT, "gold.npy"))             # (227,)
    qids = json.load(open(os.path.join(OUT, "qids.json"), encoding="utf-8"))
    assert docs.shape[0] == 86 and qs.shape[0] == len(gold) == 227
    assert docs.shape[1] == qs.shape[1], f"{side}: doc dim {docs.shape[1]} != query dim {qs.shape[1]}"

    D = l2norm(docs)
    Q = l2norm(qs)
    doc_ids = np.arange(86)

    scores = Q @ D.T                                          # (227, 86)
    order = np.argsort(-scores, axis=1, kind="stable")        # 并列按行号升序
    ranked = doc_ids[order]                                   # (227, 86) doc_id

    per_q = []
    for i in range(227):
        r = rank_of_gold(ranked[i], int(gold[i]))
        per_q.append({"question_id": qids[i], "gold_doc_id": int(gold[i]),
                      "rank": r, "top10": [int(x) for x in ranked[i][:10]]})

    recall = {}
    for k in TOP_KS:
        hit = sum(1 for p in per_q if p["rank"] is not None and p["rank"] <= k)
        recall[k] = hit / 227

    json.dump(per_q, open(os.path.join(OUT, f"perquery_{side}.json"), "w",
                          encoding="utf-8", newline=""),
              ensure_ascii=False, indent=1)

    print(f"\n===== side = {side}  (dim={docs.shape[1]}) =====")
    for k in TOP_KS:
        n = round(recall[k] * 227)
        print(f"  R@{k:<3} = {recall[k]*100:5.2f}%   ({n}/227)")

    mono = all(recall[a] <= recall[b] for a, b in zip(TOP_KS, TOP_KS[1:]))
    print(f"  单调性 R@1 ≤ R@3 ≤ R@5 ≤ R@10 : {'✅' if mono else '❌ 算错了，停'}")
    assert mono, "R@k 不单调——检索代码有错，停下修，不要继续"

    if side == "kylin":
        print("\n  [已知答案自检] 对锚定值（C-265 R@1 / C-266 R@10）：")
        ok = True
        for k, want in KYLIN_ANCHOR.items():
            got = round(recall[k], 4)
            same = got == want
            ok &= same
            print(f"    R@{k}: 实测 {got}  vs 锚定 {want}   {'✅ 逐位一致' if same else '❌ 对不上'}")
        print(f"    R@3（claims.json 无主张，本次为新增测量）= {recall[3]*100:.2f}% ({round(recall[3]*227)}/227)")
        print(f"    R@5（仅 C-265 note 提及 82.4%，非硬门）= {recall[5]*100:.2f}% ({round(recall[5]*227)}/227)")
        if not ok:
            raise SystemExit("\n❌ Kylin 侧复现不出锚定值 —— 是检索代码错了，不是 BGE 的问题。"
                             "停下修，不要继续看 BGE 的数。")
        print("\n  ✅ 自检通过：retrieve.py 正确，可以继续做 BGE 侧。")

    return recall, per_q


def mcnemar():
    a = json.load(open(os.path.join(OUT, "perquery_kylin.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(OUT, "perquery_bge.json"), encoding="utf-8"))
    ba = {p["question_id"]: p for p in a}
    bb = {p["question_id"]: p for p in b}
    print("\n===== McNemar（配对，227 题；只列不一致对的条数与方向）=====")
    for k in TOP_KS:
        only_kylin = only_bge = both = neither = 0
        flips = []
        for qid in ba:
            ha = ba[qid]["rank"] is not None and ba[qid]["rank"] <= k
            hb = bb[qid]["rank"] is not None and bb[qid]["rank"] <= k
            if ha and hb:
                both += 1
            elif ha and not hb:
                only_kylin += 1
                flips.append(("kylin", qid))
            elif hb and not ha:
                only_bge += 1
                flips.append(("bge", qid))
            else:
                neither += 1
        disc = only_kylin + only_bge
        # McNemar 精确/近似（仅参考，不作显著性结论；方案 §2.5）
        note = ""
        if disc:
            from math import comb
            b_, c_ = only_kylin, only_bge
            n = b_ + c_
            m = min(b_, c_)
            p2 = min(1.0, 2 * sum(comb(n, i) for i in range(m + 1)) / (2 ** n))
            note = f"   二项精确双侧 p≈{p2:.3f}（仅参考，n_disc={n}，有效样本远小于 227，不作结论）"
        print(f"  R@{k:<3}: 只 Kylin 命中 {only_kylin}  只 BGE 命中 {only_bge}  "
              f"均命中 {both}  均未 {neither}{note}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=["kylin", "bge"])
    ap.add_argument("--mcnemar", action="store_true")
    args = ap.parse_args()
    if args.mcnemar:
        mcnemar()
    elif args.side:
        run_side(args.side)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
