#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_recall.py —— 用冻结评测集测「知识检索召回率」硬指标（≥85%）

═══ 这是全项目第一次真正测出这个数字 ═══════════════════════════════

此前每一份报告的"已知限制"里都写着「知识检索召回率 ≥85% 目前没有任何测量
数据，不能声称达标」。现在有了冻结的评测集（169题，`eval_set.jsonl`）和
已建好的索引（95条知识单元），可以第一次给出真实数字。

═══ 召回率的定义 ═══════════════════════════════════════════════════

题目要求没有规定 top-K 取多少，本脚本报告 **recall@1 / @3 / @5 / @10**
四档，而不是自选一个对我们最有利的 K 就交差——K 越大召回率必然越高，
只报一个数字容易选择性汇报。

recall@K = 冻结集里，正确答案（doc_id）出现在检索结果前 K 名的题目占比。

═══ 分维度拆解，而不是只报一个总数 ═══════════════════════════════════

同时按 difficulty（close/hard）与 knowledge_type（case/workflow）拆开报告。
⚠️ 第二轮独立复核 R2-15 指出：这里此前写的"hard 明显低于 close 才说明有
语义理解、两档一样高反而可疑"是站不住的推理，与本文件 96-113 行
`LexicalBaseline` 处的正确论述直接打架——已按后者改写：close 档题目本就
是从知识单元里直接抠关键词造出来的（生成规则明文要求"字面重合度较高"），
不含任何语义理解的纯词面基线在 close 档也能拿高分，所以 close 档召回率
高**不代表**系统有语义理解能力，只是"字面召回下限"；真正能证明语义理解
的，是 **hard 档**上 embedding 相对纯词面基线的领先幅度（详见
`LexicalBaseline` 与《召回率实测报告.md》）。两档因此不是"越接近越可疑"
的关系，而是分别检验两件不同的事。

═══ 用法 ═══════════════════════════════════════════════════════════

    python3 measure_recall.py --backend kylin    # 默认后端，--retrieval naive
    python3 measure_recall.py --backend numpy     # 对照

═══ 关联检索消融（`关联检索消融-实施方案.md`）══════════════════════════

`search_knowledge.py` 里的四项优化（元数据过滤/MMR/BM25混合/supersede过滤）
写出来之后**从未被任何一次召回率测量用到过**——本文件此前直接调
`store.search()` 裸检索，完全绕开它们。`--retrieval` 参数把这四项接进
同一套评测流程，跑六种配置（naive/meta/mmr/bm25/supersede/all），每种都
报 recall@K + Wilson CI，用于同时回应"关联检索策略"（技术创新性30分点名
第三项，此前零证据）与"知识检索召回率"（性能指标，CI下界81.3%<85%）两条。

    python3 measure_recall.py --retrieval naive       # 默认，必须与旧报告数字逐位一致
    python3 measure_recall.py --retrieval all         # 四项全开
    python3 measure_recall.py --compare-ablation      # 六个结果文件都跑完后，汇总对比+算net_gain
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
# 本地目录名是"知识检索"，但麒麟机上对应目录是英文名 knowledge（历史遗留：
# 早期传文件时中文目录名经 PowerShell 管道被编码损坏，见《多源数据整合》
# 报告记录的坑，此后麒麟机上统一改用英文目录名）。用环境变量覆盖，
# 和 mine_cases.py 里 RAG_DATASET 的处理方式保持一致。
KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(BASE, "..", "知识检索")
sys.path.insert(0, KNOWLEDGE_DIR)
sys.path.insert(0, os.path.join(RAG, "可复现性体系"))
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))

from measure import wilson_ci  # noqa: E402

EVAL_SET_PATH = os.path.join(BASE, "eval_set.jsonl")
FROZEN_META_PATH = os.path.join(BASE, "eval_set_frozen.json")
TOP_KS = (1, 3, 5, 10)

# ═══ 消融修复-执行方案.md §A：`meta` 已改名 `meta_oracle` ══════════════
# 原 `meta` 用标准答案自身的 knowledge_type 做过滤——等于检索前先被告知
# 答案的类型，是标签泄漏，+4.9pp 全部由此解释，不代表任何可部署的检索
# 能力。改名后只能作为"上界探针"使用，永远不能以"系统召回率"现身。
# `meta_prior` 是零训练的真实策略（质心软先验，见 §D），`all` 用它不用
# oracle——四项优化全开时不能再含有标签泄漏。
RETRIEVAL_CONFIGS = ("naive", "meta_oracle", "meta_prior", "mmr", "bm25", "supersede", "all")

META_ORACLE_CAVEAT = (
    "meta_oracle —— 元数据过滤的【上界】。使用标准答案自身的 knowledge_type\n"
    "              作为过滤条件，因此不代表可部署的检索能力，仅用于回答\n"
    "              \"若能完美预测查询对应的知识类型，最多能提升多少\"。\n"
    "              这一档的数字不得以\"系统召回率\"的名义出现在报告里。"
)


