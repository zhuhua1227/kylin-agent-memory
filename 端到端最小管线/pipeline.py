#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py —— 端到端最小管线（对应 roadmap 阶段01「骨架」）

管线流程：文本 → 向量化 → 存储 → 检索 → 返回

设计说明：
  向量化这一步调用官方 KYSDK embedding SDK（通过 C 程序 emb_vec 完成，
  见同目录 emb_vec.c），本文件负责其余部分：把向量存起来、按相似度检索、
  返回结果。这是本项目最简单可用的版本（roadmap 称为"骨架"），
  存储用的是内存里的一个 Python list，不涉及任何数据库，
  检索用的是暴力遍历 + 余弦相似度，没有做任何加速优化。
  这样做的目的是先证明"文本进、结果出"整条链路走得通，
  后续阶段（roadmap 阶段02）再考虑数据量变大后要不要换更专业的向量库。

依赖：需要与本文件同目录下已编译好的可执行文件 emb_vec
      （由 emb_vec.c 编译而来，见同目录 emb_vec.c 的编译说明）。
"""

import subprocess
import math
import os

# emb_vec 可执行文件的路径，默认认为它和本脚本在同一目录
EMB_VEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emb_vec")


def get_vector(text: str) -> list[float]:
    """调用 emb_vec 工具，把一段文本变成 768 维向量（浮点数列表）。

    做法：用 subprocess 运行 `./emb_vec "文本"`，emb_vec 会在 stdout
    打印一行用空格分隔的 768 个浮点数；这里把这一行按空格切开，
    每一段转成 float，组成一个 Python 列表返回。
    """
    result = subprocess.run(
        [EMB_VEC_PATH, text],
        capture_output=True,
        text=True,       # 让 stdout/stderr 以字符串（而非 bytes）形式返回
        encoding="utf-8"  # 显式指定 UTF-8，避免中文编码出问题
    )
    if result.returncode != 0:
        raise RuntimeError(f"emb_vec 调用失败（文本：{text}）：{result.stderr}")

    numbers_str = result.stdout.strip().split()
    vector = [float(x) for x in numbers_str]
    return vector


def dot_product(a: list[float], b: list[float]) -> float:
    """两个等长向量的点积：对应位置相乘，再把所有结果加起来。"""
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total


def normalize(vec: list[float]) -> list[float]:
    """L2 归一化：把向量的"长度"（模长）变成 1，方向不变。

    模长 = 每一项平方后求和，再开根号。
    归一化 = 每一项都除以这个模长。
    归一化之后，两个向量的点积就直接等于它们的余弦相似度
    （这是余弦相似度的数学性质，团队已经理解过原理）。
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec  # 避免除以0（正常情况下 embedding 向量不会是全零向量）
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度 = 两个向量各自归一化后，再做点积。"""
    return dot_product(normalize(a), normalize(b))


# ---------------------------------------------------------------------------
# 一个最简单的"向量库"：本质就是一个 Python list，
# 每一项是一个字典 {"text": 原文, "vector": 向量}。
# 没有用任何数据库软件，数据只存在内存里，程序退出后就没了——
# 这正是"骨架"阶段该有的样子：先跑通逻辑，不考虑持久化和性能。
# ---------------------------------------------------------------------------

def add_to_store(store: list[dict], text: str) -> None:
    """把一段文本向量化后，存进 store 列表里。"""
    vector = get_vector(text)
    store.append({"text": text, "vector": vector})


def search(store: list[dict], query_text: str, top_k: int = 3) -> list[dict]:
    """在 store 里找出跟 query_text 最相似的 top_k 条记录。

    步骤：
      1. 把 query_text 也向量化；
      2. 遍历 store 里的每一条，算它跟 query 向量的余弦相似度；
      3. 按相似度从高到低排序；
      4. 取前 top_k 条，把原文和相似度分数一起返回。
    """
    query_vector = get_vector(query_text)

    scored = []
    for item in store:
        score = cosine_similarity(query_vector, item["vector"])
        scored.append({"text": item["text"], "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# 演示：建一个小知识库，然后拿一个问题去检索
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 一批示例文本，模拟"知识库"里已有的资料
    demo_texts = [
        "怎么重启网络服务",
        "如何查看磁盘剩余空间",
        "今天天气怎么样",
        "网络连接不上怎么办",
        "如何清理系统缓存文件",
    ]

    print("=" * 50)
    print("步骤1：构建知识库（对每条文本调用官方SDK向量化并存储）")
    print("=" * 50)
    store = []
    for t in demo_texts:
        add_to_store(store, t)
        print(f"  已存入：{t}  (向量维度={len(store[-1]['vector'])})")

    query = "网络服务重启方法"
    print()
    print("=" * 50)
    print(f"步骤2：检索 —— 查询文本：「{query}」")
    print("=" * 50)
    results = search(store, query, top_k=3)
    for rank, r in enumerate(results, start=1):
        print(f"  第{rank}名  相似度={r['score']:.4f}   原文：{r['text']}")
