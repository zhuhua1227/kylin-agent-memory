#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_L2.py —— 所有「L2 层」测量的实际执行者（**必须在银河麒麟 + KYSDK 环境上跑**）

═══ L2 覆盖哪些数字，为什么它们只能在这里测 ═══════════════════════

L1（见 measure_L1.py）能在任何机器上验证一大半数字，但有一类它天然验不了：
**「这些向量确实出自官方麒麟 KYSDK」**。L1 用的是固化导出的向量文件，
读者能重算相似度，却无法确认这批数字最初是怎么来的。

L2 就是补上这一环，覆盖：

  M-201  官方 SDK 输出 768 维（含两种独立方法交叉验证）
  M-202  查询向量化延迟（按文本长度分档）——「≤500ms」硬指标的主要支出项
  M-205  两种存储后端的资源占用对比——「轻量化程度量化论证」
  M-206  官方向量引擎端到端检索正确性——与 L1 的 M-106 互为印证

⚠️ 前提：环境已恢复（`~/emb_test` 能跑出 768）、已登录图形桌面
（`kylin-ai-runtime` 与向量引擎都是用户级服务，需要图形会话才起得来）、
且已放行 kysec 执行控制（`sudo setstatus -f exectl off`）。

用法：
    python3 measure_L2.py            # 跑全部
    python3 measure_L2.py M-202      # 只跑指定项
