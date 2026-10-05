#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_user_history.py —— 提取「用户行为数据」（第二类数据源）

═══ 为什么补这一路 ═══════════════════════════════════════════════════

比赛方案题目内容 (1) 原文要求：

    「构建多源数据整合模块，支持**工具执行结果、用户行为数据、手动配置信息**等
      数据的统一接入，建立数据清洗、格式标准化与质量检验机制」

首版《多源数据整合》只做了**工具执行结果**这一类（来自 Claude Code 与 OpenClaw
两个系统）。严格说那是"同一类数据的两个来源"，而不是题目要的**三类异构数据**。

本脚本补上第二类：**用户行为数据** —— 用户实际输入过的指令历史。

数据源：`~/.claude/history.jsonl`，Claude Code 记录的用户输入历史。
字段：`display`（用户输入的原文）、`timestamp`、`project`（工作目录）、`sessionId`。

═══ 它对偏好提取的价值 ═══════════════════════════════════════════════

题目要求提取三类偏好：**操作习惯、输出风格、安全策略**。

  · 操作习惯 ← 主要来自工具执行结果（用户实际调用了什么工具、什么参数）
  · **输出风格 ← 主要来自这里**（用户怎么提要求：要不要中文、要简洁还是详细、
    要不要解释原理……这些只在用户的自然语言输入里，工具调用记录里看不到）
  · 安全策略 ← 主要来自手动配置（见 extract_manual_config.py）

也就是说，缺了这一路，"输出风格"类偏好基本没有数据支撑，只能硬编。

═══ 隐私处理 ═══════════════════════════════════════════════════════

用户输入历史是自由文本，比工具调用记录更可能夹带隐私。沿用同目录 `anonymize.py`
的四层递归脱敏规则。**但必须如实说明其局限**：正则无法可靠识别中文人名等自由文本
实体，这一点在首版报告第五节已记录，此处同样适用，需人工抽查兜底。

输出：`dataset_user_history.jsonl`
"""

import json
import os
import sys

from anonymize import anonymize
from normalize_time import apply_to_record


DEFAULT_HISTORY = os.path.join(
    os.path.expanduser("~"), ".claude", "history.jsonl")
OUTPUT = "dataset_user_history.jsonl"


def extract(history_path: str):
    """逐行解析 history.jsonl，产出统一格式的 user_input 记录。

    防御性设计与首版提取脚本一致：Claude Code 的内部文件格式随版本变化、
    官方不保证兼容，因此每行独立 try/except，个别解析不了的直接跳过，
    不让一条坏数据中断整体提取。
    """
    records = []
    skipped = 0

    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = raw.get("display")
            if not text or not str(text).strip():
                skipped += 1
                continue

            rec = {
                "source": "claude_code_history",
                "record_type": "user_input",
                # ⚠️ session_id 不能过脱敏 —— 详见下方说明
                "session_id": raw.get("sessionId"),
                "timestamp": raw.get("timestamp"),
                # ⚠️ 只对**自由文本内容**做脱敏，不要整条记录一起过。
                #
                # 首版报告第六节教训⑤已经警告过这一点：session_id 这类 UUID
                # 会被"32 位以上长随机串"规则误判为密钥而打码。本脚本初版偷懒
                # 对整条记录调了 anonymize()，结果 session_id 全变成
                # [REDACTED_LONG_TOKEN]，**直接把"用户意图 ↔ 工具调用序列"的
                # 关联链路弄断了**（实测两侧 session_id 交集从 28 掉到 0）。
                #
                # 而这条关联链路正是把"工具调用日志"升格为"工作流程知识"的关键：
                # 没有意图锚点，一串命令只是流水账；有了它才是"做某件事的方法"。
                # 一个看似无害的脱敏动作，代价是一整类知识挖不出来。
                "text": anonymize(str(text)),
                # project 是工作目录路径，与已有 14047 条记录里的文件路径同类，
                # 保持一致处理（不打码），否则跨来源的路径分析会对不上
                "project": raw.get("project"),
                # 是否带了粘贴内容——粘贴长文本本身也是一种行为特征，
                # 但粘贴的具体内容不提取（体积大且隐私风险高），只记有无
                "has_pasted_content": bool(raw.get("pastedContents")),
            }
            apply_to_record(rec)          # 统一时间戳
            records.append(rec)

    return records, skipped


def main():
    history_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HISTORY
    if not os.path.exists(history_path):
        raise SystemExit(f"找不到历史文件：{history_path}")

    records, skipped = extract(history_path)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"数据源：{history_path}")
    print(f"提取记录数：{len(records)}，跳过：{skipped}")
    print(f"输出：{OUTPUT}")

    if records:
        stamps = sorted(r["timestamp"] for r in records if r.get("timestamp"))
        if stamps:
            print(f"时间跨度：{stamps[0]}  →  {stamps[-1]}")
        # 斜杠命令（如 /model）与自然语言输入的比例——
        # 这个分布本身对"操作习惯"类偏好有参考价值
        slash = sum(1 for r in records if r["text"].lstrip().startswith("/"))
        print(f"斜杠命令 {slash} 条 / 自然语言输入 {len(records) - slash} 条")


if __name__ == "__main__":
    main()
