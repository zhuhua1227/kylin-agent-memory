#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_L1.py —— 所有「L1 层」测量的实际执行者

═══ L1 是什么意思 ═══════════════════════════════════════════════

本项目的数字分两层：

  · **L1 —— 纯 Python，任何机器都能跑**，不需要银河麒麟、不需要 KYSDK。
    覆盖：数据集统计、知识可挖掘量、脱敏规则、暴力检索对比、
          numpy 后端性能与内存、以及**检索结果正确性**。

  · L2 —— 需要麒麟 V11 + KYSDK 环境。
    覆盖：768 维验证、embedding 延迟与吞吐、官方向量引擎的一切。

**为什么要费劲分这一层**：如果所有数字都只能在我们那台特定的麒麟虚机上复现，
那对读者来说和"没法验"区别不大。分层之后，任何人拿一台普通电脑就能验证一大半数字，
其中包括最关键的检索正确性——这才让"可复现"从口号变成能当场做的事。

**让 L1 能验证检索正确性的关键做法**：把官方 KYSDK 生成的向量**固化导出**随
交付物提供。这样读者不需要麒麟环境也能重算相似度、复现出与报告一致的排序与
分数。当然，他们无法验证这些向量本身确实出自官方 SDK——那部分只能由 L2 覆盖，
这个边界必须说清楚，不能含糊。

固化向量分两批，用途不同：
  · `vectors/toy_docs.tsv` + `toy_query.tsv` —— 端到端最小管线阶段的 5 篇
    玩具语料（M-106，快速demo，C-050~C-052 的历史数字出自这批）
  · `vectors/docs.tsv` + `eval_queries.tsv` —— P-6 新增，真实 95 条知识单元
    + 冻结评测集 169 题的完整固化向量（M-111，可脱离麒麟环境复算召回率，
    数字应与 L2 的 M-207 逐位一致，见 vectors/fixed_vectors_manifest.json）

用法：
    python measure_L1.py            # 跑全部
    python measure_L1.py M-104      # 只跑指定的一项
