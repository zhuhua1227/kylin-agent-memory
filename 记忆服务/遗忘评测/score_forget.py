#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_forget.py —— 精准遗忘评测计分（纯 Python，L1，评委任何机器可复现）

不调用任何 LLM，不依赖 KYSDK/embedding——只读 forget_eval_runner.py 已经
落盘的 runs_forget.jsonl / locate_log.jsonl，重算三个指标（《精准遗忘-
实施方案.md》§3.3/§3.5）。跑法：

    python3 记忆服务/遗忘评测/score_forget.py

三个指标：
  定位准确率  第一步返回的 top-1 doc_id == gold_doc_id 的任务比例
  遗忘生效率  确认后，gold=D 的所有评测题重跑，D 不在 top-k 的比例
  副作用率    确认后，gold≠D 的题，top-k 集合与遗忘前逐题相同的比例
              （"精准"二字的证据——理论上应为 100%，见 预登记.md）

═══ 副作用率的两个数：raw vs true（预登记.md「一条容易误判的边界」）═══

"raw"（原始）把 gold≠D 题里任何 hits_after != hits_before 都算一次副作用；
但如果 D 本来就排在某道无关题的原始 top-k 里，忘掉它之后该题第 k 名换成
下一名候选是过滤器**正常工作**的表现，不是精准性漏洞。"true"（重分类后）
把这类 D 本来就在 hits_before 里的行剔出去，只统计"D 根本不在 hits_before、
结果却变了"的行——那才是真正说不通、需要当 bug 查的异常。两个数都落进
结果文件，报告引用哪个必须写清楚是哪个。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RUNS_PATH = os.path.join(BASE, "runs_forget.jsonl")
LOCATE_PATH = os.path.join(BASE, "locate_log.jsonl")


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def score():
    if not os.path.exists(RUNS_PATH) or not os.path.exists(LOCATE_PATH):
        raise SystemExit(
            "缺少产物文件（先跑 forget_eval_runner.py，需要 embedding 服务/device_c）：\n"
            f"  {RUNS_PATH}\n  {LOCATE_PATH}")

    locate_rows = load_jsonl(LOCATE_PATH)
    runs_rows = load_jsonl(RUNS_PATH)

    # 定位准确率
    n_tasks = len(locate_rows)
    n_located = sum(1 for r in locate_rows if r["located_correctly"])
    locate_accuracy = n_located / n_tasks if n_tasks else 0.0

    # 遗忘生效率 / 副作用率——按行分类：一行是"目标题"（这条评测题的 gold
    # 就是本任务要忘掉的那个 doc）还是"旁观题"（不相关，测的是有没有殃及）。
    n_target_total, n_target_forgotten = 0, 0
    n_bystander_total, n_bystander_unchanged = 0, 0

    target_leaks = []               # 遗忘生效率不是 100% 时，落哪些题方便排查
    expected_backfills = []         # raw 副作用，但 D 本来就在 hits_before 里——正常
    genuine_anomalies = []          # D 不在 hits_before，结果却变了——真异常，要查

    for r in runs_rows:
        is_target = (r["gold_doc_id"] == r["target_doc_id"])
        if is_target:
            n_target_total += 1
            if r["target_doc_id"] not in r["hits_after"]:
                n_target_forgotten += 1
            else:
                target_leaks.append({"task_id": r["task_id"], "question_id": r["question_id"]})
        else:
            n_bystander_total += 1
            if set(r["hits_after"]) == set(r["hits_before"]):
                n_bystander_unchanged += 1
            else:
                row = {"task_id": r["task_id"], "question_id": r["question_id"],
                       "target_doc_id": r["target_doc_id"],
                       "hits_before": r["hits_before"], "hits_after": r["hits_after"]}
                if r["target_doc_id"] in r["hits_before"]:
                    expected_backfills.append(row)
                else:
                    genuine_anomalies.append(row)

    forget_effective_rate = n_target_forgotten / n_target_total if n_target_total else 0.0
    n_raw_regressions = len(expected_backfills) + len(genuine_anomalies)
    side_effect_free_rate_raw = (n_bystander_total - n_raw_regressions) / n_bystander_total \
        if n_bystander_total else 0.0
    side_effect_free_rate_true = (n_bystander_total - len(genuine_anomalies)) / n_bystander_total \
        if n_bystander_total else 0.0

    return {
        "n_tasks": n_tasks,
        "locate_accuracy": locate_accuracy,
        "n_located": n_located,
        "forget_effective_rate": forget_effective_rate,
        "n_target_total": n_target_total,
        "n_target_forgotten": n_target_forgotten,
        "side_effect_free_rate_raw": side_effect_free_rate_raw,
        "side_effect_free_rate_true": side_effect_free_rate_true,
        "n_bystander_total": n_bystander_total,
        "n_bystander_unchanged": n_bystander_unchanged,
        "n_expected_backfill": len(expected_backfills),
        "n_genuine_anomaly": len(genuine_anomalies),
        "target_leaks": target_leaks,
        "expected_backfills": expected_backfills,
        "genuine_anomalies": genuine_anomalies,
    }


def main():
    result = score()
    print(f"任务数：{result['n_tasks']}")
    print(f"定位准确率：{result['locate_accuracy']*100:.1f}%"
          f"（{result['n_located']}/{result['n_tasks']}）")
    print(f"遗忘生效率：{result['forget_effective_rate']*100:.1f}%"
          f"（{result['n_target_forgotten']}/{result['n_target_total']}）")
    print(f"副作用率（raw）：{result['side_effect_free_rate_raw']*100:.1f}%"
          f"（{result['n_bystander_total']-result['n_expected_backfill']-result['n_genuine_anomaly']}"
          f"/{result['n_bystander_total']}，未剔除「目标本来就在候选里」的正常backfill）")
    print(f"副作用率（true，预登记判据）：{result['side_effect_free_rate_true']*100:.1f}%"
          f"（{result['n_bystander_total']-result['n_genuine_anomaly']}/{result['n_bystander_total']}，"
          f"raw 里 {result['n_expected_backfill']} 条已按「D 本来就在 hits_before」剔除，"
          f"真异常 {result['n_genuine_anomaly']} 条）")
    if result["target_leaks"]:
        print(f"\n遗忘未生效的题（{len(result['target_leaks'])} 条）：")
        for x in result["target_leaks"][:20]:
            print(f"   {x}")
    if result["genuine_anomalies"]:
        print(f"\n真异常（{len(result['genuine_anomalies'])} 条，"
              f"按 §4 预登记，出现即视为 bug，不调判据）：")
        for x in result["genuine_anomalies"][:20]:
            print(f"   {x}")

    out_path = os.path.join(BASE, "score_forget_result.json")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写出 → {out_path}")
    return result


if __name__ == "__main__":
    main()
