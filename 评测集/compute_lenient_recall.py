#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_lenient_recall.py —— 宽松召回率（独立评审 E-6）

语料里 18 对（25.3%的知识单元）相似度≥0.70 的近重复（见 corpus_duplicates.json），
当题目的 gold 是其中一条、而模型把语义等价的另一条排到了 top-K 内，这也应当
算"找对了"，只是当前的单一 gold 判定法把它记成了未命中。

用同一份 recall_results_kylin.jsonl（已含 top_k_hit_ids，E-5修复后才有这个
字段），对每道题目：若 strict 判定未命中或命中排名较靠后，检查 gold 的近重复
搭档是否在 top_k_hit_ids 里排得更靠前，取两者较优的排名作为 lenient rank。

⚠️ 两套数字都报，不用宽松数字替换严格数字——lenient recall 衡量的是"语义
等价条目算命中"这个更宽松的问题定义，strict recall 衡量的是"精确匹配到
出题时锚定的那一条"，两者回答的问题不同，都要看。

用法：
    python compute_lenient_recall.py
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
TOP_KS = (1, 3, 5, 10)


def load_dup_partners():
    path = os.path.join(BASE, "corpus_duplicates.json")
    data = json.load(open(path, encoding="utf-8"))
    partners = {}
    for p in data["pairs"]:
        partners.setdefault(p["a"], set()).add(p["b"])
        partners.setdefault(p["b"], set()).add(p["a"])
    return partners


def main():
    partners = load_dup_partners()
    results_path = os.path.join(BASE, "recall_results_kylin.jsonl")
    results = [json.loads(l) for l in open(results_path, encoding="utf-8") if l.strip()]

    for r in results:
        strict_rank = r.get("rank")
        hit_ids = r.get("top_k_hit_ids") or []
        gold = r["doc_id"]
        best = strict_rank
        for cand_id in partners.get(gold, ()):
            if cand_id in hit_ids:
                cand_rank = hit_ids.index(cand_id) + 1
                if best is None or cand_rank < best:
                    best = cand_rank
        r["lenient_rank"] = best

    def recall_at_k(rs, k, field):
        hit = sum(1 for r in rs if r.get(field) is not None and r[field] <= k)
        return hit / len(rs) if rs else 0.0

    print(f"{'K':<5}{'strict':>10}{'lenient':>10}{'差值':>8}")
    for k in TOP_KS:
        s = recall_at_k(results, k, "rank")
        l = recall_at_k(results, k, "lenient_rank")
        print(f"R@{k:<3}{s*100:>9.1f}%{l*100:>9.1f}%{(l-s)*100:>7.1f}pp")

    changed = [r for r in results if r.get("lenient_rank") != r.get("rank")]
    print(f"\n{len(changed)} 题的排名因宽松判定而改善：")
    for r in changed[:20]:
        print(f"  [{r['difficulty']}] {r['question_id']} doc_id={r['doc_id']} "
              f"strict={r['rank']} → lenient={r['lenient_rank']}")

    out = {
        "strict": {f"recall_at_{k}": round(recall_at_k(results, k, "rank"), 4) for k in TOP_KS},
        "lenient": {f"recall_at_{k}": round(recall_at_k(results, k, "lenient_rank"), 4) for k in TOP_KS},
        "changed_count": len(changed),
        "changed_questions": [r["question_id"] for r in changed],
    }
    out_path = os.path.join(BASE, "lenient_recall_summary.json")
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n写入：{out_path}")


if __name__ == "__main__":
    main()
