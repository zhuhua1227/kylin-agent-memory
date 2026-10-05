#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_reports.py —— 核对报告正文里的数字是否与测量记录一致。

═══ 为什么 reproduce_L1.py 不够，还需要这个 ═══════════════════════════

`reproduce_L1.py` 核对的是「records/*.json ↔ claims.json」——记录里的
数字，和我们声称的主张数字，是否一致。

但**从来没有人核对过「报告正文 ↔ records/*.json」**——报告里手打的那句
"P95 = 68.4ms"，是不是真的等于记录里存的那个数？独立评审抓到过一个真实
反例：`向量化常驻服务/说明报告.md` 的结论区（第一节）停留在被 5.3 节自己
推翻的旧数字（68.4ms/86%余量），而 5.3 节本身早已更新成重跑值
（84.9ms/83%余量）——`reproduce_L1.py` 对此完全没有察觉，因为它压根不读
报告正文，只读 claims.json 里手写的 expected 值。

这正是"JSON 能编"这条批判该反过来问自己的地方：如果连报告正文都可能
和记录对不上，读者凭什么信报告？本脚本补的就是这一环。

═══ 第二轮独立复核 R2-05 指出并已修复的三个结构性缺口 ═══════════════════

1. **`if "expected" not in c: continue` 结构性跳过全部 bound/ratio 主张**
   ——claims.json 里 9 条 bound + 5 条 ratio 共 14+ 条主张没有 `expected`
   字段，**全部延迟类主张都在里面**。复核员做过阴性验证：把
   `向量化常驻服务/说明报告.md` 结论区的重跑值原样改回被推翻的旧值
   （这正是该工具文件头第13行写的立项理由本身），旧版本脚本完全无察觉，
   输出与未篡改时一字不差、退出码0。现在改为对 bound/ratio 主张也求出
   实测值（复用 `reproduce_L1.py` 的 `check_bound`/`check_ratio`），核对
   该值是否印在报告里。

2. **永远 `return 0`，从不真正失败**。改错任意一处数字，旧版本只是把它
   从"找到匹配"移进"待人工确认"，退出码依旧是0，措辞依旧是"不代表一定
   不一致"——一个从不亮红灯的检查器和没有检查器信息量相同。现在：
   自我描述性统计（见下）核对不通过时，返回非零退出码。

3. **文档"自我描述自己"的统计数字完全没有覆盖**——"合计65条主张"
   "test_detectors 23/23通过"这类数字描述的是本项目自己的交付物规模，
   不对应任何 records/*.json 里的测量值，此前没有任何机制核对它们，
   本轮复核抓到的5处过期数字全部属于这一类、旧版本一条都抓不到。现在
   显式提取这类模式，与实时计算的真实值比较，不一致直接判定失败。

═══ 第四轮独立复核 R4-02：正文支「三轮都没有失败能力」，且盲区被写成契约 ═══

第四轮复核做了三个破坏性实验，本脚本**一个都没抓到**：

  · 实验 C：把 `向量化常驻服务/说明报告.md` **结论区**的 84.9ms 改成 68.4ms
    （即本文件第 13 行写的立项理由本身）→ 输出与未篡改时**逐字节相同**、退出码 0
  · 实验 D1：某个数字在文档里印了 11 处，只改其中 1 处 → **逐字节相同**、退出码 0
  · 实验 D3：把 0.9441 改成 0.1111，共 11 处、跨 5 个文件 → 两条主张确实
    掉进了"待人工确认"清单，**退出码仍然是 0**

两个根因，本轮都从结构上修掉：

  1. **`unmatched` 永不影响退出码**——`main()` 只在
     `self_describing_mismatches or dead_regexes` 时 return 1。于是正文支
     无论抓到什么，都只是换个地方打印。现在改成
     `unmatched_required` 直接进退出码，并把"哪些主张允许匹配不到"
     从"默认全体豁免"收窄成**必须逐条显式声明 `anchor_optional` 并写理由**。
  2. **子串匹配无位置感知**——它只能回答"这个数字有没有印在这份报告里"，
     回答不了"它有没有印在**该印的那个位置**上"。同一个数字在一份报告里
     常印七八处，改错其中一处，全文 grep 照样命中。现在给每条主张加
     `anchor`（文件 + 章节标题），只在该章节范围内找。

配套还修了两处同型问题：
  · 数字比对由裸子串改为**数字边界匹配**——旧写法里 0.9441 的变体 "0.9"
    会在 "0.917" 里假命中，等于给自己发免死金牌。
  · R4-14：`if truth is None: continue`（算不出真实值就跳过）属于
    "缺失即放行"，改为显式记账并计入退出码，只保留一处有名有姓的豁免。

═══ 第四轮独立复核 R4-11：跨主张算术矛盾无人覆盖 ═══════════════════════

C-202 的 `cases_found` 是 15（跟当前**代码**），而 C-234/235/242/250/251/252
硬编码 95 条语料（= 27+59+9，跟当前**交付产物**）。15+59+9 = 83 ≠ 95。
矛盾本身是**有意的**（产物未重新生成，见 `代码产物落差登记表.md`），错的是
两个口径混在一起写、且没有任何检查覆盖这条算术。现在：每条相关主张必须
声明 `corpus_basis`（属于哪一版口径），并由 `check_corpus_arithmetic()`
断言"各类知识单元条数之和 == 该口径的语料总条数"，两个口径分别与
**真实产物文件行数** / **records 里的实测值** 对钉。

═══ 做法 ═══════════════════════════════════════════════════════════

1. 遍历项目下全部 `*.md`（排除虚拟环境/`.git` 等目录）
2. 对每条 claims.json 主张：
   - `exact`/`close` 类：核对 `expected` 值是否在报告里出现
   - `bound`/`ratio` 类：先用 reproduce_L1.py 的核对函数算出实测值，
     再核对这个实测值是否在报告里出现
   - **只在 `anchor` 指定的章节范围内找**（R4-02），不是全文 grep
3. 找不到 → 除非该主张显式标了 `anchor_optional`（且写明理由），否则
   **判定失败**并计入退出码
4. 自我描述性统计（合计N条主张/test_detectors N/N/verify_reports N条/
   数据集N条）用正则提取报告里写的数字，与实时计算的真实值精确比较——
   这一类**能且必须**给出确定性的通过/失败判定，不是"待确认"
5. 跨主张算术一致性（R4-11）：语料口径必须自洽且与真实产物/记录对得上

⚠️ 定位要放对：这不是防造假的工具（能改文件的人也能同时改报告和记录，
让两边"看起来一致"），是防"我们自己抄错数字/改了代码忘了同步报告"的工具。
"""

import glob
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
CLAIMS_PATH = os.path.join(BASE, "claims.json")
sys.path.insert(0, BASE)

from measure import load_all_records  # noqa: E402
from reproduce_L1 import check_exact, check_close, check_bound, check_ratio  # noqa: E402

EXCLUDE_DIR_MARKERS = (".git", "__pycache__", "node_modules", ".idea")

CHECKERS = {"exact": check_exact, "close": check_close,
            "bound": check_bound, "ratio": check_ratio}


class Unavailable(object):
    """「算不出真实值」的显式载体（R4-14）。

    旧写法是 `if truth is None: continue`——算不出就跳过，属于教科书式的
    "缺失即放行"：test_detectors.py 跑挂了、记录文件缺了、正则取不到数，
    检查器一声不吭照常打印"全部一致"并 return 0。**一个在自己瞎了的时候
    还报绿灯的检查器，比没有检查器更危险**，因为它会让人以为查过了。

    现在把"算不出"变成一等公民，并且只留一处有名有姓的豁免：
      · fatal=True  —— 计入退出码（子进程失败/超时/记录缺失/正则取不到数）
      · fatal=False —— **只有递归保护这一种**：本次运行是 test_detectors.py
        起的子进程，而真实值本该由调用方自己算，此处结构上不可能算出来。
        显式打印、计入"本次运行未覆盖"，不计入退出码。
        不接受第二种非致命豁免——要加就得在这里写清楚为什么。
    """

    def __init__(self, reason, fatal=True):
        self.reason = reason
        self.fatal = fatal


def occurs_as_number(text, variant):
    """判断 `variant` 是否作为一个**完整的数**出现在 text 里。

    🔴 为什么不能直接 `variant in text`：`format_number_variants` 会为
    0.9441 生成 "0.9"（一位小数写法）这个变体，而报告里遍地是 0.917、
    0.9368 之类的数——裸子串匹配下 "0.9" 必然命中，于是这条主张**永远
    找得到**，等于给自己发了张免死金牌。第四轮实验 D3 把 11 处 0.9441
    全改成 0.1111，C-050 依旧算"命中"，靠的就是这个假命中。
    左右两侧都不许紧邻数字（左侧连小数点也不许，防 "0.917" 里截出 "0.9"）。
    """
    pat = r"(?<![0-9.])" + re.escape(str(variant)) + r"(?![0-9])"
    return re.search(pat, text) is not None


def format_number_variants(value):
    """一个数字在报告里可能出现的几种常见写法。

    ⚠️ 第四轮加锚点时实测补齐的三种写法（不补的话，锚点一收窄，本来印得
    好好的数字反而全成了"找不到"——那是变异检测能力的假阳性，会逼着人
    把主张标成 anchor_optional，等于把刚收窄的口子又开回去）：
      · 两位小数百分比：`3.01%`（C-237）、`64.50%`/`86.98%`/`91.72%`
        （C-250~252 对 §九 语料规模表）——本项目报告里两位小数百分比很常见
      · 百分点写法 `pp`：`+25.3pp`（C-232）——差值类指标一律写 pp 不写 %
      · 整数值的浮点：bound/ratio 算出的实测值恒为 float，`1.0` 对应报告里
        写的"合计 1 条"（C-215）
    """
    variants = set()
    if isinstance(value, float):
        variants.add(f"{value}")
        # 🔴 只有 |value| ≥ 1 时才认「截断到一/两位小数」这种写法。
        # 实测（第四轮加锚点后重做实验 D1）：把 `端到端最小管线/说明报告.md`
        # §5.2 那一处 0.9441 改成 0.1111，检查器**仍然通过**——因为同一节的
        # 散文里有一句"相似度 0.94，明显高于其他"，而 `.2f` 变体恰好是
        # "0.94"。锚点已经收窄到该印的那一节了，仍被一个口语化的约数救了场。
        # 这正是本文件 occurs_as_number 注释里骂的那张免死金牌的另一半：
        # 那边修的是"0.9 会在 0.917 里假命中"，这边是"0.9 自己就印在文中"。
        # 判据：小于 1 的量（相似度、召回率、占比）在本项目报告里的**权威
        # 写法**只有两种——完整小数（0.9441）或百分比（94.4%）；"0.94" 是
        # 复述不是权威值。而 ≥1 的量（84.9ms、15.84ms、2.39×）权威写法本来
        # 就是一两位小数，不能砍。
        if abs(value) >= 1:
            variants.add(f"{value:.1f}")
            variants.add(f"{value:.2f}")
        variants.add(f"{value:.4f}")
        if value == int(value):
            variants.add(str(int(value)))
        # 百分比写法：0.9 -> 90%/90.0%；差值类指标常写成百分点 pp
        pct = value * 100
        variants.add(f"{pct:.0f}%")
        variants.add(f"{pct:.1f}%")
        variants.add(f"{pct:.2f}%")
        variants.add(f"{pct:.1f}pp")
        variants.add(f"{pct:.2f}pp")
    elif isinstance(value, int):
        variants.add(str(value))
        # 千分位写法：14588 -> 14,588
        variants.add(f"{value:,}")
    else:
        variants.add(str(value))
    return variants


def report_path_for(claim):
    """claims.json 的 report 字段常带"（第X节）"这类后缀，只取文件名部分
    定位真实文件路径——报告文件散落在各个子目录，用文件名在全项目内搜。"""
    raw = claim.get("report", "")
    # 形如 "知识检索/说明报告.md（B2 挖掘设计）" -> 取 .md 结尾之前的部分
    m = re.match(r"([^（(]+\.md)", raw)
    return m.group(1).strip() if m else None


def find_report_file(rel_path):
    candidate = os.path.join(RAG, rel_path)
    if os.path.exists(candidate):
        return candidate
    # 兜底：按文件名在全项目内搜（report 字段有时只写文件名不写完整路径）
    name = os.path.basename(rel_path)
    for root, dirs, files in os.walk(RAG):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_MARKERS]
        if name in files:
            return os.path.join(root, name)
    return None


def _default_read_report(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# 内部审查文档不是交付报告——它按时间顺序记录"当时发现XX条、后来改成YY条"
# 这类历史叙述，是正常内容不是过期数字，拿"当前真实值"去核对它注定全是
# 假阳性（它本来就在写"以前是多少"）。且该文件本身 gitignore 排除、不
# 随项目分发，评委看不到，扫它没有意义。
_SKIP_REPORT_NAMES = {"独立评审报告与整改清单.md"}


def all_report_files():
    files = []
    for root, dirs, fs in os.walk(RAG):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_MARKERS]
        for fn in fs:
            if fn.endswith(".md") and fn not in _SKIP_REPORT_NAMES:
                files.append(os.path.join(root, fn))
    return files


# ═════════════════════════════════════════════════ 章节锚点（R4-02 新增）

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def split_markdown_sections(text):
    """把一份 markdown 切成章节，返回 [{'level','title','body'}]。

    一个章节的范围 = 从它自己的标题行，到**下一个层级不高于它的标题**为止
    （所以 `## 六、…` 这一节包含它下面的 `### 6.1`/`### 6.2`）。这样锚点
    既能写粗（写到二级标题）也能写细（直接写 6.1），由主张自己决定精度。

    ⚠️ 必须跳过围栏代码块里的行。本项目多份报告的「复现步骤」一节整段是
    bash，里面 `# 1. 编译常驻服务`、`# 前提：环境已恢复` 这种注释行，长得
    和一级标题一模一样。不跳过的话章节边界会被这些注释切碎，锚点范围整个
    错位——而且错得很隐蔽：范围变小只会让匹配失败，看起来像"报告数字不对"，
    实际是切分器坏了。这一条是写这个函数时**先想到再验证**的，不是踩了才补。
    """
    lines = text.split("\n")
    heads = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            heads.append((len(m.group(1)), m.group(2).strip(), i))

    sections = []
    for idx, (level, title, start) in enumerate(heads):
        end = len(lines)
        for level2, _t2, start2 in heads[idx + 1:]:
            if level2 <= level:
                end = start2
                break
        sections.append({"level": level, "title": title,
                         "body": "\n".join(lines[start:end])})
    return sections


def _norm_title(s):
    """标题归一化：去掉 markdown 修饰与全部空白，只留可比对的字面。
    这样锚点写 `6.1` 就能命中 `### 6.1 「知识检索响应时间 ≤500ms」——…`，
    不必把整行标题原样抄进 claims.json（抄了反而更脆：标题一改锚点就死）。"""
    return re.sub(r"[`*_#\s]+", "", s or "")


def section_body(text, section_name):
    """取出 `section_name` 指向的章节正文；命中不到返回 None（= 死锚点）。

    命中分三档，取**最强的那一档**：完全相等 > 前缀 > 包含。分档是为了让
    短锚点（`6.1`）不至于把 `16.1` 也一起吃进来——包含档只在前两档都空时
    才启用。多个同档命中则取并集（同一个标题在一份文档里出现两次是合法的，
    比如"附"）。
    """
    want = _norm_title(section_name)
    if not want:
        return None
    exact, prefix, contains = [], [], []
    for sec in split_markdown_sections(text):
        t = _norm_title(sec["title"])
        if t == want:
            exact.append(sec["body"])
        elif t.startswith(want):
            prefix.append(sec["body"])
        elif want in t:
            contains.append(sec["body"])
    for tier in (exact, prefix, contains):
        if tier:
            return "\n".join(tier)
    return None


def claim_anchors(claim):
    """把 `anchor` 字段规整成锚点列表。允许写成单个 dict 或 dict 列表——
    同一个数字常常必须同时印在结论区和详情节（第 13 行那个真实反例就是
    结论区停在旧值、详情节已更新），两处都锚才拦得住。"""
    a = claim.get("anchor")
    if a is None:
        return []
    return list(a) if isinstance(a, list) else [a]


def anchor_chunks(claim, read_report):
    """返回 ([(报告路径, 章节正文), …], 问题列表)。

    **这是 R4-02 的核心**：范围一旦收窄到该印的位置，"错值恰好在文中别处
    出现"就不再免检。没有 `anchor` 时返回空列表，由调用方决定退路
    （`check_anchor_health()` 另行把"该有锚点却没有"判失败）。

    🔴 为什么返回**分块列表**而不是拼好的一整段（第四轮整改时改的）：
    多锚点必须是 **AND**——同一个数字常常必须同时印在结论区和详情节，
    `claim_anchors` 的注释写的也是"两处都锚才拦得住"。但原实现把几段
    `"\\n".join()` 成一整块再做子串匹配，语义就成了 **OR**：结论区被改错、
    详情节还对，拼起来照样命中。那正是本文件第 47 行实验 C 的形态——
    多写一个锚点反而让检查更松，与注释所声称的完全相反。
    """
    problems = []
    chunks = []
    for a in claim_anchors(claim):
        rel = a.get("file") or report_path_for(claim)
        path = find_report_file(rel) if rel else None
        if not path:
            problems.append((claim.get("claim_id"), "锚点文件定位不到：%s" % rel))
            continue
        body = section_body(read_report(path), a.get("section", ""))
        if body is None:
            problems.append((claim.get("claim_id"),
                             "锚点章节在 %s 里不存在：%s" % (rel, a.get("section"))))
            continue
        chunks.append((path, body))
    return chunks, problems


def resolve_anchor_text(claim, read_report):
    """`anchor_chunks` 的拼接版，仅供只关心"锚点能不能解析"的调用方使用。
    ⚠️ 不要拿它做匹配——拼接会把多锚点的 AND 语义悄悄变成 OR，见上。"""
    chunks, problems = anchor_chunks(claim, read_report)
    if not chunks:
        return None, problems
    return "\n".join(body for _p, body in chunks), problems


def check_anchor_health(claims, read_report=None):
    """锚点阴性自检——和 R3-11 的正则自检同一个道理：**指向不存在的东西的
    锚点，和没有锚点，在结果上无法区分**（两者都会让匹配范围变成整份全文
    或空），必须单独报出来让人看一眼。

    四种判失败的情形：
      ① 该有锚点却没写（且没标 anchor_optional）——否则又退回全文 grep
      ② 锚点文件定位不到
      ③ 锚点章节在该文件里找不到（标题被改了/写错了）
      ④ 标了 anchor_optional 却没写 anchor_optional_reason——豁免必须
         逐条讲得出理由，否则这个字段就是"默认全体豁免"改了个名字回来
    """
    if read_report is None:
        read_report = _default_read_report
    problems = []
    for c in claims:
        if c.get("anchor_optional"):
            if not str(c.get("anchor_optional_reason") or "").strip():
                problems.append((c.get("claim_id"),
                                 "标了 anchor_optional 却没写 anchor_optional_reason"
                                 "（无理由的豁免=默认豁免换个名字）"))
            continue
        anchors = claim_anchors(c)
        if not anchors:
            problems.append((c.get("claim_id"),
                             "未配置 anchor，且未声明 anchor_optional"
                             "（无锚点=退回全文grep=R4-02 那个盲区）"))
            continue
        _text, probs = resolve_anchor_text(c, read_report)
        problems.extend(probs)
    return problems


# ═════════════════════════════════════════════════ 跨主张算术一致性（R4-11）

def check_corpus_arithmetic(claims_doc):
    """断言「各类知识单元条数之和 == 语料总条数」，且两个口径各自对钉。

    🔴 第四轮 R4-11：C-202 说案例 15 条（跟代码），C-234 等六条说语料共
    95 条（= 27+59+9，跟产物）。15+59+9 = 83 ≠ 95，而**没有任何检查覆盖
    这条跨主张的算术**——每条主张单看都对得上自己那条 record，合起来是矛盾的。

    矛盾本身是有意的：产物重新生成会改 doc_id 编号，作废已外部锚定的
    v1.3.0 冻结评测集，所以要等整条链路一起重跑（见 `代码产物落差登记表.md`）。
    错的不是"数字不一样"，是**两个口径混在一起写、且谁都没在检查**。

    所以修法不是把 15 改成 27，而是让每条主张说清自己描述的是哪一版：
      · delivered_v1.3.0 —— 当前交付产物，与三个 .jsonl 的**真实行数**对钉
      · current_code     —— 当前代码重跑会得到的条数，与 **records** 对钉
    再对每一版分别验算 sum(parts) == total。这样"15 与 27 并存"是被声明的
    事实，而"某一版内部算术对不上"或"声明与真实产物/记录对不上"会立刻报红。
    """
    problems = []
    bases = claims_doc.get("_语料口径")
    if not isinstance(bases, dict):
        return [("_语料口径", "claims.json 缺 _语料口径 声明块，"
                             "跨主张算术无从校验（R4-11 要求）")]

    totals = {}
    for name, spec in bases.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue
        parts = spec.get("parts") or {}
        total = spec.get("total")
        totals[name] = total
        s = sum(parts.values())
        if s != total:
            problems.append((name, "各类知识单元条数之和 %d != 声明的语料总数 %s"
                                   "（parts=%s）" % (s, total, parts)))
        # 一个既不与产物文件、也不与记录对钉的口径，只能自证"我自己加得对"，
        # 属于典型的摆设字段。允许存在（有的口径确实既无产物也无记录，比如
        # "当前代码重跑会得到多少"），但必须自己说清为什么没法对钉。
        if not spec.get("files") and not spec.get("records") \
                and not str(spec.get("unpinned_reason") or "").strip():
            problems.append((name, "该口径既没有 files 也没有 records 可对钉，"
                                   "且未写 unpinned_reason（无人校验的字段=摆设）"))
        # 与真实产物文件行数对钉
        for key, rel in (spec.get("files") or {}).items():
            p = os.path.join(RAG, *rel.split("/"))
            if not os.path.exists(p):
                problems.append((name, "产物文件不存在，无法核对：%s" % rel))
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                n = sum(1 for line in f if line.strip())
            if parts.get(key) != n:
                problems.append((name, "声明 %s=%s，但 %s 实际有 %d 行"
                                       % (key, parts.get(key), rel, n)))
        # 与 records 里的实测值对钉
        records = None
        for key, ref in (spec.get("records") or {}).items():
            if records is None:
                records = load_all_records()
            rec_id, metric = ref[0], ref[1]
            rec = records.get(rec_id)
            val = None
            if rec:
                mv = rec["metrics"].get(metric)
                val = mv["value"] if isinstance(mv, dict) else mv
            if val is None:
                problems.append((name, "记录 %s.%s 取不到值，无法核对 %s"
                                       % (rec_id, metric, key)))
            elif parts.get(key) != val:
                problems.append((name, "声明 %s=%s，但记录 %s.%s = %s"
                                       % (key, parts.get(key), rec_id, metric, val)))

    # 每条声明了口径的主张，它引用的语料总数必须来自它自己那一版
    for c in claims_doc.get("claims", []):
        basis = c.get("corpus_basis")
        if not basis:
            continue
        if basis not in totals:
            problems.append((c.get("claim_id"), "corpus_basis 指向未声明的口径：%s" % basis))
            continue
        used = c.get("corpus_total_used")
        if used is None:
            continue
        if used != totals[basis]:
            problems.append((c.get("claim_id"),
                             "引用语料总数 %s，但所声明口径 %s 的总数是 %s"
                             % (used, basis, totals[basis])))
        elif not occurs_as_number(c.get("text", "") + c.get("note", ""), used):
            # 散文里必须真的出现这个总数，否则字段是摆设：机器校验的和
            # 评委读到的又成了两回事（同 R3-17 的教训）
            problems.append((c.get("claim_id"),
                             "corpus_total_used=%s 但主张散文里没有这个数" % used))
    return problems


# ═════════════════════════════════════════════════════ 自我描述性统计（R2-05 新增）

def _count_detector_cases():
    """跑一次 test_detectors.py，从输出里数"合计 N 项检测能力验证"这一行。
    直接复用同一份检测套件，不另外维护一份计数逻辑——避免这里的数字和
    test_detectors.py 自己打印的数字，两边各写一遍又不同步。

    ⚠️ 递归保护：test_detectors.py 自己有一条用例会把 verify_reports.py
    当子进程跑一遍（验证"改错数字后退出码非零"）。那条用例跑起来的
    verify_reports.py 如果又在这里反过来跑一次 test_detectors.py，
    test_detectors.py 又会再跑一次 verify_reports.py……无限递归下去。
    用环境变量在"从 test_detectors.py 的用例里被调用"这条链路上做标记，
    标记存在时直接跳过这一步（返回None，按"算不出真实值"处理，不误判），
    链路只断这一环，不影响正常独立运行 `python verify_reports.py` 时的
    完整功能。
    """
    if os.environ.get("VERIFY_REPORTS_NO_RECURSE"):
        # ★唯一的非致命"算不出"★ 见 Unavailable 的注释：本次运行是
        # test_detectors.py 起的子进程，真实值本该由调用方自己算，
        # 这里结构上不可能算出来，不是故障。
        return Unavailable("递归保护生效（本次是 test_detectors.py 的子运行）",
                           fatal=False)
    try:
        out = subprocess.run(
            [sys.executable, os.path.join(BASE, "test_detectors.py")],
            cwd=BASE, capture_output=True, text=True,
            # ⚠️ 2026-08-15 由 180 调到 900：R4-17 的用例要在临时副本里把
            # reproduce_L1.py 完整跑四遍（删本目录脚本/删旁目录脚本/整个
            # 旁目录不在/空变异基线），test_detectors 的总耗时从约 90s 涨到
            # 约 260s，原来的 180s 会超时 → 被 R4-14 那条"算不出来就报红"
            # 判成失败。**超时值必须留出真实耗时的余量**，否则一条正确的
            # 检查会因为跑得慢而变成假红，比不检查更糟——假红看多了会让人
            # 习惯性忽略红字。
            encoding="utf-8", errors="replace", timeout=900)
    except Exception as e:
        # R4-14：这里原来 return None → 上游 continue → 检查器在自己瞎了的
        # 时候报绿灯。test_detectors.py 跑不起来本身就是必须有人处理的事。
        return Unavailable("test_detectors.py 跑不起来：%r" % (e,))
    m = re.search(r"合计\s*(\d+)\s*项检测能力验证", out.stdout)
    if not m:
        return Unavailable("test_detectors.py 输出里取不到「合计 N 项检测能力"
                           "验证」（returncode=%s）" % out.returncode)
    return int(m.group(1))


def _count_claims():
    with open(CLAIMS_PATH, encoding="utf-8") as f:
        return len(json.load(f)["claims"])


def _count_checkable_claims():
    """⚠️ 必须和 main() 打印的"可核对主张：N 条"**完全同口径**，否则这条
    自我描述检查会拿一个口径去核对另一个口径写出来的文档数字，制造假红灯。
    第三轮实测踩到过：本函数算 72、main() 打印 69，差在**能不能定位到对应
    的报告文件**。当时的修法是把那一步在这里再抄一遍——**抄一遍就是病根
    本身**（同一个定义写在两处，迟早再次分叉；第四轮加锚点后口径又变了一次：
    锚点可以指向 `report` 字段之外的文件，于是"能不能定位"的判据也跟着变）。
    第四轮改为直接调用 main() 用的那个函数，一个定义只写一处。"""
    with open(CLAIMS_PATH, encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    found, unmatched, _no_report, _n = match_claims_to_reports(
        claims, load_all_records())
    return found + len(unmatched)


def _count_dataset_records():
    records = load_all_records()
    rec = records.get("M-101")
    if not rec:
        # R4-14：记录缺失是"证据没了"，不是"这次不用查"
        return Unavailable("records/M-101.json 缺失，数据集规模无从核对")
    m = rec["metrics"].get("total_records")
    v = m["value"] if isinstance(m, dict) else m
    if v is None:
        return Unavailable("M-101 里没有 total_records 指标")
    return v


# 正则 -> 取真实值的函数。捕获组必须恰好一个，且是待比较的数字。
# 🔴 第三轮独立复核 R3-11：这几条正则是**照着 5 个已知实例写的，结果一个
# 都没命中**——`可复现性说明.md` 里那 5 处过期数字（"56 条主张"原地未动，
# 还新增 2 处），跑完却报"自我描述性统计核对：5 处…全部一致"。用探针打出
# 那 5 处命中的其实是"3 个本来就对的数字 + 2 条历史豁免"。
# 根因：`[^\d]{0,10}` 的窗口太窄，中文文案里关键词和数字之间常隔着二三十
# 个汉字（"`test_detectors.py`（变异测试，验证脱敏正则/……）已完成并跑通：
# 30/30 通过"）。窗口放宽到 {0,60}。
# **更根本的教训**：写完这些正则时没做"能不能真抓到那 5 处"的阴性验证——
# 做了的话第一次运行就会发现。下面的 _self_test_regexes() 就是把这件事
# 固化成启动自检：每条正则必须在当前文档集里至少命中一次，命中 0 次
# 说明正则写错了，而不是"文档里恰好没有这个数字"。
SELF_DESCRIBING_PATTERNS = [
    (r"合计\s*\**\s*(\d+)\s*\**\s*条(?:数字)?主张", _count_claims, "claims.json 主张总数"),
    (r"claims\.json[^\d]{0,20}(\d+)\s*条", _count_claims, "claims.json 主张总数"),
    # ⚠️ 中间不能写 [^0-9]：实际文案是"…验证脱敏正则/质检函数/**K-1**过滤/
    # …已完成并跑通：30/30 通过"，中间夹着 `K-1` 的数字 1，`[^0-9]` 跨不
    # 过去，于是这条正则一次都命中不了——正是启动自检刚刚抓到的那条。
    # 改用 [\s\S] 放宽字符类，同时用尾部的"通过"把语义锁死，避免误命中
    # 别处的任意 "N/M" 形式。
    (r"test_detectors[\s\S]{0,120}?(\d+)\s*[/／]\s*\d+\s*(?:通过|passed)",
     _count_detector_cases, "test_detectors.py 检测用例总数"),
    (r"verify_reports[^0-9]{0,60}?(\d+)\s*条", _count_checkable_claims,
     "verify_reports.py 可核对主张数"),
    # ⚠️ 中间只能是空白字符（\s*，不能用 [^\d]{0,N} 这种宽松写法）——第一次
    # 实测跑这个函数时，宽松版本命中了"已对最终数据集做了人工抽样检查
    # （30条随机样本…"这种描述子集/抽样的正常句子，把"30"错当成数据集
    # 总数，产生假阳性。数字必须紧跟在"数据集"后面（最多隔个空格），
    # 才认为这是在陈述"数据集总数是多少"，而不是提到"数据集"这个词之后
    # 十个字里随便出现的另一个数字。
    (r"数据集\s{0,2}([\d,]+)\s*条", _count_dataset_records, "数据集记录总数"),
    (r"合法记录总数[：:]\s*([\d,]+)", _count_dataset_records, "合法记录总数"),
]


# 已知的、有意保留的历史快照数字——不是过期未同步，是本项目一贯的做法：
# 报告里"真实输出"代码块记录的是当时那次运行的原始结果，即便后续数字
# 变了，这段历史记录本身不应该被"修正"（勘误的价值在于留痕，不在于让
# 旧数字消失）。这两处是本函数第一次实测跑出来才发现的真实例子，
# 若不排除，每次运行都会对着永远不会变的历史记录报错，把工具变成
# "总是红灯所以没人看"——比没有检查器更差。排除必须窄而具体
# （文件名+具体数值），不能用"看起来像历史记录就跳过"这种模糊规则，
# 否则又会把真正过期的数字也一起放过。
_KNOWN_HISTORICAL_EXEMPTIONS = {
    ("说明报告.md", 14047),  # 多源数据整合/说明报告.md §十五：首版merge真实输出快照
    ("说明报告.md", 14588),  # 多源数据整合/说明报告.md §十六：第二版merge真实输出快照
}


def self_test_regexes(all_md_texts):
    """启动自检：每条自我描述性正则必须在当前文档集里**至少命中一次**。

    🔴 R3-11 的根治手段。命中 0 次只有两种可能：正则写错了，或文档里
    真的一处都没提这个统计量——前者是缺陷，后者说明这条正则已无对象、
    该删。两种情况都需要人看一眼，而不是继续报"全部一致"。
    第三轮复核实测：5 条正则一条都没命中，工具却报告"全部一致"——
    **这是三轮里唯一确认能失败的检查，恰好绕开了它被创建来抓的那 5 个
    实例。** 写完时做一次阴性验证就能发现。
    """
    dead = []
    for pattern, _get_truth, label in SELF_DESCRIBING_PATTERNS:
        if not any(re.search(pattern, t) for t in all_md_texts):
            dead.append((label, pattern))
    return dead


def check_self_describing(content, path):
    """在一份报告文本里找"文档描述自己"的数字模式，与实时算出的真实值比较。
    返回 (mismatches, checked_count)。

    mismatches 里两类条目，靠 `kind` 区分（保持返回值仍是二元组，避免调用方
    跟着改签名）：
      · kind="mismatch"    —— 文档写的数与真实值不符
      · kind="unavailable" —— **真实值算不出来**（R4-14）。旧版这里是
        `continue`，检查器瞎掉时照样报"全部一致"。现在显式记账，
        `fatal=True` 的进退出码。
    """
    mismatches = []
    checked = 0
    for pattern, get_truth, label in SELF_DESCRIBING_PATTERNS:
        for m in re.finditer(pattern, content):
            raw = m.group(1).replace(",", "")
            try:
                claimed = int(raw)
            except ValueError:
                continue
            truth = get_truth()
            if isinstance(truth, Unavailable):
                mismatches.append({
                    "kind": "unavailable", "path": path, "label": label,
                    "pattern": pattern, "claimed": claimed, "truth": None,
                    "reason": truth.reason, "fatal": truth.fatal,
                    "context": content[max(0, m.start() - 20):m.end() + 10],
                })
                continue
            checked += 1
            if claimed != truth:
                if (os.path.basename(path), claimed) in _KNOWN_HISTORICAL_EXEMPTIONS:
                    continue
                mismatches.append({
                    "kind": "mismatch", "fatal": True,
                    "path": path, "label": label, "pattern": pattern,
                    "claimed": claimed, "truth": truth,
                    "context": content[max(0, m.start() - 20):m.end() + 10],
                })
    return mismatches, checked


def match_claims_to_reports(claims, records, read_report=None):
    """把每条主张的数字拿去报告正文里找，返回 (found, unmatched, no_report,
    bound_ratio_count)。

    🔴 第三轮独立复核 R3-35：这段逻辑原先整个埋在 `main()` 里，于是
    `test_detectors.py` **只覆盖得到自我描述那一支**，正文匹配这一支
    （被复核实验 C/D1/D3 打穿的恰恰是它）一条用例都没有。抽成函数，
    并把"怎么读到报告正文"做成可注入的 `read_report`，测试就能拿合成的
    主张与合成的正文直接驱动它，不必去动交付文件。

    ⚠️ 这一支的能力边界（**第四轮 R4-02 已收窄，但仍有边界**）：
      · 主张写了 `anchor` → 只在该章节范围内找，且多锚点是 AND
        （每一个锚点章节都必须出现该数字）。"错值恰好在文中别处出现"
        在锚点范围外的情况下不再免检。
      · 主张没写 `anchor` → 退回整份报告全文，仍是老的子串匹配，
        "写错但错值在别处出现过"依旧抓不到。所以 `check_anchor_health()`
        把"没有锚点且没显式豁免"直接判失败，堵住这条退路。
      · 匹配的是**数字**不是语义：数字印对了、但上下文写错了，抓不到。
    """
    if read_report is None:
        def read_report(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

    found = 0
    unmatched = []
    no_report = []
    bound_ratio_count = 0

    for c in claims:
        # R4-02：锚点优先。锚点可以指向 `report` 字段之外的文件
        # （C-250~252 的 report 指的是脚本 .py，而数字印在召回率实测报告里），
        # 所以先解析锚点，解析得出就用锚点，解析不出才退回整份报告全文。
        chunks, _anchor_problems = anchor_chunks(c, read_report)
        rel_path = report_path_for(c)
        report_file = find_report_file(rel_path) if rel_path else None
        if not chunks:
            if not report_file:
                no_report.append(c)
                continue
            chunks = [(report_file, read_report(report_file))]
        display_path = report_file or chunks[0][0]

        if c.get("check") in ("exact", "close") and "expected" in c:
            expected = c["expected"]
        elif c.get("check") in ("bound", "ratio"):
            # R2-05：bound/ratio 主张没有 expected，改为先算出实测值，
            # 核对这个实测值是否印在报告里——这是此前被结构性跳过的一类
            checker = CHECKERS.get(c["check"])
            ok, _, actual = checker(c, records) if checker else (None, None, None)
            if actual is None:
                no_report.append(c)
                continue
            expected = actual
            bound_ratio_count += 1
        else:
            no_report.append(c)
            continue

        variants = format_number_variants(expected)
        # R4-02：① 数字边界匹配，不是裸子串（否则 0.9441 的变体 "0.9" 会在
        # "0.917" 里假命中，等于免死金牌）；② 多锚点取 AND——**每一个**
        # 锚点章节都必须出现该数字，少一处就算没匹配上。
        if all(any(occurs_as_number(body, v) for v in variants)
               for _p, body in chunks):
            found += 1
        else:
            unmatched.append((c, display_path, variants, expected))

    return found, unmatched, no_report, bound_ratio_count


def main():
    with open(CLAIMS_PATH, "r", encoding="utf-8") as f:
        claims_doc = json.load(f)
    claims = claims_doc["claims"]
    records = load_all_records()

    found, unmatched, no_report, bound_ratio_count = \
        match_claims_to_reports(claims, records)

    # R4-02 ②：unmatched 必须进退出码。豁免不再是"默认全体"，而是逐条
    # 显式声明 anchor_optional + 写理由（理由缺失由 check_anchor_health 判失败）。
    unmatched_required = [t for t in unmatched if not t[0].get("anchor_optional")]
    unmatched_exempt = [t for t in unmatched if t[0].get("anchor_optional")]
    anchor_problems = check_anchor_health(claims)
    corpus_problems = check_corpus_arithmetic(claims_doc)

    # R2-05：自我描述性统计——文档"合计N条主张"这类描述自己规模的数字，
    # 与实时计算的真实值精确比较，能给出确定性的通过/失败判定
    self_describing_mismatches = []
    self_describing_checked = 0
    all_md_texts = []
    for path in all_report_files():
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        all_md_texts.append(content)
        mismatches, checked = check_self_describing(content, path)
        self_describing_mismatches.extend(mismatches)
        self_describing_checked += checked

    # R3-11：正则阴性自检——一条都没命中的正则等于不存在，必须报出来
    dead_regexes = self_test_regexes(all_md_texts)

    print("=" * 70)
    print("verify_reports.py —— 报告正文 vs 测量记录 一致性核对")
    print("=" * 70)
    print(f"\n可核对主张：{found + len(unmatched)} 条"
          f"（含 {bound_ratio_count} 条 bound/ratio 类，R2-05 前结构性跳过）")
    print(f"  在锚点章节里找到匹配：{found} 条")
    print(f"  {len(unmatched_required)} 条**没匹配上且未获豁免 → 判失败**（R4-02）")
    print(f"  {len(unmatched_exempt)} 条没匹配上但已显式声明 anchor_optional")
    if no_report:
        print(f"  {len(no_report)} 条无法定位报告文件或算不出实测值，跳过")

    if unmatched_required:
        print(f"\n✗ 未匹配（计入退出码）：")
        for c, report_file, variants, expected in unmatched_required:
            print(f"  [{c['claim_id']}] {c['text'][:70]}")
            print(f"      报告文件：{os.path.relpath(report_file, RAG)}")
            print(f"      锚点：{c.get('anchor')}")
            print(f"      期望值 {expected!r}，尝试过的写法：{sorted(variants)}")

    if unmatched_exempt:
        print(f"\n已声明豁免（anchor_optional，不计入退出码）：")
        for c, _rf, _v, expected in unmatched_exempt:
            print(f"  [{c['claim_id']}] 期望值 {expected!r} —— "
                  f"{c.get('anchor_optional_reason', '')[:100]}")

    print(f"\n锚点健康自检（R4-02）：{len(anchor_problems)} 处问题")
    for cid, msg in anchor_problems:
        print(f"  ✗ [{cid}] {msg}")

    print(f"跨主张语料算术（R4-11）：{len(corpus_problems)} 处问题")
    for cid, msg in corpus_problems:
        print(f"  ✗ [{cid}] {msg}")

    # R4-14：`Unavailable.fatal` 此前被 check_self_describing 老老实实算了
    # 出来、却**没有任何调用方读它**——递归保护那条唯一的非致命豁免于是
    # 照样把退出码打成 1。一个算出来没人用的字段，和没写是一回事。
    real_mismatches = [m for m in self_describing_mismatches
                       if m.get("kind") != "unavailable"]
    unavailable_fatal = [m for m in self_describing_mismatches
                         if m.get("kind") == "unavailable" and m.get("fatal")]
    unavailable_ok = [m for m in self_describing_mismatches
                      if m.get("kind") == "unavailable" and not m.get("fatal")]

    print(f"\n自我描述性统计核对：{self_describing_checked} 处（合计条数/"
          f"检测用例数/数据集规模等文档自称的数字）")
    if real_mismatches:
        print(f"  ✗ {len(real_mismatches)} 处与实时计算的真实值不符：")
        for mm in real_mismatches:
            print(f"    [{os.path.relpath(mm['path'], RAG)}] {mm['label']}：")
            print(f"      文档写的是 {mm['claimed']}，实测应为 {mm['truth']}")
            print(f"      上下文：…{mm['context']}…")
    else:
        print(f"  全部一致")
    for mm in unavailable_fatal:
        print(f"  ✗ 真实值算不出（计入退出码）[{mm['label']}]：{mm['reason']}")
    for mm in unavailable_ok:
        print(f"  · 本次运行未覆盖（有名有姓的豁免，不计入退出码）"
              f"[{mm['label']}]：{mm['reason']}")

    print(f"\n正则阴性自检（R3-11）：{len(SELF_DESCRIBING_PATTERNS)} 条自我描述"
          f"正则，其中 {len(dead_regexes)} 条在全部文档里一次都没命中")
    if dead_regexes:
        print(f"  ✗ 以下正则从未命中，疑似写错（命中0次和'文档里没这个数字'"
              f"在结果上无法区分，必须人看一眼）：")
        for label, pat in dead_regexes:
            print(f"    [{label}] {pat}")
    else:
        print(f"  全部正则都至少命中一次，说明它们确实在检查东西")

    print(f"\n本脚本的能力边界：锚点把匹配范围收窄到「该印的那个章节」，但它")
    print(f"匹配的仍是**数字**不是语义——数字印对了、上下文写错了，它抓不到；")
    print(f"没有锚点的主张退回全文匹配，那一段仍是老的弱判据（所以「没锚点」")
    print(f"本身就判失败）。自我描述性统计部分是精确比较，能真正判定失败。")

    if (real_mismatches or unavailable_fatal or dead_regexes
            or unmatched_required or anchor_problems or corpus_problems):
        reasons = []
        if real_mismatches:
            reasons.append("自我描述性统计与真实值不符")
        if unavailable_fatal:
            reasons.append("自我描述性统计的真实值算不出来（检查器自己瞎了）")
        if dead_regexes:
            reasons.append("存在从未命中的自我描述正则（等于没在检查）")
        if unmatched_required:
            reasons.append("%d 条主张的数字没印在其锚点章节里且未获豁免"
                           % len(unmatched_required))
        if anchor_problems:
            reasons.append("%d 处锚点自身有问题（缺锚点/死锚点/无理由豁免）"
                           % len(anchor_problems))
        if corpus_problems:
            reasons.append("%d 处跨主张语料算术对不上" % len(corpus_problems))
        print(f"\n{'='*70}\n✗ 判定为失败：{'；'.join(reasons)}。\n{'='*70}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
