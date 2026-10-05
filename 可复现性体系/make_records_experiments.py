#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_records_experiments.py —— 为两个对照实验产出 records/M-211、M-212

《仓库状态闭合-执行方案.md》环节 B。与 M-210（关联检索消融，L2）同模式：
自包含 record + claims 指向它，`reproduce_L1.py --check-only` 读取核对。

计分层是纯 Python（`BGE-M3对照/retrieve.py`、`评测集/模型消融/score_ablation.py`
+ `robustness_v2.py`），评委任何机器可复现 → 主张标 L1；
系统侧固化向量的来源（KYSDK）不在本层证明 → 相关主张标 L2、note 说明。

用法：python3 make_records_experiments.py   （在完整项目根下）
"""
import hashlib
import importlib.util
import io
import json
import os
import platform
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
RECORDS = os.path.join(BASE, "records")
BGE = os.path.join(RAG, "BGE-M3对照")
ABL = os.path.join(RAG, "评测集", "模型消融")

import numpy as np


def sha256_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def env():
    return {"os": platform.platform(), "python": platform.python_version(),
            "numpy": np.__version__}


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ═══════════════════════════════════════════════ M-211  BGE-M3 对照

def m211():
    gold = np.load(os.path.join(BGE, "gold.npy"))
    qids = json.load(open(os.path.join(BGE, "qids.json"), encoding="utf-8"))

    def l2(m):
        m = np.asarray(m, dtype=np.float64)
        n = np.linalg.norm(m, axis=-1, keepdims=True)
        n[n == 0] = 1.0
        return m / n

    def per_query(side):
        D = l2(np.load(os.path.join(BGE, f"docs_{side}.npy")))
        Q = l2(np.load(os.path.join(BGE, f"queries_{side}.npy")))
        S = Q @ D.T
        order = np.argsort(-S, axis=1, kind="stable")
        ranked = np.arange(86)[order]
        out = []
        for i in range(227):
            w = np.where(ranked[i] == int(gold[i]))[0]
            out.append(int(w[0]) + 1 if len(w) else None)
        return out

    pk, pb = per_query("kylin"), per_query("bge")
    KS = (1, 3, 5, 10)

    def rk(ranks, k):
        return sum(1 for r in ranks if r is not None and r <= k)

    metrics = {}
    for k in KS:
        metrics[f"kylin_naive_r{k}"] = {"value": round(rk(pk, k) / 227, 4), "unit": "", "repeats": 1}
        metrics[f"bge_dense_r{k}"] = {"value": round(rk(pb, k) / 227, 4), "unit": "", "repeats": 1}
    for k in KS:
        ok = ob = 0
        for a, b in zip(pk, pb):
            ha = a is not None and a <= k
            hb = b is not None and b <= k
            ok += ha and not hb
            ob += hb and not ha
        metrics[f"mcnemar_r{k}_only_kylin_n"] = {"value": ok, "unit": "题", "repeats": 1}
        metrics[f"mcnemar_r{k}_only_bge_n"] = {"value": ob, "unit": "题", "repeats": 1}
    # 参考性二项精确 p（不作显著性结论）
    from math import comb
    for k in KS:
        b_ = metrics[f"mcnemar_r{k}_only_kylin_n"]["value"]
        c_ = metrics[f"mcnemar_r{k}_only_bge_n"]["value"]
        n = b_ + c_
        mlo = min(b_, c_)
        p2 = min(1.0, 2 * sum(comb(n, i) for i in range(mlo + 1)) / (2 ** n)) if n else 1.0
        metrics[f"mcnemar_r{k}_binom_p2"] = {"value": round(p2, 4), "unit": "", "repeats": 1}

    mono_k = all(rk(pk, a) <= rk(pk, b) for a, b in zip(KS, KS[1:]))
    mono_b = all(rk(pb, a) <= rk(pb, b) for a, b in zip(KS, KS[1:]))
    metrics["kylin_r_at_k_monotone"] = {"value": bool(mono_k), "unit": "", "repeats": 1}
    metrics["bge_r_at_k_monotone"] = {"value": bool(mono_b), "unit": "", "repeats": 1}
    metrics["n_gold_docs"] = {"value": int(len(set(int(x) for x in gold))), "unit": "个", "repeats": 1}

    bge_meta = json.load(open(os.path.join(BGE, "_bge_meta.json"), encoding="utf-8"))

    rec = {
        "record_id": "M-211",
        "title": "BGE-M3 对照实验（naive 档，227 题 / 77 gold doc）：系统 kylin-coreai-embedding vs BGE-M3 dense",
        "layer": "L1",
        "script": "BGE-M3对照/retrieve.py",
        "script_sha256": sha256_file(os.path.join(BGE, "retrieve.py")),
        "command": "cd BGE-M3对照 && python retrieve.py --side kylin && python retrieve.py --side bge && python retrieve.py --mcnemar",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": None,
        "random_seed": None,
        "environment": dict(env(), note=(
            "检索与计分纯 numpy（L1）。系统侧向量取自 可复现性体系/vectors/{docs,eval_queries}.tsv"
            "（KYSDK 固化产物，来源属 L2，由 M-201/M-206 覆盖）。BGE 侧向量由 make_vectors_bge.py"
            f" 在 Ubuntu CPU 用 {bge_meta['model']} / torch {bge_meta['torch_version']} 新算"
            "（dense-only、无指令前缀、max_length 8192、doc 最长 393 token 无截断）")),
        "inputs": {p: sha256_file(os.path.join(BGE, p)) for p in
                   ("texts.jsonl", "docs_kylin.npy", "queries_kylin.npy",
                    "docs_bge.npy", "queries_bge.npy", "gold.npy")},
        "metrics": metrics,
        "notes": (
            "已知答案自检：kylin_naive_r1 与 C-265 逐位一致、kylin_naive_r10 与 C-266 逐位一致。"
            "kylin_naive_r3=0.7621 此前在 claims.json 中【无任何主张】，本次为首次锚定。"
            "🔴 McNemar 的 p 值仅供参考、不得表述为『显著』：看了 4 个截断点，Bonferroni 阈值 0.0125，"
            "R@3 的 p≈0.049 过不了。227 题只覆盖 77 个 gold doc（约 2.95 题/doc），有效样本量远小于 227。"
            "单一语料单一领域，不构成对 BGE-M3 通用能力的评价。"),
        "succeeded": True,
        "raw_stdout": "见 BGE-M3对照/results.md 与 perquery_{kylin,bge}.json；本 record 由 make_records_experiments.py 复算生成",
    }
    return rec


# ═══════════════════════════════════════════════ M-212  模型消融 4×2

def m212():
    sc = load_mod(os.path.join(ABL, "score_ablation.py"), "_sc")
    rv = load_mod(os.path.join(ABL, "robustness_v2.py"), "_rv")

    tasks, order, cell = rv.load()
    SZ = ["2b", "4b", "9b", "k"]

    grids = {kind: rv.grid(cell, tasks, order, kind) for kind in ("strict", "adj", "ordered")}
    n = len(order)

    metrics = {}
    # 八格（strict）
    cellmap = {"2b": ("a1", "a2"), "4b": ("b1", "b2"), "9b": ("c1", "c2"), "k": ("k1", "k2")}
    for s, (lo, hi) in cellmap.items():
        metrics[lo] = {"value": grids["strict"][(s, False)], "unit": "/13", "repeats": 1}
        metrics[hi] = {"value": grids["strict"][(s, True)], "unit": "/13", "repeats": 1}
    # 四档 Δ（三口径）
    for kind in ("strict", "adj", "ordered"):
        ds = rv.deltas(grids[kind])
        for s, d in zip(SZ, ds):
            metrics[f"delta_{s}_{kind}"] = {"value": int(d), "unit": "题", "repeats": 1}
        metrics[f"ladder_monotone_{kind}"] = {"value": bool(rv.mono(ds)), "unit": "", "repeats": 1}
    # 稳健结论：三口径 × 四档全为正
    all_deltas = [d for kind in grids for d in rv.deltas(grids[kind])]
    metrics["all_positive_all_criteria"] = {"value": all(d > 0 for d in all_deltas), "unit": "", "repeats": 1}
    metrics["delta_min_all_criteria"] = {"value": int(min(all_deltas)), "unit": "题", "repeats": 1}
    metrics["delta_max_all_criteria"] = {"value": int(max(all_deltas)), "unit": "题", "repeats": 1}
    # 留一法：哪些题剔掉后严格口径阶梯不再单调
    base = rv.deltas(grids["strict"])
    breakers = []
    for drop in order:
        keep = [t for t in order if t != drop]
        g = {(s, m): sum(1 for t in keep if rv.verdict(cell, tasks, s, t, m, "strict"))
             for s in SZ for m in (False, True)}
        if not rv.mono(rv.deltas(g)):
            breakers.append(drop)
    metrics["loo_ladder_breakers"] = {"value": ",".join(breakers), "unit": "", "repeats": 1}
    # McNemar 两对（strict）：A2 vs K1（主论点）、A2 vs K2（张力题）
    def mcnemar(sa, ma, sb, mb):
        oa = ob = 0
        for t in order:
            va = rv.verdict(cell, tasks, sa, t, ma, "strict")
            vb = rv.verdict(cell, tasks, sb, t, mb, "strict")
            oa += bool(va) and not bool(vb)
            ob += bool(vb) and not bool(va)
        return oa, ob
    a, b = mcnemar("2b", True, "k", False)
    metrics["mcnemar_a2_vs_k1_only_a2"] = {"value": a, "unit": "题", "repeats": 1}
    metrics["mcnemar_a2_vs_k1_only_k1"] = {"value": b, "unit": "题", "repeats": 1}
    a, b = mcnemar("2b", True, "k", True)
    metrics["mcnemar_a2_vs_k2_only_a2"] = {"value": a, "unit": "题", "repeats": 1}
    metrics["mcnemar_a2_vs_k2_only_k2"] = {"value": b, "unit": "题", "repeats": 1}
    # 9B vs K（有记忆）逐口径
    for kind in ("strict", "adj", "ordered"):
        metrics[f"c2_vs_k2_{kind}"] = {
            "value": f"{grids[kind][('9b', True)]}:{grids[kind][('k', True)]}", "unit": "", "repeats": 1}

    rec = {
        "record_id": "M-212",
        "title": "模型消融 4×2（v2；Qwen3.5-2B/4B/9B + Gemini-3.1-Pro maxthinking；13 题环境特异任务）",
        "layer": "L1",
        "script": "评测集/模型消融/robustness_v2.py + score_ablation.py",
        "script_sha256": sha256_file(os.path.join(ABL, "robustness_v2.py")),
        "command": "cd 评测集/模型消融 && python score_ablation.py --tasks tasks_v2.jsonl --runs runs_v2.jsonl --runs2 runs_krow_v2.jsonl --md results_v2.md && python robustness_v2.py > robustness_v2.md",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": None,
        "random_seed": 42,
        "environment": dict(env(), note=(
            "计分层纯 Python（零 LLM 裁判），从 runs_v2.jsonl / runs_krow_v2.jsonl 重算 → L1。"
            "生成层（本地 llama.cpp Qwen3.5 Q4_K_M / 云端 Gemini 经 catie）属 L2，不在本层证明。"
            "thinking：本地三档关、K 行 maxthinking（不一致，报告 §六.6 登记）。")),
        "inputs": {f: sha256_file(os.path.join(ABL, f)) for f in
                   ("tasks_v2.jsonl", "runs_v2.jsonl", "runs_krow_v2.jsonl", "score_ablation.py")},
        "metrics": metrics,
        "notes": (
            "🔴 结论强度随主张一起登记（报告 §六.4）：只有『记忆机制在四档 × 三口径均产生正增益』"
            "是稳健结论；『Δ 阶梯单调不减』口径依赖（宽口径下不成立）且脆弱（留一法剔 T-02 或 T-13 即破）；"
            "『9B 追平云端』口径依赖（有序口径下不成立）。n=13、8 格共用同一批题，仅为演示性对照、"
            "不具统计效力，结论按报告 §四只作描述性表述。"),
        "succeeded": True,
        "raw_stdout": "见 评测集/模型消融/results_v2.md 与 robustness_v2.md；本 record 由 make_records_experiments.py 复算生成",
    }
    return rec


def main():
    for fn in (m211, m212):
        rec = fn()
        out = os.path.join(RECORDS, f"{rec['record_id']}.json")
        json.dump(rec, open(out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=2)
        print(f"写出 {out}")
        for k, v in rec["metrics"].items():
            print(f"  {k} = {v['value']}")
        print()


if __name__ == "__main__":
    main()
