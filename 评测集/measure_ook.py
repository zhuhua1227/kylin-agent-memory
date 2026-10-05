#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_ook.py —— 负样本测试：知识库里没有答案时，系统分得出来吗

═══ 这个脚本第一版测的是一件太容易的事 ═══════════════════════════

初版（独立评审 E-8）只有 20 条 out-of-KB 负样本，全是"红烧肉怎么做"
"唐朝为什么灭亡""冰岛的首都是哪里"——**与知识库话题毫不相干**。

🔴 第三轮独立复核 R3-31 指出这一档四轮一字未改，而且它测的东西价值有限：
把菜谱和 SSH 排错分开，任何 embedding 都做得到，分数拉开**不能说明系统
有拒答能力**。真实使用中会发生的是另一种情况——

    用户问的还是 Linux 运维、还是这台机器上的工具排障，
    只是这个知识库里恰好没做过那件事。

这一档（**领域内负样本**）此前完全空白。本版补上 20 条，与原有 20 条
out-of-domain 负样本、以及真实 in-KB 问题三组对照。

═══ 为什么必须先核验"库里真的没有" ═══════════════════════════════

一条其实在库里有答案的问题被当成负样本，会把一次**正确检索**记成误报，
测量本身就坏了。所以本脚本**不信任 in_domain_negatives.json 的自述**：
每条负样本声明的词根，测量时在三份语料里重新核验一遍，出现过的直接判废、
不参与统计，并在输出里点名。自述与实测不一致时以实测为准。

═══ 输出什么 ═══════════════════════════════════════════════════

  · 三组的 top-1 相似度分布与重叠情况
  · **拒答判定的 precision / recall**：把"top-1 相似度 < τ 就拒答"当成
    一个二分类器，扫描 τ 报出最佳 F1 及其对应的 precision/recall。
    这是 R3-31 要的 precision——单答案检索里的 precision@k 只是
    recall@k/k，是个恒等变换，报了也没有信息量。

用法（需麒麟环境）：
    RAG_KNOWLEDGE_DIR=~/rag/knowledge python3 measure_ook.py
