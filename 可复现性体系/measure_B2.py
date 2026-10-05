#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_B2.py —— 知识挖掘（B2）的测量记录

═══ 为什么挖掘本身是 L1，检索是 L2 ═══════════════════════════════════

mine_cases.py / mine_workflows.py 是纯文本处理，不调用 embedding，
不依赖麒麟环境——**它们是 L1**，任何机器都能重跑验证挖掘出的知识单元数量。

只有「向量化 + 检索」这一步需要官方 KYSDK，属于 L2（见 measure_L2_knowledge.py）。

用法：
    python measure_B2.py
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
KNOWLEDGE_DIR = os.path.join(os.path.dirname(BASE), "知识检索")
sys.path.insert(0, KNOWLEDGE_DIR)

from measure import Measurement  # noqa: E402

DATASET = os.path.join(os.path.dirname(BASE), "多源数据整合",
                       "dataset", "memory_source_records.jsonl")


def m107():
    """案例挖掘器：四步过滤（错误信号分类 → 修复判定 → 相似度过滤 → 模板前缀过滤）
    的产出量，以及异常日检测结果。"""
    import mine_cases

    with Measurement(
        "M-107", "案例挖掘：四步过滤后的历史案例产出量（含异常日检测）", layer="L1",
        inputs=[DATASET], script=mine_cases.__file__,
        command="python mine_cases.py",
    ) as m:
        recs = mine_cases.load_dataset()

        tools = [r for r in recs if r.get("record_type") == "tool_execution"]
        errs_raw = [r for r in tools if r.get("is_error")]
        outliers = mine_cases.detect_outlier_days(errs_raw)
        m.set_value("outlier_days_count", len(outliers), unit="天")
        for o in outliers:
            m.set_value(f"outlier_{o['day']}_errors", o["error_count"], unit="条")
            m.set_value(f"outlier_{o['day']}_share_pct", o["share_pct"], unit="%")
            m.set_value(f"outlier_{o['day']}_sessions", o["sessions"], unit="个")

        cases, stats = mine_cases.mine(recs)
        for k, v in stats.items():
            m.set_value(k, v, unit="条")

        # 排除异常日后的对比统计（供报告"正常日子真实分布"引用）
        if outliers:
            outlier_days = {o["day"] for o in outliers}
            normal_recs = [r for r in recs if
                           r.get("record_type") != "tool_execution"
                           or (r.get("timestamp") or "")[:10] not in outlier_days]
            _, normal_stats = mine_cases.mine(normal_recs)
            for k in ("total_errors", "permission", "infra", "technical", "unknown"):
                m.set_value(f"normal_days_{k}", normal_stats[k], unit="条")

        print(f"异常日：{len(outliers)} 个 —— {outliers}")
        print(f"出错记录 {stats['total_errors']}：权限拒绝 {stats['permission']} "
              f"({stats['permission']/stats['total_errors']*100:.1f}%)，"
              f"基础设施故障 {stats['infra']}，"
              f"技术故障 {stats['technical']} "
              f"({stats['technical']/stats['total_errors']*100:.1f}%)")
        print(f"候选修复对 {stats['candidates_before_similarity']}，"
              f"相似度过滤剔除 {stats['rejected_by_similarity']}，"
              f"模板前缀过滤剔除 {stats['rejected_by_template_check']}，"
              f"共享修复动作去重剔除 {stats.get('deduped_shared_fix', 0)}")
        print(f"最终案例：{stats['cases_found']} 条")

        m.note("三轮人工抽样验证：第一轮(25样本)发现约60%为「下一步无关操作」误判→加相似度过滤；"
               "第二轮(20样本)扩大分类规则后精度回升但发现pscp/plink共享前缀噪声→加模板前缀过滤；"
               "第三轮(20样本)验证通过。生成评测集反向出题时又发现第五个问题：连续多次相似失败"
               "后跟一次真实重试，该重试被算法复用给前面每次失败，制造人为重复案例(10/61受影响)"
               "→加第五步去重(deduplicate_shared_fix)。同时发现07-27单日污染了91.3%权限拒绝这一"
               "头部统计，已加入通用异常日检测，不再是硬编码的一次性修正。详见知识检索/说明报告.md")


def m108():
    """流程切分器：以用户意图为锚点的工作流程产出量。"""
    import mine_workflows

    with Measurement(
        "M-108", "流程切分：用户意图关联的工作流程产出量", layer="L1",
        inputs=[DATASET], script=mine_workflows.__file__,
        command="python mine_workflows.py",
    ) as m:
        recs = mine_workflows.load_dataset()
        workflows, stats = mine_workflows.mine(recs)

        for k, v in stats.items():
            m.set_value(k, v, unit="条")

        print(f"会话交集 {stats['shared_sessions']} 个，"
              f"交集内用户输入 {stats['total_user_inputs_in_shared']} 条")
        print(f"挖出工作流程：{stats['workflows_found']} 条 "
              f"(空序列跳过 {stats['empty_sequence_skipped']}，"
              f"超长截断 {stats['truncated']})")

        m.note("独立评审 K-3 指出：原判断'72%无法归类'只对意图关键词聚类"
               "成立，可复用性体现在操作序列的重复上、不在措辞的重复上——"
               "改按(失败工具→修复工具)与(工具序列)签名聚合后确有信号，"
               "见 M-110（mine_templates.py）")


