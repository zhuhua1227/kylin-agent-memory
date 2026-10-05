#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_generation_audit.py —— 回答一个问题：这批题的审计链完整吗？
（第四轮独立复核 R3-13）

═══ 它要输出的那一句话 ═══════════════════════════════════════════

    169 题中 0 题可追溯到 run_id、0 题可追溯到源知识单元。

对当前已交付的 v1.3.0，这个 0 就是真实答案，本脚本如实打印它，不做任何
粉饰。审计链的价值在于它**能报 0**——一个永远绿的检查器等于没有检查器。

═══ 两级证据，绝不混为一谈 ═══════════════════════════════════════

**L0 严格链（生成时捕获）**：出题运行**当时**写下的 run_id、prompt 指纹、
源单元内容指纹。它证明的是「这道题是那次运行、用那版 prompt、从那条
知识单元出的」。当前 v1.3.0：**0 题**。

**L1 事后重建（现在重算）**：拿每道题存下来的 `source_rendered`，与当前
知识库同 doc_id 的渲染结果逐字比对。对得上，说明 doc_id 映射至今有效、
源语料没被换过。当前 v1.3.0：169/169 对得上（其中 53 条案例题需按 K-2
之前的 150 字截断规则渲染，见下）。

    ⚠️ L1 **不能**替代 L0。它只能说明「这道题的源头看起来还是它」，
    说不清「用哪版 prompt、哪次运行、哪个模型出的」。把 L1 的 169/169
    写成「169 题全部可追溯」，就是拿弱证据冒充强证据——这正是 R3-13
    要修的那类问题，不能在修它的脚本里再犯一次。

═══ 缺失即失败 ═══════════════════════════════════════════════════

审计清单不存在 ⇒ 退出码 3（硬失败），不是「跳过」。理由同
`可复现性体系/provenance.py`：静默跳过会让「没查」和「查过了」在输出上
一模一样。

═══ 退出码 ═══════════════════════════════════════════════════════

    0  L0 严格链完整
    2  清单是事后补登（backfilled）⇒ 审计链不成立，但缺口已登记
    3  审计清单缺失/损坏 ⇒ 硬失败
    4  清单在，但对不上（run_id 集合不一致 / prompt 指纹不符 / 产物被改）

═══ 空变异基线（`--self-test`）═══════════════════════════════════

本项目四轮复核一直盯的一条方法论：新增检查器交付前必须在**不注入任何
缺陷的干净副本**上跑一次，断言它全绿。基线不绿，说明判据里混进了无关
变量，那它此后的任何「检出」都不可信。

问题是本目录**本身就是红的**（0 可追溯），拿它当基线毫无意义。所以
`--self-test` 自己造一份合成的、审计链完整的小语料 + 小候选集（不调任何
外部接口），断言：

    ① 干净副本上 L0 全绿；
    ② 逐个注入 4 类缺陷（删清单 / 改一条 run_id / 篡改 prompt 全文 /
       改动源知识单元），每一类都必须红在**指定的那一项**上。

只有 ① 和 ② 同时成立，本脚本对真实目录报出的 0 才有意义。
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generation_audit as audit  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(
    BASE, "..", "知识检索")

EXIT_OK, EXIT_BACKFILLED, EXIT_MISSING, EXIT_MISMATCH = 0, 2, 3, 4


# ═════════════════════════════════════════════════════ 语料加载

