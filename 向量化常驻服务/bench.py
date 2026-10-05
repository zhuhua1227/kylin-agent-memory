#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench.py —— 向量化性能基线测量（新旧架构对比）

测什么、为什么测：
  硬指标要求「知识检索响应时间 ≤500ms」。一次检索 = 把查询文本向量化 + 在索引里比对。
  其中「把查询文本向量化」这一步的耗时，就是这个 500ms 预算的主要支出项之一。
  本脚本把它精确测出来，作为后续拆解 500ms 预算的依据 —— 没有这个数字，
  后面所有关于性能的判断都只能靠猜。

对比两种架构：
  旧（阶段01 emb_vec）：每次向量化都起一个新进程，含建会话 + 加载 339MB 模型
  新（emb_serverd）    ：进程常驻，模型只加载一次，每次请求只剩推理开销

用法：
  # 完整对比（推荐）
  python3 bench.py --dataset /path/to/tool_execution_records.jsonl \\
                   --emb-vec ../端到端最小管线/emb_vec --samples 100

  # 只测新架构（不做旧架构对比时）
  python3 bench.py --dataset /path/to/tool_execution_records.jsonl --samples 100

说明：旧架构每条都要加载模型，很慢。脚本默认只对旧架构测少量样本（--old-samples，
默认 10 条），因为它的耗时高度稳定、不需要大样本，而且测太多会白等很久。
"""

import argparse
import json
import os
import subprocess
import time

from embedding_client import EmbeddingClient


def percentile(sorted_values: list, pct: float) -> float:
    """取百分位数。用最近秩法（nearest-rank），样本量不大时比插值法更直观、不会造出原始数据里没有的值。"""
    if not sorted_values:
        return float("nan")
    k = max(1, int(round(pct / 100.0 * len(sorted_values))))
    return sorted_values[min(k, len(sorted_values)) - 1]


def summarize(name: str, timings_ms: list) -> dict:
    s = sorted(timings_ms)
    return {
        "名称": name,
        "样本数": len(s),
        "最小": s[0],
        "P50": percentile(s, 50),
        "P95": percentile(s, 95),
        "P99": percentile(s, 99),
        "最大": s[-1],
        "平均": sum(s) / len(s),
    }


def print_table(rows: list) -> None:
    header = ["名称", "样本数", "最小", "P50", "P95", "P99", "最大", "平均"]
    print(f"{'架构':<28}{'样本':>6}{'最小':>11}{'P50':>11}{'P95':>11}{'P99':>11}{'最大':>11}{'平均':>11}")
    print("-" * 100)
    for r in rows:
        print(
            f"{r['名称']:<28}{r['样本数']:>6}"
            f"{r['最小']:>10.1f}ms{r['P50']:>10.1f}ms{r['P95']:>10.1f}ms"
            f"{r['P99']:>10.1f}ms{r['最大']:>10.1f}ms{r['平均']:>10.1f}ms"
        )


def load_texts(dataset_path: str, count: int) -> list:
    """从真实数据集里取样本文本。

    刻意用真实的工具执行记录而不是手写短句：真实文本长度分布差异很大
    （从十几字的命令到几 KB 的输出），用造出来的短句测会得到过于乐观的数字。
    """
    texts = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            if len(texts) >= count:
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 拼成一条与后续「知识卡片」形态接近的文本，让测量贴近真实使用场景
            tool = rec.get("tool_name", "")
            ti = rec.get("tool_input") or {}
            intent = ti.get("description") or ti.get("command") or json.dumps(ti, ensure_ascii=False)
            out = str(rec.get("tool_output", ""))[:500]
            text = f"{tool} | {intent} | {out}".strip()
            if text:
                texts.append(text)
    return texts


def bench_new(texts: list) -> list:
    """测常驻架构。注意：启动耗时单独报告，不计入单条耗时。"""
    print("\n[新架构] 启动 emb_serverd（含模型加载与预热，冷启动需等数十秒）...")
    t0 = time.perf_counter()
    client = EmbeddingClient()
    startup_ms = (time.perf_counter() - t0) * 1000
    print(f"[新架构] 启动完成，耗时 {startup_ms / 1000:.1f} 秒（这是一次性开销）")

    timings = []
    try:
        for i, text in enumerate(texts, start=1):
            t = time.perf_counter()
            vec = client.encode(text)
            timings.append((time.perf_counter() - t) * 1000)
            if i == 1 and len(vec) != 768:
                raise SystemExit(f"维度异常：期望 768，实得 {len(vec)}")
            if i % 20 == 0:
                print(f"  [新架构] {i}/{len(texts)}", flush=True)
    finally:
        client.close()

    return timings, startup_ms


def bench_old(emb_vec_path: str, texts: list) -> list:
    """测旧架构：每条起一个新进程。"""
    timings = []
    for i, text in enumerate(texts, start=1):
        t = time.perf_counter()
        proc = subprocess.run(
            [emb_vec_path, text],
            capture_output=True, text=True, encoding="utf-8",
        )
        elapsed = (time.perf_counter() - t) * 1000
        if proc.returncode != 0:
            print(f"  [旧架构] 第 {i} 条调用失败，跳过：{proc.stderr.strip()[:120]}")
            continue
        timings.append(elapsed)
        print(f"  [旧架构] {i}/{len(texts)}  {elapsed:.0f}ms", flush=True)
    return timings


def main():
    ap = argparse.ArgumentParser(description="向量化性能基线测量")
    ap.add_argument("--dataset", required=True, help="真实数据集 jsonl 路径")
    ap.add_argument("--samples", type=int, default=100, help="新架构测量样本数（默认 100）")
    ap.add_argument("--emb-vec", default=None, help="旧架构 emb_vec 可执行文件路径；不给则跳过旧架构对比")
    ap.add_argument("--old-samples", type=int, default=10,
                    help="旧架构测量样本数（默认 10，因为每条都要重载模型、很慢）")
    args = ap.parse_args()

    if not os.path.exists(args.dataset):
        raise SystemExit(f"数据集不存在：{args.dataset}")

    texts = load_texts(args.dataset, args.samples)
    if len(texts) < args.samples:
        print(f"注意：数据集只取到 {len(texts)} 条（少于请求的 {args.samples} 条）")
    print(f"样本取自真实数据集，共 {len(texts)} 条；"
          f"文本长度：最短 {min(len(t) for t in texts)} 字，"
          f"最长 {max(len(t) for t in texts)} 字，"
          f"平均 {sum(len(t) for t in texts) // len(texts)} 字")

    rows = []

    new_timings, startup_ms = bench_new(texts)
    rows.append(summarize("新：emb_serverd 常驻", new_timings))

    if args.emb_vec:
        if not os.path.exists(args.emb_vec):
            print(f"\n跳过旧架构对比：找不到 {args.emb_vec}")
        else:
            print(f"\n[旧架构] 开始测量 {args.old_samples} 条（每条都要重新加载模型，请耐心等待）...")
            old_timings = bench_old(args.emb_vec, texts[:args.old_samples])
            if old_timings:
                rows.append(summarize("旧：emb_vec 每次起进程", old_timings))

    print("\n" + "=" * 100)
    print("性能测量结果")
    print("=" * 100)
    print_table(rows)

    print(f"\n新架构一次性启动开销（含模型加载与预热）：{startup_ms / 1000:.1f} 秒")

    if len(rows) == 2:
        speedup = rows[1]["P50"] / rows[0]["P50"]
        print(f"P50 提速倍数：{speedup:.1f}×")
        print(f"按 P50 估算，向量化 14047 条记录："
              f"旧架构约 {rows[1]['P50'] * 14047 / 1000 / 3600:.1f} 小时，"
              f"新架构约 {rows[0]['P50'] * 14047 / 1000 / 60:.1f} 分钟")

    p95 = rows[0]["P95"]
    print(f"\n对照硬指标（知识检索响应 ≤500ms）：新架构向量化 P95 = {p95:.1f}ms，"
          f"占 500ms 预算的 {p95 / 500 * 100:.1f}%，余下 {500 - p95:.1f}ms 留给索引检索与结果组装。")


if __name__ == "__main__":
    main()