"""

import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)

sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(RAG, "多源数据整合"))
sys.path.insert(0, os.path.join(RAG, "知识检索"))

from measure import Measurement, rss_mb  # noqa: E402

DATASET = os.path.join(RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")
VECTORS_DIR = os.path.join(BASE, "vectors")

# 随机测量统一用这个种子，保证任何人重跑得到同一批随机向量
SEED = 42
BENCH_COUNT = 20000     # 与报告中的规模一致
DIM = 768


# ═════════════════════════════════════════════════════ 公共小工具

# R4-14 / R4-04：「缺失即失败」与「断言即抛异常」的统一入口，见 strictness.py
from strictness import require, optional, assert_equal, MissingInput  # noqa: E402


# 版本号从冻结记录读，不写死（2026-08-14：整链重跑时四处硬编码的
# "v1.3.0" 改一处漏三处，记录标题会出现「内容是新的、标签是旧的」）
def _eval_set_version(_rel='../评测集/eval_set_frozen.json'):
    """从 evaluation 冻结记录读版本号——唯一来源，避免多处硬编码分叉。"""
    import json as _j, os as _o
    p = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), _rel)
    try:
        with open(p, encoding="utf-8") as f:
            return "v" + _j.load(f)["version"]
    except Exception:
        return "(版本未知)"


def load_dataset():
    recs = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return recs


def load_tsv_vectors(path):
    """读 `<原文>\\t<768个float>` 格式。返回 [(文本, 向量)]。"""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            text, _, nums = line.partition("\t")
            vec = [float(x) for x in nums.split()]
            out.append((text, vec))
    return out


def make_random_vectors(n, dim, seed):
    """生成固定种子的随机单位向量。归一化是为了让余弦相似度落在正常范围。"""
    import numpy as np
    rng = np.random.default_rng(seed)
    m = rng.standard_normal((n, dim), dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m


# ═════════════════════════════════════════════════════ M-101 数据集统计

def m101():
    with Measurement(
        "M-101", "多源数据集构成与时间戳归一化覆盖", layer="L1",
        inputs=[DATASET],
        command="python measure_L1.py M-101",
    ) as m:
        recs = load_dataset()
        m.set_value("total_records", len(recs), unit="条")

        by_type = Counter(r.get("record_type") for r in recs)
        for t, n in by_type.items():
            m.set_value(f"count_{t}", n, unit="条")

        by_source = Counter(r.get("source") for r in recs)
        for s, n in by_source.items():
            m.set_value(f"source_{s}", n, unit="条")

        by_ts = Counter(r.get("timestamp_source") for r in recs)
        for k, n in by_ts.items():
            m.set_value(f"timestamp_source_{k}", n, unit="条")

        # 时间跨度。归一化成 ISO8601 后字符串字典序即时间序，可直接取 min/max
        stamps = [r["timestamp"] for r in recs
                  if r.get("timestamp") and r.get("timestamp_source") != "unknown"]
        m.set_value("time_span_start", min(stamps))
        m.set_value("time_span_end", max(stamps))

        tools = [r for r in recs if r.get("record_type") == "tool_execution"]
        errs = sum(1 for r in tools if r.get("is_error"))
        m.set_value("tool_success", len(tools) - errs, unit="条")
        m.set_value("tool_error", errs, unit="条")

        # 2026-08-04 新增：分记录类型单独看时间跨度。
        #
        # 起因：报告里曾笼统写"数据集跨度约10周"，那是把四类记录混在一起算的，
        # 由 user_input（从 05-26 起）拉长。做 B2 冷启动模拟时才发现
        # tool_execution 实际起点是 07-03，真实跨度只有约 4.4 周——
        # 差了一倍还多。任何要按 tool_execution 做时间切分的模块
        # （不只 B2，也包括任务E若用工具调用做时间维度分析）都应参考这里，
        # 不能直接照抄聚合统计的"10周"。
        span_by_type = {}
        for rtype in ("tool_execution", "user_input"):
            type_stamps = [r["timestamp"] for r in recs
                          if r.get("record_type") == rtype
                          and r.get("timestamp") and r.get("timestamp_source") != "unknown"]
            if type_stamps:
                span_by_type[rtype] = (min(type_stamps), max(type_stamps))
                m.set_value(f"{rtype}_time_span_start", min(type_stamps))
                m.set_value(f"{rtype}_time_span_end", max(type_stamps))

        print(f"总记录 {len(recs)}；类型分布 {dict(by_type)}")
        print(f"来源分布 {dict(by_source)}")
        print(f"时间戳来源 {dict(by_ts)}")
        print(f"时间跨度（全部类型混合）{min(stamps)} → {max(stamps)}")
        for rtype, (s, e) in span_by_type.items():
            print(f"  {rtype} 时间跨度：{s} → {e}")
        print(f"工具调用成功 {len(tools) - errs} / 出错 {errs}")

        if by_ts.get("unknown"):
            m.note(f"有 {by_ts['unknown']} 条时间戳无法识别，未计入时间跨度")

        # 数据代表性（独立评审D-6要求量化披露：来源集中度、会话集中度、
        # 麒麟相关关键词占比）——只在 tool_execution 上算，样本量最大、
        # 是后续挖掘/检索/评测唯一使用的记录类型。
        te_source = Counter(r.get("source") for r in tools)
        for s, n in te_source.items():
            m.set_value(f"te_source_ratio_{s}", round(n / len(tools), 4))

        sess_counter = Counter(r.get("session_id") for r in tools)
        top_sessions = sess_counter.most_common(5)
        top1_ratio = top_sessions[0][1] / len(tools) if top_sessions else 0
        top5_ratio = sum(n for _, n in top_sessions) / len(tools) if top_sessions else 0
        m.set_value("te_top1_session_ratio", round(top1_ratio, 4))
        m.set_value("te_top5_session_ratio", round(top5_ratio, 4))
        m.set_value("te_distinct_session_count", len(sess_counter), unit="个")

        keywords = ("麒麟", "kylin", "OS Agent", "磐石")
        kw_hits = Counter()
        for r in tools:
            blob = (json.dumps(r.get("tool_input", {}), ensure_ascii=False)
                    + " " + str(r.get("tool_output", ""))).lower()
            for kw in keywords:
                if kw.lower() in blob:
                    kw_hits[kw] += 1
        for kw in keywords:
            key = "te_keyword_ratio_" + kw.replace(" ", "_")
            m.set_value(key, round(kw_hits[kw] / len(tools), 4))

        print(f"数据代表性：来源{dict(te_source)}，最大会话占比{top1_ratio*100:.1f}%，"
              f"前5会话占比{top5_ratio*100:.1f}%，麒麟关键词占比{kw_hits['麒麟']/len(tools)*100:.2f}%")


# ═════════════════════════════════════════════════════ M-102 知识可挖掘量

def m102():
    """量出三类知识单元各能挖多少。

    这组数字是 B2「知识检索」设计的直接依据——正是它推翻了
    「一条工具调用记录 = 一个知识单元」的原方案：单条记录是**事件**不是知识，
    知识产生于事件之间的关系（失败→修复、意图→序列）。
    """
    with Measurement(
        "M-102", "三类知识单元的可挖掘量", layer="L1",
        inputs=[DATASET],
        command="python measure_L1.py M-102",
    ) as m:
        recs = load_dataset()
        tools = [r for r in recs if r.get("record_type") == "tool_execution"]
        inputs_ = [r for r in recs if r.get("record_type") == "user_input"]

        by_sess = defaultdict(list)
        for r in tools:
            by_sess[r.get("session_id")].append(r)
        for rs in by_sess.values():
            rs.sort(key=lambda r: r.get("timestamp") or "")

        # ---- 历史案例：失败 → 修复 ----
        # 判定规则（刻意用简单启发式，规则本身要写进报告让人能评判其合理性）：
        # 一条 is_error 之后，同一会话内 5 步以内出现同一工具的成功调用。
        err_total = 0
        pairs = 0
        for rs in by_sess.values():
            for i, r in enumerate(rs):
                if not r.get("is_error"):
                    continue
                err_total += 1
                for nxt in rs[i + 1:i + 6]:
                    if nxt.get("tool_name") == r.get("tool_name") and not nxt.get("is_error"):
                        pairs += 1
                        break
        m.set_value("error_records", err_total, unit="条")
        m.set_value("case_pairs", pairs, unit="对")
        m.set_value("case_pair_ratio_pct", round(pairs / err_total * 100, 2) if err_total else 0,
                    unit="%")

        # ---- 工作流程：用户意图 ↔ 工具调用序列 ----
        ui_by_sess = defaultdict(list)
        for r in inputs_:
            ui_by_sess[r.get("session_id")].append(r)
        shared = set(by_sess) & set(ui_by_sess)
        linkable_intents = sum(len(ui_by_sess[s]) for s in shared)
        covered_calls = sum(len(by_sess[s]) for s in shared)

        m.set_value("sessions_tool", len(by_sess), unit="个")
        m.set_value("sessions_input", len(ui_by_sess), unit="个")
        m.set_value("sessions_shared", len(shared), unit="个")
        m.set_value("linkable_intents", linkable_intents, unit="条")
        m.set_value("covered_tool_calls", covered_calls, unit="条")
        m.set_value("coverage_pct", round(covered_calls / len(tools) * 100, 2), unit="%")

        print(f"出错记录 {err_total}，其中 {pairs} 条可配成「失败→修复」案例 "
              f"({pairs / err_total * 100:.1f}%)")
        print(f"会话交集 {len(shared)}（工具侧 {len(by_sess)} / 输入侧 {len(ui_by_sess)}）")
        print(f"可关联用户意图 {linkable_intents} 条，覆盖工具调用 {covered_calls} 条 "
              f"({covered_calls / len(tools) * 100:.1f}%)")

        m.note("案例判定用的是简单启发式（同工具、同会话、5步内），"
               "命中率偏低有优化空间；规则写在代码里，可自行调整复算")


# ═════════════════════════════════════════════════════ M-103 脱敏规则

def m103():
    """验证「展平操作绕过递归脱敏」这个真实漏洞确实已修复。

    这是本项目踩过的坑：把嵌套配置展平成 {"config_path":"apiKey","config_value":"sk-xxx"}
    之后，原字段名降级成普通字符串值，承载密钥的字段改叫 config_value 不含敏感词，
    脱敏模块就漏过去了。修复方式是拿**原始路径**重新判定。
    """
    from extract_manual_config import redact_if_sensitive

    with Measurement(
        "M-103", "脱敏规则：展平后敏感路径仍被打码", layer="L1",
        command="python measure_L1.py M-103",
    ) as m:
        # (路径, 原值, 是否应被打码)
        cases = [
            ("model", "opus", False),
            ("theme", "dark", False),
            ("permissions.allow[0]", "PowerShell(Get-NetIPConfiguration *)", False),
            ("apiKey", "sk-real-secret-123", True),
            # 下面这条是首版真实泄漏过的嵌套路径，必须能被打码
            ("memorySearch.remote.apiKey", "sk-nested-secret", True),
            ("auth.token", "tok_abc", True),
            ("db.password", "hunter2", True),
        ]
        passed = 0
        for path, value, should_redact in cases:
            got = redact_if_sensitive(path, value)
            redacted = (got != value)
            ok = (redacted == should_redact)
            passed += ok
            print(f"  {'✅' if ok else '❌'} {path:<34} -> {got}")

        m.set_value("cases_total", len(cases), unit="条")
        m.set_value("cases_passed", passed, unit="条")
        if passed != len(cases):
            m.note("有用例未通过，脱敏规则存在回归，需立即修复")


# ═════════════════════════════════════════════════════ M-104 暴力检索对比

def m104():
    """回答「2万条规模下暴力检索还够不够用」。

    三种实现算法完全等价，差别只在实现方式——
    这组数字说明性能问题往往不在算法复杂度，而在实现里的重复劳动。
    """
    import math
    import numpy as np

    with Measurement(
        "M-104", f"暴力检索三种实现对比（{BENCH_COUNT} 条 × {DIM} 维）", layer="L1",
        seed=SEED, command="python measure_L1.py M-104",
    ) as m:
        matrix = make_random_vectors(BENCH_COUNT, DIM, SEED)
        query_np = make_random_vectors(1, DIM, SEED + 1)[0]

        store = matrix.tolist()          # 纯 Python 版要用 list
        query = query_np.tolist()

        # ---- 实现①：阶段01 原样（每次比较都把两个向量各归一化一遍）----
        def norm(v):
            n = math.sqrt(sum(x * x for x in v))
            return v if n == 0 else [x / n for x in v]

        def dot(a, b):
            t = 0.0
            for i in range(len(a)):
                t += a[i] * b[i]
            return t

        for _ in range(3):               # 这个实现很慢，跑 3 次够看趋势
            t0 = time.perf_counter()
            scored = [(dot(norm(query), norm(v)), i) for i, v in enumerate(store)]
            scored.sort(reverse=True)
            scored[:3]
            m.add_run("stage01_naive_ms", (time.perf_counter() - t0) * 1000, unit="ms")

        # ---- 实现②：预归一化 + 纯 Python（只做点积）----
        store_n = [norm(v) for v in store]
        qn = norm(query)
        for _ in range(3):
            t0 = time.perf_counter()
            scored = [(dot(qn, v), i) for i, v in enumerate(store_n)]
            scored.sort(reverse=True)
            scored[:3]
            m.add_run("prenorm_python_ms", (time.perf_counter() - t0) * 1000, unit="ms")

        # ---- 实现③：预归一化 + numpy（一次矩阵乘法）----
        qn_np = np.asarray(qn, dtype=np.float32)
        for _ in range(10):
            t0 = time.perf_counter()
            scores = matrix @ qn_np
            idx = np.argpartition(-scores, 3)[:3]
            idx[np.argsort(-scores[idx])]
            m.add_run("prenorm_numpy_ms", (time.perf_counter() - t0) * 1000, unit="ms")

        m.note("三种实现算法等价，①与②的差距全部来自"
               "「每次比较都重新归一化查询向量」这一处重复劳动")


# ═════════════════════════════════════════════════════ M-105 numpy 后端

def m105():
    """numpy 存储后端的性能与**内存占用**。

    内存这一项对应评分标准里点名要的「适配端侧部署的轻量化程度，需提供量化论证数据」。
    """
    from vector_store import NumpyStore
    import tempfile
    import shutil

    with Measurement(
        "M-105", f"numpy 存储后端：性能与资源占用（{BENCH_COUNT} 条）", layer="L1",
        seed=SEED, command="python measure_L1.py M-105",
    ) as m:
        matrix = make_random_vectors(BENCH_COUNT, DIM, SEED)
        vectors = matrix.tolist()
        queries = make_random_vectors(20, DIM, SEED + 1).tolist()

        data_dir = tempfile.mkdtemp(prefix="rag_m105_")
        try:
            rss_before = rss_mb()

            t0 = time.perf_counter()
            store = NumpyStore(data_dir=data_dir)
            m.set_value("cold_start_ms", round((time.perf_counter() - t0) * 1000, 3), unit="ms")

            store.create(dim=DIM)
            t0 = time.perf_counter()
            for i in range(0, len(vectors), 1000):
                store.add([(j, vectors[j]) for j in range(i, min(i + 1000, len(vectors)))])
            store.save()
            m.set_value("build_index_sec", round(time.perf_counter() - t0, 3), unit="秒")
            m.set_value("indexed_count", store.count(), unit="条")

            for q in queries:
                t0 = time.perf_counter()
                store.search(q, top_k=10)
                m.add_run("search_ms", (time.perf_counter() - t0) * 1000, unit="ms")

            rss_after = rss_mb()
            if rss_before is not None and rss_after is not None:
                m.set_value("memory_delta_mb", round(rss_after - rss_before, 1), unit="MB")
            else:
                m.note("本平台无法测量常驻内存，memory_delta_mb 缺失")

            disk = sum(os.path.getsize(os.path.join(data_dir, f))
                       for f in os.listdir(data_dir))
            m.set_value("disk_mb", round(disk / (1024 * 1024), 1), unit="MB")

            store.close()
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

        m.note("内存为本进程 RSS 增量；原始向量数据本身约 "
               f"{BENCH_COUNT * DIM * 4 / (1024*1024):.1f}MB，可据此判断额外开销大小")


# ═════════════════════════════════════════════════════ M-106 检索正确性

def m106():
    """★关键★ 用官方 SDK 固化下来的向量，复现语义检索结果。

    这条让**没有麒麟环境的读者也能验证检索是否真的按语义工作**——
    向量是官方 KYSDK 产出后固化保存的，读者重算相似度即可得到与报告一致的排序与分数。

    ⚠️ 边界要说清楚：这一条能验证的是「给定这批向量，检索逻辑正确」，
    **不能**验证「这批向量确实出自官方 SDK」——后者属于 L2，
    由麒麟机上的 M-201/M-206 覆盖。
    """
    import math

    # 用最早那批 5 篇玩具语料的固化向量（toy_docs.tsv/toy_query.tsv）——这是
    # 端到端最小管线阶段的历史产出，C-050~C-052 的数字就是从这批向量算出来的。
    # 2026-08-05 起，vectors/docs.tsv 已改指向 P-6 新固化的真实 95 条知识单元
    # 语料（供 M-111 做全量召回率复算），两批语料用途不同、不能共用同一份
    # 文件——这里显式切到 toy_ 前缀，避免 M-106 的历史数字被真实语料悄悄替换。
    docs_path = os.path.join(VECTORS_DIR, "toy_docs.tsv")
    query_path = os.path.join(VECTORS_DIR, "toy_query.tsv")

    with Measurement(
        "M-106", "语义检索正确性（使用官方 SDK 固化向量，端到端最小管线玩具语料）",
        layer="L1",
        inputs=[docs_path, query_path],
        command="python measure_L1.py M-106",
    ) as m:
        docs = load_tsv_vectors(docs_path)
        queries = load_tsv_vectors(query_path)

        m.set_value("doc_count", len(docs), unit="条")
        m.set_value("vector_dim", len(docs[0][1]), unit="维")

        def norm(v):
            n = math.sqrt(sum(x * x for x in v))
            return v if n == 0 else [x / n for x in v]

        for qtext, qvec in queries:
            qn = norm(qvec)
            scored = []
            for text, vec in docs:
                vn = norm(vec)
                scored.append((sum(a * b for a, b in zip(qn, vn)), text))
            scored.sort(reverse=True)

            print(f"查询：{qtext}")
            for rank, (score, text) in enumerate(scored[:3], 1):
                print(f"  第{rank}名  相似度={score:.4f}   原文：{text}")
                m.set_value(f"top{rank}_score", round(score, 4))
                m.set_value(f"top{rank}_text", text)

        m.note("向量由官方 KYSDK embedding 生成后固化保存；本项只验证检索逻辑，"
               "向量来源的真实性由 L2 的麒麟环境测量覆盖")


# ═════════════════════════════════════════════════════ M-111 召回率（L1，固化向量）

class _LexicalBaseline:
    """纯词面对照基线：字符 bigram + TF-IDF 余弦相似度。

    与 评测集/measure_recall.py 的同名类逻辑完全一致——那份代码跑在麒麟机上，
    依赖 KYSDK 的 embedding_client 才能 import，本文件无法直接复用，这里保留
    一份独立实现。

    ⚠️ 这里原本还写着"两份独立代码在两台不同机器上算出同一个 baseline 数字，
    这件事本身也是一次隐含的交叉验证"——**这句话是假的，已删除**。
    第三轮独立复核 R3-14 实测：两边算出的根本不是同一个数
    （L1 = 0.4911 / L2 = 0.4852，close 档 0.8049 vs 0.7927），根因是本文件
    喂给 baseline 的是 `docs.tsv` 里**带转义的文本**（`\\n` 两个字符），
    而 measure_recall.py 喂的是真实渲染文本（真正的换行符）——95 条里
    86 条文本不同。两个不同的输入算出两个不同的数，却被当成"独立实现互相
    印证"写进了代码注释，是本项目最不该出现的那类表述。
    已改为两边喂同一份真实渲染文本，并在下方加了**显式断言**：不一致就
    当场失败，而不是继续声称它们互为交叉验证。
    """

    def __init__(self, texts):
        self.doc_vecs = [self._bigram_tf(t) for t in texts]
        df = Counter()
        for vec in self.doc_vecs:
            for term in vec:
                df[term] += 1
        n = len(self.doc_vecs)
        self.idf = {term: math.log(1 + n / c) for term, c in df.items()}
        self.doc_tfidf = [self._to_tfidf(v) for v in self.doc_vecs]
        self.doc_norm = [math.sqrt(sum(w * w for w in d.values())) or 1.0
                          for d in self.doc_tfidf]

    @staticmethod
    def _bigram_tf(text):
        chars = [c for c in text if not c.isspace()]
        bigrams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
        return Counter(bigrams)

    def _to_tfidf(self, tf):
        return {term: freq * self.idf.get(term, 0.0) for term, freq in tf.items()}

    def rank_of(self, query, target_idx, top_k):
        q_tf = self._bigram_tf(query)
        q_tfidf = self._to_tfidf(q_tf)
        q_norm = math.sqrt(sum(w * w for w in q_tfidf.values())) or 1.0
        scores = []
        for i, d in enumerate(self.doc_tfidf):
            dot = sum(w * d.get(term, 0.0) for term, w in q_tfidf.items())
            sim = dot / (q_norm * self.doc_norm[i])
            scores.append((i, sim))
        scores.sort(key=lambda x: -x[1])
        ranked_ids = [i for i, _ in scores[:top_k]]
        return (ranked_ids.index(target_idx) + 1) if target_idx in ranked_ids else None


def _load_rendered_corpus_texts():
    """从三个语料文件重新渲染出与建索引/L2 完全同源的文本（R3-14）。

    复用 知识检索/render_units.py 里的唯一实现——R2-17 把 render_* 抽成
    共享模块正是为了杜绝"同一约定多处各写一份"，这里当然也要 import 它，
    而不是再抄第四份。
    """
    try:
        from render_units import render_case, render_workflow, render_template
    except ImportError:
        return None
    kdir = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(RAG, "知识检索")
    texts = []
    for fn, renderer in (("cases.jsonl", render_case),
                         ("workflows.jsonl", render_workflow),
                         ("templates.jsonl", render_template)):
        p = os.path.join(kdir, fn)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    texts.append(renderer(json.loads(line)))
    return texts


def load_eval_queries_tsv(path):
    """读 vectors/eval_queries.tsv：
    question_id\\tdoc_id\\tknowledge_type\\tdifficulty\\tquery_text\\tvector"""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            qid, doc_id, ktype, diff, qtext, nums = line.split("\t", 5)
            vec = [float(x) for x in nums.split()]
            out.append({
                "question_id": qid, "doc_id": int(doc_id),
                "knowledge_type": ktype, "difficulty": diff,
                "query": qtext, "vector": vec,
            })
    return out


def m111():
    """★P-6★ 用固化的官方 SDK 向量，在不需要麒麟环境的机器上完整复算 v1.3.0
    评测集（169题）的召回率，并同时算出纯词面baseline作对照。

    与 M-106 的区别：M-106 只抽 5 条demo展示检索确实按语义工作；本项对全部
    169 题逐一计算 rank，与 L2 的 M-207（麒麟机上用 KYSDK 实时embedding+两个
    后端实时检索得到）走的是两条独立路径，理应得到同一个数字——分别验证了
    「检索逻辑在这批向量上正确」和「这批向量确实来自官方SDK的实时管线」，
    合起来才是完整链条，任何一段单独都不够，边界必须说清楚：

    ⚠️ 本项能验证「给定这批向量，召回率确实是这个数」，**不能**验证「向量
    本身出自官方SDK」——那部分只能由 L2 的 M-201/M-206 覆盖。
    """
    docs_path = os.path.join(VECTORS_DIR, "docs.tsv")
    queries_path = os.path.join(VECTORS_DIR, "eval_queries.tsv")
    manifest_path = os.path.join(VECTORS_DIR, "fixed_vectors_manifest.json")

    with Measurement(
        "M-111", f"知识检索召回率（L1，固化向量复算，评测集{_eval_set_version()}）", layer="L1",
        inputs=[docs_path, queries_path],
        command="python measure_L1.py M-111",
    ) as m:
        # 🔴 第四轮 R4-14：这一段原本有四处「缺了就跳过、照常报绿」。
        # 固化向量是 M-111 的全部输入，缺了这条测量什么都没测；
        # 而旧写法 `m.note("跳过"); return` 会让记录以 succeeded=True 落盘。
        require(docs_path, "M-111 的召回率复算没有任何输入可算",
                hint="固化向量随交付物提供；只想核对已有记录用 "
                     "python reproduce_L1.py --check-only")
        require(queries_path, "M-111 无法知道要检索哪 169 道题")

        # 固化时刻的哈希记在 manifest 里；这里核对，防止 docs.tsv/eval_queries.tsv
        # 被后续某次挖掘/评测集改版悄悄替换掉之后这条测量还在用旧向量算出新版本
        # 的结论——同样的坑 评测集/measure_recall.py 的 assert_corpus_matches_frozen()
        # 已经踩过一次并修了（E-3），这里是同一防线搬到 L1。
        # R4-14：manifest 缺失 = 这道哈希防线整个失效，必须是失败不是跳过。
        require(manifest_path,
                "固化向量的哈希核对整条失效——向量被换掉也不会有人发现")
        if os.path.exists(manifest_path):
            import hashlib
            manifest = json.load(open(manifest_path, encoding="utf-8"))
            for key, path in (("docs_tsv", docs_path), ("eval_queries_tsv", queries_path)):
                expect = manifest[key]["sha256"]
                actual = hashlib.sha256(open(path, "rb").read()).hexdigest()
                if actual != expect:
                    raise SystemExit(
                        f"{path} 已变更（期望 {expect[:16]}…，实际 {actual[:16]}…），"
                        f"固化向量与 manifest 不一致，拒绝测量。")

        docs = load_tsv_vectors(docs_path)   # [(text, vector)]，doc_id = 行号
        queries = load_eval_queries_tsv(queries_path)
        m.set_value("doc_count", len(docs), unit="条")
        m.set_value("query_count", len(queries), unit="题")

        # 🔴 R3-14：此前是 `[t for t, _ in docs]`，即直接吃 docs.tsv 第一列
        # ——那是**为了塞进单行 TSV 而做过转义**的文本（换行变成字面的 \n
        # 两个字符）。measure_recall.py 那边吃的是真实渲染文本，两边输入
        # 不同、baseline 数字自然不同，却被注释说成"隐含交叉验证"。
        # 改为从语料文件重新渲染，与 L2 完全同源；docs.tsv 只用来取向量。
        doc_texts = _load_rendered_corpus_texts()
        # 🔴 R4-14：旧写法是"重建不出来就退回 docs.tsv 的转义文本 + 记一条
        # note"。问题在于**退回的那份文本正是 R3-14 判定为错误来源的那一份**
        # ——退回之后算出的 baseline 与 L2 不可比，而下面那条 L1/L2 一致性
        # 断言恰恰要拿它去比。第四轮实测就是靠"让语料读取失效"触发这条退路，
        # 然后整条流程绿灯退出 0。所以这里不能退，只能失败。
        if doc_texts is None or len(doc_texts) != len(docs):
            raise MissingInput(
                f"无法从语料文件重建渲染文本（得到 "
                f"{0 if doc_texts is None else len(doc_texts)} 条，需 {len(docs)} 条）。\n"
                f"  旧版本会退回使用 docs.tsv 里的**转义**文本继续算，但那正是\n"
                f"  R3-14 判定为错误来源的那一份——算出来的 baseline 与 L2 不可比，\n"
                f"  而下面的 L1/L2 一致性断言又要拿它去比，等于自己骗自己。\n"
                f"  语料文件在 知识检索/{{cases,workflows,templates}}.jsonl。")

        def _norm(v):
            n = math.sqrt(sum(x * x for x in v))
            return v if n == 0 else [x / n for x in v]
        doc_vecs_n = [_norm(v) for _, v in docs]

        baseline = _LexicalBaseline(doc_texts)

        TOP_KS = (1, 3, 5, 10)
        max_k = max(TOP_KS)
        results = []
        for q in queries:
            qn = _norm(q["vector"])
            scored = sorted(
                range(len(doc_vecs_n)),
                key=lambda i: -sum(a * b for a, b in zip(qn, doc_vecs_n[i]))
            )[:max_k]
            rank = (scored.index(q["doc_id"]) + 1) if q["doc_id"] in scored else None
            baseline_rank = baseline.rank_of(q["query"], q["doc_id"], max_k)
            results.append({**q, "rank": rank, "baseline_rank": baseline_rank})

        def recall_at_k(rs, k, field="rank"):
            hit = sum(1 for r in rs if r[field] is not None and r[field] <= k)
            return hit / len(rs) if rs else 0.0

        for k in TOP_KS:
            m.set_value(f"recall_at_{k}", round(recall_at_k(results, k), 4))
            m.set_value(f"baseline_recall_at_{k}",
                        round(recall_at_k(results, k, "baseline_rank"), 4))

        # 🔴 2026-08-15：这里原本写死 ("close", "hard") 两档。v1.4.0 第一次
        # 真正出齐了 paraphrase 档（77 题，占三分之一），而分档指标**一个都
        # 没算到它**——与 review_and_freeze.py 里写死 by_difficulty 是同一个
        # 毛病：把"当前数据碰巧只有这两类"固化成了代码里的常量。
        # 改成从数据里取，新增难度档自动进入指标。
        difficulties = sorted({r["difficulty"] for r in results})
        m.set_value("difficulty_levels", ",".join(difficulties))
        for diff in difficulties:
            sub = [r for r in results if r["difficulty"] == diff]
            m.set_value(f"n_{diff}", len(sub))
            for k in TOP_KS:
                m.set_value(f"recall_at_{k}_{diff}", round(recall_at_k(sub, k), 4))
                m.set_value(f"baseline_recall_at_{k}_{diff}",
                            round(recall_at_k(sub, k, "baseline_rank"), 4))

        # 🔴 第三轮独立复核 R3-31 的另一半：**precision / MRR / nDCG 一概没有**，
        # 四轮只报 recall@K。recall@K 只回答"命中没有"，不回答"排第几"——
        # 一个把正确答案稳定放在第 8 名的系统与放在第 2 名的系统，recall@10
        # 完全一样。MRR 与 nDCG 恰恰对名次敏感，是这个信息的补位。
        #
        # 本评测集每题**有且仅有一个**正确知识单元，因此：
        #   · MRR = mean(1/rank)，未命中记 0
        #   · nDCG@k：单一相关文档时 IDCG=1，故 nDCG@k = 1/log2(rank+1)
        #   · precision@k = recall@k / k（单答案下二者只差一个常数，
        #     **单独报它没有信息量**，所以不报——补一个恒等变换的指标
        #     只会让指标表变长，不会让结论更可信）
        def mrr(rs, field="rank"):
            return sum(1.0 / r[field] for r in rs
                       if r[field] is not None) / len(rs) if rs else 0.0

        def ndcg_at_k(rs, k, field="rank"):
            tot = sum(1.0 / math.log2(r[field] + 1) for r in rs
                      if r[field] is not None and r[field] <= k)
            return tot / len(rs) if rs else 0.0

        m.set_value("mrr", round(mrr(results), 4))
        m.set_value("baseline_mrr", round(mrr(results, "baseline_rank"), 4))
        for k in TOP_KS:
            m.set_value(f"ndcg_at_{k}", round(ndcg_at_k(results, k), 4))
            m.set_value(f"baseline_ndcg_at_{k}",
                        round(ndcg_at_k(results, k, "baseline_rank"), 4))
        for diff in difficulties:
            sub = [r for r in results if r["difficulty"] == diff]
            m.set_value(f"mrr_{diff}", round(mrr(sub), 4))
            m.set_value(f"baseline_mrr_{diff}", round(mrr(sub, "baseline_rank"), 4))
            m.set_value(f"ndcg_at_10_{diff}", round(ndcg_at_k(sub, 10), 4))

        hard = [r for r in results if r["difficulty"] == "hard"]
        hard_model_r1 = recall_at_k(hard, 1)
        hard_baseline_r1 = recall_at_k(hard, 1, "baseline_rank")
        m.set_value("hard_r1_model_minus_baseline_gap",
                     round(hard_model_r1 - hard_baseline_r1, 4))

        print(f"L1固化向量复算：R@1={recall_at_k(results,1)*100:.1f}%  "
              f"R@10={recall_at_k(results,10)*100:.1f}%"
              "（对照L2的M-207应逐位一致，因为固化向量就是M-207用的同一批embedding）")
        print(f"hard档 R@1：模型={hard_model_r1*100:.1f}% vs baseline="
              f"{hard_baseline_r1*100:.1f}%（领先 +{(hard_model_r1-hard_baseline_r1)*100:.1f}pp）")

        # ★★ R3-14：把"两边算出同一个 baseline"从**注释里的声称**变成
        # **代码里的断言**。此前那句"隐含交叉验证"描述的事实是假的（两边
        # 输入文本源不同，L1=0.4911 / L2=0.4852），而没有任何机制会发现。
        # 现在：真有 L2 的逐题结果时就逐档比对，对不上直接判失败。
        # 🔴 第四轮独立复核 R4-04：上面那段注释写着"对不上直接判失败"，
        # 而实现只调了 m.note() + print()——**不抛异常、不改退出码、记录
        # 照常 succeeded=True 落盘**。复核实测把语料读取搞坏后，
        # L1=0.4911 / L2=0.4852 明明对不上，整条流程依然绿灯退出 0。
        # 这比 R3-14 的原问题更坏：原来是注释在吹牛，改完变成"已修复"在吹牛。
        # 现在改用 strictness.assert_equal()，不等就抛，谁也 note 不过去。
        l2_results_path = os.path.join(RAG, "评测集", "recall_results_kylin.jsonl")
        l2_path = optional(
            l2_results_path,
            why="L2 逐题结果产自麒麟机，第三方拿到的交付物里可能没有它",
            impact="L1 与 L2 的词面 baseline 是否逐位一致（这条交叉验证本次未做）",
            record=m)
        if l2_path:
            l2 = [json.loads(x) for x in open(l2_path, encoding="utf-8") if x.strip()]
            l2_b1 = (sum(1 for r in l2 if r.get("baseline_rank") is not None
                         and r["baseline_rank"] <= 1) / len(l2)) if l2 else None
            l1_b1 = recall_at_k(results, 1, "baseline_rank")
            if l2_b1 is None:
                raise MissingInput(
                    f"{l2_path} 读到 0 条记录——L1/L2 交叉验证无从做起。"
                    f"文件在但是空的，比文件不在更可疑，不按可选缺失处理。")
            m.set_value("baseline_l1_vs_l2_r1_gap", round(abs(l1_b1 - l2_b1), 6))
            assert_equal(
                l1_b1, l2_b1, tolerance=1e-9, record=m,
                what="L1 与 L2 的词面 baseline R@1（同一份语料、同一批查询、"
                     "同一套算法，本应逐位相同；不一致只可能来自输入文本源不同"
                     "——历史根因是 L1 读了 docs.tsv 的转义文本，见 R3-14——"
                     "或 L2 的逐题结果产自旧代码尚未在麒麟机重跑）")
            print(f"L1/L2 词面 baseline R@1 逐位一致（{l1_b1:.4f}）——"
                  f"这才是真正的交叉验证")

        m.note("固化向量来自官方KYSDK的一次真实编码（见 vectors/fixed_vectors_manifest.json），"
               "本项只在这批向量上重算余弦排序与baseline，不重新调用KYSDK；"
               "数字应与 L2 的 M-207 逐位一致——这正是该测量存在的意义：任何人不需要"
               "麒麟环境，也能核对「检索排序确实是这样算出来的」")


# ═════════════════════════════════════════════════════ M-112 宽松召回率（E-6）

def m112():
    """独立评审 E-6：语料存在近重复，单一 gold 判定会把"模型排到了语义等价的
    另一条"计成未命中，低估真实检索质量。

    扫描语料内部近重复对（字符bigram相似度≥0.70），对评测结果重算宽松召回率：
    若 gold 的近重复搭档进了 top-K，也算命中。两套数字都报，不用宽松数字
    替换严格数字——宽松口径只是诊断信息，硬指标对照仍用严格口径。
    """
    EVAL_DIR = os.path.join(RAG, "评测集")
    dup_path = os.path.join(EVAL_DIR, "corpus_duplicates.json")
    results_path = os.path.join(EVAL_DIR, "recall_results_kylin.jsonl")

    with Measurement(
        "M-112", "宽松召回率（近重复语料multi-gold诊断，独立评审E-6）", layer="L1",
        inputs=[dup_path, results_path] if os.path.exists(dup_path) else [],
        command="python scan_corpus_duplicates.py && python compute_lenient_recall.py",
    ) as m:
        if not (os.path.exists(dup_path) and os.path.exists(results_path)):
            m.note(f"未找到 {dup_path} 或 {results_path}，跳过")
            return

        dup = json.load(open(dup_path, encoding="utf-8"))
        m.set_value("dup_pair_count", dup["pair_count"], unit="对")
        m.set_value("dup_involved_ratio", dup["involved_ratio"])

        partners = {}
        for p in dup["pairs"]:
            partners.setdefault(p["a"], set()).add(p["b"])
            partners.setdefault(p["b"], set()).add(p["a"])

        results = [json.loads(l) for l in open(results_path, encoding="utf-8") if l.strip()]
        for r in results:
            best = r.get("rank")
            hit_ids = r.get("top_k_hit_ids") or []
            for cand_id in partners.get(r["doc_id"], ()):
                if cand_id in hit_ids:
                    cand_rank = hit_ids.index(cand_id) + 1
                    if best is None or cand_rank < best:
                        best = cand_rank
            r["lenient_rank"] = best

        def recall_at_k(rs, k, field):
            hit = sum(1 for r in rs if r.get(field) is not None and r[field] <= k)
            return hit / len(rs) if rs else 0.0

        for k in (1, 3, 5, 10):
            m.set_value(f"recall_at_{k}_strict", round(recall_at_k(results, k, "rank"), 4))
            m.set_value(f"recall_at_{k}_lenient", round(recall_at_k(results, k, "lenient_rank"), 4))

        changed = sum(1 for r in results if r.get("lenient_rank") != r.get("rank"))
        m.set_value("changed_question_count", changed, unit="题")

        print(f"近重复对：{dup['pair_count']}（涉及{dup['involved_ratio']*100:.1f}%语料）；"
              f"{changed}题排名因宽松判定改善")
        m.note("宽松口径只是诊断信息，硬指标对照统一用严格口径（第三节），不互相替代")


# ═════════════════════════════════════════════════════ M-113 脱敏召回率/精确率（D-8/D-9）

def m113():
    """独立评审D-8/D-9：全项目唯一的脱敏测试此前是7条手工用例（M-103），
    且只测了个别正则、从未测过召回率（真正的敏感内容有多少被打了码）、
    精确率、误伤率——"人工看了几十条"不构成证据，这三个数字才是。

    用标注测试集（22条应打码的positive+16条不该打码的negative，见同目录
    多源数据整合/labeled_redaction_testset.json）跑 _scrub_string()，
    计算真实的召回率/精确率/误伤率。
    """
    DATA_DIR = os.path.join(RAG, "多源数据整合")
    sys.path.insert(0, DATA_DIR)
    from anonymize import _scrub_string

    testset_path = os.path.join(DATA_DIR, "labeled_redaction_testset.json")

    with Measurement(
        "M-113", "脱敏能力：召回率/精确率/误伤率实测（独立评审D-8/D-9）", layer="L1",
        inputs=[testset_path],
        command="python measure_L1.py M-113",
    ) as m:
        data = json.load(open(testset_path, encoding="utf-8"))

        tp = fn = tn = fp = 0
        fn_ids, fp_ids = [], []
        for case in data["positive"]:
            if _scrub_string(case["text"]) != case["text"]:
                tp += 1
            else:
                fn += 1
                fn_ids.append(case["id"])
        for case in data["negative"]:
            if _scrub_string(case["text"]) == case["text"]:
                tn += 1
            else:
                fp += 1
                fp_ids.append(case["id"])

        recall = tp / (tp + fn) if (tp + fn) else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0

        m.set_value("positive_count", len(data["positive"]), unit="条")
        m.set_value("negative_count", len(data["negative"]), unit="条")
        m.set_value("recall", round(recall, 4))
        m.set_value("precision", round(precision, 4))
        m.set_value("false_positive_rate", round(fpr, 4))

        print(f"召回率 {tp}/{tp+fn}={recall*100:.1f}%  精确率 {tp}/{tp+fp}={precision*100:.1f}%  "
              f"误伤率 {fp}/{fp+tn}={fpr*100:.1f}%")
        if fn_ids:
            print(f"漏检：{fn_ids}")
        if fp_ids:
            print(f"误伤：{fp_ids}")

        m.note("标注集规模（22正+16负）小于理想的100~200条，是时间投入下的合理取舍，"
               "但已经是从0到有：此前连一次真实测量都没做过")


# ═════════════════════════════════════════════════════ 入口

ALL = {
    "M-101": m101, "M-102": m102, "M-103": m103,
    "M-104": m104, "M-105": m105, "M-106": m106,
    "M-111": m111, "M-112": m112, "M-113": m113,
}

def check_preconditions():
    """跑本模块的测量之前需要哪些文件在位；缺了就返回一句人话，齐了返回 None。

    R2-28：孤立目录场景（只有 可复现性体系/ 这一个文件夹，没有旁边的
    多源数据整合/ 等其他模块目录）此前是裸 FileNotFoundError + 完整
    调用栈——对着一个陌生项目第一次跑复现脚本就看到这个，体验很差，
    也看不出"缺文件"和"代码有bug"的区别。

    🔴 第三轮独立复核 R3-31：这道检查 R2-28 时**只写在了 `__main__` 里**，
    而复核员真正会跑的是 `reproduce_L1.py`——它 `import measure_L1` 之后
    直接调 `ALL[mid]()`，绕开 `__main__`，于是孤立文件夹下完整重跑仍然是
    裸 traceback，三轮未动。这就是"修实例没修类"的又一例。
    改法：把检查抽成模块级函数，两条入口都过同一道；`reproduce_L1.py`
    在跑任何模块的 ALL 之前先调它（模块没有这个函数就视为无前置依赖）。
    """
    if not os.path.exists(DATASET):
        return (f"找不到数据集：{DATASET}\n"
                "  本模块（measure_L1.py）需要完整的项目目录结构"
                "（含 多源数据整合/dataset/），请在完整项目根目录下运行。\n"
                "  如果你手上只有 可复现性体系/ 这一个文件夹（比如只想看"
                "已有的测量记录、不需要重新生成），请改用：\n"
                "    python reproduce_L1.py --check-only")
    return None


if __name__ == "__main__":
    _msg = check_preconditions()
    if _msg:
        print(_msg)
        sys.exit(1)

    targets = sys.argv[1:] or list(ALL)
    for mid in targets:
        if mid not in ALL:
            print(f"未知编号：{mid}（可选：{', '.join(ALL)}）")
            continue
        ALL[mid]()
        print()
