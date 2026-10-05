#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_ablation.py —— 模型消融 4×2 评分（§四，零 LLM 裁判）

判对（严格口径）= must_contain 全部命中 且 must_not_contain 全部未命中。
匹配前：小写 + 连续空白压成单空格；子串匹配。

另出一个「排除子串误伤」的宽口径：某个 must_not_contain 短串（≤5 字符、纯字母数字）
仅作为更长单词的一部分出现、全文无一次带词边界的真命中时，视为误伤不计
（预登记 §四已列出这类风险：'cat' 命中 'application'、'vi' 命中 'device' 等）。
两个口径都报，headline 用严格口径。
"""
import argparse
import json
import os
import re

# 打印答案原文前，把形如 `TOKEN="xxxx"` 的赋值掩码——LLM 的回答里可能带
# 占位符（your_token_here）甚至真值，交付物不该原样带出。防御性，不影响判分。
_SECRET_ASSIGN = re.compile(
    r"""((?i:password|passwd|pw|token|api[_-]?key|secret)\s*[=:]\s*["'])([^"']{3,})(["'])""")


def _mask_secrets(s):
    # 掩码成 1 个字符（不是 "<masked>"）——否则占位串本身又会触发脱敏扫描器的 {5,} 规则
    return _SECRET_ASSIGN.sub(lambda m: m.group(1) + "…" + m.group(3), s or "")


def norm_ans(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def norm_needle(s):
    # 不去首尾：'vi ' 的尾空格是判据的一部分
    return re.sub(r"\s+", " ", (s or "").lower())


def crit_match(crit, a):
    """判据项匹配。crit 可以是：
       - 字符串           → 规范化后子串匹配
       - ["re", pattern]  → 正则 search（在规范化答案上）
       - ["any", c1, c2…] → 任一子项匹配（子项本身可以是上述任意形式）
    返回 (bool, 命中的可读标签)。"""
    if isinstance(crit, str):
        n = norm_needle(crit)
        return (n in a, crit)
    if isinstance(crit, list) and crit:
        kind = crit[0]
        if kind == "re":
            pat = crit[1].lower()
            return (re.search(pat, a) is not None, f"re:{crit[1]}")
        if kind == "any":
            for sub in crit[1:]:
                ok, lbl = crit_match(sub, a)
                if ok:
                    return True, f"any→{lbl}"
            return False, f"any({len(crit)-1})"
    return False, repr(crit)


def is_artifact(label, crit, a):
    """只对「纯字母数字短串、且无带词边界真命中」的字符串判据判为子串误伤。"""
    if not isinstance(crit, str):
        return False
    n = crit.strip().lower()
    if not n or len(n) > 5 or not re.fullmatch(r"[a-z0-9]+", n):
        return False
    if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", a):
        return False
    return True


def judge(answer, task):
    a = norm_ans(answer)
    mc = task["must_contain"]
    mn = task["must_not_contain"]
    hit = []
    mc_ok = True
    for c in mc:
        ok, lbl = crit_match(c, a)
        if ok:
            hit.append(lbl)
        else:
            mc_ok = False
    bad, arti = [], []
    for c in mn:
        ok, lbl = crit_match(c, a)
        if ok:
            bad.append(lbl)
            if is_artifact(lbl, c, a):
                arti.append(lbl)
    strict = mc_ok and not bad
    adj = mc_ok and not [b for b in bad if b not in arti]
    return {"strict": strict, "adj": adj, "hit": hit, "bad": bad, "arti": arti}


def fmt(v):
    return "·" if v is None else ("1" if v else "0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="tasks.jsonl")
    ap.add_argument("--runs", default="runs.jsonl")
    ap.add_argument("--runs2", default="", help="逗号分隔的额外 runs 文件（如 K 行）")
    ap.add_argument("--md", default="results.md")
    args = ap.parse_args()

    tlist = [json.loads(l) for l in open(args.tasks, encoding="utf-8") if l.strip()]
    tasks = {t["task_id"]: t for t in tlist}
    order = [t["task_id"] for t in tlist]
    edge = {t["task_id"] for t in tlist if t.get("edge")}

    cell = {}
    runs_files = [args.runs] + (args.runs2.split(",") if args.runs2 else [])
    for rf in runs_files:
        if not rf or not os.path.exists(rf):
            continue
        for l in open(rf, encoding="utf-8"):
            if not l.strip():
                continue
            r = json.loads(l)
            cell[(r["size"], r["task_id"], r["memory"])] = r

    sizes = ["2b", "4b", "9b"]
    if any(k[0] == "k" for k in cell):
        sizes.append("k")
    lbl = {"2b": "Qwen3.5-2B Q4_K_M", "4b": "Qwen3.5-4B Q4_K_M",
           "9b": "Qwen3.5-9B Q4_K_M", "k": "Gemini-3.1-Pro maxthinking（云端）"}
    has_k = "k" in sizes

    V = {}   # (s,tid,mem) -> dict or None-verdict
    for s in sizes:
        for tid in order:
            for mem in (False, True):
                r = cell.get((s, tid, mem))
                if r is None or r.get("error"):
                    V[(s, tid, mem)] = {"strict": None, "adj": None, "hit": [],
                                        "bad": [], "arti": [], "rec": r}
                    continue
                j = judge(r["answer_text"], tasks[tid])
                j["rec"] = r
                V[(s, tid, mem)] = j

    def score(s, mem, key="strict", subset=None):
        c = n = 0
        for tid in order:
            if subset and tid not in subset:
                continue
            v = V[(s, tid, mem)][key]
            if v is None:
                continue
            n += 1
            c += 1 if v else 0
        return c, n

    O = []
    W = O.append
    W("# 模型消融 4×2 · 结果\n")
    kmsg = "K 行 = Gemini 3.1 Pro（maxthinking），经 catie 中转；thinking 与本地三行（关）不同，见报告。" if has_k else "K 行（云端）本轮未跑。"
    W(f"> `score_ablation.py`，零 LLM 裁判。{kmsg}")
    W("> headline = 严格子串口径；另给排除 must_not_contain 子串误伤后的宽口径。")
    W("> K 行只判最终 answer（content），不看 reasoning_content。\n")

    for title, keyname in [("严格口径", "strict"), ("宽口径（排除子串误伤）", "adj")]:
        W(f"## 八格 · 全 13 题 · {title}\n")
        W("| 模型 | 无记忆 | 有记忆 | Δ |")
        W("|---|---|---|---|")
        for s in sizes:
            (c0, n0), (c1, n1) = score(s, False, keyname), score(s, True, keyname)
            W(f"| {lbl[s]} | {c0}/{n0} | {c1}/{n1} | {c1-c0:+d} |")
        if not has_k:
            W("| Gemini（云端） | —（未跑） | —（未跑） | — |")
        W("")

    W("## 去掉边缘题 T-11 / T-13 后：11 题（严格口径）\n")
    core = set(order) - edge
    W("| 模型 | 无记忆 | 有记忆 | Δ |")
    W("|---|---|---|---|")
    for s in sizes:
        (c0, n0), (c1, n1) = score(s, False, "strict", core), score(s, True, "strict", core)
        W(f"| {lbl[s]} | {c0}/{n0} | {c1}/{n1} | {c1-c0:+d} |")
    W("")

    W("## 边缘题单列（§3.3）· 严格口径 无·有\n")
    W("| 题 | " + " | ".join(s.upper() for s in sizes) + " |")
    W("|---|" + "---|" * len(sizes))
    for tid in sorted(edge):
        row = [tid]
        for s in sizes:
            a = V[(s, tid, False)]; b = V[(s, tid, True)]
            row.append(f"{fmt(a['strict'])}·{fmt(b['strict'])}")
        W("| " + " | ".join(row) + " |")
    W("")

    W("## 逐题矩阵（严格口径；1=对 0=错 ·=缺/错误）\n")
    hdr = "| 题 | 边缘 |"
    for s in sizes:
        hdr += f" {s.upper()}无 | {s.upper()}有 |"
    W(hdr)
    W("|---|---|" + "---|---|" * len(sizes))
    for tid in order:
        cs = []
        for s in sizes:
            for mem in (False, True):
                cs.append(fmt(V[(s, tid, mem)]["strict"]))
        W(f"| {tid} | {'★' if tid in edge else ''} | " + " | ".join(cs) + " |")
    W("")

    W("## McNemar（§四）\n")
    pairs = [
        ("A1", ("2b", False), "A2", ("2b", True)),
        ("B1", ("4b", False), "B2", ("4b", True)),
        ("C1", ("9b", False), "C2", ("9b", True)),
        ("A2", ("2b", True), "B2", ("4b", True)),
        ("A2", ("2b", True), "C2", ("9b", True)),
        ("B2", ("4b", True), "C2", ("9b", True)),
    ]
    if has_k:
        pairs = [
            ("A2（2B+记忆）", ("2b", True), "K1（云端无记忆）·主论点", ("k", False)),
            ("A2（2B+记忆）", ("2b", True), "K2（云端+记忆）·张力题", ("k", True)),
            ("B2（4B+记忆）", ("4b", True), "K1（云端无记忆）", ("k", False)),
            ("C2（9B+记忆）", ("9b", True), "K2（云端+记忆）", ("k", True)),
            ("K1（云端无记忆）", ("k", False), "K2（云端+记忆）·Δ", ("k", True)),
        ] + pairs
    W("**n=13，只列不一致对的条数与方向，不作显著性 / 等价推断。**\n")
    W("| 配对（前者 vs 后者） | 只前者对 | 只后者对 | 均对/均错 |")
    W("|---|---|---|---|")
    for la, A, lb, B in pairs:
        b = c = same = 0
        for tid in order:
            x = V[(A[0], tid, A[1])]["strict"]
            y = V[(B[0], tid, B[1])]["strict"]
            if x is None or y is None:
                continue
            if x and not y:
                b += 1
            elif y and not x:
                c += 1
            else:
                same += 1
        W(f"| {la} vs {lb} | {b} | {c} | {same} |")
    W("")

    W("## 判分明细\n")
    for tid in order:
        t = tasks[tid]
        W(f"### {tid}{'  ★边缘' if tid in edge else ''}  源:{t['source_unit']} (doc {t.get('source_doc_id')})")
        W(f"- must_contain={t['must_contain']}  must_not_contain={t['must_not_contain']}")
        for s in sizes:
            for mem in (False, True):
                v = V[(s, tid, mem)]
                r = v["rec"]
                if r is None:
                    W(f"  - {s} m{int(mem)}: 缺")
                    continue
                if r.get("error"):
                    W(f"  - {s} m{int(mem)}: 错误 {r['error']}")
                    continue
                tag = fmt(v["strict"])
                if v["strict"] != v["adj"]:
                    tag += f"→{fmt(v['adj'])}(宽)"
                inj = f" inj={r.get('injected_doc_ids')}" if mem else ""
                W(f"  - {s} m{int(mem)}: **{tag}**  hit={v['hit']} bad={v['bad']}"
                  + (f" 误伤={v['arti']}" if v['arti'] else "") + inj)
                W(f"    `{_mask_secrets((r.get('answer_text') or '').strip()[:280])}`")
        W("")

    open(args.md, "w", encoding="utf-8", newline="").write("\n".join(O))
    print("\n".join(O[:70]))
    print(f"\n-> {args.md}")


if __name__ == "__main__":
    main()
