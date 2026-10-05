#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_workflows.py —— 流程切分器：从「用户意图 → 工具调用序列」中挖「工作流程知识」

═══ 为什么需要用户意图这个锚点 ═══════════════════════════════════════

一串工具调用本身只是流水账——"执行了 A，然后 B，然后 C"。只有知道
"用户当时想做什么"，这串调用才升格为"做某件事的方法"。意图锚点来自
`~/.claude/history.jsonl`（第二版补充的数据源，record_type=user_input）。

关联方式：同一个 session_id 下，用户输入之后、下一个用户输入之前的
那一段工具调用，就是这条意图对应的执行序列。

《多源数据整合》报告已验证过这条关联链路：28 个会话交集，
120 条可关联的用户意图，覆盖 9094 次工具调用（64.7%）。本脚本把它落地成
真正的知识单元。

═══ 需要过滤掉的噪声 ═══════════════════════════════════════════════

不是每条用户输入后面都跟着有意义的执行序列：
  · 纯对话/确认类输入（"好的"、"继续"）后面可能没有任何工具调用；
  · 序列过短（只有 1 次调用）参考价值有限，但仍保留——
    简单操作也是一种"流程"，不应武断丢弃，只做标记以便后续按需过滤；
  · 序列过长（例如跨越上百次调用）往往意味着这段时间内用户发过多条输入，
    但因为脱敏/解析原因没被关联上，直接囫囵作为一条会稀释意图的针对性——
    设一个上限，超过上限的按后续窗口截断并标记。
