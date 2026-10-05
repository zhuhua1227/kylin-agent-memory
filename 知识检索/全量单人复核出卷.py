#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量单人复核 · 出卷（留出集 60 + 开发集 13 + 5 条重复题）。

═══ 为什么改成单人全量 ═══════════════════════════════════════
2026-09-06 的 5 人分工实测：公共集完全一致率仅 46.7%（15 条里 7 条），
开发集 13 条只有 1 条五人一致。分歧集中在 NEG 层（17%），
根因大概率是规范 §三「纯追加不算冲突」这条未被未受训标注者掌握。
另有一份（P4 开发集）conflict 0/13，与其自身留出集 75% 完全矛盾，判为无效。

结论：团队那轮**不作废**，改作「标注难度」的证据如实报告；
标签来源改为项目负责人单人标注，保证口径一致，并披露单人这一限制。

═══ 为什么用重复题而不是诱饵 ═════════════════════════════════
冻结集的正例与负例，本轮标注者此前都见过，诱饵会被认出来，失效。
且诱饵防的是"敷衍"，对本轮标注者不是主要风险；
73 条连判真正的风险是**疲劳导致前后标准漂移**。
故插入 5 条重复题（同一条隔远出现两次），测 intra-rater 一致性。

⚠️ 上一轮的教训：开发集用反向诱饵（应判 not_conflict 的负例），
   结果一个"全部判 not_conflict"的人反而全过 —— 反向诱饵抓不住这个方向。
   重复题没有这个盲区：不管往哪边偏，前后不一致都会暴露。
═══════════════════════════════════════════════════════════

用法：
    python3 全量单人复核出卷.py --out .
"""
import json, io, os, re, sys, html, random, argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260906
N_REPEAT = 5


def extract_from_sheet(path):
    """从已生成的盲测卷子里抽回题目（KEY 给标识，<pre> 给正文）。"""
    s = io.open(path, encoding="utf-8").read()
    m = re.search(r"const KEY=(\[.*?\]), N=\d+;", s, re.S)
    if not m:
        raise SystemExit(u"读不出 KEY：%s" % path)
    key = json.loads(m.group(1))
    pres = re.findall(r"<pre>(.*?)</pre>", s, re.S)
    if len(pres) != 2 * len(key):
        raise SystemExit(u"题目数对不上：%s（KEY %d，<pre> %d）"
                         % (path, len(key), len(pres)))
    items = []
    for i, k in enumerate(key):
        items.append({"id": k["id"], "layer": k["layer"], "decoy": k.get("decoy", False),
                      "old": html.unescape(pres[2 * i]),
                      "new": html.unescape(pres[2 * i + 1])})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", default=u"留出集盲测.html")
    ap.add_argument("--dev", default=u"开发集复核-P1.html")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()

    hold = [x for x in extract_from_sheet(a.holdout) if not x["decoy"]]
    dev = [x for x in extract_from_sheet(a.dev) if not x["decoy"]]
    base = hold + dev

    # 🔴 重复题必须【确定性放置】，不能靠随机洗牌碰运气：
    #    实测随机洗 50 次仍出现过间距只有 2 题的一对 —— 那种当场就被认出来，
    #    测不到任何东西。做法是：从前段选题、把副本插进末段，间距下界写死。
    rnd = random.Random(SEED)
    items = list(base)
    rnd.shuffle(items)

    HEAD = 25          # 只从前 25 题里选要重复的
    TAIL = 25          # 副本只插进最后 25 题的范围
    rep_src = rnd.sample(items[:HEAD], min(N_REPEAT, HEAD))
    tail_start = len(items) - TAIL
    for x in rep_src:
        pos = rnd.randint(tail_start, len(items))
        items.insert(pos, dict(x, repeat=True))
        tail_start += 1          # 插入后整体后移，保持在末段
    tries = 0

    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), u"分层与抽样.py")
    src = io.open(tpl_path, encoding="utf-8").read()
    m = re.search(r'_INLINE_TPL = u"""(.*?)"""', src, re.S)
    if not m:
        raise SystemExit(u"取不到卷子模板")
    tpl = m.group(1)

    cards, key = [], []
    for i, it in enumerate(items, 1):
        key.append({"n": i, "id": it["id"], "layer": it["layer"],
                    "decoy": False, "repeat": bool(it.get("repeat"))})
        cards.append(u'''
<article class="card"><header><span class="num">%02d</span></header>
<div class="pair"><section><h4>前</h4><pre>%s</pre></section>
<section><h4>后</h4><pre>%s</pre></section></div>
<footer>
<label><input type="radio" name="q%d" value="conflict"><span>冲突</span></label>
<label><input type="radio" name="q%d" value="not_conflict"><span>不是冲突</span></label>
<input class="note" placeholder="理由（可选）"></footer></article>'''
                     % (i, html.escape(it["old"]), html.escape(it["new"]), i, i))

    # 没有诱饵 → 校验改成「重复题前后一致」
    tpl = tpl.replace(
        "KEY.forEach(k=>{const v=ans[k.n];\n if(k.decoy){dTot++;if(v==='conflict')dOK++;}\n"
        " else rows.push({id:k.id,layer:k.layer,verdict:v,note:notes[k.n]||''});});",
        "const seen={};KEY.forEach(k=>{const v=ans[k.n];\n"
        " if(seen[k.id]!==undefined){dTot++;if(seen[k.id]===v)dOK++;}else seen[k.id]=v;\n"
        " rows.push({id:k.id,layer:k.layer,verdict:v,note:notes[k.n]||'',n:k.n});});")
    tpl = tpl.replace("const valid=(dOK===dTot);", "const valid=true;")
    tpl = tpl.replace("'<p>诱饵：<b class=\"'+(valid?'ok':'bad')+'\">'+dOK+' / '+dTot+'</b>'+\n"
                      " (valid?' —— 本轮有效':' —— <b class=\"bad\">整轮作废，需重做</b>')+'</p>'",
                      "'<p>重复题前后一致：<b class=\"'+(dOK===dTot?'ok':'bad')+'\">'+dOK+' / '+dTot+"
                      "'</b>　（不一致的条目在结果里能查到，供你回头复看）</p>'")
    tpl = tpl.replace(u"留出集盲测裁决", u"全量单人复核")
    tpl = tpl.replace(u"本轮不显示时间戳，「同会话同天」那条排除规则由脚本事后套用，你只判语义。",
                      u"本轮不显示时间戳、来源与任何既往裁决。只判语义；"
                      u"「同会话同天」那条排除规则由脚本事后套用。")

    out = (tpl.replace("__N__", str(len(items)))
              .replace("__CARDS__", u"\n".join(cards))
              .replace("__KEY__", json.dumps(key, ensure_ascii=False))
              .replace("holdout_blind_20260903", "fullrecheck_20260906"))
    p = os.path.join(a.out, u"全量单人复核.html")
    io.open(p, "w", encoding="utf-8", newline="\n").write(out)

    print(u"留出集 %d + 开发集 %d = %d 条，加 %d 条重复题，共 %d 题"
          % (len(hold), len(dev), len(base), len(rep_src), len(items)))
    print(u"重复题拉开距离：洗牌 %d 次" % tries)
    print(u"产出：%s" % p)


if __name__ == "__main__":
    main()
