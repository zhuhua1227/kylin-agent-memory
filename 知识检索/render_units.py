# -*- coding: utf-8 -*-
"""
render_units.py —— 知识单元→检索/出题文本的渲染规则，唯一实现

═══ 为什么要单独抽出来 ═══════════════════════════════════════════════

`build_knowledge_index.py`（建索引，决定"检索到的文本长什么样"）与
`评测集/generate_eval_candidates.py`（出题，决定"Gemini 看到的知识单元
长什么样"）必须用**同一套渲染规则**——出题端渲染的文本如果和索引端不
一致，出的题目描述的东西和检索库里实际存的文本对不上，评测集测的就不是
真实检索场景。

第二轮独立复核 R2-17 指出：这条约定此前是靠"两处各自实现一遍、互相写
注释说'与另一处保持一致'"来维持的——`render_workflow`/`render_template`
两处确实一直保持了一致，但 `render_case` 在 `build_knowledge_index.py`
里于 K-2 时把 `error_text` 截断从 150 字改成 300 字（修复"真实报错常见
的样子是噪声开头+后面才是关键报错信号"这个问题），`generate_eval_
candidates.py` 那份忘了同步改，成了两处唯一走了样的一个。"各自实现一遍
再互相写注释保证一致"这个模式本身就是问题所在——注释会被忘记检查，
import 同一个函数不会。
"""

import json


def render_case(c: dict) -> str:
    ti = c["failed_input"] or {}
    intent = ti.get("description") or ti.get("command") or json.dumps(ti, ensure_ascii=False)
    # error_text 截到 300 字（不是 150）：真实报错常见的样子是"进度条/密码回显等
    # 噪声开头 + 后面才是真正的报错信号"（如 ModuleNotFoundError、语法错误提示），
    # 150 字经常连噪声开头都不够，把关键报错词整个截没了——这是实测召回率时
    # 定位到的真实问题（2026-08-04，见《召回率实测报告.md》），不是主观猜测：
    # doc_id=22 的 "ModuleNotFoundError: No module named 'mutagen'" 出现在
    # error_text 第195~230字，150字截断会把它整个丢掉；cases.jsonl 里
    # error_text 本身就存了300字（mine_cases.py 的截断上限），这里改成一致
    # 不需要重新挖掘，只是不再浪费已经存下来的信息。
    return (f"【历史案例】使用 {c['tool_name']} 执行「{str(intent)[:150]}」时出错："
            f"{c['error_text'][:300]}。"
            f"修复方式：{json.dumps(c['fixed_input'], ensure_ascii=False)[:150]}")


def render_workflow(w: dict) -> str:
    """独立评审 K-2 指出：原实现只保留 intent + 工具名去重列表，把
    `tool_sequence` 里每一步的 `input_summary`（具体做了什么）全丢了。
    实测渲染文本平均只有 78 字，其中 42.5% 是用户原话——检索命中它，
    Agent 拿到的信息只是"这事要用 Bash，两步"，没有任何可复用的操作性
    知识。这也连带影响了召回率评测的可信度：workflow 类 R@1 此前测出
    74.8%、远高于 case 类的 49.0%，根因正是评测集问句是对 `source_
    rendered` 的改写而 rendered 主体就是用户原话——测的是"能否把改写句
    匹配回原句"，不是"能否检索到有用流程"，分数虚高恰恰因为任务被
    简化了。补上每一步的 input_summary 后，这个虚高的差距应当收窄
    （实测见《知识检索/说明报告.md》10.2 节）。
    """
    steps = w.get("tool_sequence", [])
    lines = [f"【工作流程】用户想要：{w['intent']}",
             f"共 {len(steps)} 步："]
    for i, s in enumerate(steps[:8], 1):      # 前 8 步足以表达流程骨架
        lines.append(f"  {i}. [{s['tool_name']}] {s.get('input_summary', '')}")
    if len(steps) > 8:
        lines.append(f"  …… 另有 {len(steps) - 8} 步")
    if w.get("had_errors"):
        lines.append("（含出错重试）")
    return "\n".join(lines)


def render_template(t: dict) -> str:
    if t["template_type"] == "tool_switch":
        return (f"【可复用模板】遇到 {t['trigger_tool']} 报"
                f"{t['error_category']}类错误时，可以换用 {t['switch_to']}。")
    return f"【可复用模板】常见操作套路：依次使用 {' → '.join(t['tool_sequence'])}。"


def render_conflict(k: dict) -> str:
    """冲突记录本身也是一条可检索的知识（知识冲突处理-设计方案.md §五第③点：
    "这件事的认识变化过"对用户是有价值的信息）。

    渲染出新旧两侧的原话 + 裁决结论 + 旧知识的适用边界，而不是只给结论——
    检索到一条冲突记录时，用户/Agent 要能看见"曾经认为 A，后来发现 B，
    A 在什么条件下仍然成立"这个完整变化过程，不是只给"用 B"这一句话
    （那样就是覆盖，不是设计方案 §〇 要求的"标注适用边界、可回溯"）。
    """
    return (f"【知识变化】关于这件事，认识发生过变化：\n"
            f"曾经（{k['old'].get('asserted_at', '?')}）：{k['old'].get('claim', '')}\n"
            f"现在（{k['new'].get('asserted_at', '?')}）：{k['new'].get('claim', '')}\n"
            f"结论：{('新的信息生效' if k.get('verdict') == 'new_supersedes_old' else '旧的信息仍然有效')}"
            f"（类型：{k.get('type', '?')}）。"
            + (f"旧知识的适用边界：{k['scope_of_old']}。" if k.get("scope_of_old") else ""))


def is_low_value_workflow(w: dict) -> bool:
    """标记信息量不足的流程——不从索引里删除（它们仍是评测集需要的干扰
    项，见《知识检索/说明报告.md》第七节的覆盖率讨论），只是不适合作为
    检索时优先展示的"可复用知识"。

    判据来自对当前 59 条 workflows.jsonl 的实测：17 条只有一步（一次
    工具调用不构成"流程"），24 条 intent 短于 20 字且多为"继续""说中文"
    这类占位符，两者取并集共 30 条（50.8%）。
    """
    return len(w.get("tool_sequence", [])) <= 1 or len(w.get("intent", "") or "") < 20