"""

import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)

# 麒麟机上各组件的部署位置
SERVERD_DIR = os.path.expanduser("~/rag/serverd")
VE_WORK_DIR = os.path.expanduser("~/rag/ve/work")
EMB_TEST = os.path.expanduser("~/emb_test")

sys.path.insert(0, SERVERD_DIR)
sys.path.insert(0, VE_WORK_DIR)

from measure import Measurement, rss_mb  # noqa: E402

DIM = 768
SEED = 42
BENCH_COUNT = 20000

# 与阶段01「端到端最小管线」完全相同的文本与查询，勿改——
# 改了就失去与历史记录逐位对照的意义
DEMO_TEXTS = [
    "怎么重启网络服务",
    "如何查看磁盘剩余空间",
    "今天天气怎么样",
    "网络连接不上怎么办",
    "如何清理系统缓存文件",
]
QUERY = "网络服务重启方法"

LENGTH_BUCKETS = [
    ("query_10char", 10), ("query_30char", 30),
    ("chunk_128char", 128), ("chunk_256char", 256),
    ("chunk_512char", 512), ("chunk_1024char", 1024),
    ("record_2048char", 2048),
]


# 版本号从冻结记录读，不写死（2026-08-14：整链重跑时四处硬编码的
# "v1.3.0" 改一处漏三处，记录标题会出现「内容是新的、标签是旧的」）
def _eval_set_version():
    """从冻结记录读版本号——唯一来源，避免多处硬编码分叉。

    ⚠️ 2026-08-15 修：这里原本写死相对路径 `../评测集/eval_set_frozen.json`，
    而**麒麟机上评测集目录名是英文 `eval`**——同一个历史坑，本文件其他地方
    早就用 `RAG_EVAL_DIR` 环境变量绕开了，唯独这里没有。后果是 L2 记录的
    标题**永远是"(版本未知)"**：一个只影响标题、不影响数字的小缺陷，
    因此没人注意，直到本轮重跑 M-207 才看见。

    它值得记一笔的原因是形态：**同一个坑修了大部分调用点，漏掉一个**——
    与 R2-28（只修 `__main__` 没修 import 路径）、R3-26（判定挪走了但有一处
    仍读旧字段）是同一类"修实例没修类"。
    """
    import json as _j, os as _o
    base = _o.path.dirname(_o.path.abspath(__file__))
    eval_dir = (_o.environ.get("RAG_EVAL_DIR")
                or _o.path.join(_o.path.dirname(base), "评测集"))
    p = _o.path.join(_o.path.expanduser(eval_dir), "eval_set_frozen.json")
    try:
        with open(p, encoding="utf-8") as f:
            return "v" + _j.load(f)["version"]
    except Exception:
        return "(版本未知)"


def emb_client():
    from embedding_client import EmbeddingClient
    return EmbeddingClient(serverd_path=os.path.join(SERVERD_DIR, "emb_serverd"))


# ═════════════════════════════════════════════════════ M-201 768 维

def m201():
    """官方 SDK 输出 768 维，用两种互相独立的方法验证。

    ★这是交叉验证 X-001★：
      方法一 —— 问模型自己：`embedding_model_info_get_model_dim()` 读模型元数据
      方法二 —— 数实际结果：`embedding_result_get_vector_length()` 取输出长度

    两条路径在 SDK 内部不共用同一个数据来源，一个来自模型描述文件，
    一个来自实际推理结果。两者一致，比任何单一测量都更能说明问题。
    """
    with Measurement(
        "M-201", "官方 KYSDK embedding 输出 768 维（两种独立方法交叉验证）",
        layer="L2", command="python3 measure_L2.py M-201",
    ) as m:
        prog = os.path.join(BASE, "dim_crosscheck")
        out = subprocess.run([prog], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180)
        print(out.stdout)

        # 路径一：模型元数据自述的维度（embedding_model_info_get_model_dim）
        md = re.search(r"model_dim=(-?\d+)", out.stdout)
        if md:
            m.set_value("model_dim_from_metadata", int(md.group(1)), unit="维")

        # 路径二：实际推理输出的长度（embedding_result_get_vector_length）
        vl = re.search(r"vector_length=(-?\d+)", out.stdout)
        if vl:
            m.set_value("vector_length_from_result", int(vl.group(1)), unit="维")

        m.set_value("crosscheck_agree", 1 if "交叉验证: 一致" in out.stdout else 0)

        # 前 5 个向量值：跨程序比对的锚点（见 X-003）。
        # emb_test / emb_vec / emb_serverd / dim_crosscheck 对同一文本应输出完全相同的值。
        fv = re.search(r"前5个值:\s*(.+)", out.stdout)
        if fv:
            m.set_value("first5_values", " ".join(fv.group(1).split()[:5]))

        mn = re.search(r"模型名:\s*(\S+)", out.stdout)
        if mn:
            m.set_value("model_name", mn.group(1))

        m.set_value("exit_code", out.returncode)
        if out.returncode != 0:
            m.note(f"两条路径不一致或调用失败，stderr：{out.stderr[-300:]}")


# ═════════════════════════════════════════════════════ M-202 向量化延迟

def m202():
    """向量化耗时与文本长度的关系。

    ★这是「知识检索响应 ≤500ms」硬指标的关键依据★

    必须区分两类文本，混在一起统计会得出完全错误的结论：
      · 查询文本（用户提问）—— 短，每次检索都要算，**计入 500ms 预算**
      · 文档文本（知识内容）—— 长，只在离线建索引时算一次，**不计入**
    """
    corpus_path = os.path.join(VE_WORK_DIR, "sample.jsonl")
    with Measurement(
        "M-202", "向量化延迟与文本长度的关系（分档）", layer="L2",
        inputs=[corpus_path] if os.path.exists(corpus_path) else [],
        command="python3 measure_L2.py M-202",
    ) as m:
        # 用真实数据拼语料，而不是重复字符——退化输入会得到不真实的耗时
        import json
        parts = []
        if os.path.exists(corpus_path):
            with open(corpus_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ti = r.get("tool_input") or {}
                    parts.append(f"{r.get('tool_name','')} "
                                 f"{ti.get('description') or ti.get('command') or ''} "
                                 f"{r.get('tool_output','')}")
                    if sum(len(p) for p in parts) > 100000:
                        break
        corpus = " ".join(parts) or ("在银河麒麟操作系统上部署人工智能推理环境的操作记录。" * 3000)
        m.set_value("corpus_chars", len(corpus), unit="字")

        client = emb_client()
        try:
            for name, nchars in LENGTH_BUCKETS:
                for i in range(15):
                    start = (i * 3571) % max(1, len(corpus) - nchars - 1)
                    text = corpus[start:start + nchars]
                    t0 = time.perf_counter()
                    vec = client.encode(text)
                    m.add_run(f"{name}_ms", (time.perf_counter() - t0) * 1000, unit="ms")
                    if i == 0 and len(vec) != DIM:
                        m.note(f"维度异常：{name} 得到 {len(vec)} 维")
                print(f"  {name}（{nchars}字）测量完成")
        finally:
            client.close()

        m.note("查询文本用短句档（10/30字），文档文本用长档；"
               "只有查询档计入 ≤500ms 检索响应预算")


# ═════════════════════════════════════════════════════ M-205 双后端资源

def m205():
    """两种存储后端的性能与**资源占用**对比。

    对应评分标准点名要的「适配端侧部署的轻量化程度，需提供量化论证数据」。
    """
    import numpy as np
    import shutil
    import tempfile
    from vector_store import create_store

    def engine_pid():
        try:
            out = subprocess.run(["pgrep", "-f", "^/usr/bin/kylin-ai-vector-engine "],
                                 capture_output=True, text=True)
            pids = [int(p) for p in out.stdout.split() if p.strip()]
            return pids[0] if pids else 0
        except (OSError, ValueError):
            return 0

    with Measurement(
        "M-205", f"两种存储后端资源占用对比（{BENCH_COUNT} 条 × {DIM} 维）",
        layer="L2", seed=SEED, command="python3 measure_L2.py M-205",
    ) as m:
        rng = np.random.default_rng(SEED)
        mat = rng.standard_normal((BENCH_COUNT, DIM), dtype=np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        vectors = mat.tolist()
        queries = rng.standard_normal((20, DIM), dtype=np.float32).tolist()

        tmpdir = tempfile.mkdtemp(prefix="rag_m205_")
        try:
            for backend in ("kylin", "numpy"):
                pid = engine_pid() if backend == "kylin" else os.getpid()
                rss_before = rss_mb(pid) if pid else None

                kwargs = ({"collection": "rag_m205"} if backend == "kylin"
                          else {"data_dir": os.path.join(tmpdir, "np")})
                t0 = time.perf_counter()
                store = create_store(backend, **kwargs)
                m.set_value(f"{backend}_cold_start_ms",
                            round((time.perf_counter() - t0) * 1000, 3), unit="ms")

                store.create(dim=DIM)
                t0 = time.perf_counter()
                for i in range(0, len(vectors), 1000):
                    store.add([(j, vectors[j])
                               for j in range(i, min(i + 1000, len(vectors)))])
                if backend == "numpy":
                    store.save()
                m.set_value(f"{backend}_build_index_sec",
                            round(time.perf_counter() - t0, 3), unit="秒")

                for q in queries:
                    t0 = time.perf_counter()
                    store.search(q, top_k=10)
                    m.add_run(f"{backend}_search_ms",
                              (time.perf_counter() - t0) * 1000, unit="ms")

                # 内存：官方引擎的数据在守护进程里（全系统共用，故测增量）；
                # numpy 的数据在本进程内存里
                rss_after = rss_mb(pid) if pid else None
                if rss_before is not None and rss_after is not None:
                    delta = rss_after - rss_before
                    if backend == "kylin":
                        bridge = rss_mb(store._proc.pid) or 0
                        m.set_value("kylin_engine_delta_mb", round(delta, 1), unit="MB")
                        m.set_value("kylin_bridge_mb", round(bridge, 1), unit="MB")
                        m.set_value("kylin_memory_mb", round(delta + bridge, 1), unit="MB")
                    else:
                        m.set_value("numpy_memory_mb", round(delta, 1), unit="MB")

                if backend == "kylin":
                    store.drop()      # 别把测试数据留在全系统共用的库里
                store.close()
                print(f"  后端 {backend} 测量完成")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        m.note("官方引擎内存为「守护进程增量 + 桥接进程」；numpy 为本进程 RSS 增量。"
               f"原始向量数据本身约 {BENCH_COUNT * DIM * 4 / (1024*1024):.1f}MB")


# ═════════════════════════════════════════════════════ M-206 检索正确性

def m206():
    """官方 embedding + 官方向量引擎，端到端语义检索正确性。

    ★这是交叉验证 X-002★：同一批文本与查询，在三条独立路径上得到相同分数——
      阶段01 的纯 Python 暴力遍历 / 官方向量引擎 / numpy 后端。
    三方一致，说明官方引擎的 COSINE 与我们自实现的余弦相似度在数值上等价。
    """
    from vector_store import create_store

    with Measurement(
        "M-206", "官方 embedding + 官方向量引擎端到端检索正确性", layer="L2",
        command="python3 measure_L2.py M-206",
    ) as m:
        client = emb_client()
        try:
            vectors = [client.encode(t) for t in DEMO_TEXTS]
            qvec = client.encode(QUERY)
        finally:
            client.close()

        m.set_value("vector_dim", len(vectors[0]), unit="维")

        for backend in ("kylin", "numpy"):
            kwargs = ({"collection": "rag_m206"} if backend == "kylin"
                      else {"data_dir": os.path.expanduser("~/rag/repro/np_m206")})
            store = create_store(backend, **kwargs)
            try:
                store.create(dim=len(vectors[0]))
                store.add(list(enumerate(vectors)))
                hits = store.search(qvec, top_k=3)
                print(f"后端 {backend}：")
                for rank, (doc_id, score) in enumerate(hits, 1):
                    print(f"  第{rank}名  相似度={score:.4f}   原文：{DEMO_TEXTS[doc_id]}")
                    m.set_value(f"{backend}_top{rank}_score", round(score, 4))
                    m.set_value(f"{backend}_top{rank}_text", DEMO_TEXTS[doc_id])
                if backend == "kylin":
                    store.drop()
            finally:
                store.close()

        m.note("两个后端结果应与阶段01 记录逐位一致（0.9441 / 0.7118 / 0.5326）")


# ═════════════════════════════════════════════════════ M-207 知识检索召回率

def m207():
    """用冻结的评测集（v1.3.0，169题，95条知识单元含新增template类型）在两个
    后端上实测「知识检索召回率」硬指标。

    只能放 L2：召回率测量依赖官方 KYSDK 做 query 向量化（`measure_recall.py`
    本身的用法说明也写了这一点），numpy 后端只是省掉向量引擎那一步的检索方式，
    向量化这一步两个后端都逃不掉。
    """
    import json as _json
    from vector_store import create_store

    # 麒麟机上目录名是英文（eval / knowledge），本地开发机是中文（评测集 / 知识检索）
    # ——同一个历史坑：中文目录名经 PowerShell 管道传到远端会被编码损坏，
    # 麒麟机上早就统一改用英文目录名，这里用环境变量覆盖，和
    # RAG_DATASET / RAG_KNOWLEDGE_DIR 的处理方式保持一致。
    EVAL_DIR = os.environ.get("RAG_EVAL_DIR") or os.path.join(RAG, "评测集")
    EVAL_SET = os.path.join(EVAL_DIR, "eval_set.jsonl")
    KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(RAG, "知识检索")
    sys.path.insert(0, KNOWLEDGE_DIR)

    TOP_KS = (1, 3, 5, 10)

    # R2-11：此前 inputs 只登记了 eval_set.jsonl，E-3 校验的语料哈希
    # （cases/workflows/templates.jsonl）没有落进可复现性记录本身——
    # E-3 的修复落在 eval_set_frozen.json 和两个校验脚本里，唯独没落进
    # 这里，导致"这次测量到底用的是哪份语料"这件事，记录本身回答不了。
    _corpus_files = [os.path.join(KNOWLEDGE_DIR, n)
                      for n in ("cases.jsonl", "workflows.jsonl", "templates.jsonl")]
    _inputs = [p for p in [EVAL_SET, *_corpus_files] if os.path.exists(p)]

    with Measurement(
        "M-207", f"知识检索召回率实测（评测集{_eval_set_version()}，双后端对照）", layer="L2",
        inputs=_inputs,
        command="python3 measure_L2.py M-207",
    ) as m:
        if not os.path.exists(EVAL_SET):
            m.note(f"未找到 {EVAL_SET}，跳过")
            return

        # doc_id 是语料行号，语料重新生成后行序一变全部静默失效——v1.0.0
        # 就是这么整批作废的。冻结时已把语料哈希写进 eval_set_frozen.json，
        # 这里核对一致性，不一致直接拒测而不是跑出一份看似正常实则失真的
        # 结果。详见 评测集/review_and_freeze.py 里同名逻辑的注释。
        frozen_meta_path = os.path.join(EVAL_DIR, "eval_set_frozen.json")
        if os.path.exists(frozen_meta_path):
            frozen = _json.load(open(frozen_meta_path, encoding="utf-8"))
            expected = frozen.get("knowledge_base_sha256") or {}
            for name, exp_hash in expected.items():
                with open(os.path.join(KNOWLEDGE_DIR, name), "rb") as f:
                    import hashlib as _hashlib
                    actual = _hashlib.sha256(f.read()).hexdigest()
                if actual != exp_hash:
                    raise SystemExit(
                        f"语料 {name} 已变更（期望 {exp_hash[:16]}…，实际 "
                        f"{actual[:16]}…），评测集 doc_id 可能已失效，拒绝测量。")

        eval_set = [_json.loads(l) for l in open(EVAL_SET, encoding="utf-8") if l.strip()]
        m.set_value("eval_question_count", len(eval_set), unit="题")

        for backend in ("kylin", "numpy"):
            kwargs = ({"collection": "rag_knowledge"} if backend == "kylin"
                      else {"data_dir": os.path.join(KNOWLEDGE_DIR, "knowledge_store_numpy")})
            client = emb_client()
            results = []
            try:
                with create_store(backend, **kwargs) as store:
                    for q in eval_set:
                        t0 = time.perf_counter()
                        qvec = client.encode(q["query"])
                        t_embed = (time.perf_counter() - t0) * 1000

                        t0 = time.perf_counter()
                        hits = store.search(qvec, top_k=max(TOP_KS))
                        t_search = (time.perf_counter() - t0) * 1000

                        hit_ids = [h[0] for h in hits]
                        rank = hit_ids.index(q["doc_id"]) + 1 if q["doc_id"] in hit_ids else None
                        results.append({**q, "rank": rank, "embed_ms": t_embed, "search_ms": t_search})
            finally:
                client.close()

            # 独立评审E-9：此前M-207只测正确性，延迟一个数字都没接入可复现框架，
            # claims.json里也没有对应的延迟主张指向它——延迟数字只活在
            # 评测集/召回率实测报告.md的文字里，没有机器可核对的记录支撑。
            # 端到端P95用逐题embed_ms+search_ms之和排序取分位数，不是两个
            # 独立P95相加（同E-10已修复的问题，这里从一开始就按对的做）。
            for r in results:
                m.add_run(f"{backend}_embed_ms", r["embed_ms"])
                m.add_run(f"{backend}_search_ms", r["search_ms"])
                m.add_run(f"{backend}_e2e_ms", r["embed_ms"] + r["search_ms"])

            def recall_at_k(rs, k):
                hit = sum(1 for r in rs if r["rank"] is not None and r["rank"] <= k)
                return hit / len(rs) if rs else 0.0

            for k in TOP_KS:
                m.set_value(f"recall_at_{k}_{backend}", round(recall_at_k(results, k), 4))

            for diff in ("close", "hard"):
                sub = [r for r in results if r["difficulty"] == diff]
                for k in TOP_KS:
                    m.set_value(f"recall_at_{k}_{diff}_{backend}", round(recall_at_k(sub, k), 4))
                    if backend == "kylin":  # 难度维度的对照不分后端存一份即可
                        m.set_value(f"recall_at_{k}_{diff}", round(recall_at_k(sub, k), 4))

            for ktype in ("case", "workflow"):
                sub = [r for r in results if r["knowledge_type"] == ktype]
                for k in TOP_KS:
                    m.set_value(f"recall_at_{k}_{ktype}_{backend}", round(recall_at_k(sub, k), 4))

            print(f"后端={backend}：" + "  ".join(
                f"R@{k}={recall_at_k(results,k)*100:.1f}%" for k in TOP_KS))

        m.note("recall@1/3 未达85%硬指标，recall@5/10 达标；语义理解证据改用"
               "hard档模型vs纯词面baseline的领先幅度（见M-111/L1，可脱离麒麟环境复核），"
               "而非close/hard落差本身——该论证方式已被独立评审指出有问题，"
               "完整分析见 评测集/召回率实测报告.md")


def m208():
    """独立评审 R-2：此前所有"轻量化"量化都只测了向量库本身（M-205/M-206），
    embedding 一侧的常驻内存占用（此前口头沿用的"339MB模型"）一个数字都
    没有实测过。

    实测发现：向量化模型并不加载进本项目自己的进程，而是常驻在系统级的
    `kytensor` 模型服务里（同时托管ASR/CLIP等十余个与本项目无关的模型，
    是麒麟系统公共基础设施）；本项目的 `emb_serverd` 只是约7.6MB的轻量
    桥接客户端。真实、可归因的端侧内存增量以本次测量为准，不再沿用未经
    验证的"339MB"说法。

    只能放 L2：需要在麒麟机上用 ps 检查系统级守护进程的真实 RSS。
    """
    import re as _re

    def rss_of(pid):
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.strip()
            return int(out) if out else 0
        except Exception:
            return 0

    def total_rss(pattern):
        try:
            out = subprocess.run(["pgrep", "-f", pattern],
                                 capture_output=True, text=True).stdout.strip()
            pids = [int(p) for p in out.splitlines() if p.strip()]
            return sum(rss_of(p) for p in pids)
        except Exception:
            return 0

    with Measurement(
        "M-208", "端侧内存总占用（embedding一侧，独立评审R-2）", layer="L2",
        command="python3 measure_L2.py M-208",
    ) as m:
        kytensor_before = total_rss("^/usr/bin/kytensor ")
        runtime_before = total_rss("^kylin-ai-runtime$")

        with emb_client() as client:
            emb_pid = client._proc.pid
            client.encode("怎么重启网络服务")
            emb_rss = rss_of(emb_pid)
            kytensor_after = total_rss("^/usr/bin/kytensor ")
            runtime_after = total_rss("^kylin-ai-runtime$")

        kytensor_delta = kytensor_after - kytensor_before
        runtime_delta = runtime_after - runtime_before

        m.set_value("kytensor_rss_mb", round(kytensor_before / 1024, 1), unit="MB")
        m.set_value("kylin_ai_runtime_rss_mb", round(runtime_before / 1024, 1), unit="MB")
        m.set_value("emb_serverd_rss_mb", round(emb_rss / 1024, 1), unit="MB")
        m.set_value("kytensor_delta_kb", kytensor_delta, unit="KB")
        m.set_value("kylin_ai_runtime_delta_kb", runtime_delta, unit="KB")
        attributable_mb = (emb_rss + max(0, runtime_delta) + max(0, kytensor_delta)) / 1024
        m.set_value("attributable_increment_mb", round(attributable_mb, 2), unit="MB")

        print(f"kytensor（系统级，与本项目无关的其它模型也在里面）：{kytensor_before/1024:.1f}MB，"
              f"本次调用增量 {kytensor_delta}KB")
        print(f"kylin-ai-runtime：{runtime_before/1024:.1f}MB，本次调用增量 {runtime_delta}KB")
        print(f"emb_serverd（本项目自己的客户端进程）：{emb_rss/1024:.1f}MB")
        print(f"本项目可归因的端侧内存增量：约 {attributable_mb:.1f}MB（不含kytensor常驻的1.4GB，"
              f"那是系统级公共基础设施，无论本项目用不用都会常驻）")

        m.note("稳态测量：kytensor在此之前已因本项目多次调用而常驻目标模型，增量为0正是"
               "这个稳态的体现；未测量系统首次冷加载该模型的一次性代价，如实声明")


def m209():
    """独立评审E-8：239/169题评测集里每一题都有且仅有一个in-corpus正确答案，
    没有「知识库里根本没有答案」的查询——precision/该拒答时是否拒答一概没测过。

    对20条话题与95条知识单元完全无关的问题（out_of_kb_questions.json）与
    20条随机抽样的真实in-corpus问题各取top-1相似度，比较两个分布的范围是否
    重叠——重叠说明没法靠相似度阈值区分「有答案」和「没答案」，不重叠则说明
    存在一个可用的拒答阈值，这本身是个有意义的结论，不预设答案。
    """
    import json as _json
    import random as _random
    from vector_store import create_store

    EVAL_DIR = os.environ.get("RAG_EVAL_DIR") or os.path.join(RAG, "评测集")
    OOK_PATH = os.path.join(EVAL_DIR, "out_of_kb_questions.json")
    IND_PATH = os.path.join(EVAL_DIR, "in_domain_negatives.json")
    EVAL_SET = os.path.join(EVAL_DIR, "eval_set.jsonl")

    with Measurement(
        "M-209", "负样本对照：域外/域内两档 + 拒答判定 precision/recall（E-8 + R3-31）",
        layer="L2", inputs=[OOK_PATH, EVAL_SET] if os.path.exists(OOK_PATH) else [],
        command="python3 measure_L2.py M-209",
    ) as m:
        # 🔴 2026-08-15：这里原本 note 一句就 return，产出一条**0 个指标的
        # 记录**且退出码 0——本轮在麒麟机上因为 RAG_EVAL_DIR 没设就真踩到了：
        # 看起来跑完了，实际什么都没测，而记录文件已经把上一次的结果覆盖掉。
        # 与 R2-11/R4-17 同一类：**证据缺失被当成「跳过检查」而不是「检查失败」**。
        missing = [p for p in (OOK_PATH, EVAL_SET) if not os.path.exists(p)]
        if missing:
            raise SystemExit(
                f"[M-209] 缺少输入文件：{missing}\n"
                f"  麒麟机上评测集目录名是英文，需要显式指定：\n"
                f"    RAG_EVAL_DIR=~/rag/eval python3 measure_L2.py M-209\n"
                f"  不静默跳过——跳过会写出一条 0 指标的记录覆盖掉上一次的真实结果。")

        # 🔴 R3-31：原来只有域外负样本（红烧肉/唐朝/冰岛首都），测的是
        # "embedding 能不能把菜谱和 SSH 排错分开"——太容易，分数拉开
        # 说明不了系统有拒答能力。本轮补上**领域内负样本**：问题仍是
        # Linux 运维/本机工具排障，只是这个库里恰好没做过那件事。
        # 两档都测，域外那档留作对照，用来说明
        # 「负样本容易到什么程度，结论就虚到什么程度」。
        ook = _json.load(open(OOK_PATH, encoding="utf-8"))["questions"]
        ind = []
        if os.path.exists(IND_PATH):
            ind = _json.load(open(IND_PATH, encoding="utf-8"))["questions"]
        eval_set = [_json.loads(l) for l in open(EVAL_SET, encoding="utf-8") if l.strip()]
        _random.seed(42)
        in_kb_sample = _random.sample(eval_set, 20)

        client = emb_client()
        ook_scores, ind_scores, kb_scores = [], [], []
        try:
            with create_store("kylin", collection="rag_knowledge") as store:
                for q in ook:
                    vec = client.encode(q["query"])
                    hits = store.search(vec, top_k=1)
                    if hits:
                        ook_scores.append(hits[0][1])
                for q in ind:
                    vec = client.encode(q["query"])
                    hits = store.search(vec, top_k=1)
                    if hits:
                        ind_scores.append(hits[0][1])
                for q in in_kb_sample:
                    vec = client.encode(q["query"])
                    hits = store.search(vec, top_k=1)
                    if hits:
                        kb_scores.append(hits[0][1])
        finally:
            client.close()

        groups = [("ook", ook_scores), ("kb", kb_scores)]
        if ind_scores:
            groups.append(("ind", ind_scores))
        for name, vals in groups:
            m.set_value(f"{name}_min", round(min(vals), 4))
            m.set_value(f"{name}_max", round(max(vals), 4))
            m.set_value(f"{name}_mean", round(sum(vals) / len(vals), 4))
            m.set_value(f"{name}_n", len(vals))

        overlap = max(ook_scores) >= min(kb_scores)
        m.set_value("ranges_overlap", 1 if overlap else 0)
        print(f"域外负样本 top1 范围 [{min(ook_scores):.4f}, {max(ook_scores):.4f}]")
        print(f"真实问题   top1 范围 [{min(kb_scores):.4f}, {max(kb_scores):.4f}]")
        print(f"域外 vs 真实 两分布重叠：{overlap}")

        def _best_f1(neg, pos):
            """把「top1 相似度 < τ 即拒答」当二分类器，扫 τ 取最佳 F1。
            正类是**该拒答**，所以 precision 的含义是"系统说该拒答时有多少
            真的该拒答"——正是关心的那个数。单答案检索里的 precision@k
            只是 recall@k/k 的恒等变换，报了没有信息量，故不报。"""
            cands = sorted(set(neg + pos))
            best = None
            for i, t in enumerate(cands):
                t = (t + cands[i + 1]) / 2 if i + 1 < len(cands) else t + 1e-6
                tp = sum(1 for v in neg if v < t)
                fp = sum(1 for v in pos if v < t)
                fn = len(neg) - tp
                pr = tp / (tp + fp) if (tp + fp) else 0.0
                rc = tp / (tp + fn) if (tp + fn) else 0.0
                f1 = 2 * pr * rc / (pr + rc) if (pr + rc) else 0.0
                if best is None or f1 > best[3]:
                    best = (t, pr, rc, f1)
            return best

        f1_by_tag = {}
        for tag, neg in (("ook", ook_scores), ("ind", ind_scores)):
            if not neg:
                continue
            t, pr, rc, f1 = _best_f1(neg, kb_scores)
            f1_by_tag[tag] = round(f1, 4)
            m.set_value(f"refusal_{tag}_tau", round(t, 4))
            m.set_value(f"refusal_{tag}_precision", round(pr, 4))
            m.set_value(f"refusal_{tag}_recall", round(rc, 4))
            m.set_value(f"refusal_{tag}_f1", round(f1, 4))
            print(f"拒答判定[{tag}] τ={t:.4f} precision={pr:.4f} "
                  f"recall={rc:.4f} F1={f1:.4f}")

        if ind_scores:
            ind_overlap = max(ind_scores) >= min(kb_scores)
            m.set_value("ind_ranges_overlap", 1 if ind_overlap else 0)
            # ★ 这个差值就是 R3-31 的量化答案：负样本从"域外"换成"域内"，
            #   拒答 F1 掉了多少。掉得越多，说明原来那个结论越是被负样本
            #   的容易程度撑起来的。
            m.set_value("refusal_f1_drop_ood_to_ind",
                        round(f1_by_tag["ook"] - f1_by_tag["ind"], 4))
            print(f"域内负样本 top1 范围 [{min(ind_scores):.4f}, "
                  f"{max(ind_scores):.4f}]，与真实问题重叠：{ind_overlap}")

        m.note("不预设「该拒答」这个能力已经做到——本项测的是两个分布可分到"
               "什么程度，不实现拒答阈值机制本身（那是运行时功能，超出评测范围）。"
               "★ R3-31 的核心发现：域外负样本给出 F1=1.0 的完美假象，换成"
               "领域内负样本后 F1 掉到 0.81——四轮报告里那个「区间不重叠」"
               "是被过于容易的负样本撑起来的。")


def m210():
    """关联检索消融（`关联检索消融-实施方案.md` + `消融修复-执行方案.md` A/B/C/D）：
    `search_knowledge.py` 里元数据过滤/MMR/BM25混合/supersede过滤四项优化
    写出来后从未被任何一次召回率测量用到过（`measure_recall.py` 此前直接调
    `store.search()` 裸检索）。本记录汇总七种配置
    （naive/meta_oracle/meta_prior/mmr/bm25/supersede/all）的
    recall@K + Wilson CI + 与 naive 的逐题 rank 差异，回应"关联检索策略"
    （技术创新性30分点名第三项）与"知识检索召回率≥85%"两条。

    ⚠️ 前置条件：七个 `recall_results_ablation_kylin_{config}.jsonl` 都已
    存在——运行方式见 `评测集/measure_recall.py` 文件头「关联检索消融」
    一节，先跑七次 `--retrieval <config>` 再跑本项。本项自己不重新跑
    检索（那需要七倍的 embedding 调用时间），只汇总+记录。

    （2026-08-27 A 修复把 `meta` 拆成 `meta_oracle`/`meta_prior` 后，
    本函数的 inputs 列表一度仍写死旧的 `meta` 单档，导致 M-210 直接
    SystemExit——阶段二重跑时同步更正为七档。）
    """
    EVAL_DIR = os.environ.get("RAG_EVAL_DIR") or os.path.join(RAG, "评测集")
    sys.path.insert(0, EVAL_DIR)

    ABLATION_CONFIGS = ("naive", "meta_oracle", "meta_prior", "mmr",
                        "bm25", "supersede", "all")

    with Measurement(
        "M-210",
        "关联检索消融：naive/meta_oracle/meta_prior/mmr/bm25/supersede/all 七档对比",
        layer="L2",
        inputs=[os.path.join(EVAL_DIR, f"recall_results_ablation_kylin_{c}.jsonl")
                for c in ABLATION_CONFIGS],
        command="python3 measure_recall.py --compare-ablation（先跑七次 --retrieval）",
    ) as m:
        missing = [p for p in m.inputs if not os.path.exists(p)]
        if missing:
            raise SystemExit(
                f"[M-210] 缺少消融结果文件：{missing}\n"
                f"  先跑七次：cd 评测集 && python3 measure_recall.py --backend kylin "
                f"--retrieval <{'|'.join(ABLATION_CONFIGS)}>\n"
                f"  不静默跳过——跳过会写出一条 0 指标的记录覆盖掉上一次的真实结果。")

        import measure_recall
        summary = measure_recall.compare_ablation(backend="kylin")

        for cfg, s in summary.items():
            m.set_value(f"{cfg}_r1", round(s["r1"], 4))
            m.set_value(f"{cfg}_r5", round(s["r5"], 4))
            m.set_value(f"{cfg}_r10", round(s["r10"], 4))
            m.set_value(f"{cfg}_r10_ci_lo", round(s["r10_ci_lo"], 4))
            m.set_value(f"{cfg}_r10_ci_hi", round(s["r10_ci_hi"], 4))
            if cfg != "naive":
                m.set_value(f"{cfg}_diff_from_naive_n", s["diff_from_naive"])

        naive_r10 = summary["naive"]["r10"]
        all_r10 = summary["all"]["r10"]
        all_ci_lo = summary["all"]["r10_ci_lo"]
        m.set_value("all_lift_r10_pp", round((all_r10 - naive_r10) * 100, 2))
        m.set_value("all_clears_85pct_with_ci", all_ci_lo >= 0.85)

        print(f"naive R@10={naive_r10*100:.1f}%  all R@10={all_r10*100:.1f}%"
              f"（提升 {(all_r10-naive_r10)*100:+.1f}pp）")
        print(f"all 配置 R@10 的 Wilson CI 下界 {all_ci_lo*100:.1f}% "
              f"{'≥' if all_ci_lo >= 0.85 else '<'} 85% 门槛")

        m.note("naive 档必须与 评测集/recall_results_kylin.jsonl（旧报告口径）逐题 "
               "rank 一致——这是本次消融合法性的前提，若不一致说明重构引入了偏差，"
               "应先查明原因，此记录不做这道核对（core check 在跑 --retrieval naive "
               "时由脚本自己打印，请人工确认过再采信本记录其余数字）。")
        m.note("supersede/all 档的 net_gain（扣除语料规模效应后的排序质量真实贡献）"
               "见 评测集/measure_recall.py 的 net_gain_for_supersede()，需要"
               "supersede 配置运行时打印的 n_removed 语料条数手动代入，本记录只存"
               "raw 的 recall 数字。")


ALL = {"M-201": m201, "M-202": m202, "M-205": m205, "M-206": m206, "M-207": m207,
       "M-208": m208, "M-209": m209, "M-210": m210}

if __name__ == "__main__":
    targets = sys.argv[1:] or list(ALL)
    for mid in targets:
        if mid not in ALL:
            print(f"未知编号：{mid}（可选：{', '.join(ALL)}）")
            continue
        ALL[mid]()
        print()
