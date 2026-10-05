#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_backends.py —— 两种向量存储后端的「轻量化程度」量化论证

═══ 这个脚本对应评分标准的哪一条 ═══════════════════════════════════

    「（2）方案可行性（30分）：技术架构设计合理性、算法选型适配性、原型开发可落地性，
      **适配端侧部署的轻量化程度，需提供量化论证数据**」

以及题目应用部分的原文：

    「……显著提升个性化服务响应速度、知识复用效率，**降低端侧资源占用**」

「轻量化」与「端侧资源占用」是被写进评分和题目的词，但它们不等于「检索快」。
本脚本测量五个维度，让选型有据可依：

    1. 冷启动耗时      —— 端侧应用不能让用户等
    2. 建索引耗时      —— 影响开发迭代节奏
    3. 检索延迟 P50/P95 —— 对照 ≤500ms 硬指标
    4. **常驻内存占用** —— 端侧设备最稀缺的资源
    5. **磁盘占用**     —— 同上

⚠️ 用随机向量测量的说明：
  以上五项都只取决于数据规模与向量维度，与向量数值无关，因此用随机向量测量有效。
  但随机向量**不能**用于评估召回质量——那需要真实向量与标注评测集（任务 C）。

用法：
    python3 bench_backends.py --count 20000 --repeat 20
"""

import argparse
import os
import subprocess
import time

from vector_store import create_store, DEFAULT_DIM


# ═════════════════════════════════════════════════════ 资源测量工具

def rss_kb(pid: int) -> int:
    """读某个进程的常驻内存（VmRSS，单位 KB）。读不到返回 0。"""
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def find_engine_pid() -> int:
    """找到向量引擎守护进程的 pid。

    注意：这个守护进程是**全系统共用**的用户级服务，不是我们启动的。
    所以要测的是「插入数据前后它的内存增量」，而不是它的总占用——
    总占用里包含了与本项目无关的其他数据。
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "^/usr/bin/kylin-ai-vector-engine "],
            capture_output=True, text=True)
        pids = [int(p) for p in out.stdout.split() if p.strip()]
        return pids[0] if pids else 0
    except (OSError, ValueError):
        return 0


def dir_size(path: str) -> int:
    """目录或文件的字节数。"""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total


def percentile(vals, pct):
    s = sorted(vals)
    k = max(1, int(round(pct / 100.0 * len(s))))
    return s[min(k, len(s)) - 1]


def mb(kb_or_bytes, is_bytes=False):
    return kb_or_bytes / (1024 * 1024) if is_bytes else kb_or_bytes / 1024


# ═════════════════════════════════════════════════════ 单个后端的测量

def bench_one(backend: str, vectors, queries, top_k: int):
    """测一个后端，返回各项指标的字典。"""
    import numpy as np

    result = {"后端": backend}
    engine_pid = find_engine_pid()

    # ---- 基线：测量前的资源占用 ----
    engine_rss_before = rss_kb(engine_pid) if engine_pid else 0
    self_rss_before = rss_kb(os.getpid())

    kylin_db = os.path.expanduser("~/.local/share/kylin-ai-vector-engine/default.db")
    numpy_dir = os.path.expanduser("~/.local/share/rag-bench-numpy")
    disk_before = dir_size(kylin_db) if backend == "kylin" else 0

    # ---- 1. 冷启动 ----
    kwargs = {"collection": "rag_bench_cmp"} if backend == "kylin" else {"data_dir": numpy_dir}
    t0 = time.perf_counter()
    store = create_store(backend, **kwargs)
    result["冷启动ms"] = (time.perf_counter() - t0) * 1000

    try:
        store.create(dim=DEFAULT_DIM)

        # ---- 2. 建索引 ----
        t0 = time.perf_counter()
        # 分批写入，模拟真实建库流程（一次性全塞进内存不现实）
        BATCH = 1000
        for i in range(0, len(vectors), BATCH):
            chunk = [(j, vectors[j]) for j in range(i, min(i + BATCH, len(vectors)))]
            store.add(chunk)
        if backend == "numpy":
            store.save()          # numpy 后端需要显式落盘
        result["建索引秒"] = time.perf_counter() - t0
        result["条数"] = store.count()

        # ---- 3. 检索延迟 ----
        timings = []
        for q in queries:
            t0 = time.perf_counter()
            hits = store.search(q, top_k=top_k)
            timings.append((time.perf_counter() - t0) * 1000)
        result["检索P50ms"] = percentile(timings, 50)
        result["检索P95ms"] = percentile(timings, 95)
        result["返回条数"] = len(hits)

        # ---- 4. 常驻内存 ----
        # 官方引擎：数据在守护进程里，测它的增量；再加上桥接进程本身的占用。
        # numpy   ：数据在本进程内存里，测本进程的增量。
        if backend == "kylin":
            engine_delta = (rss_kb(engine_pid) - engine_rss_before) if engine_pid else 0
            bridge = rss_kb(store._proc.pid)
            result["内存MB"] = mb(engine_delta + bridge)
            result["内存构成"] = f"引擎守护进程增量 {mb(engine_delta):.1f}MB + 桥接进程 {mb(bridge):.1f}MB"
        else:
            self_delta = rss_kb(os.getpid()) - self_rss_before
            result["内存MB"] = mb(self_delta)
            result["内存构成"] = f"本进程内存增量（向量矩阵常驻）"

        # ---- 5. 磁盘占用 ----
        if backend == "kylin":
            result["磁盘MB"] = mb(dir_size(kylin_db) - disk_before, is_bytes=True)
            result["磁盘说明"] = "共用库 default.db 的增量"
        else:
            result["磁盘MB"] = mb(dir_size(numpy_dir), is_bytes=True)
            result["磁盘说明"] = "vectors.npy + doc_ids.npy"

    finally:
        if backend == "kylin":
            try:
                store.drop()      # 清理，别把测试数据留在全系统共用的库里
            except Exception:
                pass
        store.close()

    return result