def _import_render_units(knowledge_dir):
    """按路径 import，而不是 sys.path.insert + import。

    自检要在临时目录里跑另一份语料，若走 sys.path 会因为模块缓存拿到
    上一次的实现——检查器自己被污染了却看不出来，正是本项目要避免的那种
    「判据里混了无关变量」。
    """
    p = os.path.join(knowledge_dir, "render_units.py")
    spec = importlib.util.spec_from_file_location(
        f"render_units_{abs(hash(os.path.abspath(p)))}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_units(knowledge_dir, render_variant="current"):
    """与 generate_eval_candidates.py / build_knowledge_index.py 同一套编号：
    case → workflow → template，下标即 doc_id。

    `render_variant="pre_K2"` 复现 K-2 之前的 render_case（error_text 截 150
    字）。这不是「我猜当时是这样」——见 reconstruct_report() 里的实测：
    53 条案例题在 150 字规则下 53/53 逐字一致、在当前 300 字规则下只有
    25 条一致。数据把这版规则指认出来了，但它仍然只是**重建**：当时的
    render_units.py 没有留存指纹，无法排除别的可能。
    """
    ru = _import_render_units(knowledge_dir)

    def render_case_pre_k2(c):
        ti = c["failed_input"] or {}
        intent = (ti.get("description") or ti.get("command")
                  or json.dumps(ti, ensure_ascii=False))
        return (f"【历史案例】使用 {c['tool_name']} 执行「{str(intent)[:150]}」时出错："
                f"{c['error_text'][:150]}。"
                f"修复方式：{json.dumps(c['fixed_input'], ensure_ascii=False)[:150]}")

    case_renderer = ru.render_case if render_variant == "current" else render_case_pre_k2

    units = []
    for fname, typ, fn in (("cases.jsonl", "case", case_renderer),
                           ("workflows.jsonl", "workflow", ru.render_workflow),
                           ("templates.jsonl", "template", ru.render_template)):
        p = os.path.join(knowledge_dir, fname)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    raw = json.loads(line)
                    units.append({"type": typ, "raw": raw, "rendered": fn(raw)})
    return units


def _read_jsonl(path):
    return audit._read_jsonl(path)


# ═════════════════════════════════════════════════════ 检查项

class Check:
    def __init__(self, name, status, detail):
        self.name, self.status, self.detail = name, status, detail

    ICON = {"ok": "✅", "fail": "❌", "gap": "🟠", "info": "ℹ️"}

    def line(self):
        return f"{self.ICON.get(self.status, '?')} {self.name}：{self.detail}"


def audit_report(eval_dir, knowledge_dir):
    """返回 (checks, stats)。纯函数式：不改任何文件，可对任意目录调用
    （自检就是对临时目录调用它）。"""
    checks, stats = [], {}

    manifest_path = os.path.join(eval_dir, audit.MANIFEST_NAME)
    manifest = audit.load_manifest(manifest_path)

    cand = _read_jsonl(os.path.join(eval_dir, "eval_candidates.jsonl"))
    eset = _read_jsonl(os.path.join(eval_dir, "eval_set.jsonl"))
    rawlog = _read_jsonl(os.path.join(eval_dir, "generation_raw_log.jsonl"))
    skipped = _read_jsonl(os.path.join(eval_dir, "generation_skipped.jsonl"))
    stats["question_count"] = len(eset)
    stats["candidate_count"] = len(cand)

    # ── ① 审计清单在不在。缺失 = 失败，不是跳过 ──────────────────
    if manifest is None:
        checks.append(Check(
            "审计清单", "fail",
            f"缺少 {audit.MANIFEST_NAME}——无法判断这批题出自哪次运行、哪版 "
            f"prompt。缺失按失败处理：不然「没查」和「查过了」在输出上一模一样。"))
        stats["fatal"] = "missing_manifest"
        stats.update(_trace_counts(eset, cand, None, knowledge_dir))
        stats.update(reconstruct_report(eset, knowledge_dir))
        return checks, stats

    backfilled = bool(manifest.get("backfilled"))
    if backfilled:
        checks.append(Check(
            "审计清单", "gap",
            f"清单存在但标记为**事后补登**（backfilled_at="
            f"{manifest.get('backfilled_at')}）。补登清单不承载"
            f"「出自哪次运行、哪版 prompt」，L0 严格链按 0 计。"
            f"已登记的冻结理由：{manifest.get('frozen_reason')}"))
    else:
        checks.append(Check("审计清单", "ok",
                            f"{audit.MANIFEST_NAME} 存在，run_id={manifest.get('run_id')}"))

    # ── ② 三个产物的 run_id 集合是否相同 ────────────────────────
    #     R3-13 给的做法。集合不等 ⇒ 有产物来自别的运行——v1.2.0 那份日志
    #     混进 v1.3.0 交付物，正是这种情况，而当时没有任何机制能发现。
    sets = {
        "eval_candidates.jsonl": {r.get("run_id") for r in cand},
        "generation_raw_log.jsonl": {r.get("run_id") for r in rawlog},
        "generation_skipped.jsonl": {r.get("run_id") for r in skipped},
    }
    sets = {k: v for k, v in sets.items() if v}     # 空文件不参与比较
    stats["run_id_sets"] = {k: sorted(str(x) for x in v) for k, v in sets.items()}
    distinct = {frozenset(v) for v in sets.values()}
    if not sets:
        checks.append(Check("三产物 run_id 一致性", "fail", "三个产物都是空的，无从比较"))
    elif any(None in v for v in sets.values()):
        missing = [k for k, v in sets.items() if None in v]
        # 「全都没有 run_id」与「有的有有的没有」是两回事：
        #   前者是**已登记的缺口**（补登清单里 known_gaps 写明 run_id 不存在），
        #   后者是**不一致**——说明有产物来自别的运行，必须报红。
        # 补登不是万能挡箭牌：只有在清单显式登记了这一项时才降级为 🟠，
        # 且降级只影响这一项的颜色，L0 计数照样是 0。
        wholly_absent = all(v == {None} for v in sets.values())
        registered = _gap_registered(manifest, "run_id")
        if backfilled and wholly_absent and registered:
            checks.append(Check(
                "三产物 run_id 一致性", "gap",
                "三个产物**都**没有 run_id 字段——这是补登清单里已登记的缺口"
                "（known_gaps: run_id）。当年即使有一份日志混进了别的运行"
                "（generation_raw_log.jsonl 正是这种情况），也没有任何机制能发现。"))
        else:
            checks.append(Check(
                "三产物 run_id 一致性", "fail",
                f"以下产物存在无 run_id 的记录：{'、'.join(missing)}"
                f"——没有 run_id 就无法核对它们是否出自同一次运行"
                + ("（且并非全部缺失，属于不一致而不是整体缺口）"
                   if not wholly_absent else "")))
    else:
        # ── 判据从「三者必须同一个 run_id」放宽为「都必须在清单声明的血缘里」──
        #
        # 原判据假定一次出题 = 一次运行。2026-08-14 实测这个前提站不住：
        # 上游代理不稳，86 条失败 36 条，靠 `--resume` 补齐，于是候选集里
        # **合法地**混着三个 run_id（159 + 6 + 66 条）。
        #
        # 此时若坚持原判据，只剩两条路：要么把 run_id 统一重写成最后一次的
        # （**那是造假**——把分三次出的题说成一次出的，而审计链存在的全部
        # 意义就是不让人这么干），要么让正当的续跑永远显示为红。
        # 两条都不对，所以改判据：清单如实声明 run_lineage，审计核对
        # **产物里出现的 run_id 集合 ⊆ 清单声明的血缘**，且清单当前 run_id
        # 必须在其中。多出任何一个未声明的 run_id ⇒ 照样报红（那才是
        # "有产物来自别的运行"——v1.2.0 日志混进 v1.3.0 交付物那种情况）。
        seen = set().union(*sets.values())
        lineage = set(manifest.get("run_lineage") or [manifest.get("run_id")])
        undeclared = sorted(str(x) for x in (seen - lineage))
        cur_in = manifest.get("run_id") in lineage
        if undeclared:
            checks.append(Check(
                "产物 run_id 均在清单声明的血缘内", "fail",
                f"以下 run_id 出现在产物里但**未在清单的 run_lineage 中声明**："
                f"{undeclared}——说明有产物来自未登记的运行。"
                f"清单声明的血缘：{sorted(lineage)}"))
        elif not cur_in:
            checks.append(Check(
                "产物 run_id 均在清单声明的血缘内", "fail",
                f"清单自身的 run_id={manifest.get('run_id')} 不在它声明的"
                f"run_lineage={sorted(lineage)} 里，清单自相矛盾"))
        else:
            multi = len(seen) > 1
            checks.append(Check(
                "产物 run_id 均在清单声明的血缘内", "ok",
                f"产物出现的 run_id {sorted(str(x) for x in seen)} 全部在清单"
                f"声明的血缘内"
                + ("（本次为**断点续跑**，跨 %d 次运行，已如实登记而非重写）"
                   % len(seen) if multi else "（单次运行）")))

    # ── ③ prompt 是否落盘、指纹自洽、与日志逐行呼应 ──────────────
    prompt = manifest.get("prompt")
    if not prompt:
        checks.append(Check(
            "system prompt 落盘", "fail" if not backfilled else "gap",
            "清单里没有 prompt 全文" + ("（补登清单按设计留空：当时用的哪版"
                                      "无从得知，不用当前源码冒充）"
                                      if backfilled else
                                      "——审计日志漏掉了唯一需要被审计的那部分")))
    else:
        text = prompt.get("system_prompt_text") or ""
        recomputed = audit.sha256_text(text)
        if recomputed != prompt.get("system_prompt_sha256"):
            checks.append(Check("system prompt 落盘", "fail",
                                f"清单里 prompt 全文与其 sha256 对不上"
                                f"（重算 {recomputed[:16]}… vs 登记 "
                                f"{str(prompt.get('system_prompt_sha256'))[:16]}…）"
                                f"——清单本身被改过"))
        else:
            log_shas = {r.get("system_prompt_sha256") for r in rawlog} or {None}
            if log_shas != {prompt["system_prompt_sha256"]}:
                checks.append(Check(
                    "system prompt 落盘", "fail",
                    f"原始日志里的 system_prompt_sha256 与清单不符"
                    f"（日志 {sorted(str(s)[:16] for s in log_shas)} vs 清单 "
                    f"{prompt['system_prompt_sha256'][:16]}…）"))
            else:
                checks.append(Check(
                    "system prompt 落盘", "ok",
                    f"全文已落盘（{prompt.get('version')}，"
                    f"{len(text)} 字，sha256={recomputed[:16]}…），"
                    f"日志逐行指纹与之一致"))

    # ── ④ 语料与冻结登记是否一致 ────────────────────────────────
    #     这是补登清单里唯一**可以被机器核实**的那条断言（「语料自冻结以来
    #     没被换过」）。写了断言就必须真去比对，否则它可以一直假下去——
    #     第一版清单注释里那句「与冻结登记相等」当时就是假的（归一口径 vs
    #     原始字节口径），正因为没人比对才留了下来。
    checks.append(_check_frozen_corpus(eval_dir, knowledge_dir))

    # ── ⑤ 逐题溯源 ─────────────────────────────────────────────
    stats.update(_trace_counts(eset, cand, manifest, knowledge_dir))
    n = stats["question_count"] or 1
    # 两条链都必须齐：只有 run_id 说不清题从哪条知识单元来，只有源指纹
    # 说不清是哪次运行出的。少一条就是断链，按 fail 计。
    full = (stats["question_count"] > 0
            and stats["traceable_run_id"] == stats["question_count"]
            and stats["traceable_source_unit"] == stats["question_count"])
    checks.append(Check(
        "逐题可追溯（L0 严格）",
        "ok" if full else ("gap" if backfilled else "fail"),
        f"{stats['question_count']} 题中 {stats['traceable_run_id']} 题可追溯到 run_id、"
        f"{stats['traceable_source_unit']} 题可追溯到源知识单元"
        f"（{100.0 * stats['traceable_source_unit'] / n:.1f}%）"))

    # ── ⑥ 事后重建（弱证据，单列，不计入 L0）────────────────────
    stats.update(reconstruct_report(eset, knowledge_dir))
    checks.append(Check(
        "事后重建（L1 弱证据，不能替代 L0）", "info",
        f"{stats['reconstruct_matched']}/{stats['question_count']} 题的 "
        f"source_rendered 与当前知识库同 doc_id 逐字一致"
        f"（当前渲染规则 {stats['reconstruct_current']} 题；"
        f"K-2 前 150 字截断规则另 {stats['reconstruct_pre_k2']} 题）；"
        f"对不上 {stats['reconstruct_unmatched']} 题。"
        f"它只说明源语料没被换过，说不清用哪版 prompt、哪次运行出的。"))

    return checks, stats


def _check_frozen_corpus(eval_dir, knowledge_dir):
    """拿当前语料的**原始字节** sha256 与 eval_set_frozen.json 的
    knowledge_base_sha256 逐个比对。

    口径必须是原始字节：冻结登记当年就是这么算的。用归一口径去比会全盘
    失配（本仓库检出为 CRLF），那种「全红」是判据里混了无关变量的典型症状，
    不是真的检出。
    """
    meta_path = os.path.join(eval_dir, "eval_set_frozen.json")
    if not os.path.exists(meta_path):
        return Check("语料与冻结登记一致", "fail",
                     "找不到 eval_set_frozen.json——没有冻结登记就无从判断"
                     "语料有没有被换过。缺失按失败处理。")
    with open(meta_path, encoding="utf-8") as f:
        registered = (json.load(f).get("knowledge_base_sha256") or {})
    if not registered:
        return Check("语料与冻结登记一致", "fail",
                     "eval_set_frozen.json 里没有 knowledge_base_sha256 字段")

    snap = audit.knowledge_snapshot(knowledge_dir)
    bad = []
    for name, want in registered.items():
        got = (snap.get(name) or {}).get("sha256_raw")
        if got != want:
            bad.append(f"{name}（登记 {want[:12]}… vs 现算 {str(got)[:12]}…）")
    if bad:
        return Check("语料与冻结登记一致", "fail",
                     "以下语料文件与冻结登记不符：" + "；".join(bad)
                     + "——评测集的 doc_id 指向的东西已经变了")
    return Check("语料与冻结登记一致", "ok",
                 f"{len(registered)} 个语料文件的原始字节 sha256 与冻结登记逐一相符，"
                 f"语料自冻结以来未被换过（注意：这不能说明出题当时读的就是这份）")


def _gap_registered(manifest, field_prefix):
    """这个缺口是不是在补登清单的 known_gaps 里被点名登记过。

    「已登记」和「碰巧没有」必须区分开：前者作者写清了它为什么缺、
    为什么不补；后者只是文件里恰好没这个字段。只有前者才配降级为 🟠。
    """
    for g in (manifest or {}).get("known_gaps") or []:
        if str(g.get("field", "")).startswith(field_prefix):
            return True
    return False


def _trace_counts(questions, candidates, manifest, knowledge_dir):
    """L0：每道冻结题 → 候选记录 → run_id / 源单元内容指纹。

    冻结题本身不带 run_id（它是候选集经人工复核后的子集），所以链路是
    「题 →（doc_id+难度+问句 匹配）→ 候选 → run_id」。匹配不上也算断链，
    如实计 0。
    """
    index = {}
    for c in candidates:
        index[(c.get("doc_id"), c.get("difficulty"), c.get("query"))] = c

    manifest_run = manifest.get("run_id") if manifest else None
    # 清单声明的血缘（续跑会跨多次运行）。没声明就退回只认当前 run_id，
    # 与旧行为一致；空集合意味着任何 run_id 都算不可追溯，不会误放行。
    manifest_lineage = set(
        (manifest.get("run_lineage") if manifest else None)
        or ([manifest_run] if manifest_run else []))
    unit_sha = {}
    if os.path.isdir(knowledge_dir):
        for fp in audit.unit_fingerprints(load_units(knowledge_dir)):
            unit_sha[fp["doc_id"]] = fp["unit_sha256"]

    run_ok = src_ok = matched = 0
    for q in questions:
        c = index.get((q.get("doc_id"), q.get("difficulty"), q.get("query")))
        if c is None:
            continue
        matched += 1
        # review_and_freeze.py 是整条 dict 原样带过去再补 question_id 的，
        # 所以今后的 eval_set.jsonl 自己就带 run_id 与源指纹；这里优先读
        # 题目自身的，读不到再退回「按 (doc_id, 难度, 问句) 匹配候选」。
        # 两条路都走，是因为已交付的 v1.3.0 只有后一条可走（结果是 0）。
        run_id = q.get("run_id") or c.get("run_id")
        # 判据同 ② 那条：run_id 只要落在清单声明的**血缘**里就算可追溯，
        # 不要求等于清单当前的 run_id。
        # 2026-08-14 实测踩到：改血缘判据时只改了产物集合那一处、漏了这里，
        # 于是 227 题**全都带 run_id**，却只有最后一次续跑产出的 65 题被
        # 算作可追溯——检查器把"分三次出的题"误判成"162 题来路不明"。
        # 同一条语义规则散在两处、只改一处，正是本项目四轮反复出现的形态。
        if run_id and run_id in manifest_lineage:
            run_ok += 1
        s = q.get("source_unit_sha256") or c.get("source_unit_sha256")
        if s and unit_sha.get(q.get("doc_id")) == s:
            src_ok += 1
    return {"matched_to_candidate": matched,
            "traceable_run_id": run_ok,
            "traceable_source_unit": src_ok}


def reconstruct_report(questions, knowledge_dir):
    """L1：拿题里存的 source_rendered 与当前知识库重算的渲染逐字比对。"""
    if not os.path.isdir(knowledge_dir):
        return {"reconstruct_matched": 0, "reconstruct_current": 0,
                "reconstruct_pre_k2": 0, "reconstruct_unmatched": len(questions)}
    cur = [u["rendered"] for u in load_units(knowledge_dir, "current")]
    old = [u["rendered"] for u in load_units(knowledge_dir, "pre_K2")]
    n_cur = n_old = n_bad = 0
    for q in questions:
        i, s = q.get("doc_id"), q.get("source_rendered")
        if i is None or i >= len(cur):
            n_bad += 1
        elif s == cur[i]:
            n_cur += 1
        elif s == old[i]:
            n_old += 1
        else:
            n_bad += 1
    return {"reconstruct_matched": n_cur + n_old, "reconstruct_current": n_cur,
            "reconstruct_pre_k2": n_old, "reconstruct_unmatched": n_bad}


def decide_exit(checks, manifest_backfilled):
    if any(c.name == "审计清单" and c.status == "fail" for c in checks):
        return EXIT_MISSING
    if any(c.status == "fail" for c in checks):
        return EXIT_MISMATCH
    if manifest_backfilled:
        return EXIT_BACKFILLED
    return EXIT_OK


# ═════════════════════════════════════════════════════ 空变异基线（自检）

_FIXTURE_CASE = {"tool_name": "Bash", "failed_input": {"command": "pytest -q"},
                 "error_text": "command not found: pytest" + "x" * 200,
                 "fixed_input": {"command": "python -m pytest -q"}}
_FIXTURE_WORKFLOW = {"intent": "把日志里的报错找出来并修掉",
                     "tool_sequence": [{"tool_name": "Bash", "input_summary": "grep ERROR"},
                                       {"tool_name": "Read", "input_summary": "看第 40 行"}],
                     "sequence_length": 2, "had_errors": False}
_FIXTURE_TEMPLATE = {"template_type": "tool_switch", "trigger_tool": "PowerShell",
                     "error_category": "语法", "switch_to": "Bash"}


def _build_fixture(root):
    """造一份「审计链本该长什么样」的干净副本。不调任何外部接口。"""
    kdir = os.path.join(root, "知识检索")
    edir = os.path.join(root, "评测集")
    os.makedirs(kdir)
    os.makedirs(edir)
    shutil.copy(os.path.join(DEFAULT_KNOWLEDGE_DIR, "render_units.py"), kdir)

    def w(name, rows):
        with open(os.path.join(kdir, name), "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    w("cases.jsonl", [_FIXTURE_CASE])
    w("workflows.jsonl", [_FIXTURE_WORKFLOW])
    w("templates.jsonl", [_FIXTURE_TEMPLATE])

    units = load_units(kdir)
    fps = audit.unit_fingerprints(units)
    run_id = audit.derive_run_id({"fixture": True})
    system_prompt = "你在协助构建一个评测集。（自检夹具用的假 prompt）"
    sha = audit.sha256_text(system_prompt)

    cand, rawlog, skip = [], [], []
    for fp, u in zip(fps, units):
        for diff in ("close", "hard"):
            cand.append({"run_id": run_id, "doc_id": fp["doc_id"],
                         "source_unit_sha256": fp["unit_sha256"],
                         "source_rendered_sha256": fp["rendered_sha256"],
                         "knowledge_type": u["type"],
                         "query": f"自检问句-{fp['doc_id']}-{diff}",
                         "difficulty": diff, "source_rendered": u["rendered"],
                         "bigram_coverage": 0.2, "prompt_version": "test",
                         "system_prompt_sha256": sha})
        rawlog.append({"run_id": run_id, "doc_id": fp["doc_id"],
                       "source_unit_sha256": fp["unit_sha256"],
                       "source_rendered_sha256": fp["rendered_sha256"],
                       "prompt_version": "test", "system_prompt_sha256": sha,
                       "user_prompt": "…", "raw_response_text": "{}"})
    skip.append({"run_id": run_id, "doc_id": 0,
                 "source_unit_sha256": fps[0]["unit_sha256"],
                 "source_rendered_sha256": fps[0]["rendered_sha256"],
                 "reason": "自检夹具里的一条跳过记录"})

    def wj(name, rows):
        with open(os.path.join(edir, name), "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 夹具也要有冻结登记：否则「语料与冻结登记一致」这一项在基线里永远
    # 拿不到绿，基线就不全绿，后续所有断言都失去参照。
    snap = audit.knowledge_snapshot(kdir)
    with open(os.path.join(edir, "eval_set_frozen.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"version": "fixture",
                   "knowledge_base_sha256": {k: v["sha256_raw"]
                                             for k, v in snap.items()}},
                  f, ensure_ascii=False, indent=2)

    wj("eval_candidates.jsonl", cand)
    wj("generation_raw_log.jsonl", rawlog)
    wj("generation_skipped.jsonl", skip)
    # 冻结集 = 候选集去掉一条（模拟人工复核剔除），并补 question_id
    eset = [dict(c, question_id=f"Q-{i + 1:03d}") for i, c in enumerate(cand[1:])]
    wj("eval_set.jsonl", eset)

    audit.write_manifest(
        os.path.join(edir, audit.MANIFEST_NAME), run_id=run_id,
        prompt_block={"version": "test", "system_prompt_sha256": sha,
                      "system_prompt_text": system_prompt},
        model_block={"requested": "fixture-model"},
        knowledge_dir=kdir, units=units,
        artifacts={"candidates": "eval_candidates.jsonl"},
        generator_block={"script": "fixture"})
    return edir, kdir


def _statuses(edir, kdir):
    checks, stats = audit_report(edir, kdir)
    return {c.name: c.status for c in checks}, stats, checks


def self_test():
    """① 干净副本必须全绿；② 4 类注入缺陷必须各自红在指定项上。"""
    ok = True
    root = tempfile.mkdtemp(prefix="gen_audit_selftest_")
    try:
        edir, kdir = _build_fixture(root)

        # ① 空变异基线
        st, stats, checks = _statuses(edir, kdir)
        greens = all(v in ("ok", "info") for v in st.values())
        print("── 空变异基线（干净副本，未注入任何缺陷）──")
        for c in checks:
            print("   " + c.line())
        if greens and stats["traceable_run_id"] == stats["question_count"]:
            print("   ✅ 基线全绿，且每道题都追得到 run_id 与源单元")
        else:
            ok = False
            print("   ❌ 基线不绿 ⇒ 判据里混了无关变量，本检查器的任何「检出」都不可信")

        # ② 逐类注入
        cases = [
            ("删除审计清单", lambda e, k: os.remove(os.path.join(e, audit.MANIFEST_NAME)),
             "审计清单"),
            # 检查项在 2026-08-14 由「三产物 run_id 一致性」改名为
            # 「产物 run_id 均在清单声明的血缘内」（判据放宽以支持断点续跑，
            # 见 verify() 里那段说明）。注入一个**未声明**的 run_id 仍必须报红
            # ——放宽的是"允许多个 run_id"，不是"允许来路不明的 run_id"。
            ("篡改一条记录的 run_id",
             lambda e, k: _patch_jsonl(os.path.join(e, "eval_candidates.jsonl"),
                                       0, {"run_id": "gen-19700101T000000Z-deadbeef"}),
             "产物 run_id 均在清单声明的血缘内"),
            ("篡改清单里的 prompt 全文",
             lambda e, k: _patch_json(os.path.join(e, audit.MANIFEST_NAME),
                                      lambda m: m["prompt"].__setitem__(
                                          "system_prompt_text", "被人改过的 prompt")),
             "system prompt 落盘"),
            # 改语料会同时打断两条链：源指纹对不上（L0），且与冻结登记不符。
            # 两项都必须红——只红一项反而说明另一项没在看内容。
            ("改动一条源知识单元",
             lambda e, k: _patch_jsonl(os.path.join(k, "cases.jsonl"), 0,
                                       {"tool_name": "PowerShell"}),
             {"逐题可追溯（L0 严格）", "语料与冻结登记一致"}),
            # 第 5 类不是「注入缺陷」，是**注入借口**：验证「补登」不能被当成
            # 万能挡箭牌。补登清单登记了「run_id 不存在」这个缺口，但如果实际
            # 情况是「有的记录有 run_id、有的没有」，那就是真的不一致（有产物
            # 来自别的运行），必须照红不误，不能因为清单写了 backfilled 就放行。
            ("补登清单 + 部分记录缺 run_id（借口不成立）",
             lambda e, k: (_make_backfilled(e, k),
                           _strip_run_id_except_first(
                               os.path.join(e, "eval_candidates.jsonl"))),
             "三产物 run_id 一致性"),
        ]
        for name, mutate, expect in cases:
            want = {expect} if isinstance(expect, str) else set(expect)
            r2 = tempfile.mkdtemp(prefix="gen_audit_mut_")
            try:
                e2, k2 = _build_fixture(r2)
                mutate(e2, k2)
                st2, _, _ = _statuses(e2, k2)
                red = {k for k, v in st2.items() if v == "fail"}
                good = red == want
                ok = ok and good
                print(f"{'   ✅' if good else '   ❌'} 注入「{name}」→ 期望红项"
                      f"{sorted(want)}，实际红项：{sorted(red) or '无'}")
            finally:
                shutil.rmtree(r2, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n自检结论：" + ("通过——检查器能绿也能红，报出的 0 有意义"
                        if ok else "**未通过**，本脚本的输出暂不可信"))
    return 0 if ok else 1


def _make_backfilled(edir, kdir):
    """把夹具的清单换成一份「登记了 run_id 缺口」的补登清单。"""
    audit.write_backfilled_manifest(
        os.path.join(edir, audit.MANIFEST_NAME),
        eval_set_version="fixture",
        evidence=[{"claim": "自检夹具", "observed": {}, "how_to_falsify": "-",
                   "implication": "-"}],
        known_gaps=[{"field": "run_id", "state": "不存在", "why": "自检夹具"}],
        frozen_reason="自检夹具，用于验证补登不能掩盖真实的不一致",
        knowledge_dir=kdir, units=load_units(kdir))


def _strip_run_id_except_first(path):
    rows = _read_jsonl(path)
    for r in rows[1:]:
        r.pop("run_id", None)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _patch_jsonl(path, idx, patch):
    rows = _read_jsonl(path)
    rows[idx].update(patch)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _patch_json(path, fn):
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    fn(obj)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ═════════════════════════════════════════════════════ 入口

def main():
    ap = argparse.ArgumentParser(description="核对出题审计链是否完整")
    ap.add_argument("--eval-dir", default=BASE)
    ap.add_argument("--knowledge-dir", default=DEFAULT_KNOWLEDGE_DIR)
    ap.add_argument("--self-test", action="store_true",
                    help="空变异基线 + 缺陷注入，验证本检查器自身可信")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(self_test())

    checks, stats = audit_report(args.eval_dir, args.knowledge_dir)
    manifest = audit.load_manifest(os.path.join(args.eval_dir, audit.MANIFEST_NAME))
    backfilled = bool(manifest and manifest.get("backfilled"))
    code = decide_exit(checks, backfilled)

    if args.json:
        print(json.dumps({"exit_code": code, "stats": stats,
                          "checks": [{"name": c.name, "status": c.status,
                                      "detail": c.detail} for c in checks]},
                         ensure_ascii=False, indent=2))
        raise SystemExit(code)

    print("═" * 62)
    print(f"出题审计链核对：{os.path.abspath(args.eval_dir)}")
    print("═" * 62)
    for c in checks:
        print(c.line())
    print("─" * 62)
    print(f"结论：{stats['question_count']} 题中 "
          f"{stats['traceable_run_id']} 题可追溯到 run_id、"
          f"{stats['traceable_source_unit']} 题可追溯到源知识单元。")
    print({EXIT_OK: "退出码 0：L0 严格审计链完整。",
           EXIT_BACKFILLED: "退出码 2：清单为事后补登，审计链不成立，缺口已登记。",
           EXIT_MISSING: "退出码 3：审计清单缺失/损坏（硬失败）。",
           EXIT_MISMATCH: "退出码 4：清单在，但对不上。"}[code])
    raise SystemExit(code)


if __name__ == "__main__":
    main()
