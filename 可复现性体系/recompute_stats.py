#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recompute_stats.py —— 用已保存的原始运行值重算统计量，不需要重新测量

═══ 为什么会需要这个脚本 ═══════════════════════════════════════════

这是一次真实的返工，值得记下来：

测量框架初版把「代表值」定为**中位数**，而此前的报告记录的是 **P95**。
重跑之后拿新的中位数去比旧的 P95，成了苹果比橘子——看起来"数字对不上"，
实际上只是口径不同。

发现后把框架改成同时保存 p50 与 p95。此时有两个选择：

  A. 重新跑一遍所有测量（麒麟上 M-202 一次要三分钟）
  B. **直接用已保存的 runs 数组重算**

选 B。这正体现了「保留原始数据」的价值：**报告里的任何统计量，
都应该能从原始运行值重新算出来**。如果做不到，说明记录得不够。

顺带也是对读者的一个承诺：你不必相信我们给的 P95，
runs 数组就在记录里，你可以自己换任何统计口径重算。

用法：
    python recompute_stats.py            # 就地更新 records/ 下所有记录
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from measure import summarize_runs, DEFAULT_RECORDS_DIR  # noqa: E402


def main():
    records_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RECORDS_DIR
    if not os.path.isdir(records_dir):
        raise SystemExit(f"记录目录不存在：{records_dir}")

    updated_files = 0
    updated_metrics = 0

    for fn in sorted(os.listdir(records_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(records_dir, fn)
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)

        changed = False
        for name, entry in rec.get("metrics", {}).items():
            runs = entry.get("runs")
            # 只处理有原始运行值的指标；单值指标（条数、维度）无须统计
            if not runs or len(runs) < 1:
                continue
            new_entry = summarize_runs(runs, entry.get("unit", ""))
            if new_entry != entry:
                old_val = entry.get("value")
                rec["metrics"][name] = new_entry
                print(f"  {rec['record_id']}.{name}: "
                      f"代表值 {old_val} → {new_entry['value']}"
                      f"（p50={new_entry['p50']}, p95={new_entry['p95']}）")
                changed = True
                updated_metrics += 1

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            updated_files += 1

    print(f"\n完成：更新 {updated_files} 个记录文件、{updated_metrics} 个指标。")
    print("统计量全部由记录中已保存的 runs 原始值重算，未重新测量。")


if __name__ == "__main__":
    main()
