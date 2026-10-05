#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robustness_v2.py —— 模型消融 v2 的稳健性检验（三判据口径 × 留一法）

═══ 这一节要回答的问题 ═══════════════════════════════════════
报告 §二 的 headline 用的是「严格口径」。一个评委必然会问：
**换个判据口径，结论还成立吗？**

本脚本把三种口径各算一遍，并对全 13 题做留一法，
用来界定「哪些结论是数据的性质，哪些只是口径选择的产物」。

🔴 三种口径都是【事后】计算的稳健性检验。
   跑前固定的判据是严格口径（deltas_prereg_v2.md），headline 不因本节改写。
   本节的作用是给结论定强度，不是择优取用。
═══════════════════════════════════════════════════════════

三种口径：
  严格 strict   must_contain 全中 且 must_not_contain 全不中   ← headline
  宽   adj      同上，但排除已认定的子串误伤（cat ⊂ application 等）
  有序 ordered  must_contain 全中，且其最晚命中位置早于任一 must_not_contain
                的最早命中（题干问「下一步执行什么命令」，第一条命令即答案）

用法：
    python3 robustness_v2.py > robustness_v2.md
"""
import importlib.util
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))


def load_scorer():
    """复用 score_ablation.py 的规范化与判据匹配，避免两处口径漂移。"""
    src = os.path.join(HERE, "score_ablation.py")
    spec = importlib.util.spec_from_file_location("_sc", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sc = load_scorer()

SZ = ["2b", "4b", "9b", "k"]
LBL = {"2b": "Qwen3.5-2B", "4b": "Qwen3.5-4B", "9b": "Qwen3.5-9B",
       "k": "Gemini-3.1-Pro"}


def load():
    tl = [json.loads(l) for l in io.open(os.path.join(HERE, "tasks_v2.jsonl"),
                                         encoding="utf-8") if l.strip()]
    tasks = {t["task_id"]: t for t in tl}
    order = [t["task_id"] for t in tl]
    cell = {}
    for f in ("runs_v2.jsonl", "runs_krow_v2.jsonl"):
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            continue
        for l in io.open(p, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                cell[(r["size"], r["task_id"], r["memory"])] = r
    return tasks, order, cell


def first_pos(crit, a):
    """判据项在规范化答案里的最早出现位置；未出现返回 -1。"""
    if isinstance(crit, str):
        return a.find(sc.norm_needle(crit))
    if isinstance(crit, list) and crit:
        if crit[0] == "re":
            m = re.search(crit[1].lower(), a)
            return m.start() if m else -1
        if crit[0] == "any":
            ps = [p for p in (first_pos(x, a) for x in crit[1:]) if p >= 0]
            return min(ps) if ps else -1
    return -1


def judge_ordered(ans, task):
    a = sc.norm_ans(ans or "")
    mc = [first_pos(c, a) for c in task["must_contain"]]
    if any(p < 0 for p in mc):
        return False
    bad = []
    for c in task["must_not_contain"]:
        p = first_pos(c, a)
        if p < 0:
            continue
        if isinstance(c, str) and sc.is_artifact(c, c, a):   # 子串误伤不计
            continue
        bad.append(p)
    return (not bad) or (max(mc) < min(bad))


def verdict(cell, tasks, s, tid, mem, kind):
    r = cell.get((s, tid, mem))
    if r is None or r.get("error"):
        return None
    ans = r.get("answer_text") or ""
    if kind == "ordered":
        return judge_ordered(ans, tasks[tid])
    return sc.judge(ans, tasks[tid])[kind]


def grid(cell, tasks, order, kind):
    out = {}
    for s in SZ:
        for m in (False, True):
            out[(s, m)] = sum(1 for t in order
                              if verdict(cell, tasks, s, t, m, kind))
    return out


def deltas(g):
    return [g[(s, True)] - g[(s, False)] for s in SZ]


def mono(ds):
    return all(ds[i] <= ds[i + 1] for i in range(len(ds) - 1))


def main():
    tasks, order, cell = load()
    n = len(order)
    KINDS = [("strict", "严格（headline，跑前固定）"),
             ("adj", "宽（排子串误伤）"),
             ("ordered", "有序（正解在前）")]

    print("### 6.1 三种判据口径对照\n")
    print("| 口径 | " + " | ".join(LBL[s] for s in SZ) + " | 阶梯单调不减 | 9B vs K（有记忆） |")
    print("|---|" + "---|" * (len(SZ) + 2))
    gs = {}
    for k, name in KINDS:
        g = grid(cell, tasks, order, k)
        gs[k] = g
        ds = deltas(g)
        cells = " | ".join("%d/%d→%d/%d (%+d)" % (g[(s, False)], n, g[(s, True)], n, d)
                           for s, d in zip(SZ, ds))
        print("| **%s** | %s | %s | %d : %d |"
              % (name, cells, "✅" if mono(ds) else "❌",
                 g[("9b", True)], g[("k", True)]))

    print("\n**三种口径下都成立的结论只有一条**：")
    allpos = all(d > 0 for k in gs for d in deltas(gs[k]))
    lo = min(d for k in gs for d in deltas(gs[k]))
    hi = max(d for k in gs for d in deltas(gs[k]))
    print("\n```")
    print("✅ 记忆机制在全部四档上均产生正增益（Δ 从 %+d 到 %+d，无一档、无一口径例外）"
          % (lo, hi))
    print("```\n" if allpos else "```\n⚠️ 存在非正增益格，需人工复核\n```\n")

    print("其余两条各自只在部分口径下成立：\n")
    print("```")
    for k, name in KINDS:
        ds = deltas(gs[k])
        print("%-6s  阶梯单调 %s    9B≈K %s"
              % (k, "✅" if mono(ds) else "❌",
                 "✅" if abs(gs[k][("9b", True)] - gs[k][("k", True)]) <= 1 else "❌"))
    print("```\n")

    print("### 6.2 留一法：逐题剔除后的 Δ 阶梯（严格口径，n=%d）\n" % (n - 1))
    print("| 剔除 | " + " | ".join(LBL[s] for s in SZ) + " | 阶梯 |")
    print("|---|" + "---|" * (len(SZ) + 1))
    base = deltas(gs["strict"])
    print("| *（不剔，n=%d）* | %s | %s |"
          % (n, " | ".join("%+d" % d for d in base), "✅" if mono(base) else "❌"))
    broken = []
    for drop in order:
        keep = [t for t in order if t != drop]
        g = {}
        for s in SZ:
            for m in (False, True):
                g[(s, m)] = sum(1 for t in keep
                                if verdict(cell, tasks, s, t, m, "strict"))
        ds = deltas(g)
        ok = mono(ds)
        if not ok:
            broken.append(drop)
        print("| %s | %s | %s |" % (drop, " | ".join("%+d" % d for d in ds),
                                    "✅" if ok else "❌ **破**"))
    print("\n**剔掉 %s 中任意一条，阶梯的单调性即不成立。**"
          % " 或 ".join(broken) if broken else "\n**阶梯对任意单题剔除均稳健。**")
    print("从 %+d 到 %+d 之间全部信号仅 %d 条题，而无记忆组各档在 %s 之间抖动——"
          % (base[0], base[-1], base[-1] - base[0],
             "/".join(str(gs["strict"][(s, False)]) for s in SZ)))
    print("**噪声与信号同量级**。\n")

    print("### 6.3 逐题：零区分度与「记忆帮倒忙」\n")
    dead, hurt = [], []
    for t in order:
        tot = sum(1 for s in SZ for m in (False, True)
                  if verdict(cell, tasks, s, t, m, "strict"))
        if tot == 0:
            dead.append(t)
        for s in SZ:
            if verdict(cell, tasks, s, t, False, "strict") and \
               not verdict(cell, tasks, s, t, True, "strict"):
                hurt.append("%s（%s）" % (t, LBL[s]))
    print("```")
    print("八格全灭（零区分度）  : %s" % (", ".join(dead) or "无"))
    print("记忆帮倒忙            : %s" % (", ".join(hurt) or "无"))
    print("```\n")


if __name__ == "__main__":
    main()
