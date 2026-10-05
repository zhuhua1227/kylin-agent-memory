import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/rag/knowledge"))
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
from embedding_client import EmbeddingClient

docs = [json.loads(l) for l in open(os.path.expanduser("~/rag/knowledge/knowledge_docs.jsonl"), encoding="utf-8")]
docs.sort(key=lambda d: d["doc_id"])

emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
out_path = os.path.expanduser("~/rag/eval/docs_fixed.tsv")

with EmbeddingClient(serverd_path=emb_path) as client, open(out_path, "w", encoding="utf-8") as f:
    for i, d in enumerate(docs):
        vec = client.encode(d["text"])
        text_oneline = (d["text"].replace("\t", " ")
                        .replace("\r", " \\r ").replace("\n", " \\n "))
        f.write(f"{text_oneline}\t{' '.join(f'{v:.9g}' for v in vec)}\n")
        if (i + 1) % 20 == 0 or i + 1 == len(docs):
            print(f"  {i+1}/{len(docs)}")

print(f"written: {out_path} ({len(docs)} docs)")