"""
import json
import os
import random
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(os.path.dirname(BASE), "知识检索")
SERVERD_DIR = os.environ.get("RAG_SERVERD_DIR") or os.path.expanduser("~/rag/serverd")
sys.path.insert(0, KNOWLEDGE_DIR)
sys.path.insert(0, SERVERD_DIR)


def _corpus_blob():
    """把三份语料拼成一个小写大字符串，供词根核验。"""
    blob = []
    for name in ("cases.jsonl", "workflows.jsonl", "templates.jsonl"):
        path = os.path.join(KNOWLEDGE_DIR, name)
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    blob.append(line)
    return "".join(blob).lower()


def verify_absence(questions, blob):
    """重新核验每条领域内负样本声明的词根确实不在语料里。

    返回 (合格的题, 判废的题)。判废不是静默丢弃——调用方要把它打出来，
    因为"我以为库里没有、其实有"本身就是一条值得知道的信息。
    """
    kept, rejected = [], []
    for q in questions:
        hits = [w for w in q.get("verify_absence", []) if w.lower() in blob]
        if hits:
            rejected.append({**q, "found_in_corpus": hits})
        else:
            kept.append(q)
    return kept, rejected


def refusal_classifier_stats(neg_scores, pos_scores):
    """把「top-1 相似度 < τ 就拒答」当二分类器，扫 τ 找最佳 F1。

    正类定义为**该拒答**（即负样本）。这样 precision 的含义是
    "系统说该拒答时，有多少真的该拒答"——正是关心的那个数。
    """
    cands = sorted(set(neg_scores + pos_scores))
    best = None
    for i, tau in enumerate(cands):
        # 阈值取相邻两点中点，避免恰好落在样本值上带来的边界歧义
        tau = (tau + cands[i + 1]) / 2 if i + 1 < len(cands) else tau + 1e-6
        tp = sum(1 for s in neg_scores if s < tau)     # 该拒答且拒了
        fp = sum(1 for s in pos_scores if s < tau)     # 不该拒答却拒了
        fn = len(neg_scores) - tp                      # 该拒答却答了
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if best is None or f1 > best["f1"]:
            best = {"tau": round(tau, 4), "precision": round(prec, 4),
                    "recall": round(rec, 4), "f1": round(f1, 4),
                    "tp": tp, "fp": fp, "fn": fn}
    return best


def stats(vals):
    vals = sorted(vals)
    n = len(vals)
    return {"n": n, "min": round(vals[0], 4), "max": round(vals[-1], 4),
            "mean": round(sum(vals) / n, 4), "median": round(vals[n // 2], 4)}


def main():
    from vector_store import create_store
    from embedding_client import EmbeddingClient

    with open(os.path.join(BASE, "out_of_kb_questions.json"), encoding="utf-8") as f:
        ood = json.load(f)["questions"]
    with open(os.path.join(BASE, "in_domain_negatives.json"), encoding="utf-8") as f:
        ind_all = json.load(f)["questions"]

    blob = _corpus_blob()
    ind, rejected = verify_absence(ind_all, blob)
    print(f"领域内负样本词根核验：{len(ind_all)} 条中 {len(ind)} 条通过、"
          f"{len(rejected)} 条判废")
    for r in rejected:
        print(f"  ✗ 判废 {r['id']}（{r['topic']}）：词根 {r['found_in_corpus']} "
              f"在语料中出现过，它其实**是**正样本，不能当负样本用")

    with open(os.path.join(BASE, "eval_set.jsonl"), encoding="utf-8") as f:
        eval_set = [json.loads(l) for l in f if l.strip()]
    random.seed(42)
    in_kb_sample = random.sample(eval_set, 20)

    emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
    results = {"out_of_domain": [], "in_domain_negative": [], "in_kb": []}

    with EmbeddingClient(serverd_path=emb_path) as client, \
         create_store("kylin", collection="rag_knowledge") as store:

        def probe(q, key, extra=None):
            vec = client.encode(q["query"])
            hits = store.search(vec, top_k=1)
            top1 = hits[0][1] if hits else None
            row = {"id": q["id"] if "id" in q else q["question_id"],
                   "query": q["query"], "top1_score": top1}
            if extra:
                row.update(extra(hits))
            results[key].append(row)
            return top1

        for q in ood:
            s = probe(q, "out_of_domain")
            print(f"[域外负样本] {q['id']}: top1={s:.4f}  {q['query']}")

        for q in ind:
            s = probe(q, "in_domain_negative")
            results["in_domain_negative"][-1]["topic"] = q["topic"]
            print(f"[域内负样本] {q['id']}: top1={s:.4f}  ({q['topic']}) {q['query']}")

        for q in in_kb_sample:
            s = probe(q, "in_kb",
                      extra=lambda hits, q=q: {
                          "top1_is_gold": bool(hits) and hits[0][0] == q["doc_id"]})
            print(f"[真实问题]   {q['question_id']}: top1={s:.4f}  {q['query'][:40]}")

    ood_s = [r["top1_score"] for r in results["out_of_domain"]]
    ind_s = [r["top1_score"] for r in results["in_domain_negative"]]
    kb_s = [r["top1_score"] for r in results["in_kb"]]

    ood_st, ind_st, kb_st = stats(ood_s), stats(ind_s), stats(kb_s)
    print(f"\n域外负样本 top1：{ood_st}")
    print(f"域内负样本 top1：{ind_st}")
    print(f"真实问题   top1：{kb_st}")

    def overlap(a, b):
        lo, hi = max(a["min"], b["min"]), min(a["max"], b["max"])
        return {"overlaps": hi >= lo,
                "gap": round(b["min"] - a["max"], 4) if hi < lo else 0.0}

    ov_ood = overlap(ood_st, kb_st)
    ov_ind = overlap(ind_st, kb_st)
    print(f"\n域外负样本 vs 真实问题：{ov_ood}")
    print(f"域内负样本 vs 真实问题：{ov_ind}")
    print("  ⚠️ 域内那一组重叠得多是**预期之中**的——两边都是同一领域的运维问题，"
          "这正是它比域外那组更有信息量的原因。")

    clf_ood = refusal_classifier_stats(ood_s, kb_s)
    clf_ind = refusal_classifier_stats(ind_s, kb_s)
    clf_all = refusal_classifier_stats(ood_s + ind_s, kb_s)
    print(f"\n拒答判定（top1 < τ 即拒答）最佳 F1：")
    print(f"  只用域外负样本：{clf_ood}")
    print(f"  只用域内负样本：{clf_ind}")
    print(f"  两组合并      ：{clf_all}")

    out = {
        "note": "R3-31：新增领域内负样本一档。域外那档留作对照，说明"
                "「负样本容易到什么程度，结论就虚到什么程度」。",
        "verify_absence_rejected": rejected,
        "out_of_domain": results["out_of_domain"],
        "in_domain_negative": results["in_domain_negative"],
        "in_kb": results["in_kb"],
        "out_of_domain_stats": ood_st,
        "in_domain_negative_stats": ind_st,
        "in_kb_stats": kb_st,
        "overlap_out_of_domain_vs_in_kb": ov_ood,
        "overlap_in_domain_vs_in_kb": ov_ind,
        "refusal_best_f1_out_of_domain": clf_ood,
        "refusal_best_f1_in_domain": clf_ind,
        "refusal_best_f1_combined": clf_all,
    }
    out_path = os.path.join(BASE, "ook_result.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n写入：{out_path}")


if __name__ == "__main__":
    main()