def m109():
    """mine_workflows.py 确定性回归测试 + 评测集 v1.3.0 冻结统计。

    两件不相关的事凑在一条记录里，是因为它们是同一次调查的一体两面：
    workflows.jsonl 的非确定性 bug 正是靠"评测集 doc_id 对不上索引"暴露出来的
    （见 可复现性体系/勘误与修正记录.md 第七节），修完之后自然要用同一条记录
    既验证"bug 真的修好了"，也验证"修好之后重新冻结的评测集数字对不对"。

    ⚠️ 第二轮独立复核 R2-18 指出：metric 键名此前叫 eval_set_v1_1_0_*，是
    版本号第一次从 v1.1.0 迭代时留下的历史标识符，但 title 与实测值早已是
    v1.3.0——key 名字本身自称"v1.1.0"、内容却是"169题/新sha256"，同一条
    记录里名实不符，读者光看 key 名会被误导。已改为不含版本号的
    eval_set_count/eval_set_sha256，版本号单独存成 eval_set_version（会
    随每次改版自然更新，不会重蹈"key名字里写死版本号忘记改"的覆辙）。
    """
    import hashlib
    import mine_workflows
    import test_detectors

    EVAL_SET = os.path.join(os.path.dirname(BASE), "评测集", "eval_set.jsonl")
    EVAL_FROZEN = os.path.join(os.path.dirname(BASE), "评测集", "eval_set_frozen.json")

    with Measurement(
        "M-109", "mine_workflows.py 确定性回归 + 评测集冻结统计",
        layer="L1", script=mine_workflows.__file__,
        command="python mine_workflows.py（3个独立子进程、不同哈希种子，比对md5）",
    ) as m:
        # ── 确定性回归：第二轮独立复核 R2-04 指出，此前这里是同进程内连续
        # 调用 mine() 3 次——测的只是"同一进程里反复调用是否稳定"，测不出
        # PYTHONHASHSEED 不同导致的 set 遍历顺序问题（独立评审 P-4 点名的
        # 那类真实 bug）。改为直接调用 test_detectors.py 里已验证有效的
        # 跨进程实现，而不是自己另外维护一份弱化版——同一件事只有一处
        # 真正的实现，其余地方 import 它。
        # R2-10 隔离修复后的副作用：脚本被复制进独立临时目录运行，
        # mine_workflows.py 默认按 __file__ 相对路径找数据集，在临时目录
        # 里这条相对路径不再指向真实项目树，必须显式传 RAG_DATASET。
        hashes = test_detectors.cross_process_determinism_md5s(
            mine_workflows.__file__, "workflows.jsonl",
            extra_env={"RAG_DATASET": DATASET})
        agree = 1 if len(set(hashes)) == 1 else 0
        m.set_value("determinism_runs_agree", agree)
        m.set_value("determinism_run_hashes", ",".join(hashes))
        print(f"3个独立子进程(不同哈希种子) md5：{hashes} → "
              f"{'一致' if agree else '不一致（有问题！）'}")

        # ── 评测集当前版本（v1.3.0）冻结统计（直接读冻结产出，不重新生成——
        # 生成依赖 Gemini 调用，不是纯 Python，不适合放进 L1 一键复现）
        if os.path.exists(EVAL_SET):
            with open(EVAL_SET, "rb") as f:
                content = f.read()
            sha256 = hashlib.sha256(content).hexdigest()
            count = sum(1 for line in content.split(b"\n") if line.strip())
            m.set_value("eval_set_count", count, unit="题")
            m.set_value("eval_set_sha256", sha256)
            # 版本号从**冻结记录**读，不再写死在源码里。
            # 2026-08-14 整链重跑时踩到：语料与题目全换了（95→86 条语料、
            # 169→227 题），而这里、measure_L1、measure_L2、review_and_freeze
            # 四处各写死一个 "v1.3.0"，改一处漏三处，记录里就会出现
            # 「内容是新的、版本标签是旧的」——而旧版本号已被外部锚定，
            # 混淆的代价不可逆。版本号只有冻结记录一个来源。
            _ver = "(未知)"
            if os.path.exists(EVAL_FROZEN):
                import json as _json
                try:
                    _ver = "v" + _json.load(
                        open(EVAL_FROZEN, encoding="utf-8"))["version"]
                except (OSError, KeyError, ValueError):
                    pass
            m.set_value("eval_set_version", _ver)
            print(f"评测集 {_ver}：{count} 题，sha256={sha256}")
            if os.path.exists(EVAL_FROZEN):
                import json as _json
                frozen = _json.load(open(EVAL_FROZEN, encoding="utf-8"))
                if frozen.get("sha256") != sha256:
                    m.note(f"⚠️ eval_set.jsonl 当前哈希与 eval_set_frozen.json "
                           f"记录的哈希不一致（{sha256} vs {frozen.get('sha256')}），"
                           "文件可能在冻结后被改动过")
        else:
            m.note(f"未找到 {EVAL_SET}，跳过评测集统计（不影响确定性回归结果）")

        # ── 语料覆盖率（独立评审E-7要求量化披露：出题只覆盖了多少比例的
        # 知识单元，被跳过的是哪一类）——用 eval_candidates.jsonl（复核剔除前
        # 的候选，覆盖率看的是"出题阶段覆盖了多少语料"，不受人工复核影响）
        CANDIDATES = os.path.join(os.path.dirname(BASE), "评测集", "eval_candidates.jsonl")
        cases_p = os.path.join(KNOWLEDGE_DIR, "cases.jsonl")
        workflows_p = os.path.join(KNOWLEDGE_DIR, "workflows.jsonl")
        templates_p = os.path.join(KNOWLEDGE_DIR, "templates.jsonl")
        if os.path.exists(CANDIDATES) and os.path.exists(cases_p) and os.path.exists(workflows_p):
            covered = set()
            for line in open(CANDIDATES, encoding="utf-8"):
                if line.strip():
                    covered.add(json.loads(line)["doc_id"])
            total_units = sum(1 for f in (cases_p, workflows_p, templates_p) if os.path.exists(f)
                              for l in open(f, encoding="utf-8") if l.strip())
            m.set_value("candidate_covered_units", len(covered), unit="条")
            m.set_value("candidate_total_units", total_units, unit="条")
            m.set_value("candidate_coverage_ratio", round(len(covered) / total_units, 4))
            print(f"候选覆盖率：{len(covered)}/{total_units} = "
                  f"{len(covered)/total_units*100:.1f}%")


