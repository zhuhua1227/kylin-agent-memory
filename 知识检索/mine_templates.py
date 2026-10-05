#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_templates.py —— 可复用模板挖掘器：赛题要求的第三类知识（工作流程知识、
历史案例、可复用模板）里唯一一个此前没做的类型。

═══ 为什么之前决定不做，现在又要做 ═══════════════════════════════════

《知识检索/说明报告.md》第四节记的原判断："对 97 条流程做关键词聚类，
72% 无法归类"，据此放弃这一类知识。独立评审指出两个问题：

1. **这个结论本身没有可复现材料**——全项目找不到聚类脚本、没有 M-1xx
   记录、没有中间产物，是全项目唯一一处"结论无脚本"的地方，恰好还是一处
   "放弃某项赛题要求"的决定，对比案例挖掘每一步都有 stats 计数 + claims.json
   校验，这个反差很扎眼。

2. **更关键的是：模板信号其实存在，只是找错了地方**。关键词聚类作用于
   `intent` 意图文本（自然语言，表述发散，72%聚不出类完全正常）；但"可
   复用"这个属性体现在**操作序列的重复**上，不在**措辞的重复**上——同一
   类问题被用相同的工具序列解决过多次，纵使每次的意图描述完全不同。

═══ 设计：按操作序列签名聚合，不按意图关键词聚类 ══════════════════════

两类信号来源：

  · **案例的"失败工具→修复工具"跳转**：同一种工具切换模式反复出现
    （如"pscp传输失败→改用plink远程执行"），代表一条可复用的排障经验：
    "这个工具在这种场景下不好使，换那个"。

  · **流程的工具序列**：同一组工具按相同顺序反复被组合使用（如"先
    plink连接、再sudo提权查看"），代表一条可复用的操作套路。

