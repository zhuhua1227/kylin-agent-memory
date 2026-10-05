#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tune_hybrid_params.py —— 消融修复-执行方案.md §B.3/§D.2：BM25 归一化修好、
meta_prior 质心软先验做出来之后，`hybrid_alpha` 与 `prior_beta` 的含义都
变了，需要在真实 embedding 上重新定一次值，然后冻死，阶段二不再改动。

═══ 纪律（§B.3 明文，§D.2 同样适用）═══════════════════════════════════
❌ 不允许在完整 227 题上扫参数挑最优——那是在评测集上过拟合。
✅ 只在**固定抽样**的少量样本（≤30 题）上比较候选值，抽样方式必须写进
   报告，定完之后阶段二一个数都不许再动。

═══ 抽样方式（固定实现，不是随手选的）═══════════════════════════════════
从 eval_set.jsonl 按**读入顺序**每隔 8 题取 1 题（下标 0,8,16,...），227 题
里取到 29 题。用固定步长而非随机抽样，避免"随机种子选得巧"这类质疑，且
任何人重跑这个脚本都能拿到完全相同的样本。

用法：
    python3 tune_hybrid_params.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(BASE, "..", "知识检索")
sys.path.insert(0, KNOWLEDGE_DIR)
sys.path.insert(0, os.path.join(RAG, "可复现性体系"))
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
sys.path.insert(0, BASE)

import measure_recall as mr  # noqa: E402

SAMPLE_STEP = 8


def main():
    mr.assert_corpus_matches_frozen()
    from vector_store import create_store, DocStore
    from embedding_client import EmbeddingClient
    from search_knowledge import BM25

    eval_set = mr.load_eval_set()
    sample = eval_set[::SAMPLE_STEP]
    print(f"评测集共 {len(eval_set)} 题，固定抽样步长={SAMPLE_STEP}，样本 {len(sample)} 题：")
    print(f"  question_id = {[q['question_id'] for q in sample]}")

    docs = DocStore(os.path.join(KNOWLEDGE_DIR, "knowledge_docs.jsonl"))
    all_doc_ids = sorted(docs._docs.keys())
    all_texts = [docs.get(i)["text"] for i in all_doc_ids]
    bm25_index = BM25(all_doc_ids, all_texts)

    emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
    with EmbeddingClient(serverd_path=emb_path) as client, \
         create_store("kylin", collection="rag_knowledge") as store:

        print("计算类型质心（供 meta_prior/β 调参用）...")
        vecs_by_type = {}
        for did in all_doc_ids:
            t = docs.get(did)["meta"]["type"]
            vecs_by_type.setdefault(t, []).append(client.encode(docs.get(did)["text"]))
        type_centroids = {
            t: [sum(v[i] for v in vs) / len(vs) for i in range(len(vs[0]))]
            for t, vs in vecs_by_type.items()
        }
        print(f"  质心：{ {t: len(v) for t, v in vecs_by_type.items()} }")

        qvecs = {q["question_id"]: client.encode(q["query"]) for q in sample}

        def recall_at_10(config, **kw):
            hit = 0
            for q in sample:
                hits = mr.retrieve_ablation(config, q["query"], qvecs[q["question_id"]],
                                             store, docs, 10, **kw)
                if q["doc_id"] in hits:
                    hit += 1
            return hit / len(sample)

        print("\n=== §B.3：hybrid_alpha 候选比较（config=bm25，固定样本 R@10，n=%d） ===" % len(sample))
        alpha_results = {}
        for alpha in (0.5, 0.7, 0.9):
            r10 = recall_at_10("bm25", bm25=bm25_index, hybrid_alpha=alpha)
            alpha_results[alpha] = r10
            print(f"  hybrid_alpha={alpha}  R@10={r10*100:.1f}%")
        best_alpha = max(alpha_results, key=lambda a: (alpha_results[a], -abs(a - 0.7)))
        print(f"  → 选定 hybrid_alpha={best_alpha}"
              f"（R@10 并列时优先取离旧默认值0.7更近的一个，减少无谓变动）")

        print("\n=== §D.2：prior_beta 候选比较（config=meta_prior，固定样本 R@10，n=%d） ===" % len(sample))
        beta_results = {}
        for beta in (0.3, 0.5, 0.8):
            r10 = recall_at_10("meta_prior", type_centroids=type_centroids, prior_beta=beta)
            beta_results[beta] = r10
            print(f"  prior_beta={beta}  R@10={r10*100:.1f}%")
        best_beta = max(beta_results, key=lambda b: (beta_results[b], -abs(b - 0.5)))
        print(f"  → 选定 prior_beta={best_beta}"
              f"（R@10 并列时优先取居中的 0.5，避免选一个未经充分验证的极端值）")

        print(f"\n最终冻结：--hybrid-alpha {best_alpha}  --type-prior-beta {best_beta}")


if __name__ == "__main__":
    main()
