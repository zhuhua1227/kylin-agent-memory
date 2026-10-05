#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_claude_code.py —— 从本机 Claude Code 会话记录提取"工具调用+执行结果"数据

数据来源：~/.claude/projects/<project>/<session-id>.jsonl（Claude Code 官方文档确认的存储位置）
输出：dataset_claude_code.jsonl，每行一条统一格式记录（见 README 的 schema 说明）

解析策略（防御性设计，原因见下）：
    官方文档明确说明：这个 JSONL 内部格式是 Claude Code 的实现细节，会随版本变化，
    不保证向后兼容。因此本脚本对每一行都做 try/except，遇到解析失败或结构不符合
    预期的记录一律跳过，不让个别"看不懂"的记录中断整个提取过程。

配对逻辑：
    Claude Code 的对话记录里，一次工具调用分两步体现：
      1. 一条 assistant 消息里有一个 content block，type="tool_use"，
         带 id / name / input（要调用的工具名和参数）；
      2. 随后一条消息（通常是 user 角色，代表"工具执行完毕、结果回传"）里，
         有一个 content block，type="tool_result"，带 tool_use_id（对应上面的id）和 content（结果）。
    本脚本先扫一遍收集所有 tool_use，再扫一遍收集所有 tool_result，
    最后按 id 配对，只有两边都找到的才输出一条完整记录。
"""

import json
import glob
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anonymize import anonymize

CLAUDE_PROJECTS_DIR = os.path.expanduser(os.path.join("~", ".claude", "projects"))
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_claude_code.jsonl")


def flatten_content_to_text(content):
    """tool_result 的 content 字段可能是字符串，也可能是一个 content-block 列表
    （比如 [{"type":"text","text":"..."}]）。这里统一转换成纯文本字符串，
    方便后续存储和阅读。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    # 非文本块（比如图片），用占位说明代替，不尝试还原二进制内容
                    parts.append(f"[non-text block: {block.get('type', '?')}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def extract_from_file(filepath, session_id):
    """处理单个 .jsonl 会话文件，返回 (本文件里所有配对成功的记录列表, 跳过计数)。

    独立评审D-11：此前两处 except 块静默 continue，不计数不记录，出问题时
    完全看不出是"这个文件本来就没几条记录"还是"解析悄悄丢了一大半"。
    照 extract_user_history.py 已有的 skipped 计数模式改。
    """
    tool_uses = {}    # tool_use_id -> {"name":..., "input":..., "timestamp":...}
    tool_results = {}  # tool_use_id -> {"output":..., "is_error":...}
    skipped = {"bad_json": 0, "bad_structure": 0}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                skipped["bad_json"] += 1
                continue  # 格式不对的行直接跳过，不中断整体提取

            try:
                timestamp = record.get("timestamp")
                message = record.get("message", {})
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tool_uses[block.get("id")] = {
                            "name": block.get("name"),
                            "input": block.get("input"),
                            "timestamp": timestamp,
                        }
                    elif block.get("type") == "tool_result":
                        tool_results[block.get("tool_use_id")] = {
                            "output": flatten_content_to_text(block.get("content")),
                            "is_error": block.get("is_error", False),
                        }
            except Exception:
                skipped["bad_structure"] += 1
                continue  # 单条记录内部结构异常也跳过，不影响其他记录

    records = []
    for tid, use_info in tool_uses.items():
        result_info = tool_results.get(tid)
        if result_info is None:
            continue  # 只有调用、没有结果的（比如会话中途中断）不纳入
        records.append({
            "source": "claude_code",
            "session_id": session_id,
            "timestamp": use_info["timestamp"],
            "tool_name": use_info["name"],
            "tool_input": use_info["input"],
            "tool_output": result_info["output"],
            "is_error": result_info["is_error"],
        })
    return records, skipped


# 自指污染过滤：独立审查 D-13 指出，数据集里混入了本项目自己的开发记录
# （65条含本项目关键词，其中2条甚至包含 anonymize.py 源码正文本身），
# 这会造成循环论证——用本项目自己的会话去支撑/评测本项目。
#
# ⚠️ 按"项目目录"过滤不可行：本环境所有会话统一记在通用目录
# `~/.claude/projects/C--Users-<USER>/` 下（VSCode 扩展的运行方式如此），
# 不是按 `cd 到具体项目目录` 分开存放的，一个物理会话可能混杂了 RAG 项目
# 工作与完全无关的工作。因此改成**按单条记录内容过滤**：只要这条工具调用
# 涉及的路径指向本项目目录（`Desktop\RAG` 或 `Desktop/RAG`），就跳过这一条，
# 而不是跳过整个会话文件——同一会话里跟 RAG 项目无关的其他记录仍然保留。
SELF_PROJECT_MARKERS = ("desktop\\rag", "desktop/rag")


def is_self_referential(record: dict) -> bool:
    blob = json.dumps([record.get("tool_input"), record.get("tool_output")],
                       ensure_ascii=False, default=str).lower()
    return any(marker in blob for marker in SELF_PROJECT_MARKERS)


def main():
    pattern = os.path.join(CLAUDE_PROJECTS_DIR, "**", "*.jsonl")
    files = glob.glob(pattern, recursive=True)
    print(f"找到 {len(files)} 个会话文件，开始提取...")

    all_records = []
    total_skipped = {"bad_json": 0, "bad_structure": 0}
    files_failed = 0
    for fp in files:
        session_id = os.path.splitext(os.path.basename(fp))[0]
        try:
            records, skipped = extract_from_file(fp, session_id)
            all_records.extend(records)
            total_skipped["bad_json"] += skipped["bad_json"]
            total_skipped["bad_structure"] += skipped["bad_structure"]
        except Exception as e:
            files_failed += 1
            print(f"  [跳过整个文件] {fp}：{e}")

    self_referential_skipped = sum(1 for r in all_records if is_self_referential(r))
    all_records = [r for r in all_records if not is_self_referential(r)]

    print(f"自指过滤：跳过 {self_referential_skipped} 条涉及本项目目录（Desktop/RAG）本身的记录")
    if total_skipped["bad_json"] or total_skipped["bad_structure"] or files_failed:
        print(f"逐行跳过（独立评审D-11）：JSON格式错误 {total_skipped['bad_json']} 行，"
              f"结构异常 {total_skipped['bad_structure']} 行，整文件失败 {files_failed} 个")
    print(f"共提取到 {len(all_records)} 条工具调用+结果配对记录，开始脱敏...")

    # 注意：只脱敏 tool_input / tool_output 这两个"可能包含真实内容"的字段，
    # 不要对整条记录（包括 session_id）做脱敏——session_id 是内部分组用的
    # UUID，字符串本身长得像"32位以上乱码"，会被正则规则误判成密钥打码掉，
    # 导致所有记录的 session_id 变成同一个占位符，丢失了会话分组信息。
    anonymized_records = []
    for r in all_records:
        r2 = dict(r)
        r2["tool_input"] = anonymize(r["tool_input"])
        r2["tool_output"] = anonymize(r["tool_output"])
        anonymized_records.append(r2)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in anonymized_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"完成，已写入：{OUTPUT_FILE}")

    # 简单统计，方便核对
    from collections import Counter
    tool_counter = Counter(r["tool_name"] for r in anonymized_records)
    print("按工具类型分布（前10）：")
    for name, count in tool_counter.most_common(10):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