跨会话（`session_id` 不同）出现 ≥2 次的序列即产出一条模板，附带支持度
（出现次数）与涉及的会话数，供检索时判断可信度。
"""

import json
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mine_cases import classify_error, command_text, inner_verb  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
MIN_SUPPORT = 2


def mine_case_templates(cases: list) -> list:
    """按 (失败工具, 修复工具, 错误类别) 签名聚合"失败→切换工具"模式。

    只有当失败工具与修复工具**不同**时才算"切换模式"（工具相同的情况
    属于案例挖掘器已经覆盖的"同工具重试"，不是模板要抓的"换个工具"）。
    """
    # 第二轮独立复核 R2-22 指出本函数三个 bug，均已修复：
    # ① 修复动作的"用了哪个命令"此前用 fixed_cmd.split()[0]，同 mine_cases.py
    #    K-1/R2-01 同一个盲区——本数据集命令多是 plink/pscp 包装形式，首词
    #    恒为 plink/&，永远取不到真正被执行的命令。改用 inner_verb() 剥壳。
    # ② "失败命令与修复命令是否同一个命令"这道守卫此前写成
    #    `fixed_word == command_text(...).split()[0:1]`——左边是字符串，
    #    右边 `[0:1]` 切片出来的是列表，str == list 恒为 False，这道守卫
    #    从未生效过。改成两边都用 inner_verb() 取字符串再比较。
    buckets = defaultdict(list)
    for c in cases:
        failed_tool = c["tool_name"]
        fi = c.get("fixed_input") or {}
        # 修复动作用的工具名不直接存在案例记录里（案例挖掘器只存了修复时的
        # tool_input，没单独存修复调用的 tool_name——同一工具内换参数是
        # 常态，这里退而求其次用剥壳后的"命令内层动词"近似代表"用了哪个
        # 工具/命令"）。
        fixed_cmd = command_text(fi)
        fixed_word = inner_verb(fixed_cmd)
        failed_word = inner_verb(command_text(c["failed_input"]))
        if not fixed_word or fixed_word == failed_word:
            continue
        err_kind = classify_error(c["error_text"])
        sig = (failed_tool, fixed_word, err_kind)
        buckets[sig].append(c)

    templates = []
    for (failed_tool, fixed_word, err_kind), items in buckets.items():
        # ③ 文件头声明"跨会话出现≥2次"，但过滤门槛此前用的是 len(items)
        #    （出现次数），不是 len(sessions)（会话数）——同一个会话里连续
        #    出现两次也能通过门槛，与"跨会话重复=可信的可复用经验"这个
        #    设计初衷不符（单次会话内的重复更可能是这一次任务恰好这样，
        #    不代表这是个稳定复现的操作模式）。改用会话数做门槛。
        sessions = {c["session_id"] for c in items}
        if len(sessions) < MIN_SUPPORT:
            continue
        templates.append({
            "kind": "template",
            "template_type": "tool_switch",
            "trigger_tool": failed_tool,
            "switch_to": fixed_word,
            "error_category": err_kind,
            "support": len(items),
            "session_count": len(sessions),
            "example_error": items[0]["error_text"][:150],
            "example_fix": command_text(items[0]["fixed_input"])[:150],
        })
    return templates


def mine_workflow_templates(workflows: list) -> list:
    """按工具序列签名（去重后的有序工具名元组）聚合重复出现的操作套路。"""
    buckets = defaultdict(list)
    for w in workflows:
        seen = []
        for step in w["tool_sequence"]:
            name = step["tool_name"]
            if name not in seen:
                seen.append(name)
        sig = tuple(seen)
        if len(sig) < 2:
            continue   # 单工具序列不构成"套路"，案例挖掘器已覆盖这类信号
        buckets[sig].append(w)

    templates = []
    for sig, items in buckets.items():
        # 同 mine_case_templates()：门槛用会话数而非出现次数（R2-22 bug③）
        sessions = {w["session_id"] for w in items}
        if len(sessions) < MIN_SUPPORT:
            continue
        # 🔴 第三轮独立复核 R3-25：R2-22 的三个 bug 修完之后，重跑产出的
        # 4 条模板**100% 是零复用价值的纯工具名对**——`Bash→PowerShell`、
        # `Bash→Read`、`Write→Bash`、`Bash→Write`，example_intent 分别是
        # "好了吗""鱿鱼游戏有多少集"。R2-22 原本就抱怨过这一类，那条没处理，
        # 修完 bug 反而只剩这类。
        # 根因：签名只由**工具名**构成，而 Bash/Write/Read 是通用工具，
        # "先 Bash 再 Write"这种组合在任何任务里都会出现，它描述的是
        # 工具的普遍用法，不是"可复用的操作套路"。
        # 门槛：①必须有一条像样的意图文本（复用价值来自"这套操作是干嘛的"，
        # 意图是"好了吗"就说明这条模板讲不出自己在解决什么问题）；
        # ②序列不能全由通用工具构成，至少要出现一个有领域含义的工具。
        if not _has_reusable_signal(sig, items):
            continue
        templates.append({
            "kind": "template",
            "template_type": "tool_sequence",
            "tool_sequence": list(sig),
            "support": len(items),
            "session_count": len(sessions),
            "example_intent": _best_intent(items),
        })
    return templates


# 通用工具：几乎任何任务都会用到，单靠它们组成的序列没有领域信息量
GENERIC_TOOLS = {"Bash", "PowerShell", "Read", "Write", "Edit",
                 "TaskCreate", "TaskUpdate", "TaskList", "exec"}
MIN_INTENT_CHARS = 12          # 意图文本至少这么长才算讲清了要解决什么


def _best_intent(items):
    """挑信息量最大的那条意图当样例，而不是永远取 items[0]。
    （R3-25：旧代码取第一条，恰好取到"好了吗"这种占位符。）"""
    return max((w.get("intent") or "" for w in items), key=len)


def _has_reusable_signal(sig, items) -> bool:
    if len(_best_intent(items)) < MIN_INTENT_CHARS:
        return False
    if all(t in GENERIC_TOOLS for t in sig):
        return False
    return True


def render_template(t: dict) -> str:
    """🔴 第三轮独立复核 R3-24：这里原本自己写了**第四份** render_template
    （比 render_units 那份多一段"（跨 N 个会话出现 M 次）"）——R2-17 点名的
    "同一约定多处各写一份"模式，在**未被点名的第四个 renderer 上残留**。
    改为 import 唯一实现；支持度信息作为可选后缀单独拼，不再复制渲染逻辑。
    """
    from render_units import render_template as _render
    base = _render(t)
    return f"{base}（跨 {t['session_count']} 个会话出现 {t['support']} 次）"


def main():
    # 🔴 第四轮独立复核 R4-05：三个挖掘器此前无条件覆写自己的产物，而这些
    # 产物正被评测集冻结哈希锚定——照项目自己写的复现步骤跑一遍就打破冻结链。
    # 守卫逻辑与另两个挖掘器共用同一份实现，见 freeze_guard.py 文件头。
    import argparse
    from freeze_guard import add_arguments, guard, write_provenance
    args = add_arguments(argparse.ArgumentParser(
        description="可复用模板挖掘器")).parse_args()
    # 守卫放在算之前：拒跑要快，别让人白等（同 mine_cases.py）
    out_path = guard(os.path.join(BASE, "templates.jsonl"),
                     force=args.force, out_override=args.out)

    cases_path = os.path.join(BASE, "cases.jsonl")
    workflows_path = os.path.join(BASE, "workflows.jsonl")
    cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
    workflows = [json.loads(l) for l in open(workflows_path, encoding="utf-8") if l.strip()]

    case_templates = mine_case_templates(cases)
    workflow_templates = mine_workflow_templates(workflows)
    templates = case_templates + workflow_templates

    print(f"案例池 {len(cases)} 条，流程池 {len(workflows)} 条")
    print(f"工具切换类模板：{len(case_templates)} 条（支持度≥{MIN_SUPPORT}）")
    print(f"工具序列类模板：{len(workflow_templates)} 条（支持度≥{MIN_SUPPORT}）")
    print(f"模板总计：{len(templates)} 条")

    with open(out_path, "w", encoding="utf-8") as f:
        for t in templates:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"\n已写出：{out_path}")
    write_provenance(__file__, out_path, [cases_path, workflows_path])

    if templates:
        print("\n样例：")
        for t in templates[:5]:
            print(f"  · {render_template(t)}")
    else:
        print("\n⚠️ 未产出任何模板（支持度≥2 的重复序列为 0）——如实记录，"
              "不硬凑。当前数据规模下没有模板信号，不代表方法不成立，"
              "见说明报告第四节的诚实讨论。")


if __name__ == "__main__":
    main()
