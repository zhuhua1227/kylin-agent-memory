#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""留出集分层 + 抽样 + 生成盲测卷子。

严格执行《留出集抽样与裁决-协议.md》v1.0（2026-09-03 冻结）。

═══ 为什么抽样条数写死在协议里而不是这里调 ═══════════════════
先看每层多少条、再决定抽多少，就是按数据设计实验。
本脚本的 QUOTA 必须与协议 §2.1 逐字一致；改它等于违反协议，
必须先改协议、记录偏离理由，不得静默调整。
═══════════════════════════════════════════════════════════

用法（在麒麟机上跑，候选池在那边）：
    python3 分层与抽样.py \\
        --pool /tmp/conflicts_d1d7_v14.jsonl \\
        --eval 知识检索/conflicts_eval.jsonl \\
        --out  知识检索/

产出两份：
    抽样清单.md      ← 🔴 先提交进 git，再开始裁决
    留出集盲测.html  ← 拷到本地用浏览器打开
"""
import json, io, os, re, sys, html, random, argparse, collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260903                      # 协议 §2.3
QUOTA = {"C": 15, "B": 8, "A": 7}    # 协议 §2.1，不得在此调整
NEG_N = 30                           # 协议 §2.2
DECOY_N = 5                          # 协议 §3④

# ═══ 命名空间前缀：留出集池与开发集池的 conflict_id 会撞号 ═══════
# 留出集池（D1~D7，麒麟侧挖出）编号跑到 K-0502，而开发集池（Windows 侧、
# 已冻结的 conflicts_eval.jsonl）用的是 K-0001~K-0149 —— 整段重叠，
# 同一个 K-0098 在两个池子里是【两条不同的记录】。
# 裁决结果往回合并时无法区分，故给留出集统一加前缀。
# 开发集保持裸 K-xxxx，与已冻结的《冲突判定标注规范》§六 判例表一致。
POOL_PREFIX = "HO-"                  # holdout

EXCLUDE_DATE = "2026-08-25"          # 协议 §〇：备采准备会话，非历史非采集
WINDOW_START = "2026-08-26"          # D1

REBOOT_DAYS = ("2026-08-29", "2026-08-31")
ENV_KW = ("sshd", "kysdk", "/usr", "uptime", "boot", "PID", "主 PID",
          "kytensor", "kylin-ai-runtime")

_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def side(rec, which):
    v = rec.get(which)
    return v if isinstance(v, dict) else {}


def text_of(rec, which):
    s = side(rec, which)
    return str(s.get("claim") or s.get("text") or "")


def date_of(rec, which="new"):
    s = side(rec, which)
    v = str(s.get("asserted_at") or s.get("timestamp")
            or rec.get("timestamp") or "")
    return v[:10]


def digits_stripped(t):
    """把数字全部换成占位符并归一空白——协议 §一 A 层判据。"""
    return _WS.sub(" ", _NUM.sub("<N>", t)).strip()


def stratum(rec):
    """按协议 §一 顺序判层，第一个命中即定。"""
    old_t, new_t = text_of(rec, "old"), text_of(rec, "new")

    # A · 数值漂移：去掉数字后两段完全相同
    if old_t and digits_stripped(old_t) == digits_stripped(new_t):
        return "A"

    # B · 环境失效
    if rec.get("source") == "anchor":
        return "B"
    blob = old_t + new_t
    if any(k in blob for k in ENV_KW):
        d_old, d_new = date_of(rec, "old"), date_of(rec, "new")
        if d_old and d_new and any(d_old < r <= d_new for r in REBOOT_DAYS):
            return "B"

    # C · 语义更正
    return "C"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--split", type=int, default=0,
                    help="分给 N 个人做（重叠设计：公共 15 条 + 各自独有 + 全部诱饵）。"
                         "0 = 单人一份，默认")
    ap.add_argument("--common", type=int, default=15,
                    help="公共集条数，用于算标注者间一致率")
    a = ap.parse_args()

    pool = [json.loads(l) for l in io.open(a.pool, encoding="utf-8") if l.strip()]
    ev = [json.loads(l) for l in io.open(a.eval, encoding="utf-8") if l.strip()]

    # ── 协议 §〇 排除 ────────────────────────────────
    kept, dropped = [], collections.Counter()
    for r in pool:
        d = date_of(r)
        if not d:
            dropped["无日期"] += 1; continue
        if d == EXCLUDE_DATE:
            dropped["08-25 备采准备"] += 1; continue
        if d < WINDOW_START:
            dropped["早于 D1"] += 1; continue
        kept.append(r)

    pos = [r for r in kept if r.get("structural_label") == "candidate"]
    neg = [r for r in kept if r.get("structural_label") != "candidate"]

    layers = collections.defaultdict(list)
    for r in pos:
        layers[stratum(r)].append(r)

    # ── 抽样（固定种子，按 conflict_id 排序后再抽，保证可复现）──
    rnd = random.Random(SEED)
    picked, shortfall = {}, {}
    for L in ("C", "B", "A"):
        avail = sorted(layers[L], key=lambda r: str(r.get("candidate_id") or r.get("conflict_id") or ""))
        want = QUOTA[L]
        if len(avail) <= want:
            picked[L] = avail
            shortfall[L] = want - len(avail)
        else:
            picked[L] = rnd.sample(avail, want)
            shortfall[L] = 0

    neg_sorted = sorted(neg, key=lambda r: str(r.get("candidate_id") or r.get("conflict_id") or ""))
    picked["NEG"] = neg_sorted if len(neg_sorted) <= NEG_N else rnd.sample(neg_sorted, NEG_N)

    ev_pos = sorted([r for r in ev if r.get("label") == "conflict"],
                    key=lambda r: str(r.get("conflict_id")))
    decoys = rnd.sample(ev_pos, min(DECOY_N, len(ev_pos)))

    # ── 抽样清单（先提交，再裁决）────────────────────
    def cid(r):
        """留出集池的标识一律带 HO- 前缀，见文件头 POOL_PREFIX 的说明。"""
        return POOL_PREFIX + str(r.get("candidate_id") or r.get("conflict_id") or "?")

    m = []
    m.append(u"# 留出集抽样清单")
    m.append(u"")
    m.append(u"> 🔴 **本清单必须先提交进 git，再开始裁决。** 提交后不许重抽。")
    m.append(u"> 依据《留出集抽样与裁决-协议.md》v1.0，种子 `%d`。" % SEED)
    m.append(u"")
    m.append(u"## 排除（协议 §〇）")
    m.append(u"")
    for k, v in dropped.most_common():
        m.append(u"- %s：%d 条" % (k, v))
    m.append(u"")
    m.append(u"## 分层实况")
    m.append(u"")
    m.append(u"| 层 | 池中条数 | 协议配额 | 实抽 | 缺口 |")
    m.append(u"|---|---|---|---|---|")
    for L in ("C", "B", "A"):
        m.append(u"| **%s** | %d | %d | %d | %s |"
                 % (L, len(layers[L]), QUOTA[L], len(picked[L]),
                    (u"**缺 %d**" % shortfall[L]) if shortfall[L] else u"—"))
    m.append(u"| 负例 | %d | %d | %d | — |" % (len(neg), NEG_N, len(picked["NEG"])))
    m.append(u"")
    if shortfall.get("C"):
        m.append(u"> ⚠️ **C 层不足配额。** 按协议这不是问题，但**必须在报告里写明**："
                 u"这七天的数据「量大但语义更正类偏少」，而 C 层才是有说服力的那层。")
        m.append(u"")
    m.append(u"## 抽中的 conflict_id")
    m.append(u"")
    m.append(u"> **命名空间**：`HO-K-xxxx` = 留出集池（D1~D7，麒麟侧）；裸 `K-xxxx` = 开发集池（已冻结的 conflicts_eval.jsonl，Windows 侧）。"
             u"两池编号区间重叠，**合并裁决结果时必须认前缀**。")
    m.append(u"")
    for L in ("C", "B", "A", "NEG"):
        m.append(u"**%s（%d 条）**：`%s`" % (L, len(picked[L]),
                                            u"` `".join(cid(r) for r in picked[L])))
        m.append(u"")
    m.append(u"**诱饵（%d 条，来自冻结集已确认正例）**：`%s`"
             % (len(decoys), u"` `".join(str(r.get("conflict_id")) for r in decoys)))
    io.open(os.path.join(a.out, u"抽样清单.md"), "w", encoding="utf-8",
            newline="\n").write(u"\n".join(m) + u"\n")

    # ── 盲测卷子 ────────────────────────────────────
    items = []
    for L in ("C", "B", "A", "NEG"):
        for r in picked[L]:
            items.append({"id": cid(r), "layer": L, "decoy": False,
                          "old": text_of(r, "old"), "new": text_of(r, "new")})
    for r in decoys:
        items.append({"id": str(r.get("conflict_id")), "layer": "DECOY", "decoy": True,
                      "old": text_of(r, "old"), "new": text_of(r, "new")})
    rnd.shuffle(items)

    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "盲测卷子模板.html")
    tpl = io.open(tpl_path, encoding="utf-8").read() if os.path.exists(tpl_path) else _INLINE_TPL

    def emit(sheet_items, path, lsk, who=u""):
        """出一份卷子。lsk 是 localStorage 键，多人各不相同才不会互相覆盖。"""
        cards, key = [], []
        for i, it in enumerate(sheet_items, 1):
            key.append({"n": i, "id": it["id"], "layer": it["layer"],
                        "decoy": it["decoy"], "who": who})
            cards.append(u'''
<article class="card"><header><span class="num">%02d</span></header>
<div class="pair"><section><h4>前</h4><pre>%s</pre></section>
<section><h4>后</h4><pre>%s</pre></section></div>
<footer>
<label><input type="radio" name="q%d" value="conflict"><span>冲突</span></label>
<label><input type="radio" name="q%d" value="not_conflict"><span>不是冲突</span></label>
<input class="note" placeholder="理由（可选）"></footer></article>'''
                         % (i, html.escape(it["old"]), html.escape(it["new"]), i, i))
        out = (tpl.replace("__N__", str(len(sheet_items)))
                  .replace("__CARDS__", u"\n".join(cards))
                  .replace("__KEY__", json.dumps(key, ensure_ascii=False))
                  .replace("holdout_blind_20260903", lsk)
                  .replace(u"留出集盲测裁决",
                           u"留出集盲测裁决" + (u"　·　%s" % who if who else u"")))
        io.open(path, "w", encoding="utf-8", newline="\n").write(out)

    if a.split and a.split > 1:
        reals = [it for it in items if not it["decoy"]]
        decoys_it = [it for it in items if it["decoy"]]
        common = reals[:a.common]
        rest = reals[a.common:]
        n = a.split
        chunks = [rest[i::n] for i in range(n)]          # 轮流发牌，各层分布更均匀
        for i in range(n):
            who = u"P%d" % (i + 1)
            sheet = common + chunks[i] + decoys_it
            random.Random(SEED + 1000 + i).shuffle(sheet)   # 每人顺序不同，防抄
            emit(sheet, os.path.join(a.out, u"留出集盲测-%s.html" % who),
                 "holdout_blind_20260903_%s" % who.lower(), who)
        # 分工说明
        d = []
        d.append(u"# 留出集盲测 · 分工说明")
        d.append(u"")
        d.append(u"> 依据《留出集抽样与裁决-协议.md》v1.0。**本次为协议 §三「标注者：使用者本人」"
                 u"的一处偏离**，按协议 §六 记录如下。")
        d.append(u"")
        d.append(u"## 偏离项与理由")
        d.append(u"")
        d.append(u"- **偏离**：由 1 名标注者改为 %d 名，采用重叠设计" % n)
        d.append(u"- **理由**：单人 65 题约 2 小时；分工后每人约 45 分钟，"
                 u"且**公共集可产出标注者间一致率**——这是单人方案拿不到的证据")
        d.append(u"- **代价**：45 条独有题各只有 1 个人判。"
                 u"其可信度由公共集一致率间接佐证，须在报告中写明")
        d.append(u"")
        d.append(u"## 每人卷子构成")
        d.append(u"")
        d.append(u"| 卷子 | 公共 | 独有 | 诱饵 | 合计 |")
        d.append(u"|---|---|---|---|---|")
        for i in range(n):
            d.append(u"| **P%d** | %d | %d | %d | %d |"
                     % (i + 1, len(common), len(chunks[i]), len(decoys_it),
                        len(common) + len(chunks[i]) + len(decoys_it)))
        d.append(u"")
        d.append(u"## 开工前每人必读")
        d.append(u"")
        d.append(u"1. 先读《冲突判定标注规范》v1.0.1 **§五 判定流程**，再开始看题")
        d.append(u"2. 判断标准只有一句：**「后」是否让「前」里的某个说法不再成立**")
        d.append(u"3. 不确定的题，**在右边输入框写一句理由** —— 那是后面判边界的依据")
        d.append(u"4. **不要互相讨论**，一致率是这次分工的核心产出，讨论过就没了")
        d.append(u"5. 卷子自动保存，可以分几次做完；换浏览器会丢，尽量固定一台机器")
        d.append(u"")
        d.append(u"## 诱饵")
        d.append(u"")
        d.append(u"每份卷子都含 **%d 条诱饵**（来自冻结集已确认正例，明显该判冲突）。" % len(decoys_it))
        d.append(u"**某人诱饵没全中，该人这份作废重做**，不影响其他人。")
        d.append(u"")
        d.append(u"## 收卷后")
        d.append(u"")
        d.append(u"每人提交后页面会吐一段 JSON，收齐 %d 份合并。**公共集先算一致率，"
                 u"再定最终标签**（多数决；平局由项目负责人裁定并记录理由）。" % n)
        io.open(os.path.join(a.out, u"盲测分工说明.md"), "w", encoding="utf-8",
                newline="\n").write(u"\n".join(d) + u"\n")
        print(u"已出 %d 份卷子：公共 %d + 独有 %s + 诱饵 %d"
              % (n, len(common), "/".join(str(len(c)) for c in chunks), len(decoys_it)))
    else:
        emit(items, os.path.join(a.out, u"留出集盲测.html"), "holdout_blind_20260903")

    print(u"分层：C=%d B=%d A=%d 负例=%d" % (len(layers["C"]), len(layers["B"]),
                                            len(layers["A"]), len(neg)))
    print(u"抽中：C=%d B=%d A=%d 负例=%d + 诱饵 %d，共 %d 题"
          % (len(picked["C"]), len(picked["B"]), len(picked["A"]),
             len(picked["NEG"]), len(decoys), len(items)))
    for L in ("C", "B", "A"):
        if shortfall[L]:
            print(u"  ⚠️ %s 层缺 %d 条（池中仅 %d）" % (L, shortfall[L], len(layers[L])))
    print(u"\n产出：抽样清单.md（🔴 先提交）、留出集盲测.html")


_INLINE_TPL = u"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>留出集盲测裁决</title><style>
:root{--bg:#f5f7f5;--card:#fff;--ink:#1a1f1c;--body:#3d453f;--muted:#7a827c;
--line:#dee4de;--green:#2f6b57;--rust:#b0502a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--body);font-size:15px;
line-height:1.7;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 90px}
h1{font-size:24px;color:var(--ink);margin:0 0 6px}
.rule{background:#fff;border:1px solid var(--line);border-radius:6px;padding:16px 20px;margin:18px 0 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;margin-bottom:18px;overflow:hidden}
.card header{padding:10px 16px;border-bottom:1px solid var(--line);background:#fafbfa}
.num{font-family:ui-monospace,Consolas,monospace;font-weight:700;color:var(--green)}
.pair{display:grid;grid-template-columns:1fr 1fr}
.pair section{padding:14px 16px}
.pair section:first-child{border-right:1px solid var(--line);background:#fcfcfb}
h4{margin:0 0 8px;font-size:12px;letter-spacing:.1em;color:var(--muted)}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:13.5px;line-height:1.65;
font-family:inherit;max-height:340px;overflow:auto}
.card footer{display:flex;gap:16px;align-items:center;padding:12px 16px;border-top:1px solid var(--line);flex-wrap:wrap}
label{display:flex;gap:6px;align-items:center;cursor:pointer}
.note{flex:1;min-width:200px;padding:6px 10px;border:1px solid var(--line);border-radius:4px;font:inherit;font-size:13px}
.bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);
padding:12px 20px;display:flex;gap:16px;align-items:center;justify-content:center}
button{padding:9px 22px;border:0;border-radius:5px;background:var(--green);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button:disabled{background:#bbb;cursor:not-allowed}
#out{background:#fff;border:1px solid var(--line);border-radius:6px;padding:18px 20px;margin-top:24px;display:none}
textarea{width:100%;height:220px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.bad{color:var(--rust);font-weight:600}.ok{color:var(--green)}
@media(max-width:760px){.pair{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<h1>留出集盲测裁决</h1>
<div class="rule"><b>判断标准（《冲突判定标注规范》v1.0.1 §五）：「后」是否让「前」里的某个说法不再成立？</b>
<ul><li><b>冲突</b> —— 前说 A，后说「A 是错的 / 已过时 / 更正为 B」</li>
<li><b>不是冲突</b> —— 纯追加（前仍成立）／润色（信息没变）／部分推翻但核心用途未变／两段不是一件事</li></ul>
<b>注意</b>：本轮不显示时间戳，「同会话同天」那条排除规则由脚本事后套用，你只判语义。</div>
__CARDS__
<div id="out"></div></div>
<div class="bar"><span id="prog">0 / __N__</span><button id="go" disabled>提交</button></div>
<script>
const KEY=__KEY__, N=__N__;
const prog=document.getElementById('prog'), go=document.getElementById('go');
function upd(){const d=document.querySelectorAll('input[type=radio]:checked').length;
prog.textContent=d+' / '+N; go.disabled=(d<N);}
document.addEventListener('change',upd);
go.onclick=()=>{
const ans={};document.querySelectorAll('input[type=radio]:checked').forEach(r=>ans[+r.name.slice(1)]=r.value);
const notes={};document.querySelectorAll('.note').forEach((e,i)=>{if(e.value.trim())notes[i+1]=e.value.trim();});
let dOK=0,dTot=0;const rows=[];
KEY.forEach(k=>{const v=ans[k.n];
 if(k.decoy){dTot++;if(v==='conflict')dOK++;}
 else rows.push({id:k.id,layer:k.layer,verdict:v,note:notes[k.n]||''});});
const valid=(dOK===dTot);
let h='<h3>结果</h3><p>诱饵：<b class="'+(valid?'ok':'bad')+'">'+dOK+' / '+dTot+'</b>'+
 (valid?' —— 本轮有效':' —— <b class="bad">整轮作废，需重做</b>')+'</p>';
if(valid){
 const who=(KEY[0]&&KEY[0].who)||'', tag=who?('-'+who):'';
 h+='<p><b>请把结果文件发回</b>（不要发这个 html —— 答案存在浏览器里，不在文件里）：</p>'+
   '<p><button id="dl" style="background:#b0502a">下载结果 JSON</button>'+
   '<span style="margin-left:12px;color:#7a827c;font-size:13px">'+
   '文件名 裁决'+tag+'.json</span></p>'+
   '<details><summary style="cursor:pointer;color:#7a827c;font-size:13px">'+
   '下载不了？点开复制文本</summary>'+
   '<textarea readonly>'+JSON.stringify(rows,null,1)+'</textarea></details>';
 setTimeout(()=>{const b=document.getElementById('dl');if(!b)return;
  b.onclick=()=>{try{
   const blob=new Blob([JSON.stringify(rows,null,1)],{type:'application/json'});
   const u=URL.createObjectURL(blob), a=document.createElement('a');
   a.href=u; a.download='裁决'+tag+'.json'; document.body.appendChild(a);
   a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),1000);
  }catch(e){alert('下载失败，请展开下方文本框手动复制');}};},0);}
const o=document.getElementById('out');o.innerHTML=h;o.style.display='block';o.scrollIntoView({behavior:'smooth'});};

// ── 自动保存：每次改动写 localStorage，重开自动恢复 ──────────
const LSK='holdout_blind_20260903';
function _save(){try{
  const d={};
  document.querySelectorAll('input[type=radio]:checked').forEach(r=>d['r'+r.name]=r.value);
  document.querySelectorAll('.note').forEach((e,i)=>{if(e.value.trim())d['n'+(i+1)]=e.value;});
  localStorage.setItem(LSK,JSON.stringify(d));
}catch(e){}}
function _restore(){try{
  const d=JSON.parse(localStorage.getItem(LSK)||'{}');let n=0;
  Object.keys(d).forEach(k=>{
    if(k[0]==='r'){const el=document.querySelector('input[name="'+k.slice(1)+'"][value="'+d[k]+'"]');
                   if(el){el.checked=true;n++;}}
    else{const e=document.querySelectorAll('.note')[(+k.slice(1))-1];if(e)e.value=d[k];}});
  return n;
}catch(e){return 0;}}
document.addEventListener('change',_save);
document.addEventListener('input',_save);
(function(){const n=_restore();
  if(n>0){const b=document.createElement('div');
    b.style.cssText='background:#e4efea;border:1px solid #bcd4c9;border-radius:5px;padding:10px 16px;margin-bottom:18px;font-size:14px';
    b.innerHTML='已恢复上次进度：<b>'+n+'</b> 题。继续做完即可；'+
      '如需从头重来，按 F12 打开控制台执行 <code>localStorage.removeItem("'+LSK+'")</code> 后刷新。';
    const w=document.querySelector('.wrap');w.insertBefore(b,w.children[2]);}})();

upd();
</script></body></html>"""


if __name__ == "__main__":
    main()
