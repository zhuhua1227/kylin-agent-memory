#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_eval_candidates.py —— 用 Gemini 3.1 Pro 从真实知识单元反向生成评测问句

═══ 这一步在整个可信度设计里的位置 ═══════════════════════════════════

评测集要回答的是「知识检索召回率 ≥85%」这项硬指标，而这个指标只有一份
问题—答案对照表才能测。生成这份对照表时必须守住一条边界（此前已与用户确认）：

    Gemini 生成的是「问题」，不是「事实」。

  · **合法**：知识单元是真实的（来自 B2 挖掘出的案例+流程+模板三类知识单元），
    Gemini 只负责换着法子问——"pytest命令找不到"和"命令行提示command not found
    怎么办"指向的是同一条真实历史案例。
  · **不合法**：不会让 Gemini 凭空编知识单元本身，那是自己给自己递答案。

因此本脚本的输入是**已经挖掘好、已经索引进检索库的知识单元**（`cases.jsonl` +
`workflows.jsonl` + `templates.jsonl`），输出是 `query → 期望命中的 doc_id`，
doc_id 与 `build_knowledge_index.py` 建索引时用的编号规则完全一致（cases 在前，
workflows 紧接着，templates 最后——三类具体条数随每次挖掘结果变化，不写死
数字在这里，运行时打印的会话日志里会给出实际编号区间），这样生成的评测集
才能直接拿去测已经建好的索引，不需要重新编号或猜测对应关系。

═══ 调用方式 ═══════════════════════════════════════════════════════

走用户提供的 litellm 代理（Anthropic Messages API 格式，无需鉴权）：
    POST http://192.168.100.3:8082/v1/messages

═══ 用法 ═══════════════════════════════════════════════════════════

    python generate_eval_candidates.py                  # 全量知识单元
    python generate_eval_candidates.py --limit 10        # 先小规模试跑
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# 出题审计链（R3-13）：run_id 派生、prompt 落盘、逐题溯源。
# 独立成模块而不是散在本文件里，是为了让校验脚本 verify_generation_audit.py
# 能 import 同一套指纹算法——「两处各写一遍 + 注释声称一致」这个办法本项目
# 已经栽过（render_units.py 的截断长度走样），不再重复。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generation_audit as audit  # noqa: E402

# 麒麟机上目录名是英文 knowledge，本地开发机是中文 知识检索——与
# measure_recall.py / review_and_freeze.py 的 RAG_KNOWLEDGE_DIR 处理方式
# 保持一致，此前这里漏掉了这层覆盖（独立评审 P-5 类问题的另一处实例：
# 同一套约定，散落的几个脚本各自实现了一遍，容易漏掉某一个）。
KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "知识检索")
BASE = os.path.dirname(os.path.abspath(__file__))

PROXY_URL = "http://192.168.100.3:8082/v1/messages"
MODEL = "claude-3-5-sonnet-20241022"  # 代理会把这个别名路由到 Gemini 3.1 Pro，
                                      # 见 8082 根路径自报"Anthropic Proxy for LiteLLM"，
                                      # 实测响应里的 model 字段回的是 openai/gemini-3.1-pro-preview

MAX_TOKENS = 1024

OUT_CANDIDATES = os.path.join(BASE, "eval_candidates.jsonl")
OUT_RAW_LOG = os.path.join(BASE, "generation_raw_log.jsonl")   # 保留每次调用的原始请求与响应，供可信度审计
OUT_SKIPPED = os.path.join(BASE, "generation_skipped.jsonl")
OUT_AUDIT = audit.MANIFEST_PATH                                 # 审计清单（prompt 全文与逐单元指纹）


# ═════════════════════════════════════════════════════ 加载知识单元（与建索引时的编号规则完全一致）

