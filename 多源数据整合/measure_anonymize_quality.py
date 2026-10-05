#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_anonymize_quality.py —— 脱敏能力的召回率/精确率/误伤率实测（独立评审D-8/D-9）

此前全项目唯一的脱敏测试是 7 条手工用例，且只测了个别正则，从未有过系统性的
召回率（真正的敏感内容有多少被打了码）/精确率（打了码的里有多少其实不该打）
测量——"我们人工看了几十条"不构成证据，召回率/精确率数字才是。

用法：
    python measure_anonymize_quality.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from anonymize import _scrub_string  # noqa: E402


def load_testset():
    path = os.path.join(BASE, "labeled_redaction_testset.json")
    return json.load(open(path, encoding="utf-8"))


def main():
    data = load_testset()

    tp = fn = 0  # positive案例：应打码。tp=确实打了码，fn=漏检
    tn = fp = 0  # negative案例：不该打码。tn=确实没动，fp=误伤
    fn_cases, fp_cases = [], []

    for case in data["positive"]:
        out = _scrub_string(case["text"])
        if out != case["text"]:
            tp += 1
        else:
            fn += 1
            fn_cases.append(case)

    for case in data["negative"]:
        out = _scrub_string(case["text"])
        if out == case["text"]:
            tn += 1
        else:
            fp += 1
            fp_cases.append((case, out))

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    false_positive_rate = fp / (fp + tn) if (fp + tn) else 0.0

    print(f"标注集：positive {len(data['positive'])} 条，negative {len(data['negative'])} 条")
    print(f"召回率（敏感内容被正确打码的比例）：{tp}/{tp+fn} = {recall*100:.1f}%")
    print(f"精确率（打码里有多少确实该打）：{tp}/{tp+fp} = {precision*100:.1f}%")
    print(f"误伤率（不该打码的里有多少被误伤）：{fp}/{fp+tn} = {false_positive_rate*100:.1f}%")

    if fn_cases:
        print(f"\n漏检（{len(fn_cases)} 条，应打码但没打）：")
        for c in fn_cases:
            print(f"  [{c['id']}] {c['text']!r}  —— {c['why']}")

    if fp_cases:
        print(f"\n误伤（{len(fp_cases)} 条，不该打码但被打了）：")
        for c, out in fp_cases:
            print(f"  [{c['id']}] {c['text']!r}  —— {c['why']}")
            print(f"        -> {out!r}")

    result = {
        "positive_count": len(data["positive"]),
        "negative_count": len(data["negative"]),
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "fn_ids": [c["id"] for c in fn_cases],
        "fp_ids": [c["id"] for c, _ in fp_cases],
    }
    out_path = os.path.join(BASE, "anonymize_quality_result.json")
    json.dump(result, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n写入：{out_path}")


if __name__ == "__main__":
    main()
