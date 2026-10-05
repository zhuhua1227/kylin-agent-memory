#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发集正例复核 · 出卷。

═══ 为什么要这一轮 ═══════════════════════════════════════════
冻结集 36 对里：
  · 17 条负例  —— 2026-08-27 已由使用者盲测复核过
  · K-0052     —— 使用者本人逐条判过
  · 其余正例   —— 至今【只有 Claude 裁决过】，未经人工复核
这一轮补上最后这块。报告里"由 AI 裁决"这条已知限制因此可以撤下，
换成"全部 36 对经团队盲测复核，一致率 X%"。

═══ 与留出集那轮的三点不同 ═══════════════════════════════════
① 数据源不同：开发集（Windows 侧历史），不是 D1~D7 采集
② 归属协议不同：本轮不受《留出集抽样与裁决-协议》v1.0 约束
③ 🔴 诱饵是【反向】的 —— 混入已确认的负例。
   本轮待判项全部来自正例候选，若诱饵也用正例，
   一个"全判冲突"的人能满分通过，诱饵就失效了。
   反向诱饵抓的是相反的失败模式：滥判。
═══════════════════════════════════════════════════════════

人数不多、题量小（约 18 题），所以【每人做全部题目】而不是分工——
这样每一条都有 5 个独立判断，一致率数据最完整。

用法：
    python3 开发集复核出卷.py --eval conflicts_eval.jsonl \\
        --list 抽样清单.md --n 5 --out .
"""
import json, io, os, re, sys, html, random, argparse, importlib.util

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260903
REVERSE_DECOY_N = 5


def load_template():
    """复用 分层与抽样.py 里的卷子模板，避免两处样式漂移。"""
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, u"分层与抽样.py")
    spec = importlib.util.spec_from_file_location("_sampler", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._INLINE_TPL


def holdout_decoy_ids(list_path):
    """从抽样清单里读出留出集那轮用掉的诱饵 —— 它们已在那轮被判过，本轮不重复。"""
    if not os.path.exists(list_path):
        return set()
    txt = io.open(list_path, encoding="utf-8").read()
    m = re.search(u"诱饵[^：]*：(.+)", txt)
    return set(re.findall(r"K-\d+", m.group(1))) if m else set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default="conflicts_eval.jsonl")
    ap.add_argument("--list", default="抽样清单.md")
    ap.add_argument("--n", type=int, default=5, help="出几份（每份内容相同，顺序不同）")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()

    rows = [json.loads(l) for l in io.open(a.eval, encoding="utf-8") if l.strip()]
    pos = [r for r in rows if r.get("label") == "conflict"]
    neg = [r for r in rows if r.get("label") != "conflict"]

    used = holdout_decoy_ids(a.list)
    used.add("K-0052")                       # 使用者已逐条判过

    todo = [r for r in pos if str(r.get("conflict_id")) not in used]

    rnd = random.Random(SEED)
    rdecoys = rnd.sample(neg, min(REVERSE_DECOY_N, len(neg)))

    def txt(r, side):
        s = r.get(side) or {}
        return str(s.get("claim") or s.get("text") or "")

    base = []
    for r in todo:
        base.append({"id": str(r.get("conflict_id")), "decoy": False,
                     "old": txt(r, "old"), "new": txt(r, "new")})
    for r in rdecoys:
        base.append({"id": str(r.get("conflict_id")), "decoy": True,
                     "old": txt(r, "old"), "new": txt(r, "new")})

    tpl = load_template()
    # 反向诱饵：正确答案是 not_conflict，故把校验逻辑对调
    tpl = tpl.replace("if(v==='conflict')dOK++;", "if(v==='not_conflict')dOK++;")
    tpl = tpl.replace(u"留出集盲测裁决", u"开发集正例复核")
    tpl = tpl.replace(u"本轮不显示时间戳，「同会话同天」那条排除规则由脚本事后套用，你只判语义。",
                      u"本轮不显示时间戳与任何既往裁决。就按 §五 的流程判，"
                      u"不要去猜「这条以前被判成什么」。")

    for i in range(a.n):
        who = u"P%d" % (i + 1)
        items = list(base)
        random.Random(SEED + 2000 + i).shuffle(items)
        cards, key = [], []
        for k, it in enumerate(items, 1):
            key.append({"n": k, "id": it["id"], "layer": "DEV", "decoy": it["decoy"], "who": who})
            cards.append(u'''
<article class="card"><header><span class="num">%02d</span></header>
<div class="pair"><section><h4>前</h4><pre>%s</pre></section>
<section><h4>后</h4><pre>%s</pre></section></div>
<footer>
<label><input type="radio" name="q%d" value="conflict"><span>冲突</span></label>
<label><input type="radio" name="q%d" value="not_conflict"><span>不是冲突</span></label>
<input class="note" placeholder="理由（可选）"></footer></article>'''
                         % (k, html.escape(it["old"]), html.escape(it["new"]), k, k))
        out = (tpl.replace("__N__", str(len(items)))
                  .replace("__CARDS__", u"\n".join(cards))
                  .replace("__KEY__", json.dumps(key, ensure_ascii=False))
                  .replace("holdout_blind_20260903", "devrecheck_20260903_%s" % who.lower())
                  .replace(u"开发集正例复核", u"开发集正例复核　·　%s" % who, 1))
        io.open(os.path.join(a.out, u"开发集复核-%s.html" % who), "w",
                encoding="utf-8", newline="\n").write(out)

    print(u"待复核正例：%d 条（已排除留出集诱饵 %d 条与 K-0052）"
          % (len(todo), len(used) - 1))
    print(u"反向诱饵：%d 条（来自已人工确认的负例）" % len(rdecoys))
    print(u"每份共 %d 题，出了 %d 份：开发集复核-P1..P%d.html"
          % (len(base), a.n, a.n))
    print(u"\n🔴 收卷判据：反向诱饵必须【全部判为「不是冲突」】，"
          u"有人判成冲突 = 该人滥判，该份作废重做")


if __name__ == "__main__":
    main()
