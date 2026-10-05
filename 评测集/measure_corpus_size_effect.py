#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_corpus_size_effect.py —— 语料规模对召回率的机械影响（R2-02 对照实验）

═══ 为什么需要这个脚本 ═══════════════════════════════════════════════

v1.2.0（152条语料/239题）→ v1.3.0（95条语料/169题）迁移时，报告把
"recall@10 90.0%→91.7%、recall@5 84.5%→87.0%（新达标）"写成了系统变好了，
只用一句"数字不可直接比较行号/doc_id，但可以比较整体趋势"轻轻带过。

第二轮独立复核指出这句话是错的：候选池（语料）小了三分之一，召回率
**不需要检索质量有任何变化就会自动升高**——错误答案变少了，正确答案
排到前面的概率机械性地上升。"整体趋势"恰恰是最不能跨语料规模比较的
东西，比绝对数字更容易骗人。

本脚本把这个混淆效应**独立测出来**：固定检索系统不变（同一批固化向量、
同一套暴力余弦排序），只改变候选池大小，看召回率单纯因为语料变小
能自动涨多少。测出这条曲线之后，才能回答"v1.3.0 那 1.7pp/0.9pp 的
涨幅，到底是语料变小的假象，还是真实的检索质量提升"。

═══ 方法 ═══════════════════════════════════════════════════════════

对每个候选语料规模 N，独立重复 30 次：
  对每一道题，从 95 条语料里抽出 N 条构成候选池——**gold 文档必须始终在
  池内**（否则测的是"抽样有没有抽漏"而不是"池子小了排序会不会自动变好"，
  两回事），另外 N-1 条从其余 94 条里随机抽。在这个 N 条候选池上重新
  排序、算 recall@k，30 次试验取均值。

N=95（不作任何子采样，即全量语料）应与 M-111/M-207 的真实召回率数字
逐位吻合——这是脚本本身正确性的内部校验，不吻合说明实现有 bug。

═══ 用法 ═══════════════════════════════════════════════════════════

    python measure_corpus_size_effect.py
