#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_vectors_bge.py —— BGE-M3 (dense) 侧向量生成。

在 Ubuntu 100.3 的 venv 里跑：
  HF_HOME=/mnt/data/bge/hf HF_ENDPOINT=https://hf-mirror.com \
  /mnt/data/bge/venv/bin/python make_vectors_bge.py

输入：texts.jsonl（与 Kylin 侧逐字节相同的 86 doc + 227 query）
产出：docs_bge.npy (86,1024)  queries_bge.npy (227,1024)  _bge_meta.json

规矩（《BGE-M3对照-实施方案.md》§三）：
  · 只用 dense：return_dense=True, return_sparse=False, return_colbert_vecs=False
  · 不加任何指令前缀（BGE-M3 不需要，加了掉点）
  · 不在这里做 L2 归一化 —— 交给 retrieve.py 统一做（两侧一致）
  · 先统计 token 长度，确认无截断（BGE-M3 max 8192）
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTS = os.path.join(HERE, "texts.jsonl")


def main():
    rows = [json.loads(l) for l in open(TEXTS, encoding="utf-8") if l.strip()]
    docs = [r for r in rows if r["kind"] == "doc"]
    docs.sort(key=lambda r: r["doc_id"])
    qs = [r for r in rows if r["kind"] == "query"]
    assert len(docs) == 86 and [d["doc_id"] for d in docs] == list(range(86))
    assert len(qs) == 227

    doc_texts = [d["text"] for d in docs]
    q_texts = [q["text"] for q in qs]

    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)

    # --- token 长度统计（截断自检，§3.3） ---
    tok = model.tokenizer
    dlens = [len(tok.encode(t)) for t in doc_texts]
    qlens = [len(tok.encode(t)) for t in q_texts]
    over512 = sum(1 for x in dlens if x > 512)
    over8192 = sum(1 for x in dlens if x > 8192)
    print(f"[token] doc: min {min(dlens)} 中位 {sorted(dlens)[len(dlens)//2]} max {max(dlens)}  "
          f">512: {over512}/86  >8192: {over8192}/86")
    print(f"[token] query: min {min(qlens)} 中位 {sorted(qlens)[len(qlens)//2]} max {max(qlens)}")
    print(f"[token] BGE-M3 上限 8192，超限条数 {over8192} → {'无截断' if over8192 == 0 else '⚠️ 有截断，两个口径都要报'}")

    def enc(texts, bs=8):
        out = model.encode(texts, batch_size=bs, max_length=8192,
                           return_dense=True, return_sparse=False,
                           return_colbert_vecs=False)["dense_vecs"]
        return np.asarray(out, dtype=np.float32)

    dv = enc(doc_texts)
    qv = enc(q_texts)
    assert dv.shape == (86, 1024) and qv.shape == (227, 1024), (dv.shape, qv.shape)

    # 逐条校 sha256：确认编码的就是 texts.jsonl 里那串（与 Kylin 侧一致）
    import hashlib
    for d in docs:
        assert hashlib.sha256(d["text"].encode()).hexdigest() == d["sha256"]
    for q in qs:
        assert hashlib.sha256(q["text"].encode()).hexdigest() == q["sha256"]

    np.save(os.path.join(HERE, "docs_bge.npy"), dv)
    np.save(os.path.join(HERE, "queries_bge.npy"), qv)

    meta = {
        "model": "BAAI/bge-m3",
        "flagembedding_version": __import__("FlagEmbedding").__version__ if hasattr(__import__("FlagEmbedding"), "__version__") else "?",
        "torch_version": __import__("torch").__version__,
        "dense_only": True, "instruction_prefix": None, "max_length": 8192,
        "use_fp16": False, "normalize_here": False,
        "doc_token_len": {"min": min(dlens), "median": sorted(dlens)[len(dlens)//2], "max": max(dlens), "over_8192": over8192, "over_512": over512},
        "query_token_len": {"min": min(qlens), "median": sorted(qlens)[len(qlens)//2], "max": max(qlens)},
        "docs_shape": list(dv.shape), "queries_shape": list(qv.shape),
    }
    json.dump(meta, open(os.path.join(HERE, "_bge_meta.json"), "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=2)
    print("\n写出 docs_bge.npy / queries_bge.npy / _bge_meta.json")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