def m110():
    """模板挖掘器（K-3 新增第三类知识）：按操作序列签名而非意图关键词聚合
    出的可复用模板产出量。"""
    import mine_templates

    cases_path = os.path.join(KNOWLEDGE_DIR, "cases.jsonl")
    workflows_path = os.path.join(KNOWLEDGE_DIR, "workflows.jsonl")

    with Measurement(
        "M-110", "模板挖掘：按操作序列签名聚合的可复用模板产出量", layer="L1",
        inputs=[cases_path, workflows_path], script=mine_templates.__file__,
        command="python mine_templates.py",
    ) as m:
        cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
        workflows = [json.loads(l) for l in open(workflows_path, encoding="utf-8") if l.strip()]

        case_templates = mine_templates.mine_case_templates(cases)
        workflow_templates = mine_templates.mine_workflow_templates(workflows)

        m.set_value("tool_switch_templates", len(case_templates), unit="条")
        m.set_value("tool_sequence_templates", len(workflow_templates), unit="条")
        m.set_value("templates_total", len(case_templates) + len(workflow_templates), unit="条")

        print(f"工具切换类模板：{len(case_templates)} 条")
        print(f"工具序列类模板：{len(workflow_templates)} 条")
        print(f"模板总计：{len(case_templates) + len(workflow_templates)} 条")

        m.note("按(失败工具,修复工具,错误类别)与(去重后有序工具名元组)两种"
               "签名分别聚合，支持度阈值2，跨会话重复才计入——不是按意图"
               "关键词聚类（那条路径已实测证明72%聚不出类）")


ALL = {"M-107": m107, "M-108": m108, "M-109": m109, "M-110": m110}


def check_preconditions():
    """同 measure_L1.check_preconditions（R3-31）：缺文件返回人话，齐了返回 None。
    本模块吃的是 知识检索/ 下的三份挖掘产物与 多源数据整合/ 的数据集。"""
    need = [
        (DATASET, "多源数据整合/dataset/"),
        (os.path.join(KNOWLEDGE_DIR, "cases.jsonl"), "知识检索/"),
        (os.path.join(KNOWLEDGE_DIR, "workflows.jsonl"), "知识检索/"),
    ]
    missing = [(p, d) for p, d in need if not os.path.exists(p)]
    if missing:
        lines = [f"缺少 {len(missing)} 个输入文件，本模块（measure_B2.py）跑不了："]
        lines += [f"    {p}" for p, _ in missing]
        lines.append("  需要完整的项目目录结构（含 "
                     + "、".join(sorted({d for _, d in missing})) + "）。")
        lines.append("  只想核对已有记录、不重新生成的话，用："
                     "python reproduce_L1.py --check-only")
        return "\n".join(lines)
    return None


if __name__ == "__main__":
    _msg = check_preconditions()
    if _msg:
        print(_msg)
        sys.exit(1)
    for mid, fn in ALL.items():
        fn()
        print()