"""

import json
import math
import os
import random
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
RECOMP_DIR = os.path.join(RAG, "可复现性体系")
sys.path.insert(0, RECOMP_DIR)

from measure import Measurement  # noqa: E402
from measure_L1 import load_tsv_vectors, load_eval_queries_tsv  # noqa: E402

VECTORS_DIR = os.path.join(RECOMP_DIR, "vectors")
DOCS_PATH = os.path.join(VECTORS_DIR, "docs.tsv")
QUERIES_PATH = os.path.join(VECTORS_DIR, "eval_queries.tsv")

SEED = 42
TRIALS = 30
TOP_KS = (1, 3, 5, 10)
# 与独立复核报告表格用同一组规模，方便逐行对照复核员的数字
# 采样点按**当前真实语料条数**生成，不再写死 95——写死会在语料变化后
# 静默产出「采样点比语料还多」的无效行（2026-08-14 语料 95→86 时踩到）。
def _corpus_sizes(n, step=10, min_gap=5):
    """按当前语料条数生成采样点：从 n 起每隔 step 递减，直到 25。

    `min_gap`：相邻两点至少相差这么多。第一版直接用固定表
    `[n] + [85,75,…]`，语料 86 条时生成了 `(86, 85, 75, …)` —— 头两点只差
    1 条，大端三点几乎重合，拿它们拟合斜率毫无意义（断言当场拦下了，
    这也说明第三轮加的那条大端断言是有用的）。现在按步长生成，
    并丢掉与前一点靠得太近的点。
    """
    pts = [n]
    x = (n // step) * step
    while x >= 25:
        if pts[-1] - x >= min_gap:
            pts.append(x)
        x -= step
    return tuple(pts)

# v1.2.0 已发布的历史实测值（152条语料/239题，测量脚本与固化向量当时未保留，
# 这里只是引用既成事实做对照计算，不是本脚本重新测出来的）
V1_2_0 = {"corpus_size": 152, "question_count": 239,
          "recall_at_1": 0.636, "recall_at_5": 0.845, "recall_at_10": 0.900}


def _load_current_from_m111():
    """当前版本的数字不手抄——直接读 M-111 记录里的精确值，避免手抄引入的
    舍入误差（报告正文写的"87.0%"是四舍五入到1位小数展示用的，记录里的
    精确值是0.8698，两者一致但精度不同，这里要用精确值做减法）。

    🔴 2026-08-14：`corpus_size` 原来**硬编码成 95**，而召回率是从 M-111
    动态读的。整链重跑到 v1.4.0（语料 86 条）之后，这个函数返回的就是
    「86 条语料的召回率」配「95」这个规模标签——版本标签与内容脱节，
    与 review_and_freeze 里那个写死的 VERSION 是同一个病。
    改为从冻结记录读真实语料条数，一处来源。
    """
    path = os.path.join(RECOMP_DIR, "records", "M-111.json")
    frozen = os.path.join(BASE, "eval_set_frozen.json")
    size = None
    if os.path.exists(frozen):
        try:
            with open(frozen, encoding="utf-8") as f:
                kb = json.load(f).get("knowledge_base_lines") or {}
            size = sum(kb.values()) or None
        except (OSError, ValueError):
            size = None
    if not os.path.exists(path):
        raise SystemExit(
            "缺少 records/M-111.json，无法取得当前召回率——"
            "本实验的全部结论都建立在它之上，缺失即失败，不做降级猜测。")
    rec = json.load(open(path, encoding="utf-8"))
    m = rec["metrics"]

    def val(k):
        v = m[k]
        return v["value"] if isinstance(v, dict) else v
    if size is None:
        size = int(val("doc_count")) if "doc_count" in m else None
    if size is None:
        raise SystemExit("无法确定当前语料条数（冻结记录与 M-111 都读不到）")
    return {
        "corpus_size": size,
        "question_count": int(val("query_count")) if "query_count" in m else None,
        "recall_at_1": val("recall_at_1"),
        "recall_at_5": val("recall_at_5"),
        "recall_at_10": val("recall_at_10"),
    }


CURRENT = _load_current_from_m111()
V1_3_0 = CURRENT      # 兼容旧引用名


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return v if n == 0 else [x / n for x in v]


def recall_at_k(ranks, k):
    hit = sum(1 for r in ranks if r is not None and r <= k)
    return hit / len(ranks) if ranks else 0.0


def run_one_trial(docs_vecs_n, queries, corpus_size, rng):
    """对每道题独立子采样一个大小为 corpus_size 的候选池（gold 必逃不掉，
    始终在池内），在池内重新排序，返回这次试验里每道题的 gold 排名。"""
    n_docs = len(docs_vecs_n)
    ranks = {k: [] for k in TOP_KS}
    for q in queries:
        gold = q["doc_id"]
        others = [i for i in range(n_docs) if i != gold]
        pool_size = min(corpus_size, n_docs)
        if pool_size <= 1:
            pool = [gold]
        else:
            pool = [gold] + rng.sample(others, pool_size - 1)
        qn = _norm(q["vector"])
        scored = sorted(pool, key=lambda i: -sum(a * b for a, b in zip(qn, docs_vecs_n[i])))
        rank = scored.index(gold) + 1  # gold 必在池内，必有名次
        for k in TOP_KS:
            ranks[k].append(rank if rank <= max(TOP_KS) else None)
    return {k: recall_at_k(ranks[k], k) for k in TOP_KS}


def m114():
    """🔴 第三轮独立复核 R3-15：本测量声明 layer="L1"（纯 Python、只吃固化
    向量、不调 embedding），却**没有接进 `reproduce_L1.py` 的一键重跑**，
    产出的 3 条主张因此被结论区归进"只能在真实麒麟环境上产生"那一档。
    抽成模块级函数并导出 ALL，与 measure_L1/measure_B2 同一约定。
    """
    if not (os.path.exists(DOCS_PATH) and os.path.exists(QUERIES_PATH)):
        print(f"缺少固化向量 {DOCS_PATH} / {QUERIES_PATH}，无法运行。")
        return 2

    docs = load_tsv_vectors(DOCS_PATH)
    queries = load_eval_queries_tsv(QUERIES_PATH)
    docs_vecs_n = [_norm(v) for _, v in docs]
    n_docs = len(docs)

    print("=" * 78)
    print("语料规模对召回率的机械影响 —— R2-02 对照实验")
    print("=" * 78)
    print(f"固化语料 {n_docs} 条 / 冻结评测集 {len(queries)} 题")
    print(f"每个规模独立重复 {TRIALS} 次取均值，随机种子从 {SEED} 派生\n")

    with Measurement(
        "M-114", "语料规模对召回率的机械影响（子采样对照实验，R2-02）", layer="L1",
        inputs=[DOCS_PATH, QUERIES_PATH],
        command="python measure_corpus_size_effect.py",
    ) as m:
        rows = []
        sizes = _corpus_sizes(n_docs)
        for size in sizes:
            master_rng = random.Random(f"{SEED}-{size}")
            trial_results = []
            for t in range(TRIALS):
                trial_seed = master_rng.randrange(2**32)
                trial_rng = random.Random(trial_seed)
                trial_results.append(run_one_trial(docs_vecs_n, queries, size, trial_rng))
            avg = {k: sum(tr[k] for tr in trial_results) / TRIALS for k in TOP_KS}
            rows.append((size, avg))
            for k in TOP_KS:
                m.set_value(f"corpus_{size}_recall_at_{k}", round(avg[k], 4))
            print(f"语料={size:>3}条  " + "  ".join(
                f"R@{k}={avg[k]*100:5.2f}%" for k in TOP_KS))

        print()
        print(f"（N={n_docs} 一行不作子采样，理论上应与 M-111/M-207 的真实召回率")
        print(" 逐位吻合，是本脚本实现正确性的内部校验，不吻合说明本脚本有 bug）")
        full = dict(rows)[n_docs]
        print(f"  N={n_docs} 本脚本算出：R@1={full[1]*100:.2f}%  "
              f"R@5={full[5]*100:.2f}%  R@10={full[10]*100:.2f}%")
        print(f"  M-111/M-207 实测：R@1={V1_3_0['recall_at_1']*100:.2f}%  "
              f"R@5={V1_3_0['recall_at_5']*100:.2f}%  R@10={V1_3_0['recall_at_10']*100:.2f}%")

        # 用相邻两个规模点的斜率，估计"每减少50条语料"的机械涨幅
        # （线性最小二乘拟合 recall~corpus_size，斜率换算成"每50条"的量纲）
        #
        # 🔴 第三轮独立复核 R3-39：原来只拟合**一条**直线，而且把 N=25 也纳入。
        # 问题在于召回率-池子大小的真实关系是凹的（池子越小，每再拿掉一条能
        # 带来的涨幅越大），把远端的 N=25 纳进来会把直线压得更陡，**系统性
        # 高估**机械涨幅，进而**系统性低估**残差（让"质量下降"看起来比实际更
        # 严重）。实测：全 7 点拟合出的 R@1 残差 -10.75pp，只用大端 3 点
        # （95/85/75，最贴近真实的 152→95 迁移区间）是 **-7.48pp**，差 3.27pp。
        #
        # ⚠️ 评审原文给的大端值是 -6.30pp，**没能复现**：拿冻结的 M-114 数据
        # 穷举了 2~7 点的全部前缀子集（-7.19 / -7.48 / -7.05 / -8.52 / -8.76 /
        # -10.75）与全部两点斜率，没有任何一种取法算得出 -6.30。评审的 -10.75
        # 与本脚本逐位吻合，说明它确实跑过这份数据，那个 -6.30 更可能是笔误或
        # 中途换过取点方式。**方向判断成立、照改；具体数字按实测值写**——照抄
        # 一个自己没验过的数字，正是这套体系一直在防的事。
        #
        # 两条拟合都不是"正确答案"——真实关系非线性，往区间外的 152 外插本来
        # 就没有精确解。所以不再挑一条报，改成**报区间**：两条拟合给出上下界，
        # 结论只使用"区间是否整体落在同一侧"这个层面的信息。这也正是这套体系
        # 一直在强调的做法（同 R2-05 置信区间）：拿不出精确值时，报出不确定性
        # 的范围，比挑一个好看的点估计诚实。
        def slope_per_50(k, subset=None):
            pts = subset if subset is not None else rows
            xs = [size for size, _ in pts]
            ys = [a[k] for _, a in pts]
            n = len(xs)
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            den = sum((x - mean_x) ** 2 for x in xs)
            slope = num / den if den else 0.0
            return -slope * 50  # 语料变小(x减少)对应 recall 变化，取"每减50条"的正向量纲

        # 大端 3 点：95/85/75，最贴近真实迁移所处的规模区间，且避开凹曲线的
        # 陡峭远端。与全点拟合一起构成区间的两端。
        large_end = rows[:3]
        # 采样点随语料条数自适应之后，大端三点不再固定是 95/85/75，
        # 但"必须有三个、且是最大的三个"这个前提仍要守住。
        # ⚠️ `list == tuple` 在 Python 里**恒为 False**，哪怕元素完全一样。
        # 第一版写成 `[s for s, _ in large_end] == sizes[:3]`（左 list 右 tuple），
        # 于是这条断言在采样点完全正确时也照样炸——与第二轮 R2-22 那个
        # `str == list[0:1]` 恒假的守卫是同一个错误形态，这次是我自己复刻的。
        # 统一成 tuple 再比。
        assert len(large_end) == 3 and tuple(s for s, _ in large_end) == sizes[:3], \
            f"大端 3 点取值异常（实得 {[s for s, _ in large_end]}，" \
            f"采样点 {sizes}）——语料过小时这段区间拟合的前提不成立"

        print("\n每减少 50 条语料，机械涨幅（线性拟合斜率，非精确值，报区间不报点）：")
        slopes_all, slopes_large = {}, {}
        for k in TOP_KS:
            sl_all = slope_per_50(k)
            sl_lg = slope_per_50(k, large_end)
            slopes_all[k], slopes_large[k] = sl_all, sl_lg
            m.set_value(f"slope_per_50_recall_at_{k}", round(sl_all, 4))
            m.set_value(f"slope_per_50_largeend_recall_at_{k}", round(sl_lg, 4))
            print(f"  R@{k}: 全7点拟合 +{sl_all*100:5.2f}pp/50条   "
                  f"大端3点拟合 +{sl_lg*100:5.2f}pp/50条")
        print("  （全点拟合含 N=25，会把凹曲线的陡峭远端算进来，偏高；"
              "大端拟合偏低。真值在两者之间。）")

        # ── 跨版本对照：只在**题目集没换**时才成立 ────────────────────
        #
        # 🔴 2026-08-14 必须加的守卫。本段的算法是：拿两个版本实测召回率之差
        # 减去"语料规模变化带来的机械涨幅"，倒推真实检索质量升降。它有一个
        # **从未被写下来的前提**：两个版本用的是**同一批题目**，否则差值里
        # 混进了"题目难度变了"这个完全没被建模的变量。
        #
        # v1.2.0(239题) → v1.3.0(169题) 时这个前提就已经不成立，只是当时
        # 语料/题目同步缩水，方向恰好一致，没暴露出来。到 v1.4.0(227题，
        # **新增了整整一档 paraphrase 77 题**)，脚本算出 R@1 "质量下降
        # -25.7pp" —— 这个数字毫无意义：它把"新加了一档中等难度题"记到了
        # "检索质量下降"头上。
        #
        # 正确做法不是调参数，是**拒绝下结论**。同一批题目才比得了。
        _base_n = V1_2_0.get("question_count")
        _cur_n = CURRENT.get("question_count")
        if _base_n and _cur_n and _base_n != _cur_n:
            print(f"\n{'='*78}")
            print("⚠️ 跨版本对照已跳过：两个版本的题目集不同"
                  f"（基准 {_base_n} 题 vs 当前 {_cur_n} 题）。")
            print("本段算法假定两版用同一批题目，只有语料规模在变；题目集换了之后，")
            print("实测差值里混进了「题目难度变了」这个未被建模的变量，")
            print("倒推出的「质量升降」不成立。**同一批题目才比得了**——")
            print("上面那张「语料规模 → 召回率」的子采样曲线不受影响，它是")
            print("在同一批题目上做的，仍然有效；不成立的只是跨版本那一步减法。")
            print("=" * 78)
            m.note("跨版本对照本次跳过：题目集从 %s 题变为 %s 题，"
                   "本段算法的「同一批题目」前提不成立。子采样曲线仍有效。"
                   % (_base_n, _cur_n))
            return 0

        delta_size = V1_2_0["corpus_size"] - CURRENT["corpus_size"]
        print(f"\n真实迁移 {V1_2_0['corpus_size']}→{CURRENT['corpus_size']} 条，"
              f"减少 {delta_size} 条：")
        print(f"{'指标':<8}{'机械预期涨幅(区间)':<22}{'实测涨幅':<12}{'扣除语料效应后(区间)':<20}")
        verdict_lines = []
        for k, key in ((1, "recall_at_1"), (5, "recall_at_5"), (10, "recall_at_10")):
            exp_hi = slopes_all[k] * (delta_size / 50)     # 偏高的机械预期
            exp_lo = slopes_large[k] * (delta_size / 50)   # 偏低的机械预期
            actual = V1_3_0[key] - V1_2_0[key]
            # 机械预期越大 ⇒ 残差越小，故上下界交叉对应
            res_lo, res_hi = actual - exp_hi, actual - exp_lo
            m.set_value(f"expected_mechanical_gain_r{k}", round(exp_hi, 4))
            m.set_value(f"expected_mechanical_gain_largeend_r{k}", round(exp_lo, 4))
            m.set_value(f"actual_observed_gain_r{k}", round(actual, 4))
            m.set_value(f"residual_after_corpus_effect_r{k}", round(res_lo, 4))
            m.set_value(f"residual_after_corpus_effect_largeend_r{k}", round(res_hi, 4))
            # 判定只用"区间是否整体落在同一侧"——跨 0 就只能说"无法判定"，
            # 这比挑一端下结论诚实（R3-39）
            if res_lo > 0.005 and res_hi > 0.005:
                verdict = "质量提升"
            elif res_lo < -0.005 and res_hi < -0.005:
                verdict = "质量下降"
            elif res_lo <= 0.005 and res_hi >= -0.005 and res_lo * res_hi <= 0:
                verdict = "区间跨0，无法判定升降"
            else:
                verdict = "基本持平"
            verdict_lines.append((k, res_lo, res_hi, verdict))
            print(f"R@{k:<6}+{exp_lo*100:5.2f}~+{exp_hi*100:5.2f}pp        "
                  f"+{actual*100:5.2f}pp      "
                  f"{res_lo*100:+6.2f}~{res_hi*100:+6.2f}pp（{verdict}）")

        print("\n" + "=" * 78)
        print("结论（注意下面这段的措辞克制程度，不要只看数字）：")
        print("=" * 78)
        print("⚠️ 上面\"扣除语料效应后\"那一列是线性拟合外插到152条算出来的——")
        print("拟合用的7个采样点在[25,95]区间内，152在区间外、且几乎是95的1.6倍，")
        print("召回率对候选池大小的真实关系大概率不是全局线性的（更可能是随池子")
        print("变大边际效应递减），外插到区间外的点估计精度低，具体pp数不能当")
        print("精确数字使用，只能看方向和量级。")
        print("R3-39 起进一步改为**报区间**：两条拟合（全7点 / 大端3点）给出上下界，")
        print("下面的判定只使用\"区间是否整体落在同一侧\"这一层信息。")
        for k, res_lo, res_hi, verdict in verdict_lines:
            print(f"  R@{k}: 方向指向「{verdict}」，外插区间 "
                  f"{res_lo*100:+.2f} ~ {res_hi*100:+.2f}pp"
                  f"（区间本身也只是量级参考，两端都不是精确值）")
        print("\n**能站住的结论只有方向性的那一句**：三项指标扣除语料规模的机械效应后，")
        print("没有一项指向\"检索质量有明显提升\"，多数指向持平或下降。v1.2.0 与")
        print("v1.3.0 的 recall@k 因此不能直接比较——两者候选池规模不同，报告应")
        print("明确声明这一点，不应称 recall@5 达标为\"新达标\"（这个说法暗示的是")
        print("\"系统改进带来的达标\"，而真实情况是候选池变小的机械效应至少是主要")
        print("贡献者，是否还有真实质量提升的贡献，凭这条外插曲线无法确证）。")

        m.note(
            "对照实验方法：对每道题独立子采样候选池（gold 必在池内、其余从95条"
            "剩余94条里随机抽），30次独立试验取均值。N=95行不作采样，用于校验"
            "本脚本实现与M-111/M-207的真实召回率是否吻合。斜率用线性最小二乘对"
            "(语料规模, recall)拟合得到，只是数量级参考，不是精确的因果模型——"
            "真实的语料规模-召回率关系未必线性，7个采样点在152这个规模外插也有"
            "误差，但方向性结论（语料变小会机械性推高召回率）是稳健的。"
            "R3-39：拟合改为报区间——全7点拟合（含N=25，落在凹曲线陡峭远端，"
            "机械涨幅偏高⇒残差偏低）与大端3点拟合（95/85/75，最贴近真实迁移"
            "区间，机械涨幅偏低⇒残差偏高）各给一端，真值在两者之间；"
            "升降判定只在区间整体落在同一侧时才下，跨0则如实报\"无法判定\"。"
        )

    return 0


# 供 reproduce_L1.py 接线（R3-15）——与 measure_L1/measure_B2 同一约定
ALL = {"M-114": m114}


def check_preconditions():
    """R3-31：与 measure_L1/measure_B2 同一约定，缺文件返回人话、齐了返回 None。
    本模块只吃固化导出的向量 TSV，不调 embedding SDK。"""
    missing = [p for p in (DOCS_PATH, QUERIES_PATH) if not os.path.exists(p)]
    if missing:
        return ("缺少固化向量文件，本模块（measure_corpus_size_effect.py）跑不了：\n"
                + "\n".join(f"    {p}" for p in missing)
                + "\n  这些 TSV 是从麒麟环境导出、随交付物一同提供的（正是"
                  "为了让没有麒麟的机器也能验召回率）。\n"
                  "  只想核对已有记录的话用：python reproduce_L1.py --check-only")
    return None


def main():
    return m114()


if __name__ == "__main__":
    sys.exit(main())
