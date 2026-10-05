#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_preferences.py —— 偏好提取模块的测量记录

═══ 为什么是 L1 ═══════════════════════════════════════════════════

`mine_preferences.py` 的提取、切分、评测全部是纯 Python 规则/统计
处理，不调用 embedding、不依赖麒麟环境——同 measure_conflicts.py 的
分层理由。

═══ 四个 M-4xx 分别测什么 ═══════════════════════════════════════════

  M-401  提取产出量（三类偏好各自的量级，含哪一类是真实空结果）
  M-402  行为预测：准确率 vs 多数基线 vs 提升幅度（本模块的核心指标，
         按 category 分开报，不合成一个数——设计方案 §六.3/§九第2点）
  M-403  切分安全性：会话完整性 100%（不允许"部分通过"）
  M-404  跨场景：windows/kylin 两侧的偏好支持度分布（赛题"跨场景适配"
         的量化披露；本项目老数据几乎全部是 windows 场景，如实报告
         "kylin 侧样本不足，无法做有统计意义的跨场景对比"，不假装能

用法：
    python measure_preferences.py            # 跑全部
    python measure_preferences.py M-402       # 只跑指定一项
"""

import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)
KNOWLEDGE_DIR = os.path.join(RAG, "知识检索")
sys.path.insert(0, KNOWLEDGE_DIR)

from measure import Measurement  # noqa: E402

DATASET = os.path.join(RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")


def _load_dataset():
    recs = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


# ═════════════════════════════════════════════════════ M-401 提取产出量

def m401():
    with Measurement(
        "M-401", "偏好提取：三类产出量与门槛过滤统计", layer="L1",
        inputs=[DATASET], script=os.path.join(KNOWLEDGE_DIR, "mine_preferences.py"),
        command="python mine_preferences.py",
    ) as m:
        import mine_preferences

        recs = _load_dataset()
        prefs, stats = mine_preferences.extract(recs)
        for k, v in stats.items():
            if isinstance(v, (int, float, bool)):
                m.set_value(k, v, unit="条" if "extracted" in k or "rejected" in k else "")

        by_category = Counter(p["category"] for p in prefs)
        for cat in ("operation_habit", "output_style", "safety_policy"):
            m.set_value(f"category_{cat}", by_category.get(cat, 0), unit="条")

        # 偏好版本化（设计方案 §5.1：偏好变更就是一种知识冲突）——复用
        # 知识冲突模块的 adjudicate()，不重新发明版本化机制。
        pref_conflicts = mine_preferences.detect_preference_conflicts(recs)
        m.set_value("preference_version_changes", len(pref_conflicts), unit="条")

        print(f"提取合计 {len(prefs)} 条，按类别：{dict(by_category)}")
        print(f"过滤统计：{stats}")
        print(f"偏好版本变更（复用知识冲突裁决器）：{len(pref_conflicts)} 条")
        for c in pref_conflicts:
            print(f"  {c['stable_key']}: {c['old_choice']} → {c['new_choice']} "
                  f"（{c['type']}，{c['verdict']}）")

        m.note("safety_policy 实测 0 条——865 条 user_rejected 记录里 92.3% 来自"
               "2026-07-27 单一会话（session=bada921e）的批处理污染，与"
               "mine_cases.py 文档字符串记录的同一枚地雷；排除异常日后正常"
               "拒绝率仅 0.8%，无任何命令类型有统计支持的高拒绝率。如实"
               "报告为空结果，不强行调松门槛凑出'高证据强度'的结论。")
        m.note("output_style 只在收窄 DOC_STYLE_EXTENSIONS 为设计方案原定的"
               ".md/.txt/.json 三种后才有产出（1条）；混入.html/.yaml/.csv"
               "时 .md 占比被稀释到 0.65 门槛以下，产出为 0——修的是范围"
               "定义错误，不是调松门槛，见 mine_preferences.py 对应注释。")


# ═════════════════════════════════════════════════════ M-402 行为预测：准确率/基线/提升幅度

def m402():
    with Measurement(
        "M-402", "偏好提取：留出期行为预测准确率 vs 多数基线 vs 提升幅度", layer="L1",
        inputs=[DATASET], script=os.path.join(KNOWLEDGE_DIR, "mine_preferences.py"),
        command="python measure_preferences.py M-402",
    ) as m:
        import mine_preferences

        recs = _load_dataset()
        result = mine_preferences.evaluate(recs)
        m.set_value("train_sessions", result["train_sessions"], unit="个")
        m.set_value("test_sessions", result["test_sessions"], unit="个")
        m.set_value("preferences_extracted", result["preferences_extracted"], unit="条")

        for cat, r in result["by_category"].items():
            m.set_value(f"{cat}_n_trigger", r["n_trigger"], unit="次")
            if r["accuracy"] is None:
                continue
            m.set_value(f"{cat}_accuracy", r["accuracy"])
            m.set_value(f"{cat}_accuracy_ci_lo", r["accuracy_wilson_ci"][0])
            m.set_value(f"{cat}_accuracy_ci_hi", r["accuracy_wilson_ci"][1])
            m.set_value(f"{cat}_baseline", r["baseline"])
            m.set_value(f"{cat}_baseline_ci_lo", r["baseline_wilson_ci"][0])
            m.set_value(f"{cat}_baseline_ci_hi", r["baseline_wilson_ci"][1])
            m.set_value(f"{cat}_lift", r["lift"])
            m.set_value(f"{cat}_lift_ci_lo", r["lift_ci_conservative"][0])
            m.set_value(f"{cat}_lift_ci_hi", r["lift_ci_conservative"][1])

        print(f"训练期 {result['train_sessions']} 会话 / 留出期 {result['test_sessions']} 会话")
        for cat, r in result["by_category"].items():
            if r["accuracy"] is None:
                print(f"  {cat}: n=0，无法评测（提取阶段无产出）")
                continue
            print(f"  {cat}: n={r['n_trigger']} 准确率={r['accuracy']*100:.1f}% "
                  f"基线={r['baseline']*100:.1f}% 提升={r['lift']*100:+.1f}pp "
                  f"提升区间(保守)=[{r['lift_ci_conservative'][0]*100:.1f}%, "
                  f"{r['lift_ci_conservative'][1]*100:.1f}%]")

        oh = result["by_category"].get("operation_habit") or {}
        if oh.get("lift_ci_conservative") and oh["lift_ci_conservative"][0] > 0:
            m.note("operation_habit 提升幅度区间下界>0，可以确信提取器优于"
                   "多数基线（这是本模块唯一有统计显著提升的类别）。")
        os_r = result["by_category"].get("output_style") or {}
        if os_r.get("lift") == 0:
            m.note("output_style 提升幅度=0：该类别当前只挖到一个触发情境"
                   "（doc_format），类别级多数基线与该情境的多数在数学上"
                   "恒等，不是提取器无效，是这一类目前只有一个候选情境"
                   "——按赛题'未达基础指标需明确优化方向'如实处理：优化"
                   "方向是拓宽输出风格的情境切分（如按目录/项目区分），"
                   "不是这个数字本身有问题。")
        sp = result["by_category"].get("safety_policy") or {}
        if sp.get("n_trigger") == 0:
            m.note("safety_policy n=0：提取阶段已是空集，评测阶段自然无"
                   "可评测对象，与 M-401 的空结果一致，不是评测环节的"
                   "独立问题。")


# ═════════════════════════════════════════════════════ M-403 切分安全性

def m403():
    with Measurement(
        "M-403", "偏好提取：会话完整性切分（零泄漏）", layer="L1",
        inputs=[DATASET], script=os.path.join(KNOWLEDGE_DIR, "mine_preferences.py"),
        command="python measure_preferences.py M-403",
    ) as m:
        import mine_preferences

        recs = _load_dataset()
        train, test = mine_preferences.split_by_time_keeping_sessions(recs)
        train_sess = set(r.get("session_id") for r in train if r.get("session_id"))
        test_sess = set(r.get("session_id") for r in test if r.get("session_id"))
        overlap = train_sess & test_sess

        m.set_value("train_sessions", len(train_sess), unit="个")
        m.set_value("test_sessions", len(test_sess), unit="个")
        m.set_value("session_overlap", len(overlap), unit="个")

        print(f"训练期 {len(train_sess)} 会话 / 留出期 {len(test_sess)} 会话 / "
              f"重叠 {len(overlap)} 会话")

        # 对照：本数据集最长会话横跨 23 天（知识冲突模块挖掘时的实测），
        # 朴素按时间戳切分必定切断它——用真实这个会话核对本模块的切分
        # 确实把它完整保留在一侧，不是理论上安全，是对着已知的真实
        # 高风险案例验证过。
        long_session = "1cd1ff9f-63ca-40ff-ad37-fdbe63256352"
        long_session_in_train = long_session in train_sess
        long_session_in_test = long_session in test_sess
        m.set_value("known_23day_session_kept_together",
                    long_session_in_train != long_session_in_test)
        print(f"已知横跨23天的会话（{long_session[:8]}）："
              f"在训练期={long_session_in_train} 在留出期={long_session_in_test}")

        if overlap:
            raise AssertionError(f"会话切分泄漏：{overlap} 同时出现在训练期与留出期")
        if long_session_in_train == long_session_in_test:
            raise AssertionError("已知的23天长会话未被完整保留在一侧（两侧都没有或被切断）")


# ═════════════════════════════════════════════════════ M-404 跨场景分布

def m404():
    with Measurement(
        "M-404", "偏好提取：跨场景（windows/kylin）支持度分布披露", layer="L1",
        inputs=[DATASET], script=os.path.join(KNOWLEDGE_DIR, "mine_preferences.py"),
        command="python measure_preferences.py M-404",
    ) as m:
        import mine_preferences

        recs = _load_dataset()
        prefs, _ = mine_preferences.extract(recs)

        total_by_scenario = Counter()
        for p in prefs:
            for scen, n in p["scenario"]["n_by_scenario"].items():
                total_by_scenario[scen] += n

        for scen in ("windows", "kylin", "unknown"):
            m.set_value(f"scenario_{scen}_count", total_by_scenario.get(scen, 0), unit="次")
        total = sum(total_by_scenario.values()) or 1
        windows_ratio = total_by_scenario.get("windows", 0) / total
        kylin_ratio = total_by_scenario.get("kylin", 0) / total
        m.set_value("scenario_windows_ratio", round(windows_ratio, 4))
        m.set_value("scenario_kylin_ratio", round(kylin_ratio, 4))

        print(f"场景分布：{dict(total_by_scenario)}")
        print(f"windows占比={windows_ratio*100:.1f}% kylin占比={kylin_ratio*100:.1f}%")

        m.note("本项目老数据集（Windows笔记本真实开发记录）几乎全部是 windows "
               "场景，kylin 场景样本量不足以做有统计意义的跨场景对比——与"
               "知识冲突模块 §一.2 记录的同一条环境边界（'新的Kylin目标机"
               "数据冲突尚未长出'）。scenario.n_by_scenario 字段与"
               "detect_scenario() 机制已就绪、可直接用，卡的是数据积累"
               "时间，不是机制未实现。")


# ═════════════════════════════════════════════════════ 入口

ALL = {"M-401": m401, "M-402": m402, "M-403": m403, "M-404": m404}


def check_preconditions():
    """缺文件返回人话，齐了返回 None（同 measure_conflicts.py 的模式，R3-31）。"""
    if not os.path.exists(DATASET):
        return (f"缺少输入文件：{DATASET}\n"
               "  只想核对已有记录、不重新生成的话，用："
               "python reproduce_L1.py --check-only")
    return None


if __name__ == "__main__":
    _msg = check_preconditions()
    if _msg:
        print(_msg)
        sys.exit(1)
    targets = sys.argv[1:] or list(ALL)
    for mid in targets:
        if mid not in ALL:
            print(f"未知编号：{mid}（可选：{', '.join(ALL)}）")
            continue
        ALL[mid]()
        print()
