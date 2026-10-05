#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_by_length.py —— 向量化耗时与文本长度的关系测量

为什么需要这个脚本（一次真实的认知修正）：
  bench.py 的首轮结果显示新架构 P95 高达 3348ms，看起来「≤500ms 硬指标」已经没救了。
  但仔细看会发现那是把 49~4342 字的文本混在一起统计的结果，而这两类文本在本项目里
  承担的角色完全不同：

    · 查询文本（用户提问）—— 短，几个字到几十个字。它的耗时才计入「知识检索响应时间 ≤500ms」。
    · 文档文本（知识库内容）—— 可能很长。它只在【离线建索引】时被向量化一次，
      用户查询时根本不会重新算，因此不占查询延迟预算。

  把两者混在一个 P95 里，得到的数字既不代表查询延迟、也不代表建索引吞吐，两头不着。
  本脚本按长度分档单独测量，把「500ms 预算够不够用」这个问题回答清楚。

用法：
  python3 bench_by_length.py --dataset sample.jsonl
"""

import argparse
import json
import time

from embedding_client import EmbeddingClient


# 各长度档位。前两档模拟真实查询语句，后面几档模拟文档切块与整条长记录。
LENGTH_BUCKETS = [
    ("查询-短句(约10字)",   10),
    ("查询-长句(约30字)",   30),
    ("文档块(128字)",      128),
    ("文档块(256字)",      256),
    ("文档块(512字)",      512),
    ("文档块(1024字)",    1024),
    ("整条长记录(2048字)", 2048),
]

REPEAT = 15  # 每档重复次数


def percentile(sorted_values, pct):
    if not sorted_values:
        return float("nan")
    k = max(1, int(round(pct / 100.0 * len(sorted_values))))
    return sorted_values[min(k, len(sorted_values)) - 1]


def build_corpus(dataset_path):
    """把数据集里的文本拼成一个长语料，供各档位截取。用真实文本而非重复字符，
    避免模型对退化输入（如 'aaaa...'）表现出不真实的耗时。"""
    parts = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ti = rec.get("tool_input") or {}
            intent = ti.get("description") or ti.get("command") or ""
            parts.append(f"{rec.get('tool_name','')} {intent} {rec.get('tool_output','')}")
            if sum(len(p) for p in parts) > 100000:
                break
    return " ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    args = ap.parse_args()

    corpus = build_corpus(args.dataset)
    print(f"语料总长 {len(corpus)} 字，每档重复 {REPEAT} 次\n")

    print(f"{'长度档位':<22}{'字数':>7}{'最小':>10}{'P50':>10}{'P95':>10}{'最大':>10}")
    print("-" * 72)

    results = []
    with EmbeddingClient() as client:
        for name, nchars in LENGTH_BUCKETS:
            timings = []
            for i in range(REPEAT):
                # 每次从语料不同位置截取，避免命中任何潜在的重复输入缓存
                start = (i * 3571) % max(1, len(corpus) - nchars - 1)
                text = corpus[start:start + nchars]
                t = time.perf_counter()
                client.encode(text)
                timings.append((time.perf_counter() - t) * 1000)

            s = sorted(timings)
            row = {
                "档位": name, "字数": nchars,
                "最小": s[0], "P50": percentile(s, 50),
                "P95": percentile(s, 95), "最大": s[-1],
            }
            results.append(row)
            print(f"{name:<22}{nchars:>7}{row['最小']:>9.1f}ms{row['P50']:>9.1f}ms"
                  f"{row['P95']:>9.1f}ms{row['最大']:>9.1f}ms")

    # 结论部分：直接回答「500ms 预算够不够」
    print("\n" + "=" * 72)
    print("对照硬指标：知识检索响应时间 ≤500ms")
    print("=" * 72)
    q = results[1]   # 以「查询-长句(约30字)」作为查询延迟的代表，取较保守的一档
    print(f"查询向量化（30字）P95 = {q['P95']:.1f}ms，占 500ms 预算的 {q['P95']/500*100:.1f}%")
    print(f"余下 {500 - q['P95']:.1f}ms 留给索引检索、结果组装与网络开销。")

    chunk512 = next(r for r in results if r["字数"] == 512)
    print(f"\n建索引吞吐估算（按 512 字切块、P50 {chunk512['P50']:.0f}ms/块）：")
    print(f"  14047 条记录若平均切成 2 块 ≈ 28094 块，"
          f"单线程约 {chunk512['P50'] * 28094 / 1000 / 60:.0f} 分钟（离线一次性开销）")


if __name__ == "__main__":
    main()
