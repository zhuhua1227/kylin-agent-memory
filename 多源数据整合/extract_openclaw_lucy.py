#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_openclaw_lucy.py —— 从 OpenClaw「lucy」agent 的会话记录提取"工具调用+执行结果"数据

⚠️ 本脚本设计为在 OpenClaw 所在的远程 Linux 机器上运行（而不是在本机 Windows 上），
   目的是让"原始飞书聊天内容 → 脱敏"这个过程全程在远程机器完成，只把脱敏后的
   结果传回本机，避免中间产生一份未脱敏的明文文件落在容易被分享的本机目录里。

范围说明（对应项目决策记录）：
    OpenClaw 当前活跃的4个agent（lucy/maya/mako/butler）中，只有 lucy 绑定的是
    用户自己私人使用的飞书账号，其余3个都直接对接真实第三方（QQ群/其他飞书群），
    经与用户确认，本次只提取 lucy 的数据。

数据来源：/root/.openclaw/agents/lucy/sessions/*.jsonl
         （注意：只处理"普通" .jsonl 消息记录文件，不含 *.trajectory.jsonl 后缀的
          OTel风格链路追踪文件——后者是全量上下文快照格式，同一条消息会在多条
          trace记录里重复出现，直接解析容易重复计数；经检查，普通 .jsonl 文件的
          message/content/tool_use/tool_result 结构与 Claude Code 的格式一致，
          足够满足"工具调用+执行结果"这一数据源的提取需求，因此本次只处理这部分，
          .trajectory.jsonl 的解析留作后续扩展，已在报告"已知限制"中说明）。

解析逻辑与 extract_claude_code.py 完全一致（同样的 tool_use/tool_result 配对方式），
因为两者的消息记录格式结构相同。
"""

import json
import glob
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anonymize import anonymize

LUCY_SESSIONS_DIR = "/root/.openclaw/agents/lucy/sessions"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_openclaw_lucy.jsonl")


def flatten_content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(f"[non-text block: {block.get('type', '?')}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def extract_from_file(filepath, session_id):
    """OpenClaw 的消息格式经实测与 Claude Code 不同（字段名不一样），具体差异：

    - 工具调用：是 content 数组里 type=="toolCall" 的块，字段叫
      id / name / arguments（Claude Code 对应叫 id / name / input）；
    - 工具执行结果：不是嵌套在某个 content 块里，而是【独立的一整条消息】，
      这条消息的 role=="toolResult"，用 toolCallId 字段（而不是content块内的
      tool_use_id）指回对应的调用，结果本身在这条消息的 content 数组里
      （type=="text" 的块）。

    两种格式配对的思路是一样的（先收集调用、再收集结果、按 id 配对），
    只是要读的字段名不同。
    """
    tool_uses = {}
    tool_results = {}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue

            try:
                message = record.get("message", {})
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                content = message.get("content")
                timestamp = message.get("timestamp") or record.get("timestamp")

                # ① 工具调用：从 content 数组里找 type=="toolCall" 的块
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            tool_uses[block.get("id")] = {
                                "name": block.get("name"),
                                "input": block.get("arguments"),
                                "timestamp": timestamp,
                            }

                # ② 工具执行结果：整条消息 role=="toolResult"
                if role == "toolResult":
                    call_id = message.get("toolCallId")
                    tool_results[call_id] = {
                        "output": flatten_content_to_text(content),
                        "is_error": message.get("isError", False),
                    }
            except Exception:
                continue

    records = []
    for tid, use_info in tool_uses.items():
        result_info = tool_results.get(tid)
        if result_info is None:
            continue
        records.append({
            "source": "openclaw_lucy",
            "session_id": session_id,
            "timestamp": use_info["timestamp"],
            "tool_name": use_info["name"],
            "tool_input": use_info["input"],
            "tool_output": result_info["output"],
            "is_error": result_info["is_error"],
        })
    return records


def main():
    # 只取"普通" .jsonl，排除 *.trajectory.jsonl
    all_jsonl = glob.glob(os.path.join(LUCY_SESSIONS_DIR, "*.jsonl"))
    files = [f for f in all_jsonl if not f.endswith(".trajectory.jsonl")]
    print(f"找到 {len(files)} 个 lucy 会话文件（已排除 trajectory 格式），开始提取...")

    all_records = []
    for fp in files:
        session_id = os.path.splitext(os.path.basename(fp))[0]
        try:
            records = extract_from_file(fp, session_id)
            all_records.extend(records)
        except Exception as e:
            print(f"  [跳过] {fp}：{e}")

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

    from collections import Counter
    tool_counter = Counter(r["tool_name"] for r in anonymized_records)
    print("按工具类型分布：")
    for name, count in tool_counter.most_common(20):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