def load_eval_set():
    with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def assert_corpus_matches_frozen():
    """评测集的 doc_id 是语料（cases.jsonl / workflows.jsonl）的行号，
    是相对编号。语料一旦重新生成、行序变化，全部 doc_id 会静默错位，
    而 eval_set.jsonl 自身的 sha256 校验照样通过——因为它只校验题目文件，
    不知道语料变了。

    v1.0.0（241题）就是这么整批作废的：mine_workflows.py 的 set 遍历
    非确定性让 workflows.jsonl 行序在两次运行间不一致，评测集生成时用的
    是一次运行的产物，建索引时用的是另一次运行的产物，doc_id 全部对不上，
    而当时的冻结元信息里没有语料哈希，没有任何机制能在测量前发现这一点。

    因此每次跑召回率测量前，先校验当前语料哈希是否与冻结时记录的一致，
    不符就直接拒跑，而不是跑出一份看似正常、实则全盘失真的结果。

    ═══ 第二轮独立复核 R2-11：这道守卫本身有三处绕过口，已全部堵上 ═══════

    "缺失即失败，不是缺失即放行"——第一轮的教训是"证据消失必须等价于
    核对失败"（reproduce_L1 已照此改了退出码），但这道守卫上原来的三处
    分支全部反过来做了"缺失/不完整 → 静默或降级放行"：

    | 复核员的绕过实验 | 原实现的实际行为 |
    |---|---|
    | 篡改 eval_set.jsonl 本身 | 不拒跑——只校验知识库语料哈希，从未校验题目文件自己的哈希 |
    | 删掉 knowledge_base_sha256 字段 | 打印一行警告后继续跑（降级为警告，不是拒绝） |
    | 整个删掉 eval_set_frozen.json | `if not os.path.exists: return`——静默放行 |

    三处全部改为直接拒跑（`SystemExit`），并新增对 eval_set.jsonl 自身
    sha256（冻结元信息里的顶层 `sha256` 字段）的校验——这是此前从未做过
    的第四类保护，堵住"改题目本身而不改知识库语料"这条此前完全没设防
    的路径。
    """
    if not os.path.exists(FROZEN_META_PATH):
        raise SystemExit(
            f"冻结元信息 {FROZEN_META_PATH} 不存在——无法证明本次测量用的是"
            f"冻结版评测集，拒绝测量。（R2-11修复：此前这里是静默放行）")
    with open(FROZEN_META_PATH, "r", encoding="utf-8") as f:
        frozen = json.load(f)

    # 题目文件自身的哈希——此前完全没有这一层校验，只校验知识库语料。
    # 🔴 第三轮独立复核 R3-12（绕过口④）：上一轮写成 `if frozen_eval_set_
    # sha256:`——**字段不存在就整个跳过这道校验**，删掉 frozen.json 的顶层
    # sha256 字段再随便改题目即可放行，实测绕过成功。这与它上面第 81~96 行
    # 亲手写下的"证据缺失/不完整必须等价于校验失败"是**直接矛盾**的：
    # 原则写进了注释，却没有贯彻到紧接着自己新加的那道校验上。
    frozen_eval_set_sha256 = frozen.get("sha256")
    if not frozen_eval_set_sha256:
        raise SystemExit(
            "冻结元信息缺少 eval_set 的 sha256 字段，无法证明题目文件未被修改，"
            "拒绝测量。（R3-12：此前字段缺失会静默跳过这道校验）")
    with open(EVAL_SET_PATH, "rb") as f:
        actual_eval_set_sha256 = hashlib.sha256(f.read()).hexdigest()
    if actual_eval_set_sha256 != frozen_eval_set_sha256:
        raise SystemExit(
            f"eval_set.jsonl 已被修改（冻结时 sha256={frozen_eval_set_sha256[:16]}…，"
            f"当前 sha256={actual_eval_set_sha256[:16]}…），与冻结记录不符，拒绝测量。"
            f"（R2-11新增：此前只校验知识库语料哈希，题目文件本身改了检测不到）")

    # 🔴 R3-12（绕过口⑥）：`if not expected` 只挡得住**空字典**，挡不住
    # **不完整字典**——把 knowledge_base_sha256 裁剪成只留 cases.jsonl，
    # 再改 workflows.jsonl，照样放行。同样是"缺失即失败"没贯彻到底：
    # 缺一个语料文件的哈希，和一个都没有，在证据意义上是同一回事。
    REQUIRED_CORPUS = {"cases.jsonl", "workflows.jsonl", "templates.jsonl"}
    expected = frozen.get("knowledge_base_sha256") or {}
    missing_corpus = REQUIRED_CORPUS - set(expected)
    if missing_corpus:
        raise SystemExit(
            f"冻结元信息的语料哈希登记不完整（缺 {sorted(missing_corpus)}），"
            f"这些语料的任何改动都将无法被检测，拒绝测量。"
            f"（R3-12：此前只挡空字典，不挡不完整字典）")
    for name, exp_hash in expected.items():
        path = os.path.join(KNOWLEDGE_DIR, name)
        with open(path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        if actual != exp_hash:
            raise SystemExit(
                f"语料 {name} 已变更（期望 {exp_hash[:16]}…，实际 {actual[:16]}…）。\n"
                f"评测集的 doc_id 已可能失效，必须重新生成评测集（generate_eval_"
                f"candidates.py → review_and_freeze.py）后再测量，不能直接拿旧"
                f"评测集测新语料。")


class LexicalBaseline:
    """纯词面对照基线：字符 bigram + TF-IDF 余弦相似度，不含任何模型、
    不调用 embedding。

    ═══ 为什么每次测召回率都要固化跑一遍这个对照 ═══════════════════════

    独立评审 E-1 指出：这不是推测，是实证——用同一份语料建一个完全不含
    embedding 的 baseline，close 档召回率反而比官方 embedding 全流程更高。
    根因在生成规则：close 档明文要求"保留知识单元中的关键词，字面重合度
    较高"，这类题目本质是从被检索文档里抠关键词造出来的，检索到它是必然
    的，**不能证明系统有语义理解能力**。

    把这个基线做成常设对照（而不是一次性分析），每次测召回率都同时报
    "模型挣了多少、词面白捡了多少"，避免总体数字被"送分题"悄悄撑起来
    却没人发现。close 档的召回率因此必须标注为"字面召回下限验证"而非
    语义能力验证，只有 **hard 档**（字面重合度低、baseline 天然做不好）
    embedding 相对 baseline 的领先幅度，才是"系统确有语义理解"的有效证据
    ——这也是 C-232 改用的论据（详见 claims.json 与《召回率实测报告.md》）。
    """

    def __init__(self, doc_ids, texts):
        self.doc_ids = list(doc_ids)
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
    def _bigram_tf(text: str) -> Counter:
        chars = [c for c in text if not c.isspace()]
        bigrams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
        return Counter(bigrams)

    def _to_tfidf(self, tf: Counter) -> dict:
        return {term: freq * self.idf.get(term, 0.0) for term, freq in tf.items()}

    def search(self, query: str, top_k: int):
        q_tf = self._bigram_tf(query)
        q_tfidf = self._to_tfidf(q_tf)
        q_norm = math.sqrt(sum(w * w for w in q_tfidf.values())) or 1.0

        scores = []
        for i, doc_id in enumerate(self.doc_ids):
            d = self.doc_tfidf[i]
            dot = sum(w * d.get(term, 0.0) for term, w in q_tfidf.items())
            sim = dot / (q_norm * self.doc_norm[i])
            scores.append((doc_id, sim))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


# ═════════════════════════════════════════════════════ 关联检索消融

def _cosine(a, b):
    """普通余弦相似度，供 meta_prior 的质心软先验用（§D.2）。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve_ablation(config, query, qvec, store, docs, max_k,
                      gold_type=None, edges=None, bm25=None,
                      mmr_lambda=0.7, hybrid_alpha=0.7,
                      type_centroids=None, prior_beta=0.5):
    """按 `config` 组装检索管线，返回命中的 doc_id 有序列表。

    ⚠️ `naive` 必须是原始的 `store.search(qvec, top_k=max_k)` 一行，不能
    套用下面任何一段处理——这是与旧报告数字逐位复现的前提（验收标准
    第一条），任何"顺手"的重构都可能引入一次归一化、一次排序稳定性差异，
    悄悄改变结果却测不出来。

    与 `search_knowledge.py:search()` 用同一套调用序列与同一批现成函数
    （`BM25`/`_mmr_rerank`/`apply_supersede_filter`/`_minmax_normalize`），
    不重新发明——那个函数本身是 CLI 打印函数不能当库用（见文件头说明），
    这里照抄它内部的组装顺序：元数据 → supersede 过滤 → BM25 混合 →
    MMR 重排 → 截断。

    ⚠️ 刻意不接入 `apply_low_value_penalty()`：那是 workflow 类知识的
    通用降权，不是 `关联检索消融-实施方案.md` §1.1 列出的四个消融轴之一，
    混进任何一档都会让"一个配置只改一个变量"这条消融的基本要求破功。

    ═══ 消融修复-执行方案.md §A/§C：`gold_type` 只有 config=="meta_oracle"
    才允许被读取 ═══════════════════════════════════════════════════════
    这是唯一允许的例外，因为 meta_oracle 就是刻意构造的"上界探针"：直接用
    标准答案自身的类型做硬过滤，不代表任何可部署的检索能力，产物永远不能
    以"系统召回率"的名义出现在报告里。其余六个配置一律不碰 gold_type
    ——`test_detectors.py` 第19项专门守着这条（用例里硬编码只豁免
    "meta_oracle" 这一个名字，不派生自这里的判断逻辑，防止两处同时放宽
    而测不出来）。

    ═══ §D.2：`meta_prior`（含 `all`）用零训练的类型质心软先验 ══════════
    拿查询向量与每个 knowledge_type 的质心向量（该类型全部文档向量的
    均值，只用语料自身的类型标注，不看227题的标签）算余弦相似度作为该
    类型的"软先验"，对分数做 `final = vscore * (1 + β·prior)` 的温和加权，
    不做硬过滤——类型猜错时只是降权（仍可能被召回），比 meta_oracle 的
    硬过滤更稳健。
    """
    if config == "naive":
        hits = store.search(qvec, top_k=max_k)
        return [h[0] for h in hits]

    use_meta_oracle = config == "meta_oracle"
    use_meta_prior = config in ("meta_prior", "all")
    use_bm25 = config in ("bm25", "all")
    use_supersede = config in ("supersede", "all")
    use_mmr = config in ("mmr", "all")

    # 任一重排/过滤开启都需要比 max_k 更大的候选池，否则重排无从谈起
    # （过滤会丢掉不匹配的，MMR/BM25 要有得选才能起作用）——照抄
    # search_knowledge.py:260 的取值方式，不能省。
    fetch_k = max(max_k * 5, 50)
    raw_hits = list(store.search(qvec, top_k=fetch_k))

    if use_meta_oracle and gold_type:
        raw_hits = [(did, s) for did, s in raw_hits
                    if docs.get(did) and docs.get(did)["meta"]["type"] == gold_type]

    if use_meta_prior and type_centroids:
        reweighted = []
        for did, s in raw_hits:
            t = (docs.get(did) or {}).get("meta", {}).get("type")
            prior = _cosine(qvec, type_centroids.get(t)) if t in type_centroids else 0.0
            reweighted.append((did, s * (1 + prior_beta * prior)))
        raw_hits = sorted(reweighted, key=lambda x: -x[1])

    if use_supersede and edges is not None:
        from search_knowledge import apply_supersede_filter
        raw_hits = apply_supersede_filter(raw_hits, docs, edges)

    if use_bm25 and bm25 is not None:
        from search_knowledge import _minmax_normalize
        bm25_scores = bm25.scores(query)
        vscores = [s for _, s in raw_hits]
        bscores = [bm25_scores.get(did, 0.0) for did, _ in raw_hits]
        v_norm = _minmax_normalize(vscores)
        b_norm = _minmax_normalize(bscores)
        combined = [(did, hybrid_alpha * v + (1 - hybrid_alpha) * b)
                    for (did, _), v, b in zip(raw_hits, v_norm, b_norm)]
        raw_hits = sorted(combined, key=lambda x: -x[1])

    if use_mmr:
        from search_knowledge import _mmr_rerank
        texts_by_id = {did: (docs.get(did) or {}).get("text", "") for did, _ in raw_hits}
        hits = _mmr_rerank(raw_hits, texts_by_id, max_k, mmr_lambda)
    else:
        hits = raw_hits[:max_k]

    return [h[0] for h in hits]


def check_gold_superseded(eval_set, docs, edges):
    """§2.2：跑消融前先统计有多少题的标准答案（gold doc_id）本身已被标记
    为 superseded_by——若 >0，这些题在 supersede/all 配置下永远答不对，
    那不是检索变差，是评测集与知识库状态不一致，必须单独列出、不能混进
    总数（否则 supersede 档的召回率会被这类"结构性打不中"的题目拖低，
    误判成排序变差）。
    """
    from search_knowledge import is_superseded_meta
    affected = []
    for q in eval_set:
        meta = (docs.get(q["doc_id"]) or {}).get("meta")
        if meta and is_superseded_meta(meta, edges):
            affected.append(q["question_id"])
    return affected


def net_gain_for_supersede(raw_gain_pp, n_removed):
    """§2.1：过滤掉 superseded 单元 = 候选池变小，语料变小本身就会让召回率
    机械上涨（`measure_corpus_size_effect.py` 已测过这条曲线），排序质量
    之外的这部分涨幅必须扣掉，否则就是把"题目变简单"当成"系统变好"
    （v1.2.0→v1.3.0 犯过的错，见该函数所在报告的教训）。

    用 `slope_per_50_largeend_recall_at_10`（M-114 记录里的"大语料端"局部
    斜率，不是跨全区间 30~86 条的平均斜率）——supersede 只会移除极少数
    单元，评估的是当前语料规模（86条）附近的局部效应，不是整条 30~86
    曲线的平均效应，两者量纲不同，用错斜率会算出偏高的机械涨幅估计。
    """
    m114_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                             "可复现性体系", "records", "M-114.json")
    if not os.path.exists(m114_path):
        return None, None
    with open(m114_path, encoding="utf-8") as f:
        m114 = json.load(f)
    slope = m114["metrics"].get("slope_per_50_largeend_recall_at_10")
    if slope is None:
        return None, None
    slope_val = slope["value"] if isinstance(slope, dict) else slope
    size_effect_pp = slope_val * (n_removed / 50) * 100
    net_gain_pp = raw_gain_pp - size_effect_pp
    return size_effect_pp, net_gain_pp


def compare_ablation(backend="kylin"):
    """§2.3/§六：六个配置都跑完（各自的 `recall_results_ablation_{backend}_
    {config}.jsonl` 已存在）后，汇总成消融表：R@K + Wilson CI +
    与 naive 的逐题 rank 差异题数，并把 supersede/all 的 raw_gain/
    size_effect/net_gain 一并算出来。不重新跑检索，纯读盘统计。
    """
    def recall_hits(rs, k, field="rank"):
        return sum(1 for r in rs if r.get(field) is not None and r[field] <= k)

    loaded = {}
    missing = []
    for cfg in RETRIEVAL_CONFIGS:
        path = os.path.join(BASE, f"recall_results_ablation_{backend}_{cfg}.jsonl")
        if not os.path.exists(path):
            missing.append(cfg)
            continue
        with open(path, encoding="utf-8") as f:
            loaded[cfg] = [json.loads(l) for l in f if l.strip()]
    if missing:
        raise SystemExit(
            f"缺少配置的结果文件，先把六种配置都跑一遍再对比：{missing}\n"
            f"（python measure_recall.py --backend {backend} --retrieval <配置名>）")

    n = len(loaded["naive"])
    print(f"{'配置':<10}{'R@1':<8}{'R@5':<8}{'R@10':<8}{'R@10 95%CI':<20}{'与naive rank差异题数':<20}")
    naive_by_q = {r["question_id"]: r["rank"] for r in loaded["naive"]}
    summary = {}
    for cfg in RETRIEVAL_CONFIGS:
        rs = loaded[cfg]
        r1, r5, r10 = (recall_hits(rs, k) / n for k in (1, 5, 10))
        hits10 = recall_hits(rs, 10)
        lo, hi = wilson_ci(hits10, n)
        diff_n = sum(1 for r in rs if r["rank"] != naive_by_q.get(r["question_id"]))
        print(f"{cfg:<10}{r1*100:<7.1f}{r5*100:<7.1f}{r10*100:<7.1f}"
              f"[{lo*100:.1f}, {hi*100:.1f}]{'':<8}{diff_n if cfg!='naive' else '—':<20}")
        if cfg == "meta_oracle":
            print(f"    {META_ORACLE_CAVEAT}")
        summary[cfg] = {"r1": r1, "r5": r5, "r10": r10, "r10_ci_lo": lo, "r10_ci_hi": hi,
                        "diff_from_naive": diff_n}

    # 六档两两不得全等（每个开关都真的生效）——验收标准第二条
    all_pairs_differ = True
    for i, a in enumerate(RETRIEVAL_CONFIGS):
        for b in RETRIEVAL_CONFIGS[i + 1:]:
            ranks_a = [r["rank"] for r in loaded[a]]
            ranks_b = [r["rank"] for r in loaded[b]]
            if ranks_a == ranks_b:
                all_pairs_differ = False
                print(f"⚠️ 配置 {a} 与 {b} 的逐题 rank 完全相同——其中至少一个是 no-op，先查为什么，不要写进报告")
    if all_pairs_differ:
        print("\n✅ 六种配置两两之间逐题 rank 均不完全相同（没有 no-op 的开关）")

    # supersede/all 的 net_gain（§2.1）——n_removed 从跑 supersede 配置时
    # 写下的旁车元信息文件里读，不是猜的（之前的写法是硬编码 n_removed=0，
    # 会让 size_effect 恒为 0，悄悄算出一个错误但看起来合理的 net_gain，
    # 比不算还糟——见 main() 里写 .meta.json 那段的说明）。
    for cfg in ("supersede", "all"):
        if cfg not in loaded:
            continue
        meta_path = os.path.join(BASE, f"recall_results_ablation_{backend}_{cfg}.meta.json")
        if not os.path.exists(meta_path):
            print(f"\n⚠️ {cfg} 档缺少 {os.path.basename(meta_path)}，"
                  f"无法计算 net_gain（该文件应由跑 --retrieval {cfg} 时自动生成，"
                  f"文件不在多半是用旧版脚本跑的，需要重跑）")
            continue
        with open(meta_path, encoding="utf-8") as f:
            ablation_meta = json.load(f)
        n_removed = ablation_meta["n_superseded_units"]
        raw_gain_pp = (summary[cfg]["r10"] - summary["naive"]["r10"]) * 100
        size_effect_pp, net_gain_pp = net_gain_for_supersede(raw_gain_pp, n_removed)
        print(f"\n{cfg} 档（相对 naive）：raw_gain(R@10) = {raw_gain_pp:+.2f}pp"
              f"，移除了 {n_removed} 条被 superseded 的知识单元")
        if size_effect_pp is not None:
            print(f"  size_effect（语料变小的机械涨幅）= {size_effect_pp:+.2f}pp")
            print(f"  net_gain（扣除语料规模效应后的排序质量真实贡献）"
                  f"= {net_gain_pp:+.2f}pp")
        else:
            print(f"  找不到 M-114 的 slope_per_50_largeend_recall_at_10 记录，"
                  f"无法计算 size_effect——先跑 measure_corpus_size_effect.py")
        if ablation_meta.get("gold_superseded_question_ids"):
            gs = ablation_meta["gold_superseded_question_ids"]
            print(f"  ⚠️ {len(gs)} 道题的标准答案本身已被 superseded（{gs[:5]}...），"
                  f"这些题在本档结构性答不对，报告里需单独归一档，不要混进上面的总数")

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["kylin", "numpy"], default="kylin")
    ap.add_argument("--collection", default="rag_knowledge")
    ap.add_argument("--max-k", type=int, default=10)
    ap.add_argument("--retrieval", choices=RETRIEVAL_CONFIGS, default="naive",
                    help="关联检索消融配置，见文件头「关联检索消融」一节")
    ap.add_argument("--compare-ablation", action="store_true",
                    help="不跑检索，汇总七种配置已保存的结果文件并输出消融表")
    ap.add_argument("--mmr-lambda", type=float, default=0.7)
    # 消融修复-执行方案.md §B.3：归一化修好之后旧默认值0.7的含义变了，已用
    # 固定抽样（tune_hybrid_params.py，227题里每隔8题取1题固定得到29题，
    # 抽样方式与结果写进报告）重新定过一次：候选0.5/0.7/0.9的R@10分别是
    # 93.1%/89.7%/89.7%，0.5明显胜出，已冻结为新默认值，阶段二不再改动。
    ap.add_argument("--hybrid-alpha", type=float, default=0.5)
    # §D.2：质心软先验的权重，同样纪律定过一次——候选0.3/0.5/0.8在同一份
    # 29题固定样本上 R@10 三者打平（均89.7%，这份小样本区分不出差异），
    # 按"打平时取居中值"的规则定为0.5，同样冻结。
    ap.add_argument("--type-prior-beta", type=float, default=0.5)
    args = ap.parse_args()

    if args.compare_ablation:
        compare_ablation(args.backend)
        return

    assert_corpus_matches_frozen()

    from vector_store import create_store, DocStore
    from embedding_client import EmbeddingClient

    eval_set = load_eval_set()
    print(f"评测集：{len(eval_set)} 题，后端：{args.backend}，检索配置：{args.retrieval}")
    if args.retrieval == "meta_oracle":
        print(f"⚠️ {META_ORACLE_CAVEAT}")

    kwargs = ({"collection": args.collection} if args.backend == "kylin"
              else {"data_dir": os.path.join(KNOWLEDGE_DIR, "knowledge_store_numpy")})

    # 纯词面baseline（独立评审E-1）：用同一份语料建一个不含embedding的对照，
    # 每次测召回率都固化跑一遍，不是一次性分析。
    docs = DocStore(os.path.join(KNOWLEDGE_DIR, "knowledge_docs.jsonl"))
    all_doc_ids = sorted(docs._docs.keys())
    all_texts = [docs.get(i)["text"] for i in all_doc_ids]
    baseline = LexicalBaseline(all_doc_ids, all_texts)
    print(f"纯词面baseline已建立（{len(all_doc_ids)}条语料，字符bigram+TF-IDF余弦）")

    # 关联检索消融需要的额外结构，只在对应配置下才建——naive 完全不碰这些，
    # 保证与旧报告数字逐位一致（验收标准第一条）。
    bm25_index = edges = None
    n_superseded_units = 0
    if args.retrieval in ("bm25", "all"):
        from search_knowledge import BM25
        bm25_index = BM25(all_doc_ids, all_texts)
    if args.retrieval in ("supersede", "all"):
        import mine_conflicts
        edges = mine_conflicts.build_supersede_edges()
        from search_knowledge import is_superseded_meta
        n_superseded_units = sum(1 for did in all_doc_ids
                                 if is_superseded_meta(docs.get(did)["meta"], edges))
        print(f"当前索引里被 superseded 标记的知识单元：{n_superseded_units} 条"
              f"（size_effect 计算需要这个数字，见 net_gain_for_supersede）")
        gold_superseded = check_gold_superseded(eval_set, docs, edges)
        if gold_superseded:
            print(f"⚠️ {len(gold_superseded)} 道题的标准答案本身已被 superseded："
                  f"{gold_superseded[:10]}{'...' if len(gold_superseded) > 10 else ''}\n"
                  f"   这些题在本配置下永远答不对，不是检索变差——报告里需单独归一档，"
                  f"不要混进总数（关联检索消融-实施方案.md §2.2）")

    emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
    results = []

    with EmbeddingClient(serverd_path=emb_path) as client, \
         create_store(args.backend, **kwargs) as store:

        # §D.2：meta_prior（含 all）需要的类型质心——查询向量与每个
        # knowledge_type 的质心向量算余弦相似度当软先验。质心只用语料自身
        # 的类型标注（86条语料各编码一次），不看227题的标签，因此不构成
        # 标签泄漏；只在用得到的配置下才算，naive/meta_oracle/mmr/bm25/
        # supersede 都不需要这份额外开销。
        type_centroids = None
        if args.retrieval in ("meta_prior", "all"):
            print("正在计算 meta_prior 的类型质心向量（零训练软先验，§D.2）...")
            vecs_by_type = {}
            for did in all_doc_ids:
                t = docs.get(did)["meta"]["type"]
                vecs_by_type.setdefault(t, []).append(client.encode(docs.get(did)["text"]))
            type_centroids = {
                t: [sum(v[i] for v in vs) / len(vs) for i in range(len(vs[0]))]
                for t, vs in vecs_by_type.items()
            }
            print(f"  质心已就绪：{{{', '.join(f'{t}:{len(v)}条' for t, v in vecs_by_type.items())}}}")

        for i, q in enumerate(eval_set):
            t0 = time.perf_counter()
            qvec = client.encode(q["query"])
            t_embed = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            hit_doc_ids = retrieve_ablation(
                args.retrieval, q["query"], qvec, store, docs, args.max_k,
                gold_type=q.get("knowledge_type"), edges=edges, bm25=bm25_index,
                mmr_lambda=args.mmr_lambda, hybrid_alpha=args.hybrid_alpha,
                type_centroids=type_centroids, prior_beta=args.type_prior_beta)
            t_search = (time.perf_counter() - t0) * 1000

            rank = (hit_doc_ids.index(q["doc_id"]) + 1) if q["doc_id"] in hit_doc_ids else None

            baseline_hits = baseline.search(q["query"], args.max_k)
            baseline_ids = [h[0] for h in baseline_hits]
            baseline_rank = (baseline_ids.index(q["doc_id"]) + 1
                             if q["doc_id"] in baseline_ids else None)

            results.append({
                "question_id": q["question_id"],
                "doc_id": q["doc_id"],
                "difficulty": q["difficulty"],
                "knowledge_type": q["knowledge_type"],
                "query": q["query"],
                "rank": rank,   # None 表示 top-K 内未命中
                "baseline_rank": baseline_rank,  # 纯词面baseline的排名，同上
                # 独立评审 E-5 指出：此前只存 rank，未命中题目诊断不出"差一点"
                # 还是"完全不沾边"——见《召回率实测报告.md》第五节里 doc_id=22
                # 那次真实排第13名、只是差一点没进top-10的发现过程，如果当时
                # 没存top-K命中列表根本定位不到，只能看到"未命中"三个字。
                "top_k_hit_ids": hit_doc_ids,
                "embed_ms": round(t_embed, 2),
                "search_ms": round(t_search, 2),
            })
            if (i + 1) % 30 == 0 or i + 1 == len(eval_set):
                print(f"  {i+1}/{len(eval_set)}")

    # ---- 汇总 ----
    def recall_at_k(rs, k, field="rank"):
        hit = sum(1 for r in rs if r[field] is not None and r[field] <= k)
        return hit / len(rs) if rs else 0.0

    print(f"\n{'='*60}")
    print(f"总体召回率（{len(results)} 题，后端={args.backend}）——模型 vs 纯词面baseline")
    print(f"{'='*60}")
    for k in TOP_KS:
        r = recall_at_k(results, k)
        b = recall_at_k(results, k, "baseline_rank")
        mark = "✅" if r >= 0.85 else "❌"
        print(f"  recall@{k:<2}  模型={r*100:5.1f}%  {mark}   baseline={b*100:5.1f}%"
              f"   （模型对照硬指标≥85%；baseline仅供参照，不是本项目的指标）")

    for dim, key in (("难度", "difficulty"), ("知识类型", "knowledge_type")):
        print(f"\n按{dim}拆分（模型 / baseline）：")
        groups = {}
        for r in results:
            groups.setdefault(r[key], []).append(r)
        for g, rs in sorted(groups.items()):
            model_str = "  ".join(f"R@{k}={recall_at_k(rs,k)*100:.1f}%" for k in TOP_KS)
            base_str = "  ".join(f"R@{k}={recall_at_k(rs,k,'baseline_rank')*100:.1f}%" for k in TOP_KS)
            print(f"  {g:<10} n={len(rs):<4} 模型: {model_str}")
            print(f"  {'':<10} {'':<4} base: {base_str}")

    # ── E-2：close/hard 落差本身不能证明语义理解（baseline在这项上落差更大），
    # 真正有效的证据是 hard 档模型相对 baseline 的领先幅度。见 LexicalBaseline
    # 的文档字符串与 claims.json 的 C-232。
    hard_rs = [r for r in results if r["difficulty"] == "hard"]
    if hard_rs:
        hard_model_r1 = recall_at_k(hard_rs, 1)
        hard_base_r1 = recall_at_k(hard_rs, 1, "baseline_rank")
        print(f"\n★ hard档 R@1：模型={hard_model_r1*100:.1f}% vs baseline="
              f"{hard_base_r1*100:.1f}%"
              f"（模型领先{(hard_model_r1-hard_base_r1)*100:+.1f}pp——"
              f"这才是「系统确有语义理解」的有效证据，而不是close/hard落差本身，"
              f"因为纯词面baseline在close/hard之间的落差通常更大，"
              f"不能靠这个落差区分「真理解」和「纯关键词匹配」两种假设）")

    embed_times = [r["embed_ms"] for r in results]
    search_times = [r["search_ms"] for r in results]
    # 独立评审 E-10 指出：p95(A)+p95(B) 严格来说不等于 p95(A+B)（同一题的
    # 向量化耗时和检索耗时不一定同时落在各自的P95分位上）。改成对逐题的
    # 端到端耗时（同一题的embed_ms+search_ms）直接排序取分位，口径正确。
    e2e_times = sorted(r["embed_ms"] + r["search_ms"] for r in results)
    embed_times_sorted = sorted(embed_times)
    search_times_sorted = sorted(search_times)
    p95_embed = embed_times_sorted[int(len(embed_times_sorted) * 0.95)]
    p95_search = search_times_sorted[int(len(search_times_sorted) * 0.95)]
    p95_e2e = e2e_times[int(len(e2e_times) * 0.95)]
    print(f"\n延迟：向量化 P95={p95_embed:.1f}ms，检索 P95={p95_search:.1f}ms，"
          f"端到端 P95={p95_e2e:.1f}ms（对照 ≤500ms，逐题端到端耗时直接排序取"
          f"分位，不是两个独立P95相加）")

    未命中 = [r for r in results if r["rank"] is None]
    print(f"\ntop-{args.max_k} 内完全未命中：{len(未命中)} 题")
    for r in 未命中[:10]:
        print(f"  [{r['difficulty']}] {r['question_id']} doc_id={r['doc_id']}: {r['query']}")

    # ⚠️ 消融结果一律写到新文件名，不覆盖既有的 recall_results_{backend}.jsonl
    # ——那份文件是 measure_recall_ci.py（M-115）与 claims.json 里 C-255 等
    # 主张的输入，被覆盖会让"naive 逐位复现旧数字"这条验收标准反而失去
    # 对照基准。`--retrieval naive` 也走这个新文件名，用 compare_ablation()
    # 里单独打印的数字去对照旧报告，不依赖文件名相同。
    out_path = os.path.join(BASE, f"recall_results_ablation_{args.backend}_{args.retrieval}.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n逐题结果已写入：{out_path}")
    if args.retrieval == "naive":
        print(f"请与 recall_results_{args.backend}.jsonl（旧报告口径）逐题核对 rank 是否一致"
              f"——这是消融实验合法性的前提（验收标准第一条）。")

    # supersede/all 档把 n_superseded_units 存进旁车元信息文件，供
    # compare_ablation() 精确计算 net_gain——不能让它去猜这个数字
    # （之前的写法是硬编码 n_removed=0，会让 size_effect 恒为 0，
    # 悄悄算出一个错误但看似合理的 net_gain，比不算还糟）。
    if args.retrieval in ("supersede", "all"):
        meta_path = os.path.join(BASE, f"recall_results_ablation_{args.backend}_{args.retrieval}.meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"n_superseded_units": n_superseded_units,
                      "gold_superseded_question_ids": gold_superseded},
                     f, ensure_ascii=False, indent=2)
        print(f"消融元信息（n_superseded_units 等）已写入：{meta_path}")


if __name__ == "__main__":
    main()
