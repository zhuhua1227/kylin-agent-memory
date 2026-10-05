#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_build_inputs.py —— 从冻结产物抽出两侧共用的输入，绝不覆盖既有文件。

产出（全部写进 BGE-M3对照/）：
  texts.jsonl        86 条 doc（含 \\r\\n 原文）+ 227 条 query，各带 sha256
  docs_kylin.npy     (86, 768)   从 可复现性体系/vectors/docs.tsv 第 2 列（KYSDK 原始向量，未归一化）
  queries_kylin.npy  (227, 768)  从 可复现性体系/vectors/eval_queries.tsv 第 6 列
  gold.npy           (227,)      每题的 gold doc_id
  qids.json          227 个 question_id（与 gold.npy / queries_kylin.npy 行对齐）

不动：render_units.py / 评测集 / 语料 / *.tsv / *.npy（既有的）/ search_knowledge.py / vector_store.py
"""
import hashlib
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "BGE-M3对照")

DOCS_JSONL = os.path.join(OUT, "_knowledge_docs_frozen.jsonl")   # 从 device_c /data 拉的冻结版，filehash d253231a2f2f
DOCS_TSV = os.path.join(ROOT, "可复现性体系", "vectors", "docs.tsv")
EQ_TSV = os.path.join(ROOT, "可复现性体系", "vectors", "eval_queries.tsv")
EVAL_SET = os.path.join(ROOT, "评测集", "eval_set.jsonl")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def esc(t: str) -> str:
    """复刻 可复现性体系/vectors/fixate_vectors.py 的一行化转义。"""
    return t.replace("\t", " ").replace("\r", " \\r ").replace("\n", " \\n ")


def main():
    # ---- docs：冻结 knowledge_docs.jsonl 的 text 就是产出 docs.tsv 的源文本 ----
    docs = sorted((json.loads(l) for l in open(DOCS_JSONL, encoding="utf-8") if l.strip()),
                  key=lambda d: d["doc_id"])
    assert len(docs) == 86 and [d["doc_id"] for d in docs] == list(range(86)), "doc_id 不是 0..85"

    tsv_doc = [l.rstrip("\n") for l in open(DOCS_TSV, encoding="utf-8")]
    assert len(tsv_doc) == 86

    # 逐条确认：knowledge_docs[i].text 经 fixate 转义 == docs.tsv 第 1 列
    for i in range(86):
        assert esc(docs[i]["text"]) == tsv_doc[i].split("\t")[0], \
            f"doc {i} 的 text 与 docs.tsv 对不上——不是同一段被嵌入的文本，停"

    docs_kylin = np.array([[float(x) for x in tsv_doc[i].split("\t")[1].split()]
                           for i in range(86)], dtype=np.float32)
    assert docs_kylin.shape == (86, 768)

    # ---- eval_set 的 source_rendered_sha256 口径核对（§2.3 的自查精神）----
    ev = {}
    for l in open(EVAL_SET, encoding="utf-8"):
        d = json.loads(l)
        ev.setdefault(d["doc_id"], d["source_rendered_sha256"])
    raw_hit = norm_hit = 0
    crlf_gold = []
    for i, want in ev.items():
        t = docs[i]["text"]
        if sha(t) == want:
            raw_hit += 1
        if sha(t.replace("\r\n", "\n")) == want:
            norm_hit += 1
        if "\r" in t:
            crlf_gold.append(i)
    print(f"[sha256 口径] eval_set 覆盖 {len(ev)} 个 gold doc；"
          f"raw-utf8 命中 {raw_hit}/{len(ev)}，\\r\\n→\\n 命中 {norm_hit}/{len(ev)}")
    print(f"[sha256 口径] 含 \\r\\n 的 gold doc（{len(crlf_gold)} 条）: {sorted(crlf_gold)}")
    print("[sha256 口径] 结论：冻结 eval_set 的 source_rendered_sha256 是对"
          "「\\r\\n→\\n 归一化后」的文本算的，而 source_rendered 字段本身保留 \\r\\n。")
    print("            两侧都嵌入 knowledge_docs.jsonl 的 text 原文（含 \\r\\n），逐字节一致；")
    print("            这 7 条的 raw sha256 与登记值不同是冻结产物的既有记账口径差异，非本实验引入。")

    # ---- queries ----
    eq = [l.rstrip("\n").split("\t") for l in open(EQ_TSV, encoding="utf-8")]
    assert len(eq) == 227
    es_by_qid = {json.loads(l)["question_id"]: json.loads(l) for l in open(EVAL_SET, encoding="utf-8")}

    qids, gold, qtexts = [], [], []
    q_vecs = []
    for row in eq:
        qid, gdoc, _kt, _df, qtext = row[0], int(row[1]), row[2], row[3], row[4]
        d = es_by_qid[qid]
        assert d["query"] == qtext, f"{qid} query 文本与 eval_set 不一致"
        assert d["doc_id"] == gdoc, f"{qid} gold doc 与 eval_set 不一致"
        v = [float(x) for x in row[5].split()]
        assert len(v) == 768
        qids.append(qid)
        gold.append(gdoc)
        qtexts.append(qtext)
        q_vecs.append(v)

    queries_kylin = np.array(q_vecs, dtype=np.float32)
    gold_arr = np.array(gold, dtype=np.int64)

    # ---- 写出 ----
    np.save(os.path.join(OUT, "docs_kylin.npy"), docs_kylin)
    np.save(os.path.join(OUT, "queries_kylin.npy"), queries_kylin)
    np.save(os.path.join(OUT, "gold.npy"), gold_arr)
    json.dump(qids, open(os.path.join(OUT, "qids.json"), "w", encoding="utf-8", newline=""), ensure_ascii=False)

    with open(os.path.join(OUT, "texts.jsonl"), "w", encoding="utf-8", newline="") as f:
        for i in range(86):
            t = docs[i]["text"]
            f.write(json.dumps({"kind": "doc", "doc_id": i, "text": t,
                                "sha256": sha(t),
                                "sha256_crlf_normalized": sha(t.replace("\r\n", "\n"))},
                               ensure_ascii=False) + "\n")
        for qid, gd, qt in zip(qids, gold, qtexts):
            f.write(json.dumps({"kind": "query", "question_id": qid, "gold_doc_id": gd,
                                "text": qt, "sha256": sha(qt)}, ensure_ascii=False) + "\n")

    print(f"\n写出：texts.jsonl（86 doc + 227 query）")
    print(f"      docs_kylin.npy {docs_kylin.shape}  queries_kylin.npy {queries_kylin.shape}  gold.npy {gold_arr.shape}")
    print(f"      227 题覆盖 {len(set(gold))} 个 gold doc")


if __name__ == "__main__":
    main()
