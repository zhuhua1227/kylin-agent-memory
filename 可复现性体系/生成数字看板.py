#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成《当前数字看板.md》——所有对外文档引用数字的唯一出处。

═══ 为什么需要这个文件 ═══════════════════════════════════════════
本项目已经三次栽在「文档层用记忆、代码层用科学方法」上：

  ① 召回率报告 §3.3 分类型表用的是 v1.2.0 的过期数字，
     与同一份报告 §3.1 的总表（86.3%）直接矛盾
  ② 关联检索消融方案把跨版本对照表的左右列混取，写出
     R@5 87.0% > R@10 86.3% —— 召回率对 k 单调不减，这在数学上不可能
  ③ 可复现体系主张数三处并存：表 XIII 说 56、正文说 80、PPT 说 80/58，
     而 claims.json 实际是 104

独立评审当时开的药是「复活 verify_reports.py」，被判断为
"增量价值有限"而搁置——③ 是第三次证明那个判断是错的。

本脚本 + check_report_numbers.py 就是那味药的最小可用版：
  · 本脚本：从 claims.json 机器生成权威数字
  · 检查脚本：拿这些数字去扫文档，报出对不上的

**任何文档要引用数字，先跑这个脚本，照抄看板。不要凭记忆写。**
═══════════════════════════════════════════════════════════════
"""
import json
import io
import os
import sys
import collections
import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
CLAIMS = os.path.join(BASE, "claims.json")
OUT = os.path.join(BASE, "当前数字看板.md")

# 故意保留为「不符」的主张——如实报告不达标，不是缺陷。
# 增删这份名单必须同时更新报告里的说明，否则会出现"报告说 2 条、
# 实际 3 条"这种新的不一致。
KNOWN_RED = {
    "C-255": "总体 recall@10 的 Wilson 95% CI 下界 81.3%，够不着 85% 门槛",
    "C-304": "冲突判定——留出集单独口径 56/60 的 Wilson 95% CI 下界 84.1%，够不着 88% "
             "门槛（合并口径 91/96 下界 88.4% 已达标，见 C-316——两条并读，"
             "不能只留 C-316）",
}


def load_claims():
    d = json.load(io.open(CLAIMS, encoding="utf-8"))
    return d if isinstance(d, list) else d.get("claims", [])


def main():
    cs = load_claims()
    by_layer = collections.Counter(c.get("layer") for c in cs)
    by_check = collections.Counter(c.get("check") for c in cs)

    total = len(cs)
    l1 = by_layer.get("L1", 0)
    l2 = by_layer.get("L2", 0)
    red = len(KNOWN_RED)
    green = total - red

    ids = {c.get("claim_id") for c in cs}
    missing = [k for k in KNOWN_RED if k not in ids]

    lines = []
    w = lines.append

    w(u"# 当前数字看板")
    w(u"")
    w(u"> 🔴 **本文件由 `生成数字看板.py` 机器生成，不要手改。**")
    w(u"> 所有对外文档（技术报告 / 技术方案 / PPT / 说明报告）引用数字时**一律照抄本表**，")
    w(u"> 不要凭记忆写——本项目已因此栽过三次，见脚本头部注释。")
    w(u"")
    w(u"> 生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    w(u"> 数据源：`claims.json`")
    w(u"")
    w(u"---")
    w(u"")
    w(u"## 可复现性体系 · 主张数")
    w(u"")
    w(u"| 量 | 值 |")
    w(u"|---|---|")
    w(u"| **主张总数** | **%d** |" % total)
    w(u"| L1（纯 Python，**不需要麒麟环境**） | **%d** |" % l1)
    w(u"| L2（需 KYSDK / 麒麟真机） | **%d** |" % l2)
    w(u"| 预期通过 | %d |" % green)
    w(u"| **故意保留为「不符」** | **%d** |" % red)
    w(u"")
    w(u"### 那 %d 条故意保留为不符的" % red)
    w(u"")
    for k in sorted(KNOWN_RED):
        w(u"- **%s** —— %s" % (k, KNOWN_RED[k]))
    w(u"")
    w(u"> 它们**不是缺陷**，是「如实报告不达标而非用点估计冒充」的体现。")
    w(u"> 报告里写「除 %s 外无新增失败」时，这两个编号要写全。"
      % " / ".join(sorted(KNOWN_RED)))
    if missing:
        w(u"")
        w(u"> ⚠️ **警告**：%s 不在 claims.json 里，KNOWN_RED 名单已过期，请更新本脚本。"
          % ", ".join(missing))
    w(u"")
    w(u"### 按校验类型")
    w(u"")
    w(u"| check | 条数 |")
    w(u"|---|---|")
    for k, v in by_check.most_common():
        w(u"| `%s` | %d |" % (k, v))
    w(u"")
    w(u"---")
    w(u"")
    w(u"## ⚠️ 知识单元数 86，别和主张数串台")
    w(u"")
    w(u"| 量 | 值 | 说明 |")
    w(u"|---|---|---|")
    w(u"| L1 主张数 | **%d** | 可复现性体系里不需麒麟环境的主张条数 |" % l1)
    w(u"| 知识单元数 | **86** | `cases 31 + workflows 54 + templates 1`，检索语料的规模 |")
    w(u"")
    if l1 == 86:
        w(u"🔴 **两者当前都等于 86**——写文档务必带限定语（「86 条 L1 主张」 /")
        w(u"「86 条知识单元」），光写「86 条」会让读者串台。")
    else:
        w(u"这两个数**曾经都等于 86**、被混用过（见脚本头部注释③）。现在 L1 主张数是 %d，"
          % l1)
        w(u"但「86 条知识单元」仍是检索语料规模，引用时照样带上限定语。")
    w(u"")
    w(u"---")
    w(u"")
    w(u"## 引用规范")
    w(u"")
    w(u"1. 数字**只能**来自本看板或 `claims.json`，不得凭记忆")
    w(u"2. 每次指标封盘后重跑本脚本，再更新文档")
    w(u"3. 提交前跑 `check_report_numbers.py`，它会扫出对不上的地方")
    w(u"4. 引用具体一对冲突时用 `conflict_id`（K-xxxx），**不要用 `eval_id`**")
    w(u"   —— 后者是行位置产物，重挖即失效（见《冲突判定标注规范》v1.0.1 §七.3b）")

    io.open(OUT, "w", encoding="utf-8", newline="\n").write(u"\n".join(lines) + u"\n")
    print(u"已生成 %s" % OUT)
    print(u"  主张总数 %d（L1 %d / L2 %d），故意保留不符 %d" % (total, l1, l2, red))
    if missing:
        print(u"  ⚠️ KNOWN_RED 名单过期：%s 不在 claims.json 里" % ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
