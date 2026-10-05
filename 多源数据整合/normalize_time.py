#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize_time.py —— 时间戳格式统一

═══ 为什么这一步是必需的，而不是可选的 ═══════════════════════════════

首版《多源数据整合》报告的"已知限制"第 2 条记录过：

    Claude Code 用 ISO8601 字符串（如 "2026-07-31T09:31:13.404Z"），
    OpenClaw 用纪元毫秒数字（如 1783332242578），
    merge_and_validate.py 目前没有做统一转换。

当时判断这是"后续若需按时间分析再补"的可选项。后来两件事把它变成了硬前置：

  1. **偏好的「版本化管理」**：题目要求偏好能版本化更新与回溯，
     那就必须能按时间排序、能界定"某个时间点之前的偏好是什么"；
  2. **评测方法的可信度设计**：偏好提取的主力评测方式是
     「按时间切分 → 用早期数据提取偏好 → 验证它能否预测留出窗口的真实行为」。
     时间戳不统一，这个切分根本做不出来。

第 2 条尤其关键——它是整个偏好评测**不依赖主观判断**的基础。

═══ 统一目标 ═══════════════════════════════════════════════════════

一律转成 **ISO8601 UTC 字符串**（形如 `2026-07-31T09:31:13.404000+00:00`）。

选 ISO8601 而不是纪元毫秒的原因：字符串按字典序排序即等于按时间排序，
在 jsonl 文本处理里可以直接 sort，不必先解析成数字；且人眼可读，便于抽查。

**原始值保留在 `timestamp_raw` 字段**，格式来源记在 `timestamp_source`——
数据加工不应丢弃原始信息，出问题时要能追溯到底是转换错了还是源数据本身就有问题。
"""

from datetime import datetime, timezone


def normalize(value):
    """把任意已知格式的时间戳转成 ISO8601 UTC 字符串。

    返回 (iso字符串, 来源格式名)；无法识别时返回 (None, "unknown")。

    支持：
      · 纪元毫秒（int/float，或纯数字字符串）—— OpenClaw 用这种
      · 纪元秒（数值明显偏小的情况）
      · ISO8601 字符串（含结尾 Z 的写法）—— Claude Code 用这种
    """
    if value is None:
        return None, "unknown"

    # ---- 数值：纪元时间 ----
    if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        # 判据：2001-09-09 之后的纪元**秒**约为 1e9，纪元**毫秒**约为 1e12。
        # 以 1e11 为界足以区分这两者（对应秒制的公元 5138 年、毫秒制的 1973 年），
        # 本项目数据都在 2020 年之后，不会落在模糊区间。
        if n > 1e11:
            dt = datetime.fromtimestamp(n / 1000.0, tz=timezone.utc)
            return dt.isoformat(), "epoch_ms"
        dt = datetime.fromtimestamp(n, tz=timezone.utc)
        return dt.isoformat(), "epoch_s"

    # ---- 字符串：ISO8601 ----
    if isinstance(value, str):
        s = value.strip()
        # Python 3.11 之前的 fromisoformat 不认结尾的 "Z"，先换成 "+00:00"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None, "unknown"
        # 没带时区的当作 UTC —— 本项目两个来源都是 UTC，不会误判
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(), "iso8601"

    return None, "unknown"


def apply_to_record(record: dict) -> dict:
    """就地给一条记录补上统一后的时间字段，返回该记录。

    · timestamp        → 统一后的 ISO8601 UTC
    · timestamp_raw    → 原始值（保留，便于追溯）
    · timestamp_source → 原始格式名（iso8601 / epoch_ms / epoch_s / unknown）
    """
    raw = record.get("timestamp")
    iso, kind = normalize(raw)
    record["timestamp_raw"] = raw
    record["timestamp_source"] = kind
    if iso is not None:
        record["timestamp"] = iso
    return record


if __name__ == "__main__":
    # 自检：两种真实格式各来一条，外加两个边界情况
    cases = [
        "2026-07-31T09:31:13.404Z",      # Claude Code 真实格式
        1783332242578,                    # OpenClaw 真实格式（纪元毫秒）
        "1783332242578",                  # 同上，但以字符串形式出现
        "2026-08-01T12:00:00+08:00",      # 带非 UTC 时区，应换算
        None,                             # 缺失
        "不是时间",                        # 无法识别
    ]
    print(f"{'原始值':<32}{'统一后':<38}来源格式")
    print("-" * 88)
    for c in cases:
        iso, kind = normalize(c)
        print(f"{str(c):<32}{str(iso):<38}{kind}")
