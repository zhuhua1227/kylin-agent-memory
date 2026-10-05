#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_bruteforce.py —— 暴力检索在真实数据规模下还够不够用？

问题背景：
  阶段01 的「端到端最小管线」用的是最朴素的检索方式——把查询向量和库里每一条
  逐个算余弦相似度，再排序取 top-K（即"暴力检索"）。当时只有 5 条数据，跑得飞快。
  但本项目真实数据切块后约 2~3 万条，硬指标又要求「知识检索响应 ≤500ms」，
  所以必须回答：这个规模下暴力检索还撑不撑得住？

本脚本对比四种做法在同一批数据上的检索耗时：
  1. 阶段01 原样实现        —— 每次比较都对两个向量各做一次 L2 归一化
  2. 预归一化 + 纯 Python   —— 建库时就把向量归一化好，检索时只做点积
  3. 预归一化 + numpy       —— 把逐条循环换成一次矩阵乘法
  4. （官方向量引擎见 ve_bench.cpp，不在本脚本内）

⚠️ 关于用随机向量测量的说明：
  检索耗时只取决于数据规模与向量维度，与向量的具体数值无关，因此用随机向量测
  **延迟**是有效的。但随机向量**不能**用来评估召回质量——那需要真实向量与
  标注好的评测集，属于任务 C 的范畴。本脚本只回答"快不快"，不回答"准不准"。

用法：
  python3 bench_bruteforce.py --count 20000 --repeat 5
"""

import argparse
import math
import random
import time

DIM = 768   # 与官方 embedding 输出维度一致


# --------------------------------------------------------------------------
# 做法一：阶段01 pipeline.py 的原样实现（未做任何优化）
# --------------------------------------------------------------------------

def dot_product(a, b):
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total


def normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def cosine_similarity(a, b):
    return dot_product(normalize(a), normalize(b))


def search_stage01(store, qvec, top_k=3):
    """阶段01 原样写法。注意它每比较一条就把 query 重新归一化一次——
    库里有 2 万条，query 就被白白归一化 2 万次。"""
    scored = [(cosine_similarity(qvec, item), i) for i, item in enumerate(store)]
    scored.sort(reverse=True)
    return scored[:top_k]


# --------------------------------------------------------------------------
# 做法二：预归一化 + 纯 Python
# 建库时就把每条向量归一化好，查询向量也只归一化一次，
# 检索时余弦相似度就退化成一次点积。算法完全等价，只是把重复劳动挪走了。
# --------------------------------------------------------------------------

def search_prenorm(store_norm, qvec_norm, top_k=3):
    scored = [(dot_product(qvec_norm, v), i) for i, v in enumerate(store_norm)]
    scored.sort(reverse=True)
    return scored[:top_k]


# --------------------------------------------------------------------------
# 做法三：预归一化 + numpy
# 把「逐条循环算点积」换成「一次矩阵 × 向量」，由 BLAS 用向量化指令完成。
# --------------------------------------------------------------------------

def search_numpy(matrix, qvec_norm, top_k=3):
    import numpy as np
    scores = matrix @ qvec_norm              # (N,768) @ (768,) -> (N,)
    # 独立评审R-15：argpartition 的 kth 参数是0-based下标，此前传 top_k
    # （不是 top_k-1），当 count==top_k 时 kth 越界触发 IndexError——
    # vector_store.py 同一操作已经是对的（k-1），这里对齐它。
    k = min(top_k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]   # 只挑出前K，不做全排序
    idx = idx[np.argsort(-scores[idx])]
    return [(float(scores[i]), int(i)) for i in idx]


# --------------------------------------------------------------------------

def percentile(sorted_vals, pct):
    k = max(1, int(round(pct / 100.0 * len(sorted_vals))))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def report(name, timings_ms, budget=500.0):
    s = sorted(timings_ms)
    p50, p95 = percentile(s, 50), percentile(s, 95)
    verdict = "✅ 达标" if p95 <= budget else "❌ 超标"
    print(f"{name:<34}{p50:>10.1f}ms{p95:>10.1f}ms   {verdict}"
          f"（P95 占 500ms 预算 {p95 / budget * 100:.0f}%）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20000, help="库中向量条数")
    ap.add_argument("--repeat", type=int, default=5, help="每种做法重复检索次数")
    ap.add_argument("--skip-stage01", action="store_true",
                    help="跳过阶段01 原样实现（它很慢，大规模时可跳过）")
    args = ap.parse_args()

    print(f"生成 {args.count} 条 {DIM} 维随机向量作为知识库...")
    random.seed(42)
    store = [[random.uniform(-1, 1) for _ in range(DIM)] for _ in range(args.count)]
    query = [random.uniform(-1, 1) for _ in range(DIM)]
    print("生成完毕\n")

    print(f"{'检索做法':<32}{'P50':>12}{'P95':>12}   结论")
    print("-" * 88)

    # 做法一
    if not args.skip_stage01:
        t = []
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            search_stage01(store, query)
            t.append((time.perf_counter() - t0) * 1000)
        report("① 阶段01 原样（每次都归一化）", t)

    # 做法二
    store_norm = [normalize(v) for v in store]
    qn = normalize(query)
    t = []
    for _ in range(args.repeat):
        t0 = time.perf_counter()
        search_prenorm(store_norm, qn)
        t.append((time.perf_counter() - t0) * 1000)
    report("② 预归一化 + 纯 Python", t)

    # 做法三
    try:
        import numpy as np
        matrix = np.array(store_norm, dtype=np.float32)
        qn_np = np.array(qn, dtype=np.float32)
        t = []
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            search_numpy(matrix, qn_np)
            t.append((time.perf_counter() - t0) * 1000)
        report("③ 预归一化 + numpy", t)
    except ImportError:
        print("③ 预归一化 + numpy               —— numpy 未安装，跳过")

    print("\n注：以上只是「检索」这一步的耗时。完整的检索响应还要加上")
    print("    「查询文本向量化」的耗时（实测 30 字查询 P95 = 68.4ms），")
    print("    两者之和才是要对照 500ms 硬指标的数字。")


if __name__ == "__main__":
    main()
