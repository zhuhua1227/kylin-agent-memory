#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
identity_map_loader.py —— 全项目**唯一**的身份假名映射入口（第三轮 R3-28/R3-29）

═══ 为什么把它提到项目根目录 ═══════════════════════════════════════════

此前有**两份** `identity_map.txt`：`多源数据整合/` 一份（给 `anonymize.py`
脱敏语料用）、`可复现性体系/` 一份（给 `measure.py` 假名化记录里的主机名与
路径用）。两份各自维护，于是必然发生的事发生了——第三轮独立复核 R3-28 实测：

  · **3 条真实值在两份表里映射到不同假名**；
  · 且 `可复现性体系` 那份把**两个不同的真实值都映射成 `<USER>`**（多对一坍缩）。

后果不是"某处没打码"，而是**一致性假名化这件事本身失效了**：同一台机器在
测量记录里叫 A、在语料里叫 B，读者无法判断"记录里那台机器"和"语料里那台
机器"是不是同一台；反过来两个不同的人被坍缩成同一个假名，又会让人以为是
同一个人。一致性假名化的全部价值就在于**保持可关联性**（这正是勘误记录里
"过度脱敏切断数据关联"那次事故的反向修正），映射不一致等于把这个价值抵消掉。

根因是"同一份数据存两处"。所以修法不是把两份表改成一样——那只会在下次
新增条目时重新分叉——而是**只留一份数据、只留一份加载逻辑**。

═══ 假名词表为什么沿用 `多源数据整合` 那一份 ═══════════════════════════

合并时必须选一套假名。选的是**数据侧**那一套（`user_a` / `device_a` …），
不是因为它更好看，而是因为**它已经冻结在产物里了**：语料是用它脱敏的，
知识单元的文本、doc_id 编号、v1.3.0 评测集的冻结哈希全建立在那份文本上。
改数据侧假名 = 语料变 → doc_id 变 → 冻结集作废 → 全套召回率数字作废
（见《代码产物落差登记表.md》）。而 `可复现性体系` 侧的假名只出现在
`records/*.json` 的 `environment` 字段里，是纯标签，改动代价近乎零。

**代价小的一侧让步**——这条在类似取舍里通用，不只适用于这一次。

═══ R3-29：纯字母的身份值改为全词匹配 ═════════════════════════════════

表里有几条真实值是**纯字母、首字母大写的常见英文词**。原实现是精确大小写的
**裸子串**替换，于是这类词一旦作为普通英文单词出现（尤其在句首大写时），
就会被替换成假名。当前实测在交付文本里 0 处命中，尚未造成损害，但这是
"还没炸"而不是"不会炸"。

处理：纯字母条目改为**全词匹配**（前后不得紧邻其它字母），含数字/连字符的
条目（`DESKTOP-XXX123` 这类主机名）保持子串匹配——它们不会和英文词撞。

⚠️ **如实说明这条修法的边界**：全词匹配挡得住"匹配到更长单词的内部"
（`Foo` 不再命中 `Foobar`），但**挡不住该词作为独立英文单词出现**——那种
场景本来就是全词。要彻底解决只能移除该条目，但移除意味着这个真实主机名
从此不再被脱敏，那是拿隐私换整洁，更糟。所以这里选的是"降低误伤面 +
把残余风险写明"，而不是假装问题已经消失。

═══ 文件格式 ═══════════════════════════════════════════════════════

    真实值<TAB>假名[<TAB>match=word|substr]

第三列可选，用来显式覆盖上面那条自动判定；不写就按"纯字母→全词、
其余→子串"自动决定。以 `#` 开头的行是注释。

真实映射表 `identity_map.txt` 由 `.gitignore` 排除，仓库里只留
`identity_map.txt.example` 模板——表的内容本身（真实用户名、主机名）
就是敏感信息，不能进 git。表不存在时全部接口返回空，调用方降级为
"不做假名化"，不报错。
"""

import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
MAP_PATH = os.path.join(BASE, "identity_map.txt")


def _match_mode(real, explicit):
    if explicit in ("word", "substr"):
        return explicit
    # 纯字母 ⇒ 可能撞英文词 ⇒ 全词；带数字/连字符的主机名 ⇒ 子串
    return "word" if real.isalpha() else "substr"


def load_pairs(path=None):
    """返回 [(真实值, 假名, 匹配模式)]，按真实值长度**降序**。

    降序是必须的：短值可能是长值的前缀（典型形态：某用户名同时是
    "该用户名+数字后缀"的前缀），先替换短的会把长的拆坏。
    """
    path = path or MAP_PATH
    if not os.path.exists(path):
        return []
    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            real, pseudo = parts[0].strip(), parts[1].strip()
            explicit = None
            if len(parts) >= 3 and parts[2].strip().startswith("match="):
                explicit = parts[2].strip()[len("match="):]
            if real:
                pairs.append((real, pseudo, _match_mode(real, explicit)))
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def load_mapping(path=None):
    """{真实值: 假名}。给 measure.py 的主机名精确查表用。"""
    return {real: pseudo for real, pseudo, _mode in load_pairs(path)}


def load_patterns(path=None):
    """[(已编译正则, 假名)]，给 anonymize.py 的自由文本替换用。"""
    out = []
    for real, pseudo, mode in load_pairs(path):
        if mode == "word":
            # 用 (?<![A-Za-z]) 而不是 \b：\b 会让 `Foo` 命中 `Foo_bar` 里的
            # `Foo`（下划线算单词字符，`\b` 认为没有边界，反而不命中），
            # 而这里真正要挡的是"紧邻其它字母"，下划线/连字符两侧仍应命中。
            pat = re.compile(r"(?<![A-Za-z])" + re.escape(real) + r"(?![A-Za-z])")
        else:
            pat = re.compile(re.escape(real))
        out.append((pat, pseudo))
    return out


def conflicts(path=None):
    """自检：返回 (一真实值多假名, 多真实值同假名)。

    第二项**不一定是错**——同一个人的三个别名（本地用户名、GitHub 用户名、
    显示名）本就该映射到同一个假名，那正是一致性假名化要的效果。所以这里
    只负责把它列出来供人判断，不直接判失败。第一项则一定是错。
    """
    pairs = load_pairs(path)
    by_real, by_pseudo = {}, {}
    for real, pseudo, _m in pairs:
        by_real.setdefault(real, set()).add(pseudo)
        by_pseudo.setdefault(pseudo, set()).add(real)
    multi_pseudo = {r: sorted(ps) for r, ps in by_real.items() if len(ps) > 1}
    shared_pseudo = {p: len(rs) for p, rs in by_pseudo.items() if len(rs) > 1}
    return multi_pseudo, shared_pseudo
