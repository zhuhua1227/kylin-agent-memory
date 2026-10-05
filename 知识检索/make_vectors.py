#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_vectors.py —— 用官方 embedding SDK 生成向量文件，供 ve_demo 灌入官方向量引擎

刻意使用与阶段01「端到端最小管线」完全相同的五条文本和同一个查询：
这样 ve_demo 的检索结果就可以和历史记录逐位比对——如果排序或分数对不上，
就能立刻定位到是「向量引擎」这一环引入的偏差，而不是向量本身变了。

输出：
  docs.tsv   每行 `<原文>\\t<768个float，空格分隔>`
  query.tsv  一行 同上格式

依赖：同项目《向量化常驻服务》的 embedding_client.py 与已编译的 emb_serverd
"""

import os
import sys

# 复用《向量化常驻服务》的客户端，不重复实现
SERVERD_DIR = os.path.expanduser("~/rag/serverd")
sys.path.insert(0, SERVERD_DIR)

from embedding_client import EmbeddingClient, DEFAULT_SERVERD_PATH  # noqa: E402


# 与阶段01 完全一致，勿改——改了就失去与历史记录对照的意义
DEMO_TEXTS = [
    "怎么重启网络服务",
    "如何查看磁盘剩余空间",
    "今天天气怎么样",
    "网络连接不上怎么办",
    "如何清理系统缓存文件",
]
QUERY = "网络服务重启方法"


def write_tsv(path, items):
    """items: [(原文, 向量)]"""
    with open(path, "w", encoding="utf-8") as f:
        for text, vec in items:
            # 原文里不能有制表符和换行，否则会撑破 TSV 格式。
            # 本 demo 的文本都是短句，这里只做防御性处理。
            safe = text.replace("\t", " ").replace("\n", " ")
            f.write(safe + "\t" + " ".join(f"{v:.6f}" for v in vec) + "\n")
    print(f"已写出 {path}（{len(items)} 行）")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)

    with EmbeddingClient(serverd_path=os.path.join(SERVERD_DIR, "emb_serverd")) as client:
        docs = [(t, client.encode(t)) for t in DEMO_TEXTS]
        query = [(QUERY, client.encode(QUERY))]

    write_tsv(os.path.join(out_dir, "docs.tsv"), docs)
    write_tsv(os.path.join(out_dir, "query.tsv"), query)
    print(f"向量维度：{len(docs[0][1])}")


if __name__ == "__main__":
    main()
