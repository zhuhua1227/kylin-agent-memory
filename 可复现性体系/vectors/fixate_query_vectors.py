import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/rag/knowledge"))
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
from embedding_client import EmbeddingClient

qs = [json.loads(l) for l in open(os.path.expanduser("~/rag/eval/eval_set.jsonl"), encoding="utf-8")]

emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
out_path = os.path.expanduser("~/rag/eval/queries_fixed.tsv")

with EmbeddingClient(serverd_path=emb_path) as client, open(out_path, "w", encoding="utf-8") as f:
    for i, q in enumerate(qs):
        vec = client.encode(q["query"])
        query_oneline = (q["query"].replace("\t", " ")
                         .replace("\r", " \\r ").replace("\n", " \\n "))
        f.write(f"{q['question_id']}\t{q['doc_id']}\t{q['knowledge_type']}\t{q['difficulty']}\t"
                f"{query_oneline}\t{' '.join(f'{v:.9g}' for v in vec)}\n")
        if (i + 1) % 30 == 0 or i + 1 == len(qs):
            print(f"  {i+1}/{len(qs)}")

print(f"written: {out_path} ({len(qs)} queries)")
