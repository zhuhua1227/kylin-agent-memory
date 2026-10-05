#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_conflicts.py —— 知识冲突处理模块的测量记录

═══ 为什么是 L1 ═══════════════════════════════════════════════════

`mine_conflicts.py` 的挖掘、`is_conflict()`、`adjudicate()`、
`retrieve_old()` 全部是纯 Python 规则/字符串处理，不调用 embedding、
不依赖麒麟环境——跟 mine_cases.py/mine_workflows.py 同一类，任何机器
都能重跑验证（同 measure_B2.py 的分层理由）。

═══ 三个子指标为什么分开测、不合成一个数 ═══════════════════════════════

设计方案 §六.1 明确要求拆开：判定准确率（是不是冲突）、裁决正确率
（谁生效/什么类型/为什么），回溯完整性（旧知识是否仍可查）——这是前四轮
"总体 vs 分档"教训的直接应用，合成一个数会互相掩盖：is_conflict() 的
判定准确率(M-302)高不代表 adjudicate() 的类型判断(M-303)也准，混在一起
报告会误导。

用法：
    python measure_conflicts.py            # 跑全部
    python measure_conflicts.py M-302       # 只跑指定一项
"""

import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)
KNOWLEDGE_DIR = os.path.join(RAG, "知识检索")
sys.path.insert(0, KNOWLEDGE_DIR)

from measure import Measurement, wilson_ci  # noqa: E402

CONFLICTS_POOL = os.path.join(KNOWLEDGE_DIR, "conflicts.jsonl")
EVAL_SET = os.path.join(KNOWLEDGE_DIR, "conflicts_eval.jsonl")
DATASET = os.path.join(RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")
# 留出集裁决结果（《留出集裁决结果记录.md》的机器可读版，2026-09-07 单人全量盲测）
HOLDOUT_ADJUDICATION = os.path.join(KNOWLEDGE_DIR, "全量单人复核-裁决.json")


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _pair_from_eval_row(row):
    """把 conflicts_eval.jsonl 一行（_claim() 产出的 schema：claim/
    asserted_at）转成 mine_conflicts.is_conflict()/adjudicate() 期望的
    字段名（text/timestamp）。两边字段名不同是因为评测集是给人看的
    摘要格式，裁决器吃的是原始记录格式，转换只在这一处做，不让两套
    命名散到各个调用点。"""
    pair = {"kind": row["old"]["kind"]}
    pair["old"] = {**row["old"], "text": row["old"]["claim"],
                    "timestamp": row["old"]["asserted_at"]}
    pair["new"] = {**row["new"], "text": row["new"]["claim"],
                    "timestamp": row["new"]["asserted_at"]}
    return pair


# ═════════════════════════════════════════════════════ M-301 挖掘产出统计

def m301():
    with Measurement(
        "M-301", "知识冲突挖掘：候选池构成（含两轮方法修正前后对比）", layer="L1",
        inputs=[DATASET], script=os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        command="python mine_conflicts.py",
    ) as m:
        import mine_conflicts

        recs = []
        with open(mine_conflicts.DATASET, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))

        conflicts, stats = mine_conflicts.mine(recs)
        for k, v in stats.items():
            m.set_value(k, v, unit="条")

        print(f"候选池合计 {len(conflicts)} 条，正例（structural candidate）"
              f"{stats['positive_candidates_total']} 条")
        print(f"doc_revision: append_only={stats['rejected_by_append_only']} "
              f"same_sitting={stats['rejected_by_same_session']} "
              f"candidate={stats['doc_revision_candidates']}")
        print(f"case_signature: groups={stats['case_signature_groups']} "
              f"rejected_by_low_text_overlap={stats.get('rejected_by_low_text_overlap', 0)} "
              f"candidate={stats['case_conflict_candidates']}")
        print(f"cmd_drift(S1): rejected_by_non_bash={stats['rejected_by_non_bash']} "
              f"rejected_by_same_day={stats['rejected_by_same_day']} "
              f"cmd_rejected_by_same_session={stats['cmd_rejected_by_same_session']} "
              f"cmd_rejected_by_permission={stats['cmd_rejected_by_permission']} "
              f"rejected_by_high_similarity={stats['rejected_by_high_similarity']} "
              f"candidate={stats['cmd_drift_candidates']}")

        m.note("S1（命令输出漂移）实测 0 条正例：当前数据集里还没有任何一条"
               "命令跨天且跨会话被重复执行过（《每日锚点16条.md》要求的"
               "每日固定读数尚未开始积累），这条矿脉是为其供给方投入使用"
               "后才会产出候选的客观通道，不是实现有问题——detector 本身"
               "的判据正确性由 test_detectors.py 的阴性验证与 mutate.py "
               "的变异测试单独保证（见 S1检测器-实施方案.md）。")
        m.note("S3（case签名分组）实测 0 条正例——4 组签名分组全部因文本重叠度"
               "过滤剔除（见 mine_conflicts.py 的 CASE_SIGNATURE_OVERLAP_THRESHOLD "
               "注释：Exit code/Traceback 样板文本把不相关事件聚成了同一组），"
               "与设计方案估算的'5~8组'不符，如实报告而非调低过滤阈值凑数。")
        m.note("S2（doc revision）经历两轮真实方法修正：①最初的'同会话即排除'"
               "判据被发现会误杀跨天恢复的长驻会话（实测一个会话横跨23天），"
               "改为'同会话且同一天'后候选从4条回升到19条；②append_only判据"
               "从'前缀匹配'放宽为'子串匹配'后剔除了2条MEMORY.md索引行插入"
               "造成的假阳性。两次修正均有实测数字对比，详见 mine_conflicts.py"
               "对应位置的注释。")


# ═════════════════════════════════════════════════════ M-302 判定准确率

def m302():
    with Measurement(
        "M-302", "知识冲突判定准确率（is_conflict，36对含负例）", layer="L1",
        inputs=[EVAL_SET], script=os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        command="python measure_conflicts.py M-302",
    ) as m:
        import mine_conflicts

        rows = _load_jsonl(EVAL_SET)
        tp = fp = tn = fn = 0
        wrong = []
        for r in rows:
            pair = _pair_from_eval_row(r)
            pred = mine_conflicts.is_conflict(pair)
            actual = (r["label"] == "conflict")
            if pred and actual:
                tp += 1
            elif pred and not actual:
                fp += 1
                wrong.append(r["conflict_id"])
            elif not pred and actual:
                fn += 1
                wrong.append(r["conflict_id"])
            else:
                tn += 1

        n = len(rows)
        acc = (tp + tn) / n
        lo, hi = wilson_ci(tp + tn, n)
        m.set_value("n", n, unit="对")
        m.set_value("positive_n", tp + fn, unit="对")
        m.set_value("negative_n", tn + fp, unit="对")
        m.set_value("tp", tp)
        m.set_value("fp", fp)
        m.set_value("tn", tn)
        m.set_value("fn", fn)
        m.set_value("accuracy", round(acc, 4))
        m.set_value("wilson_ci_lo", round(lo, 4))
        m.set_value("wilson_ci_hi", round(hi, 4))

        print(f"判定准确率 {tp+tn}/{n} = {acc*100:.1f}%（Wilson 95% CI "
              f"[{lo*100:.1f}%, {hi*100:.1f}%]）")
        if wrong:
            print(f"误判：{wrong}")

        m.note(f"n={n} < 50：样本量不足以支撑对 88% 门槛的可靠判定，"
               f"Wilson 区间下界 {lo*100:.1f}% 是最诚实的表述方式。")
        if fp:
            m.note(f"已知误判 K-0067（结构层判定为候选，人工复核后否决）：纯措辞"
                   f"精简无信息差异，结构规则（同文件+非纯追加+跨会话跨天）无法"
                   f"区分'有意义的修订'与'无意义的措辞调整'，这是 L1 结构层"
                   f"本身的能力边界，不是可简单打补丁修掉的实现 bug——"
                   f"《知识冲突处理说明报告.md》有专门讨论。")


# ═════════════════════════════════════════════════════ M-303 裁决正确率

def m303():
    with Measurement(
        "M-303", "知识冲突裁决正确率（adjudicate，19对已确认冲突正例）", layer="L1",
        inputs=[EVAL_SET], script=os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        command="python measure_conflicts.py M-303",
    ) as m:
        import mine_conflicts

        rows = [r for r in _load_jsonl(EVAL_SET) if r["label"] == "conflict"]
        verdict_ok = type_ok = reason_ok = 0
        type_wrong = []
        for r in rows:
            pair = _pair_from_eval_row(r)
            got = mine_conflicts.adjudicate(pair)
            if got["verdict"] == "new_supersedes_old":
                verdict_ok += 1
            if got["type"] == r["type"]:
                type_ok += 1
            else:
                type_wrong.append((r["conflict_id"], r["type"], got["type"]))
            if got["reason_kind"] == r["reason_kind"]:
                reason_ok += 1

        n = len(rows)
        v_acc, t_acc, r_acc = verdict_ok / n, type_ok / n, reason_ok / n
        v_lo, v_hi = wilson_ci(verdict_ok, n)
        t_lo, t_hi = wilson_ci(type_ok, n)
        r_lo, r_hi = wilson_ci(reason_ok, n)

        m.set_value("n", n, unit="对")
        m.set_value("verdict_accuracy", round(v_acc, 4))
        m.set_value("verdict_wilson_ci_lo", round(v_lo, 4))
        m.set_value("verdict_wilson_ci_hi", round(v_hi, 4))
        m.set_value("type_accuracy", round(t_acc, 4))
        m.set_value("type_wilson_ci_lo", round(t_lo, 4))
        m.set_value("type_wilson_ci_hi", round(t_hi, 4))
        m.set_value("reason_kind_accuracy", round(r_acc, 4))
        m.set_value("reason_kind_wilson_ci_lo", round(r_lo, 4))
        m.set_value("reason_kind_wilson_ci_hi", round(r_hi, 4))

        print(f"裁决三层准确率（n={n}）：")
        print(f"  谁生效(verdict)   {verdict_ok}/{n} = {v_acc*100:.1f}%  "
              f"CI[{v_lo*100:.1f}%, {v_hi*100:.1f}%]")
        print(f"  类型(T1-T4)       {type_ok}/{n} = {t_acc*100:.1f}%  "
              f"CI[{t_lo*100:.1f}%, {t_hi*100:.1f}%]")
        print(f"  reason_kind       {reason_ok}/{n} = {r_acc*100:.1f}%  "
              f"CI[{r_lo*100:.1f}%, {r_hi*100:.1f}%]")
        print(f"  类型误判：{type_wrong}")

        m.note("谁生效(verdict)在本数据集上 19/19=100%——但这是问题结构决定的"
               "（19条真实案例里'更晚的断言正确'无一例外成立，adjudicate()的"
               "verdict规则本质是'总是选更新的'，在此数据集与人工标注恰好"
               "吻合，不代表规则验证了比时间序更难的语义判断，报告不会"
               "把这条当成规则裁决器'很聪明'的证据）。")
        m.note("T1~T4 类型判断是三层里最弱的一层：加入环境变更关键词"
               "（ENV_CHANGE_PATTERNS）前 7/18=38.9%，加入后 8/18=44.4%"
               "（v1.2.0评测集，n=18）。v1.3.0起源查找修复后评测集扩至n=19"
               "（新增K-0052，起源修复前被误判same_sitting漏检），类型判断"
               "为8/19=42.1%——K-0052被adjudicate()判为T4，金标为T1，是"
               "随样本扩充新暴露的真实类型误判，不是代码改动导致的回归。"
               "人工复核过程中也发现同一现实事件（做种盘拔除）在不同上下文"
               "被人工标成了不同类型（K-0024标T3、K-0025标T1），说明T1/T3"
               "在'事实因环境变化而更新'这类场景边界本身就模糊，不是规则"
               "没写好，如实报告不继续调参拟合。")


# ═════════════════════════════════════════════════════ M-304 回溯完整性

def m304():
    with Measurement(
        "M-304", "知识冲突回溯完整性（retrieve_old）", layer="L1",
        inputs=[CONFLICTS_POOL], script=os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        command="python measure_conflicts.py M-304",
    ) as m:
        import mine_conflicts

        candidates = _load_jsonl(CONFLICTS_POOL)
        index = mine_conflicts._all_known_stable_keys(candidates)

        eval_rows = [r for r in _load_jsonl(EVAL_SET)
                     if not r["conflict_id"].startswith("U-")]  # 人工构造的无关对不在挖掘索引里
        ok = 0
        for r in eval_rows:
            try:
                mine_conflicts.retrieve_old(r["old"]["stable_key"], index)
                mine_conflicts.retrieve_old(r["new"]["stable_key"], index)
                ok += 1
            except KeyError:
                pass

        raised = False
        try:
            mine_conflicts.retrieve_old("this-stable-key-does-not-exist", index)
        except KeyError:
            raised = True

        m.set_value("eval_pairs_checked", len(eval_rows), unit="对")
        m.set_value("eval_pairs_both_sides_retrievable", ok, unit="对")
        m.set_value("missing_key_raises", raised)
        print(f"评测集里 {ok}/{len(eval_rows)} 对的新旧两侧都能按 stable_key 回溯取回")
        print(f"故意查一个不存在的 stable_key，是否正确报错：{raised}")

        m.note("这是数据层的回溯完整性（stable_key索引到原始记录），不是"
               "检索侧的回溯（supersedes/superseded_by接入索引后按查询返回"
               "旧版本），后者是第3天检索侧集成的范畴，另在报告里说明边界。")

        if ok != len(eval_rows) or not raised:
            raise AssertionError(
                f"回溯完整性未达 100%（{ok}/{len(eval_rows)}）或缺失键未正确报错"
                f"（missing_key_raises={raised})——这是本模块的硬门槛，不能'部分"
                f"通过'，出现任一失败都必须当场看见，不能被 note 悄悄带过。")


# ═════════════════════════════════════════════════════ M-305 规则冻结哈希 + 分界线 + 确定性

def _sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def m305():
    """冲突判定规则冻结锚点（《冲突规则冻结-执行方案.md》§三）。

    固化三件事，都可被第三方在无麒麟环境下五分钟内证伪：
      ① 规则代码的 sha256——09-02 重算，一致则"规则先于留出集数据"成立；
      ② 开发集 / 留出集分界线——**按数据日期，不按行号**（v1.3.0 起）：
         new.asserted_at >= 2026-08-26 的候选＝留出集。行号定义（旧：
         "第 150 行起"）在起源查找修复重挖后即失效，见《冻结修订记录 #1》§6。
         conflicts.jsonl / conflicts_eval.jsonl 的行数仍登记为规模快照。
      ③ conflicts_eval.jsonl 可确定性重建——build_eval_set() 连调两次
         必须逐字节一致（v1.1.0 修掉 adjudicated_at 用 datetime.now() 的
         缺陷后才成立，这条就是那次修复的阴性验证）。
    """
    import review_and_freeze_conflicts as rf

    RULE_FILES = {
        "mine_conflicts_sha256": os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        "review_and_freeze_conflicts_sha256": os.path.join(KNOWLEDGE_DIR, "review_and_freeze_conflicts.py"),
        "measure_conflicts_sha256": os.path.join(BASE, "measure_conflicts.py"),
        "mine_cases_sha256": os.path.join(KNOWLEDGE_DIR, "mine_cases.py"),  # mine_conflicts 依赖它的 PERMISSION/INFRA 正则
    }

    with Measurement(
        "M-305", "冲突判定规则冻结锚点（rule sha256 + 开发/留出分界日期 + 评测集确定性）",
        layer="L1", script=os.path.join(KNOWLEDGE_DIR, "review_and_freeze_conflicts.py"),
        command="python measure_conflicts.py M-305",
        inputs=[CONFLICTS_POOL, EVAL_SET],
    ) as m:
        for key, path in RULE_FILES.items():
            digest = _sha256_of(path)
            m.set_value("conflict_rules_" + key, digest)
            print(f"  {key:38s} {digest}")

        pool_lines = sum(1 for _l in open(CONFLICTS_POOL, encoding="utf-8") if _l.strip())
        eval_lines = sum(1 for _l in open(EVAL_SET, encoding="utf-8") if _l.strip())
        m.set_value("conflicts_pool_lines", pool_lines, unit="行")
        m.set_value("conflicts_eval_lines", eval_lines, unit="行")
        m.set_value("conflicts_pool_sha256", _sha256_of(CONFLICTS_POOL))
        m.set_value("conflicts_eval_sha256", _sha256_of(EVAL_SET))
        print(f"  规模快照：conflicts.jsonl {pool_lines} 行 / conflicts_eval.jsonl {eval_lines} 行")

        # 开发集 / 留出集分界线：按数据日期（不按行号——行号不耐重挖，
        # 见《冻结修订记录 #1》§6）。重算多少次都稳定。
        _pool = _load_jsonl(CONFLICTS_POOL)
        _dev, _holdout, _undated = rf.split_dev_holdout(_pool)
        m.set_value("dev_holdout_boundary_date", rf.HOLDOUT_BOUNDARY_DATE)
        m.set_value("dev_candidate_count", len(_dev), unit="条")
        m.set_value("holdout_candidate_count", len(_holdout), unit="条")
        m.set_value("undated_s4_signal_count", len(_undated), unit="条")
        print(f"  分界线（按数据日期 new.asserted_at >= {rf.HOLDOUT_BOUNDARY_DATE}）："
              f"开发集 {len(_dev)} 条 / 留出集 {len(_holdout)} 条 / 无日期(S4信号) {len(_undated)} 条")

        # ③ 确定性：同一份候选池，build_eval_set() 连调两次逐字节一致
        cand = rf._load_candidates()
        dump1 = json.dumps(rf.build_eval_set(cand), ensure_ascii=False, sort_keys=True)
        dump2 = json.dumps(rf.build_eval_set(cand), ensure_ascii=False, sort_keys=True)
        agree = 1 if dump1 == dump2 else 0
        m.set_value("eval_set_build_determinism_agree", agree)
        m.set_value("eval_set_frozen_version", rf.VERSION)
        print(f"  build_eval_set() 连调两次逐字节一致：{'是' if agree else '否（有问题！）'}  "
              f"（评测集冻结版本 v{rf.VERSION}）")

        m.note("这条记录本身不证明规则'对'——它证明的是'规则在 commit <sha> "
               "时长这样、之后没动过'。09-02 用 reproduce_L1.py --check-only "
               "重算这几个 sha256，一致 = 规则确实先于 D2~D7 留出集数据冻结，"
               "不一致 = 报告里所有'留出集'措辞作废。commit sha 与推送时间记在 "
               "知识检索/冲突规则冻结记录.md。")
        m.note("v1.1.0 = v1.0.0(2026-08-16) 的确定性修复：adjudicated_at 从 "
               "datetime.now() 改成固定常量。18 正 18 负、label/type/reason_kind "
               "逐字未变，M-302/303/304 的数字全部不受影响，见 review_and_"
               "freeze_conflicts.py 的 VERSION 注释与《勘误与修正记录》。")
        m.note("v1.2.0 → v1.3.0（2026-08-28）与前两次不同，是真实内容修订，"
               "不是数字不受影响的空跑：_find_origin() 起源查找 bug 修复后，"
               "E-032/K-0052 由 not_conflict 改判 conflict（reason_kind=旧结论"
               "当时为假），19 正 17 负，不再对称。§一 用修复版重跑冻结 36 对"
               "验证'只动候选挖掘、未动判定逻辑'，结果仍 35/36（M-302 不变、"
               "CI 下界仍 85.8%）。但 M-303 的类型判断（T1~T4）确实从 8/18="
               "44.4% 降到 8/19=42.1%，因为 K-0052 是新纳入的真实类型误判"
               "（金标T1，adjudicate()判T4），如实记录，不因指标下降而回避。"
               "详见《冻结修订记录 #1》。")
        m.note("分界线口径变更（v1.3.0，《冻结修订记录 #1》§6）：开发集/留出集"
               "由「conflicts.jsonl 行号（第 150 行起 = 留出集）」改为「候选的"
               "数据日期（new.asserted_at >= 2026-08-26 = 留出集）」。理由：起源"
               "查找修复后重挖，候选行号全部重排，行号定义在任何一次重挖后即"
               "失效；数据日期不随挖掘器版本变化。冻结时（D2 挖掘器尚未跑）留出"
               "集候选为 0——全部 %d 条候选的 new.asserted_at 均 < 2026-08-26。"
               "conflicts_pool_lines / conflicts_eval_lines 仍登记，作为规模"
               "快照而非分界依据。" % len(_dev))
        m.note("measure_conflicts.py sha256 于 2026-09-07 变更一次（新增 M-306，"
               "把留出集单人全量盲测裁决结果接入可复现性体系）——**is_conflict()"
               "/adjudicate() 判定逻辑、M-301~305 的测量口径一行未动**，"
               "conflict_rules_mine_conflicts_sha256（7378b51d…）不变，"
               "「规则先于留出集数据」的证明链不受影响。变更前 measure_conflicts.py "
               "为 a20de134…（2026-09-02 起源修复收尾时），此后为当前值。")

        if agree != 1:
            raise AssertionError(
                "build_eval_set() 两次调用产出不一致——评测集不可确定性重建，"
                "'按 sha256 冻结'这件事就没有意义。必查 build_eval_set 里是否又"
                "混进了 datetime.now()/set 遍历顺序这类非确定性来源。")


# ═══════════════════════════════ M-306 留出集裁决结果接入（分层准确率 + 三口径 + 重复题一致性）

def m306():
    """把《留出集裁决结果记录.md》的结论接入可复现性体系。

    ═══ 为什么是 M-306、为什么不重跑挖掘 ══════════════════════════════
    留出集（D1~D7 采集，new.asserted_at >= 2026-08-26）的候选在规则冻结
    （2026-08-27，`mine_conflicts.py` 哈希 bebd5ccd → 历史重写后 7378b51d，
    判定/裁决函数逐字节不变）**之后**才产生，规则从未见过它们。2026-09-07
    单人全量盲测把 73 条（60 留出 + 13 开发集复核）+ 5 条重复题逐条裁定，
    结果写进 `知识检索/全量单人复核-裁决.json`。

    本度量点**只读那份冻结的裁决 JSON + 冻结的 36 对评测集**，不调用
    `mine_conflicts.mine()`——留出集的「规则侧判断」由抽样时的层决定
    （层在看到任何结果之前即写入《留出集抽样与裁决-协议》v1.0）：
        layer ∈ {A,B,C,DEV} → 规则判 conflict
        layer == NEG        → 规则判 not_conflict
    开发集 / 合并口径的 is_conflict() 部分与 M-302 同法，只在冻结的 36 对
    上重跑判定函数（不是重跑挖掘），保证合并数字与 M-302 逐位对得上。

    ═══ 三个口径为什么必须分开报（《留出集裁决结果记录.md》§三） ═══════
      开发集 n=36（规则在其上开发过）  35/36 = 97.2%  下界 85.8%  ❌
      留出集 n=60（规则从未见过）      56/60 = 93.3%  下界 84.1%  ❌
      合并   n=96（全部标注数据）      91/96 = 94.8%  下界 88.4%  ✅
    合并口径达标**来自样本量增加，不是泛化优于开发集**——点估计 94.8%
    落在开发集 97.2% 与留出集 93.3% 之间，留出集单独下界 84.1% 未达门槛。
    两句必须并读，claims.json 的 C-304（留出集，红）与 C-316（合并，绿）
    并存，缺一即为「用合并口径掩盖留出集」。
    """
    import mine_conflicts

    with Measurement(
        "M-306", "留出集裁决结果接入（分层准确率 + 开发/留出/合并三口径 + 重复题一致性）",
        layer="L1", script=os.path.join(KNOWLEDGE_DIR, "mine_conflicts.py"),
        command="python measure_conflicts.py M-306",
        inputs=[HOLDOUT_ADJUDICATION, EVAL_SET],
    ) as m:
        rows = json.load(open(HOLDOUT_ADJUDICATION, encoding="utf-8"))

        # 去重：5 条重复题在卷中出现两次（间距 29~70 题），按 id 保留首次出现。
        seen, uniq, repeats = {}, [], []
        for r in rows:
            if r["id"] in seen:
                repeats.append((r["id"], seen[r["id"]]["verdict"], r["verdict"]))
            else:
                seen[r["id"]] = r
                uniq.append(r)
        repeat_consistent = sum(1 for _id, a, b in repeats if a == b)

        m.set_value("rows_total", len(rows), unit="条")
        m.set_value("rows_deduped", len(uniq), unit="条")
        m.set_value("repeat_pairs", len(repeats), unit="对")
        m.set_value("repeat_consistent", repeat_consistent, unit="对")
        m.set_value("repeat_consistency_rate",
                    round(repeat_consistent / len(repeats), 4) if repeats else None)

        def rule_pred(layer):
            return "conflict" if layer in ("A", "B", "C", "DEV") else "not_conflict"

        # 开发集复核：那 13 条此前只有 Claude 裁决过，冻结标注全为 conflict，
        # 本轮人工盲测复核是否维持。
        dev = [r for r in uniq if r["layer"] == "DEV"]
        dev_maintained = sum(1 for r in dev if r["verdict"] == "conflict")
        m.set_value("dev_review_n", len(dev), unit="条")
        m.set_value("dev_maintained_conflict", dev_maintained, unit="条")

        # 留出集分层准确率（规则从未见过这批数据）。
        holdout = [r for r in uniq if r["layer"] in ("A", "B", "C", "NEG")]
        for L in ("C", "B", "A", "NEG"):
            g = [r for r in holdout if r["layer"] == L]
            hit = sum(1 for r in g if rule_pred(r["layer"]) == r["verdict"])
            lo, hi = wilson_ci(hit, len(g))
            m.set_value("stratum_%s_hits" % L, hit)
            m.set_value("stratum_%s_n" % L, len(g), unit="条")
            m.set_value("stratum_%s_accuracy" % L, round(hit / len(g), 4))
            m.set_value("stratum_%s_wilson_ci_lo" % L, round(lo, 4))
            m.set_value("stratum_%s_wilson_ci_hi" % L, round(hi, 4))
            print("  %-3s %2d/%-2d = %5.1f%%  Wilson 95%% CI [%.1f%%, %.1f%%]"
                  % (L, hit, len(g), hit / len(g) * 100, lo * 100, hi * 100))

        h_hit, h_n = sum(1 for r in holdout
                         if rule_pred(r["layer"]) == r["verdict"]), len(holdout)
        h_lo, h_hi = wilson_ci(h_hit, h_n)
        m.set_value("holdout_hits", h_hit)
        m.set_value("holdout_n", h_n, unit="对")
        m.set_value("holdout_accuracy", round(h_hit / h_n, 4))
        m.set_value("holdout_wilson_ci_lo", round(h_lo, 4))
        m.set_value("holdout_wilson_ci_hi", round(h_hi, 4))

        # 开发集口径（n=36，规则在其上开发过）——与 M-302 同法，只在冻结的
        # 36 对上重跑 is_conflict()，不重跑挖掘。
        eval_rows = _load_jsonl(EVAL_SET)
        d_hit = sum(1 for r in eval_rows
                    if mine_conflicts.is_conflict(_pair_from_eval_row(r))
                    == (r["label"] == "conflict"))
        d_n = len(eval_rows)
        d_lo, d_hi = wilson_ci(d_hit, d_n)
        m.set_value("devset_hits", d_hit)
        m.set_value("devset_n", d_n, unit="对")
        m.set_value("devset_accuracy", round(d_hit / d_n, 4))
        m.set_value("devset_wilson_ci_lo", round(d_lo, 4))

        # 合并口径（全部标注数据 = 开发集 36 + 留出集 60）。
        c_hit, c_n = d_hit + h_hit, d_n + h_n
        c_lo, c_hi = wilson_ci(c_hit, c_n)
        m.set_value("combined_hits", c_hit)
        m.set_value("combined_n", c_n, unit="对")
        m.set_value("combined_accuracy", round(c_hit / c_n, 4))
        m.set_value("combined_wilson_ci_lo", round(c_lo, 4))
        m.set_value("combined_wilson_ci_hi", round(c_hi, 4))

        print("  去重：%d 行 - %d 条重复题 = %d 条唯一（60 留出 + 13 开发集复核）"
              % (len(rows), len(repeats), len(uniq)))
        print("  重复题前后一致 %d/%d；开发集 13 条维持 conflict %d/%d"
              % (repeat_consistent, len(repeats), dev_maintained, len(dev)))
        print("  开发集 %d/%d = %.1f%%（下界 %.1f%%）  留出集 %d/%d = %.1f%%（下界 %.1f%%）  "
              "合并 %d/%d = %.1f%%（下界 %.1f%%）"
              % (d_hit, d_n, d_hit / d_n * 100, d_lo * 100,
                 h_hit, h_n, h_hit / h_n * 100, h_lo * 100,
                 c_hit, c_n, c_hit / c_n * 100, c_lo * 100))

        m.note("合并口径达标（91/96，下界 88.4% >= 88%）来自样本量增加，不是"
               "泛化优于开发集：点估计 94.8% 落在开发集 97.2% 与留出集 93.3% "
               "之间，**留出集单独下界 84.1% 未达门槛**。C-304（留出集，红）与 "
               "C-316（合并，绿）并存，只留一条即为「用合并口径掩盖留出集」。")
        m.note("合并集包含规则开发时看过的 36 条。两点说明：① 开发期两次结构"
               "修订（same_sitting 口径、append_only 匹配方式）是 bug 修复，不是"
               "精度调参；② 留出集点估计 93.3% 与开发集 97.2% 的差距落在各自 "
               "Wilson 区间内，无显著退化。")
        m.note("留出集 4 处误判单一根因（《留出集裁决结果记录.md》§四）：提示词 "
               "B 组⑤ 要求笔记写「读数：<值>（原为 <旧值>，已不再是）」，而挖掘器 "
               "append_only 用「旧文本 ⊂ 新文本」子串匹配，两者交互双向失效——"
               "史注漂移（HO-K-0053/0036，当前值未变误判为冲突）+ 假追加"
               "（HO-K-0204/0247，旧值进史注误判为纯追加）。**已作为判例补入"
               "《冲突判定标注规范》v1.0.2 §六，代码未改、指标未重算、冻结完好**；"
               "两个方向都修好后留出集应为 60/60——该缺陷当前在压低成绩而非抬高。")
        m.note("规则侧判断由抽样层决定，不在此重跑 is_conflict()：层在看到任何"
               "结果之前即写入《留出集抽样与裁决-协议》v1.0（C 15 / B 8 / A 7 / "
               "NEG 30，随机种子同时提交）。留出集候选的 new.asserted_at 全部 "
               ">= 2026-08-26，规则冻结哈希（M-305 登记）先于这批数据——重算 "
               "M-305 的 mine_conflicts.py sha256 一致即证明「规则先于留出集数据」。")
        m.note("开发集复核 13/13 维持 conflict、改判 0——报告中「开发集由 AI "
               "裁决、未经人工复核」这条已知限制可撤下，改为「全部 36 对均经"
               "人工盲测复核」。冻结集正例仍 19 条，19 正 / 17 负比例不变。")
        m.note("重复题前后一致 5/5 是单人全量标注唯一能自证的可靠性指标"
               "（5 条题目隔 29~70 题出现两次，标注者知道存在但不知是哪几条）。"
               "2026-09-06 曾试 5 人分工盲测，因无法核实标注投入程度（开发集 "
               "conflict 0/13 与自身留出集 75% 矛盾、两轮零条理由、公共集完全"
               "一致率 46.7%）整批弃用，原始文件保留但不进证据链、不写入 claims。")


# ═════════════════════════════════════════════════════ 入口

ALL = {"M-301": m301, "M-302": m302, "M-303": m303, "M-304": m304, "M-305": m305,
       "M-306": m306}


def check_preconditions():
    """缺文件返回人话，齐了返回 None（同 measure_L1/measure_B2 的模式，R3-31）。"""
    need = [
        (DATASET, "多源数据整合/dataset/"),
        (CONFLICTS_POOL, "知识检索/（先跑 python mine_conflicts.py）"),
        (EVAL_SET, "知识检索/（先跑 python review_and_freeze_conflicts.py）"),
        (HOLDOUT_ADJUDICATION, "知识检索/（留出集单人全量盲测裁决，见《留出集裁决结果记录.md》）"),
    ]
    missing = [(p, d) for p, d in need if not os.path.exists(p)]
    if missing:
        lines = [f"缺少 {len(missing)} 个输入文件，本模块（measure_conflicts.py）跑不了："]
        lines += [f"    {p}" for p, _ in missing]
        lines.append("  只想核对已有记录、不重新生成的话，用："
                     "python reproduce_L1.py --check-only")
        return "\n".join(lines)
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
