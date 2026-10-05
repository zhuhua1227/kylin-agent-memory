#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描交付文档里的关键数字，跟 claims.json 核对。

═══ 这是 verify_reports.py 的最小可用版 ═══════════════════════
独立评审第一轮就指出「代码层用科学方法，文档层用记忆」，开的药是
复活 verify_reports.py，当时判断"增量价值有限"而搁置。

后来这个判断被证伪了三次：
  ① 召回率报告 §3.3 用过期分类型数字，与自己的 §3.1 总表矛盾
  ② 消融方案跨版本混取列，写出 R@5 > R@10（数学上不可能）
  ③ 主张数三处并存：表 XIII 56 / 正文 80 / PPT 80，实际 104

**为什么是「模式匹配」而不是「通用校验」**：通用地理解散文里的数字
需要语义分析，做不可靠。这里只盯**少数几个反复出错、且句式稳定**的量，
用正则抓出来跟权威值比。覆盖面小，但抓的正是实际出过事的那几类。

用法：
    python3 check_report_numbers.py           # 扫描并报告
    python3 check_report_numbers.py --fix     # 只列出建议改法，不自动改
                                              # （自动改文档太危险，人工确认）
退出码非 0 = 有对不上的，提交前必须处理。
═══════════════════════════════════════════════════════════════
"""
import json
import io
import os
import re
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CLAIMS = os.path.join(BASE, "claims.json")

# 要扫的文档。新增交付文档时加进来。
TARGETS = [
    u"准备复现.md",
    u"可复现性体系/可复现性说明.md",
    u"技术报告/技术报告-v1.0.md",
    u"评测集/召回率实测报告.md",
    u"知识检索/知识冲突处理说明报告.md",
    u"知识检索/偏好提取说明报告.md",
]

# 已知的历史性表述——这些是在描述"当时是多少"，不该被改。
# 命中这些正则的行整行跳过。
HISTORICAL = [
    # 只认明确的历史标记；"此前"/"曾"太常见，会把有效行误跳过
    re.compile(u"当时(是|为|的)|原(表|文|判|来)(是|为|写)|旧版|v1\\.[0-2]\\.0 时(期)?|已订正|勘误"),
]


def load_authoritative():
    d = json.load(io.open(CLAIMS, encoding="utf-8"))
    cs = d if isinstance(d, list) else d.get("claims", [])
    by_layer = collections.Counter(c.get("layer") for c in cs)
    return {
        u"主张总数": len(cs),
        u"L1主张数": by_layer.get("L1", 0),
        u"L2主张数": by_layer.get("L2", 0),
    }


# (正则, 该数字应等于哪个权威量, 人话描述)
# 正则必须恰好一个捕获组 = 待核对的数字
RULES = [
    (re.compile(u"合计\\s*\\*{0,2}(\\d+)\\s*条(?:数字)?主张"), u"主张总数", u"合计 N 条主张"),
    (re.compile(u"(\\d+)\\s*条(?:数字)?主张(?:可机器核对|全绿|全部通过)"), u"主张总数", u"N 条主张可机器核对"),
    (re.compile(u"`claims\\.json`[\\s\\S]{0,40}?（(\\d+)\\s*条）"), u"主张总数", u"claims.json（N 条）"),
    (re.compile(u"其中\\s*\\*{0,2}(\\d+)\\s*条?\\*{0,2}\\s*不需要?(?:银河)?麒麟"), u"L1主张数", u"其中 N 条不需要麒麟环境"),
    (re.compile(u"\\|\\s*\\*{0,2}L1\\*{0,2}\\s*\\|[^|]*\\|\\s*(\\d+)\\s*\\|"), u"L1主张数", u"表格里 L1 那行的主张数"),
    (re.compile(u"\\|\\s*\\*{0,2}L2\\*{0,2}\\s*\\|[^|]*\\|\\s*(\\d+)\\s*\\|"), u"L2主张数", u"表格里 L2 那行的主张数"),
]


def is_historical(line):
    return any(p.search(line) for p in HISTORICAL)


def main():
    auth = load_authoritative()
    print(u"权威值（来自 claims.json）：")
    for k, v in auth.items():
        print(u"   %-8s %d" % (k, v))
    print()

    problems = []
    for rel in TARGETS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print(u"（跳过，文件不存在）%s" % rel)
            continue
        lines = io.open(p, encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines, 1):
            if is_historical(line):
                continue
            for rx, key, desc in RULES:
                m = rx.search(line)
                if not m:
                    continue
                got = int(m.group(1))
                want = auth[key]
                if got != want:
                    problems.append((rel, i, desc, got, want, line.strip()[:100]))

    if not problems:
        print(u"✅ 未发现对不上的数字。")
        return 0

    print(u"🔴 发现 %d 处对不上：\n" % len(problems))
    for rel, i, desc, got, want, text in problems:
        print(u"  %s:%d" % (rel, i))
        print(u"     %s —— 写的是 %d，应为 %d" % (desc, got, want))
        print(u"     %s" % text)
        print()
    print(u"处理方式：照《当前数字看板.md》改，**不要反过来改 claims.json**。")
    print(u"若某行确实是在描述历史（\"当时是 80 条\"），把它改成带限定语的写法，")
    print(u"或在 HISTORICAL 里加对应模式。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