"""

import json
import os
import sys

# Windows 默认控制台是 GBK，同 mine_cases.py 的 K-6 修复（详见那边注释）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATASET = os.environ.get("RAG_DATASET") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "多源数据整合", "dataset", "memory_source_records.jsonl")

MAX_SEQUENCE_LEN = 30   # 单条流程最多纳入的工具调用数，避免过长序列稀释意图


def load_dataset():
    recs = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return recs


def mine(recs):
    tools = [r for r in recs if r.get("record_type") == "tool_execution"]
    inputs = [r for r in recs if r.get("record_type") == "user_input"]

    tools_by_session = {}
    for r in tools:
        tools_by_session.setdefault(r.get("session_id"), []).append(r)
    for rs in tools_by_session.values():
        rs.sort(key=lambda r: r.get("timestamp") or "")

    inputs_by_session = {}
    for r in inputs:
        inputs_by_session.setdefault(r.get("session_id"), []).append(r)
    for rs in inputs_by_session.values():
        rs.sort(key=lambda r: r.get("timestamp") or "")

    # 2026-08-04 修复：不确定性排序 bug（生成评测集反向出题、召回率实测出现
    # 大规模 doc_id 错位时定位到的根因）。
    #
    # 原写法 `set(tools_by_session) & set(inputs_by_session)` 用两个 set 求交集，
    # 而 Python 的 set 遍历顺序受进程级哈希随机化影响（PYTHONHASHSEED 默认
    # 每次进程启动都不同）——**同一份输入数据，两次运行本脚本，产出的
    # workflows.jsonl 里条目顺序可能不同**，而下游所有环节（建索引、生成
    # 评测集）的 doc_id 都是按文件行号顺序编的。如果建索引和生成评测集这两步
    # 用的是本脚本两次不同运行的产物，doc_id 编号就会错位——评测集以为
    # doc_id=55 是"备份塞尔达存档"，实际索引里 doc_id=55 是完全不相关的另一
    # 条流程，导致召回率测量结果失真（曾把可用的检索能力测成了"workflow类
    # 召回率0%"）。
    #
    # 只用 set 做**成员判断**（O(1)查找），不用它决定**遍历顺序**——
    # 顺序改为跟随 tools_by_session 的字典插入序（本身来自文件读入的行序，
    # 确定性的），保证同一份输入数据无论跑多少次都产出完全相同顺序的文件。
    shared_set = set(inputs_by_session)
    shared_sessions = [sid for sid in tools_by_session if sid in shared_set]

    workflows = []
    empty_sequence = 0
    truncated = 0

    for sid in shared_sessions:
        session_inputs = inputs_by_session[sid]
        session_tools = tools_by_session[sid]

        for idx, ui in enumerate(session_inputs):
            t_start = ui.get("timestamp") or ""
            # 下一条用户输入的时间戳作为窗口终点；没有下一条就取到会话结尾
            t_end = (session_inputs[idx + 1].get("timestamp")
                     if idx + 1 < len(session_inputs) else None)

            seq = [t for t in session_tools
                   if (t.get("timestamp") or "") >= t_start
                   and (t_end is None or (t.get("timestamp") or "") < t_end)]

            if not seq:
                empty_sequence += 1
                continue

            was_truncated = len(seq) > MAX_SEQUENCE_LEN
            if was_truncated:
                seq = seq[:MAX_SEQUENCE_LEN]
                truncated += 1

            workflows.append({
                "kind": "workflow",
                "session_id": sid,
                "intent": ui.get("text"),
                "intent_at": t_start,
                "tool_sequence": [
                    {"tool_name": t.get("tool_name"),
                     "input_summary": _summarize_input(t.get("tool_input")),
                     "is_error": t.get("is_error", False)}
                    for t in seq
                ],
                "sequence_length": len(seq),
                "truncated": was_truncated,
                "had_errors": any(t.get("is_error") for t in seq),
            })

    stats = {
        "shared_sessions": len(shared_sessions),
        "total_user_inputs_in_shared": sum(len(inputs_by_session[s]) for s in shared_sessions),
        "workflows_found": len(workflows),
        "empty_sequence_skipped": empty_sequence,
        "truncated": truncated,
    }
    return workflows, stats


def _summarize_input(ti):
    if not isinstance(ti, dict):
        return str(ti)[:80]
    for key in ("description", "command", "file_path", "query", "pattern"):
        if ti.get(key):
            return str(ti[key])[:80]
    return json.dumps(ti, ensure_ascii=False)[:80]


def render_text(wf: dict) -> str:
    """控制台预览用。真正用于建索引的是 build_knowledge_index.py 里同名
    逻辑的 render_workflow()——两处必须保持一致（独立评审 K-2 之前发现
    两处都只保留了工具名去重列表、丢了每一步的 input_summary，这里同步
    改成一致的版本，不再只是"意图+工具种类"这种信息量很低的渲染）。
    """
    steps = wf["tool_sequence"]
    lines = [f"【工作流程】用户想要：{wf['intent']}", f"共 {len(steps)} 步："]
    for i, s in enumerate(steps[:8], 1):
        lines.append(f"  {i}. [{s['tool_name']}] {s.get('input_summary', '')}")
    if len(steps) > 8:
        lines.append(f"  …… 另有 {len(steps) - 8} 步")
    if wf["had_errors"]:
        lines.append("（含出错重试）")
    return "\n".join(lines)


def main():
    # 🔴 第四轮 R4-05：冻结守卫，见 freeze_guard.py 文件头（三个挖掘器共用一份）
    import argparse
    from freeze_guard import add_arguments, guard, write_provenance
    args = add_arguments(argparse.ArgumentParser(
        description="工作流程切分器")).parse_args()
    # 守卫放在算之前：拒跑要快，别让人白等（同 mine_cases.py）
    out_path = guard(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "workflows.jsonl"),
                     force=args.force, out_override=args.out)

    recs = load_dataset()
    workflows, stats = mine(recs)

    print(f"会话交集：{stats['shared_sessions']} 个")
    print(f"交集内用户输入总数：{stats['total_user_inputs_in_shared']} 条")
    print(f"挖出工作流程：{stats['workflows_found']} 条")
    print(f"  空序列跳过：{stats['empty_sequence_skipped']} 条"
          f"（该输入后未找到关联的工具调用，如纯确认性发言）")
    print(f"  超长截断：{stats['truncated']} 条（超过 {MAX_SEQUENCE_LEN} 步）")

    with open(out_path, "w", encoding="utf-8") as f:
        for w in workflows:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")
    print(f"\n已写出：{out_path}")
    write_provenance(__file__, out_path, [DATASET])

    if workflows:
        print("\n样例（前 3 条）：")
        for w in workflows[:3]:
            print(f"  · {render_text(w)}")


if __name__ == "__main__":
    main()
