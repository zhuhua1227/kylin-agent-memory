#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_index_ablation.py —— 模型消融 v2 专用索引构建（不动冻结的 render_units.py）

与 build_knowledge_index.py 的唯一区别：`render_case` 的「修复方式」段。
v1 用 `json.dumps(fixed_input)[:150]`，问题（见报告 §六.7）：
  - 命令里的 claude 临时脚本路径超长（~120 字符），把 150 预算吃光，
    真正的修复动词（timeout 500 / pscp ... /tmp/ / base64）被截没。
v2 修法（只清噪声，不加信息）：
  - 把 `C:\\Users\\x\\AppData\\Local\\Temp\\claude\\C--Users-x\\<uuid>\\scratchpad\\`
    和 `/c/Users/x/AppData/Local/Temp/claude/C--Users-x/<uuid>/scratchpad/`
    这类本地临时脚本路径压成 `<临时目录>/`；
  - `[REDACTED_LONG_TOKEN]...` 压成 `<TOKEN>`；
  - 清理后取 `.command`（没有则整个 dict），截 400 字符（error_text 仍 300）。

输出：`~/RAG/ablation/knowledge_docs_v2.jsonl` + kylin 集合 `rag_knowledge_v2`。
仅三类语料（不含 conflict），与 v1 同口径（86 条）。
"""
import json
import os
import re
import sys

KD = os.path.expanduser("~/RAG/知识检索")
sys.path.insert(0, KD)
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
os.environ["RAG_KNOWLEDGE_DIR"] = KD

from vector_store import create_store, DocStore, DEFAULT_DIM      # noqa: E402
from render_units import render_workflow, render_template, is_low_value_workflow  # noqa: E402
from embedding_client import EmbeddingClient                       # noqa: E402

OUT_DOCS = os.path.expanduser("~/RAG/ablation/knowledge_docs_v2.jsonl")
COLLECTION = "rag_knowledge_v2"

_WIN = re.compile(r'[A-Za-z]:\\Users\\[^\\"\s]+\\AppData\\Local\\Temp\\claude\\C--Users-[^\\"\s]+\\[0-9a-fA-F-]{16,}\\scratchpad\\')
_NIX = re.compile(r'/c/Users/[^/"\s]+/AppData/Local/Temp/claude/C--Users-[^/"\s]+/[0-9a-fA-F-]{16,}/scratchpad/')
_TOK = re.compile(r'\[REDACTED_LONG_TOKEN\][^"\s]*')


def clean(s: str) -> str:
    s = _WIN.sub(r'<临时目录>\\', s)
    s = _NIX.sub(r'<临时目录>/', s)
    s = _TOK.sub('<TOKEN>', s)
    return s


def render_case_v2(c: dict) -> str:
    ti = c["failed_input"] or {}
    intent = ti.get("description") or ti.get("command") or json.dumps(ti, ensure_ascii=False)
    fi = c["fixed_input"] or {}
    fix = fi.get("command") if isinstance(fi, dict) and fi.get("command") else \
        json.dumps(fi, ensure_ascii=False)
    fix = clean(str(fix))
    return (f"【历史案例】使用 {c['tool_name']} 执行「{str(intent)[:150]}」时出错："
            f"{c['error_text'][:300]}。"
            f"修复方式：{fix[:400]}")


def load_units():
    units = []
    for line in open(os.path.join(KD, "cases.jsonl"), encoding="utf-8"):
        if line.strip():
            c = json.loads(line)
            units.append({"type": "case", "text": render_case_v2(c), "raw": c})
    for line in open(os.path.join(KD, "workflows.jsonl"), encoding="utf-8"):
        if line.strip():
            w = json.loads(line)
            units.append({"type": "workflow", "text": render_workflow(w), "raw": w})
    for line in open(os.path.join(KD, "templates.jsonl"), encoding="utf-8"):
        if line.strip():
            t = json.loads(line)
            units.append({"type": "template", "text": render_template(t), "raw": t})
    return units


def main():
    units = load_units()
    print(f"知识单元合计：{len(units)} 条（案例 "
          f"{sum(1 for u in units if u['type']=='case')} + 流程 "
          f"{sum(1 for u in units if u['type']=='workflow')} + 模板 "
          f"{sum(1 for u in units if u['type']=='template')}）")

    client = EmbeddingClient(serverd_path=os.path.expanduser("~/rag/serverd/emb_serverd"))
    docs = DocStore(OUT_DOCS)
    docs._docs = {}
    try:
        with create_store("kylin", collection=COLLECTION) as store:
            store.create(dim=DEFAULT_DIM)
            print("向量化并写入索引...")
            for i, u in enumerate(units):
                vec = client.encode(u["text"])
                store.add([(i, vec)])
                low = (u["type"] == "workflow" and is_low_value_workflow(u["raw"]))
                docs.add(i, u["text"], meta={"type": u["type"], "raw": u["raw"], "low_value": low})
                if (i + 1) % 20 == 0 or i + 1 == len(units):
                    print(f"  {i+1}/{len(units)}")
                    docs.save()
            docs.save()
            print(f"\n索引条数：{store.count()}")
            print(f"文档存储：{docs.path}（{len(docs)} 条）")
    finally:
        client.close()

    # 打印 6 条问题任务的 case 文本，人工核对答案串是否进来了
    print("\n=== v2 渲染后，6 条问题任务的源单元文本 ===")
    d = {json.loads(l)["doc_id"]: json.loads(l) for l in open(OUT_DOCS, encoding="utf-8")}
    for did in (8, 18, 12, 24, 29, 30):
        print(f"--- doc {did} ---\n{d[did]['text']}\n")


if __name__ == "__main__":
    main()