# ═════════════════════════════════════════════════════ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20000, help="索引条数")
    ap.add_argument("--repeat", type=int, default=20, help="检索重复次数")
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()

    import numpy as np
    print(f"生成 {args.count} 条 {DEFAULT_DIM} 维随机向量...")
    rng = np.random.default_rng(42)
    vectors = rng.standard_normal((args.count, DEFAULT_DIM), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors.tolist()
    queries = rng.standard_normal((args.repeat, DEFAULT_DIM), dtype=np.float32).tolist()
    print("生成完毕\n")

    results = []
    for backend in ("kylin", "numpy"):
        print(f"—— 测量后端：{backend} ——", flush=True)
        results.append(bench_one(backend, vectors, queries, args.top_k))
        print("  完成\n", flush=True)

    # ---- 输出对比表 ----
    print("=" * 84)
    print(f"两种存储后端对比（{args.count} 条 × {DEFAULT_DIM} 维，top-{args.top_k}）")
    print("=" * 84)
    rows = [
        ("冷启动", "冷启动ms", "{:.1f} ms"),
        ("建索引", "建索引秒", "{:.2f} 秒"),
        ("检索 P50", "检索P50ms", "{:.2f} ms"),
        ("检索 P95", "检索P95ms", "{:.2f} ms"),
        ("常驻内存", "内存MB", "{:.1f} MB"),
        ("磁盘占用", "磁盘MB", "{:.1f} MB"),
    ]
    print(f"{'指标':<14}{'官方麒麟向量引擎':>22}{'轻量 numpy':>20}")
    print("-" * 84)
    for label, key, fmt in rows:
        a = fmt.format(results[0][key])
        b = fmt.format(results[1][key])
        print(f"{label:<14}{a:>22}{b:>20}")

    print("\n内存构成说明：")
    for r in results:
        print(f"  {r['后端']:<8}{r['内存构成']}")
    print("\n磁盘占用说明：")
    for r in results:
        print(f"  {r['后端']:<8}{r['磁盘说明']}")

    # ---- 对照硬指标 ----
    # 独立评审R-5指出：查询向量化P95先后测出68.4/84.9/118~124/28.4ms四个值，
    # 此前这里硬编码了最早、已被重跑推翻的68.4ms（《向量化常驻服务/说明报告.md》
    # 结论区已更新为84.9ms）。改用84.9ms，且此值只在此处硬编码一处，报告与
    # 其它脚本统一从这里/该报告引用，不再各自维护一份容易失步的副本。
    EMB_P95 = 84.9   # 《向量化常驻服务/说明报告.md》5.3节实测：30字查询向量化P95（2026-08-04重跑值）
    print("\n" + "=" * 84)
    print("对照硬指标：知识检索响应时间 ≤500ms")
    print("=" * 84)
    for r in results:
        e2e = r["检索P95ms"] + EMB_P95
        print(f"  {r['后端']:<8}端到端 P95 上界估计 = 查询向量化 {EMB_P95}ms + 检索 {r['检索P95ms']:.2f}ms"
              f" ≈ {e2e:.1f}ms（占预算 {e2e / 500 * 100:.1f}%，两个独立基准的P95相加是保守上界，非逐题求和分位数）  "
              f"{'✅ 达标' if e2e <= 500 else '❌ 超标'}")

    print("\n注：两个后端的检索结果在正确性上已验证等价"
          "（见 vector_store.py 自检，均与阶段01 记录逐位一致）。")


if __name__ == "__main__":
    main()