# render_case/render_workflow/render_template 已抽到 知识检索/render_units.py
# （第二轮独立复核 R2-17）——此前这三个函数在本文件里各自照抄一遍、靠注释
# 声明"与 build_knowledge_index.py 保持一致"，render_case 的 error_text
# 截断长度实际上早就走样了（这里还是150字，build_knowledge_index.py 那边
# K-2 时已改成300字），说明"写注释保证一致"这个办法本身就不可靠，
# import 同一个实现才是真正的保证。
sys.path.insert(0, KNOWLEDGE_DIR)
from render_units import render_case, render_workflow, render_template  # noqa: E402


def load_units():
    """与 build_knowledge_index.py 的 load_units() 保持完全一致的加载顺序与编号——
    这是本脚本产出的 doc_id 能直接对上已建索引的唯一保证。"""
    units = []

    cases_path = os.path.join(KNOWLEDGE_DIR, "cases.jsonl")
    with open(cases_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                units.append({
                    "type": "case", "raw": c, "rendered": render_case(c),
                    # 给 Gemini 的上下文用更完整的信息，不局限于渲染文本
                    "context": {
                        "工具": c["tool_name"],
                        "失败时的操作": c["failed_input"],
                        "报错内容": c["error_text"],
                        "修复后的操作": c["fixed_input"],
                    },
                })

    workflows_path = os.path.join(KNOWLEDGE_DIR, "workflows.jsonl")
    with open(workflows_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                w = json.loads(line)
                units.append({
                    "type": "workflow", "raw": w, "rendered": render_workflow(w),
                    "context": {
                        "用户意图": w["intent"],
                        "使用的工具": sorted(set(s["tool_name"] for s in w["tool_sequence"])),
                        "每一步做了什么": [f"[{s['tool_name']}] {s.get('input_summary','')}"
                                       for s in w["tool_sequence"]],
                        "步数": w["sequence_length"],
                        "含出错重试": w["had_errors"],
                    },
                })

    # 独立评审 K-3 之后新增的第三类知识——doc_id 编号必须紧接在
    # workflow 之后，与 build_knowledge_index.py 的 load_units() 顺序一致
    # （case → workflow → template），否则评测集 doc_id 又会对不上索引。
    templates_path = os.path.join(KNOWLEDGE_DIR, "templates.jsonl")
    if os.path.exists(templates_path):
        with open(templates_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    t = json.loads(line)
                    units.append({
                        "type": "template", "raw": t, "rendered": render_template(t),
                        "context": {k: v for k, v in t.items() if k != "kind"},
                    })

    return units


# ═════════════════════════════════════════════════════ Gemini 调用

# ═════════════════════════════════════════════════════ 三档难度设计（R2-03）
#
# ⚠️ 第二轮独立复核 R2-03 指出：E-1 此前只做了"承认"没做"修复"——报告老实
# 写了"close档是字面召回、不能证明语义理解"，但出题 prompt 本身一字未改，
# 复核员独立实现的纯词面baseline（字符bigram+ASCII词元双通道TF-IDF）在
# close档 R@1 反超 embedding（89.0% vs 85.4%），说明"close档就是抠关键词
# 能做的题"这个问题依然存在，只是被写进了免责声明，没有被真正修掉。
# 全项目搜索 paraphrase 零命中——第一轮就承诺过的第三档从未建过。
#
# 根治办法：不是靠一句话"要求AI别抄关键词"（prompt本身对bigram覆盖率没有
# 可执行约束，AI不一定真的照做），而是①把约束量化成具体的bigram覆盖率
# 目标区间，②出题后用代码实际算一遍覆盖率，超标的自动打回重出（见下方
# coverage() 与 GENERATE_MAX_RETRIES），不达标不能进最终候选池。
DIFFICULTY_SPECS = {
    "close": ("复述知识单元的核心事实，可以保留至多1个专有名词（工具名或"
              "报错类名），其余用自己的话转述。禁止整段复制原文措辞。"
              "目标：query与知识单元原文的字符bigram覆盖率 ≤ 0.45。"),
    "paraphrase": ("用完全不同的措辞描述同一个问题，不得出现知识单元原文中"
                   "的任何工具名、命令名、路径、报错类名——改用功能性描述"
                   "替代（比如把'pscp'说成'从本地往服务器传文件的命令'，"
                   "把'ModuleNotFoundError'说成'提示模块找不到'）。"
                   "目标：bigram覆盖率落在 0.10~0.30 之间。"),
    "hard": ("站在遇到同类问题、但不知道具体术语的用户视角提问，只描述"
             "现象与想达成的目标，完全不涉及知识单元里的具体工具/命令/"
             "专有名词。目标：bigram覆盖率 ≤ 0.10。"),
}

SYSTEM_PROMPT = """你在协助构建一个「知识检索」功能的评测集。

背景：这是一个 OS Agent 记忆系统，会把用户过去真实的操作历史（工具调用失败后
如何修复、用户提出需求后 Agent 执行了哪些步骤）整理成「知识单元」存起来，
之后用户提问时，系统要能检索到语义相关的知识单元并给出参考。

你的任务：给定一条真实的知识单元，生成用户**可能会怎么问**才会需要用到这条知识——
换句话说，反向出题：如果这条知识确实有用，用户当时的问题会是什么样子？

严格要求：
1. 只允许改写「提问方式」，不允许编造知识单元里没有的事实、工具名、错误信息。
2. 生成三条不同难度的问题，每一档都有具体的"字面重合度"目标（生成后会用代码
   核对，不达标会被打回要求重出，所以务必认真按目标执行，不要只是形式上
   换几个词）：
   - "close"：{close}
   - "paraphrase"：{paraphrase}
   - "hard"：{hard}
3. 问题要像真实用户会打出来的中文口语化短句——可以不完整、可以简略，
   参考风格："pytest命令找不到"、"帮我检查jellyfin"、"ssh连接被拒绝怎么办"，
   不要写成书面语或客服话术。
4. 如果这条知识单元的内容本身就很含糊、不足以支撑一个清晰的问题
   （比如意图是"继续"这种没有信息量的内容），如实说明，不要硬造问题。

只输出 JSON，不要有任何其他文字、不要用 markdown 代码块包裹，格式：
{{"questions": [{{"query": "...", "difficulty": "close"}}, {{"query": "...", "difficulty": "paraphrase"}}, {{"query": "...", "difficulty": "hard"}}],
 "unanswerable_reason": null}}

如果第 4 条触发（知识单元不足以出题），则：
{{"questions": [], "unanswerable_reason": "具体原因"}}
""".format(**DIFFICULTY_SPECS)

# ── prompt 版本号（R3-13）─────────────────────────────────────────
# 版本号是**人**声明的语义标签，sha256 是**机器**算的字面指纹，两个都要：
# 改了 prompt 忘了升版本号，指纹会揭穿；只有指纹没有版本号，则说不清
# 「这次改动是不是有意的、改了什么」。审计清单里两者一起落盘。
#
# 版本沿革（每次改 SYSTEM_PROMPT 或 DIFFICULTY_SPECS 都要在这里加一行）：
#   v1  两档（close / hard），无覆盖率约束 —— v1.2.0 与 v1.3.0 评测集用的就是
#       这一档位结构，但**当时没有落盘，本行是根据产物反推的**（已交付候选
#       只有 close/hard 两档、且无 bigram_coverage 字段），
#       不能据此声称当时的 prompt 全文就是某个具体版本。
#   v2  三档（close / paraphrase / hard）+ 每档 bigram 覆盖率目标与验收区间
#       （第二轮 R2-03）。当前源码即 v2，尚未真正跑过一次出题。
PROMPT_VERSION = "v2"
SYSTEM_PROMPT_SHA256 = audit.sha256_text(SYSTEM_PROMPT)

# 用户侧 prompt 是拼出来的，模板本身也要能被审计（改了模板同样会影响出题）
USER_PROMPT_TEMPLATE = ("知识单元类型：{type}\n"
                        "知识单元内容：{context_json}\n\n"
                        "请按要求生成问题。")

GENERATE_MAX_RETRIES = 3  # 重合度超标时的最多重出次数


def coverage(query: str, doc_text: str) -> float:
    """query 的字符 bigram 有多少比例出现在知识单元原文（doc_text）里，
    0~1之间——衡量"字面抄了多少"，不是语义相似度。用字符bigram而不是
    分词，因为中文分词本身不稳定、跨库结果不一致，字符级指标更容易独立
    复现，评委不需要装同一套分词器就能核对这个数字。"""
    q_bigrams = {query[i:i + 2] for i in range(len(query) - 1)}
    d_bigrams = {doc_text[i:i + 2] for i in range(len(doc_text) - 1)}
    if not q_bigrams:
        return 0.0
    return len(q_bigrams & d_bigrams) / len(q_bigrams)


# 各档位的覆盖率验收区间（生成后核对用，与 prompt 里给 Gemini 的目标一致）
COVERAGE_BOUNDS = {
    "close": (0.0, 0.45),
    "paraphrase": (0.10, 0.30),
    "hard": (0.0, 0.10),
}


def check_coverage(questions: list, doc_text: str) -> list:
    """返回每条问题的 (query, difficulty, coverage值, 是否达标) 列表。"""
    out = []
    for q in questions:
        c = coverage(q.get("query", ""), doc_text)
        lo, hi = COVERAGE_BOUNDS.get(q.get("difficulty"), (0.0, 1.0))
        out.append({**q, "coverage": round(c, 4), "coverage_ok": lo <= c <= hi})
    return out


def call_gemini(unit, retries=3):
    user_content = USER_PROMPT_TEMPLATE.format(
        type=unit["type"],
        context_json=json.dumps(unit["context"], ensure_ascii=False, indent=2))

    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }, ensure_ascii=False).encode("utf-8")

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                PROXY_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            # 超时 60→180s：2026-08-14 实测，上游代理在卡顿期整分钟不返回，
            # 恢复后单次正常响应也要 19s。60s 会把"代理慢"误判成"调用失败"，
            # 而失败会被记成 skipped——**一次瞬时拥塞就变成"这条知识单元
            # 出不了题"这个永久结论**。宁可等，也不要把慢当成不可能。
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = json.loads(resp.read().decode("utf-8"))

            text_blocks = [b["text"] for b in raw.get("content", []) if b.get("type") == "text"]
            text = "".join(text_blocks).strip()
            return text, raw, user_content
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            # 线性退避 2/4/6s 对"代理整段时间不可用"这种形态基本无效——
            # 12 秒后它多半还是不可用。改指数退避，给它真正的恢复窗口。
            if attempt < retries:
                time.sleep(min(60, 5 * (2 ** (attempt - 1))))
    raise RuntimeError(f"调用失败（重试{retries}次后放弃）：{last_err}")


def parse_response(text):
    """解析 Gemini 返回的 JSON。做防御性处理——即使提示词要求了纯 JSON，
    真实调用中偶尔还是会包一层 markdown 代码块或带多余说明文字，需要兜底提取。"""
    candidate = text.strip()
    # 去掉可能的 markdown 代码块包裹
    m = re.search(r"\{.*\}", candidate, re.DOTALL)
    if m:
        candidate = m.group(0)
    return json.loads(candidate)


# ═════════════════════════════════════════════════════ 主流程

def assert_not_clobbering_frozen():
    """已冻结的评测集不许被一次新出题静默覆盖。

    `eval_candidates.jsonl` 是 `eval_set.jsonl` 的上游，而后者的 sha256 已写进
    `eval_set_frozen.json` 并做过外部时间戳锚定。重新出题必然产出不同的题
    （模型输出不确定），所以它必须伴随「重新复核 + 重新冻结 + 重新锚定」
    一整套动作，不能是跑一下脚本就顺手把上游冲掉。
    这里只拦一步：冻结登记存在时，覆盖上游要显式 --force。
    """
    frozen_meta = os.path.join(BASE, "eval_set_frozen.json")
    if not os.path.exists(frozen_meta) or not os.path.exists(OUT_CANDIDATES):
        return
    with open(frozen_meta, encoding="utf-8") as f:
        meta = json.load(f)
    raise SystemExit(
        f"拒绝执行：评测集 v{meta.get('version')} 已冻结"
        f"（{meta.get('question_count')} 题，sha256={str(meta.get('sha256'))[:16]}…），"
        f"而本脚本会覆写它的上游 {os.path.basename(OUT_CANDIDATES)}。\n"
        f"重新出题必须连带重新复核、重新冻结、重新做外部时间戳锚定——"
        f"确认要走这一整套流程再加 --force。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 条知识单元（调试用）")
    ap.add_argument("--resume", action="store_true",
                    help="从 generation_checkpoint.jsonl 断点继续，跳过已完成的知识单元。"
                         "上游代理卡死/网络中断导致跑到一半时用它，不必重烧配额。"
                         "不带此参数时会清掉旧断点重新开始。")
    ap.add_argument("--force", action="store_true",
                    help="已有冻结评测集时仍允许覆写候选集（须同时重新冻结+重新锚定）")
    args = ap.parse_args()

    if not args.force:
        assert_not_clobbering_frozen()

    started_at = datetime.now(timezone.utc)

    units = load_units()
    if args.limit:
        units = units[:args.limit]

    print(f"知识单元总数：{len(units)}（doc_id 0~{len(load_units())-1}，本次处理 {len(units)} 条）")

    # ══ 审计三件套之一：run_id ═══════════════════════════════════════
    # 由「时间戳 + 输入指纹」派生。输入指纹囊括了知识库、prompt、脚本自身、
    # 模型与采样参数——换掉其中任何一个，run_id 的指纹段就会变，评委据此
    # 能判断自己手上的输入与我们这次是否一致（详见 generation_audit.py）。
    fingerprints = {
        "knowledge_base": audit.knowledge_snapshot(KNOWLEDGE_DIR),
        "system_prompt_sha256": SYSTEM_PROMPT_SHA256,
        "user_prompt_template_sha256": audit.sha256_text(USER_PROMPT_TEMPLATE),
        "script_sha256": audit.sha256_file(os.path.abspath(__file__)),
        "render_units_sha256": audit.sha256_file(
            os.path.join(KNOWLEDGE_DIR, "render_units.py")),
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "coverage_bounds": COVERAGE_BOUNDS,
        "limit": args.limit,
    }
    run_id = audit.derive_run_id(fingerprints, started_at)
    unit_fp = audit.unit_fingerprints(units)     # doc_id → 内容指纹
    print(f"run_id = {run_id}")
    print(f"system prompt {PROMPT_VERSION}，sha256={SYSTEM_PROMPT_SHA256[:16]}…（全文写入 {OUT_AUDIT}）")

    def stamped(doc_id, **fields):
        """审计三件套之三：逐题溯源。

        三个产物（候选集 / 原始日志 / 跳过日志）的每一条记录都过这个函数，
        因此三者的 run_id 必然相同——校验脚本第一步就是核对三者的 run_id
        集合是否相等，不等即说明有产物来自别的运行。
        除 doc_id（会随重新挖掘整体重排的位置编号）外还带上源单元的**内容
        指纹**，这样以后语料重编号了，仍能凭指纹判断源头还在不在、变没变。
        """
        fp = unit_fp[doc_id]
        return {"run_id": run_id, "doc_id": doc_id,
                "source_unit_sha256": fp["unit_sha256"],
                "source_rendered_sha256": fp["rendered_sha256"],
                **fields}

    candidates = []
    skipped = []
    raw_log = []

    coverage_violations = []  # R2-03：重合度超标、重试后仍未达标的记录（如实留档，不静默丢弃）

    # ── 断点续跑（2026-08-14 新增）──────────────────────────────────
    #
    # 起因是一次真实事故：全量出题跑到 **40/86** 时，上游 Gemini 代理卡死
    # （进程活着、端口在听、accept 队列堆积，但一个请求都不返回），脚本随之
    # 中断。而三个 `dump_jsonl` 全在循环**之后**——已经花掉的 40 次模型调用
    # 连同结果**一起丢光**，重跑等于重烧一遍配额。
    #
    # 这类"只在最后落盘"的写法，在**单次运行几十分钟且依赖外部服务**的场景
    # 下是脆的：中断概率不低，而中断代价是全损。改为每完成一条就追加落盘。
    #
    # 恢复判据用 `doc_id`：断点文件里已有的 doc_id 直接跳过。不用"行数"判断
    # ——跳过的单元不产候选，行数与进度对不上。
    ckpt_path = os.path.join(BASE, "generation_checkpoint.jsonl")
    done_doc_ids = set()
    if args.resume and os.path.exists(ckpt_path):
        with open(ckpt_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue          # 中断时可能写了半行，跳过即可
                done_doc_ids.add(rec["doc_id"])
                kind = rec.get("_kind")
                if kind == "candidate":
                    candidates.append(rec["payload"])
                elif kind == "skipped":
                    skipped.append(rec["payload"])
                elif kind == "raw":
                    raw_log.append(rec["payload"])
                elif kind == "coverage":
                    coverage_violations.append(rec["payload"])
        print(f"断点续跑：已完成 {len(done_doc_ids)} 条知识单元，"
              f"跳过它们直接从未完成处继续。")
    elif not args.resume and os.path.exists(ckpt_path):
        os.remove(ckpt_path)          # 不续跑就清掉旧断点，避免半新半旧

    def ckpt_append(doc_id, kind, payload):
        with open(ckpt_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"doc_id": doc_id, "_kind": kind,
                                "payload": payload}, ensure_ascii=False) + "\n")

    for doc_id, unit in enumerate(units):
        if doc_id in done_doc_ids:
            continue
        print(f"[{doc_id+1}/{len(units)}] 类型={unit['type']} ...", end=" ", flush=True)
        _call_failed = False   # 本条是否因调用失败而中止（见下方 break 处说明）

        parsed = None
        checked_questions = None
        last_text, last_raw_response, last_prompt_used = None, None, None
        # R2-03：出题后核对每一档的字面重合度是否落在目标区间，不达标的
        # 打回重出（最多 GENERATE_MAX_RETRIES 次）——这是"根治"而不是
        # "在 prompt 里再多写一句话希望AI照做"，因为覆盖率是代码核出来的，
        # 不是靠信任模型自觉。
        for attempt in range(1, GENERATE_MAX_RETRIES + 1):
            try:
                text, raw_response, prompt_used = call_gemini(unit)
            except RuntimeError as e:
                print(f"失败：{e}")
                skipped.append(stamped(doc_id, reason=f"调用失败: {e}"))
                # ⚠️ **不写进断点**。这一条是 2026-08-14 实测踩出来的：
                # 上游代理不稳，86 条里 36 条因 `timed out` 进了 skipped，
                # 而断点把它们记成"已完成" ⇒ `--resume` 会永久跳过它们，
                # **一次瞬时网络故障被固化成了"这条知识单元出不了题"这个结论**。
                # 模型判定的"不可出题"是结论，调用失败不是——两者绝不能同等对待。
                _call_failed = True
                break
            last_text, last_raw_response, last_prompt_used = text, raw_response, prompt_used

            try:
                parsed = parse_response(text)
            except (json.JSONDecodeError, AttributeError):
                print(f"JSON解析失败，原文前80字：{text[:80]!r}")
                skipped.append(stamped(doc_id, reason="JSON解析失败", raw_text=text[:300]))
                parsed = None
                break

            if not parsed.get("questions"):
                break  # 模型主动声明"不足以出题"，不是重合度问题，不用重试

            checked_questions = check_coverage(parsed["questions"], unit["rendered"])
            if all(q["coverage_ok"] for q in checked_questions):
                break
            if attempt < GENERATE_MAX_RETRIES:
                print(f"(第{attempt}次重合度超标，重出) ", end="", flush=True)

        if last_text is not None:
            raw_log.append(stamped(
                doc_id,
                called_at=datetime.now(timezone.utc).isoformat(),
                model_requested=MODEL,
                model_responded=last_raw_response.get("model"),
                max_tokens=MAX_TOKENS,
                # R3-13 的「新发现的设计缺陷」：此前 prompt 字段只存
                # user_content，SYSTEM_PROMPT 从不落盘——而争议的核心恰恰是
                # system prompt 里的档位定义与覆盖率目标，审计日志系统性地
                # 漏掉了唯一需要被审计的那部分。这里改为：全文只在审计清单
                # 里存一份（逐行重复 6KB 的 prompt 毫无意义），日志逐行存
                # 版本号 + sha256，两边对不上就说明日志与清单不是一次运行。
                prompt_version=PROMPT_VERSION,
                system_prompt_sha256=SYSTEM_PROMPT_SHA256,
                user_prompt=last_prompt_used,
                attempts=attempt,
                raw_response_text=last_text,
            ))
            # 原始响应也必须进断点：续跑后若只恢复候选、不恢复它，
            # 审计链会缺前半段，而 verify_generation_audit.py 正是靠
            # 三个产物的 run_id 集合是否一致来判定完整性的。
            ckpt_append(doc_id, "raw", raw_log[-1])

        if parsed is None:
            # 调用失败或 JSON 解析失败，已经记进 skipped。
            # 调用失败**不进断点**（见上），下次 --resume 会重试这一条；
            # JSON 解析失败则是模型真的给了坏输出，属于结论，进断点。
            if not _call_failed:
                ckpt_append(doc_id, "skipped", skipped[-1])
            continue

        if not parsed.get("questions"):
            reason = parsed.get("unanswerable_reason", "未说明原因")
            print(f"跳过：{reason}")
            _rec = stamped(doc_id, reason=reason)
            skipped.append(_rec)
            ckpt_append(doc_id, "skipped", _rec)
            continue

        for q in checked_questions:
            if not q["coverage_ok"]:
                _cov = stamped(
                    doc_id, query=q.get("query", ""),
                    difficulty=q.get("difficulty"), coverage=q["coverage"])
                coverage_violations.append(_cov)
                ckpt_append(doc_id, "coverage", _cov)
            _cand = stamped(
                doc_id,
                knowledge_type=unit["type"],
                query=q.get("query", "").strip(),
                difficulty=q.get("difficulty", "unknown"),
                source_rendered=unit["rendered"],
                bigram_coverage=q["coverage"],
                prompt_version=PROMPT_VERSION,
                system_prompt_sha256=SYSTEM_PROMPT_SHA256,
            )
            candidates.append(_cand)
            ckpt_append(doc_id, "candidate", _cand)
        print(f"生成 {len(checked_questions)} 条"
              + (f"（{sum(1 for q in checked_questions if not q['coverage_ok'])}条"
                 "重试后仍超标，已如实标注不剔除）"
                 if any(not q["coverage_ok"] for q in checked_questions) else ""))

    def dump_jsonl(path, rows):
        # newline="\n"：Windows 上默认会写成 CRLF，而冻结登记与血缘都按
        # 归一后的内容算指纹，混着写会让「同一份内容」在两台机器上长得不一样。
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 走到这里说明整轮跑完了，断点文件已无用——留着会让下次 `--resume`
    # 误以为还有未完成的工作。删除前先确认三个正式产物都写成功（见下），
    # 所以删除动作放在 dump 之后。
    dump_jsonl(OUT_CANDIDATES, candidates)
    dump_jsonl(OUT_RAW_LOG, raw_log)
    dump_jsonl(OUT_SKIPPED, skipped)
    if os.path.exists(ckpt_path):
        os.remove(ckpt_path)

    if coverage_violations:
        cov_path = os.path.join(BASE, "generation_coverage_violations.jsonl")
        dump_jsonl(cov_path, coverage_violations)
        print(f"\n⚠️ {len(coverage_violations)} 条问题重试{GENERATE_MAX_RETRIES}次后"
              f"重合度仍超标，未剔除但已如实标注 → {cov_path}")

    # ══ 审计三件套之二：prompt 全文落盘 ═════════════════════════════
    # 源码会改，改完就再也说不清「当时用的是哪版」——v1.3.0 的 169 题现在
    # 正处在这个状态。把实际发出去的 prompt 全文连同 sha256 写进清单，
    # 这份清单才是审计的入口，源码只是它的一个可能来源。
    # 续跑时产物里会合法地混着历次 run_id（断点恢复回来的那些记录带着
    # 它们当时的戳）。如实收集起来写进清单的 run_lineage——不重写它们，
    # 也不假装这是一次跑完的。理由见 generation_audit.write_manifest 的注释。
    _lineage = {r.get("run_id") for r in (candidates + raw_log + skipped)
                if r.get("run_id")}
    audit.write_manifest(
        OUT_AUDIT,
        run_id=run_id,
        run_lineage=sorted(_lineage),
        prompt_block={
            "version": PROMPT_VERSION,
            "system_prompt_sha256": SYSTEM_PROMPT_SHA256,
            "system_prompt_text": SYSTEM_PROMPT,
            "user_prompt_template": USER_PROMPT_TEMPLATE,
            "user_prompt_template_sha256": audit.sha256_text(USER_PROMPT_TEMPLATE),
            "difficulty_specs": DIFFICULTY_SPECS,
            "difficulty_tiers": sorted(DIFFICULTY_SPECS),
            "coverage_bounds": {k: list(v) for k, v in COVERAGE_BOUNDS.items()},
            "max_retries_on_coverage": GENERATE_MAX_RETRIES,
        },
        model_block={
            "requested": MODEL,
            "responded": sorted({r.get("model_responded") for r in raw_log
                                 if r.get("model_responded")}),
            "endpoint": PROXY_URL,
            "params": {"max_tokens": MAX_TOKENS},
            "note": "模型输出不确定，本清单不声称重跑能得到同一批题；"
                    "它记的是这批题产生时的条件，不是可复现的配方。",
        },
        knowledge_dir=KNOWLEDGE_DIR,
        units=units,
        artifacts={
            "candidates": os.path.basename(OUT_CANDIDATES),
            "raw_log": os.path.basename(OUT_RAW_LOG),
            "skipped": os.path.basename(OUT_SKIPPED),
            "counts": {"candidates": len(candidates), "raw_log": len(raw_log),
                       "skipped": len(skipped),
                       "coverage_violations": len(coverage_violations)},
        },
        generator_block={
            "script": "评测集/generate_eval_candidates.py",
            "script_sha256": audit.sha256_file(os.path.abspath(__file__)),
            "render_units_sha256": audit.sha256_file(
                os.path.join(KNOWLEDGE_DIR, "render_units.py")),
            "limit": args.limit,
        },
        started_at=started_at,
    )

    print(f"\n候选问题：{len(candidates)} 条 → {OUT_CANDIDATES}")
    print(f"跳过的知识单元：{len(skipped)} 条 → {OUT_SKIPPED}")
    print(f"原始调用日志：{OUT_RAW_LOG}（每行带 run_id 与 system_prompt_sha256）")
    print(f"审计清单：{OUT_AUDIT}（prompt 全文 + 逐单元内容指纹，run_id={run_id}）")
    print(f"下一步：python verify_generation_audit.py 核对审计链是否完整")


if __name__ == "__main__":
    main()
