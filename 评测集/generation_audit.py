#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generation_audit.py —— 出题审计链：run_id、prompt 落盘、逐题溯源
（第四轮独立复核 R3-13 / R2-07，三轮遗留）

═══ 要解决的问题 ═══════════════════════════════════════════════════

项目对评委的核心叙事是「评测集可信度设计：记录生成 prompt、复核规则、
冻结时间与哈希，使评委可复核不是**自己出题自己考**」。第四轮实测这句话
是落空的——拿到评测集之后，没有任何办法回答下面四个问题里的任何一个：

    这道题是哪一次运行出的？        → 没有 run_id
    当时发给模型的 prompt 是哪一版？→ SYSTEM_PROMPT 只活在源码里，从不落盘
    它来自哪条知识单元、内容是什么？→ 只有 doc_id 这个会整体重排的序号
    用的哪个模型、什么参数？        → 只有冻结元信息里一句人写的自述

第三个尤其致命：`doc_id` 是**位置编号**，不是稳定标识。挖掘器一改，
95 条语料就能变成 152 条，同一个 `doc_id=61` 指向的东西完全不同。
`generation_raw_log.jsonl` 正是栽在这里——它记的是 v1.2.0 那轮
（doc_id 0~151），与当前 v1.3.0（0~94）根本不是一套编号，却一直挂在
交付物表里写着「供可信度审计」。

═══ 审计三件套 ═══════════════════════════════════════════════════

1. **run_id**：一次出题运行一个 id，**三个产物写同一个**
   （候选集 / 原始日志 / 跳过日志）。审计时先核对三者的 run_id 集合是否
   相同——集合不同就说明有产物来自别的运行，正是 v1.2.0 那份日志混进
   v1.3.0 交付物的那种情况，而当时没有任何机制能发现它。

   派生方式是「时间戳 + 输入指纹」而不是 `uuid4()`：
   - 纯随机 id 无法复现，评委拿到同样的输入重跑一遍，对不上任何东西；
   - 纯哈希又无法区分「同样输入跑了两次」（模型输出不确定，两次结果不同）。
   两段拼起来：时间戳区分运行，指纹段证明输入相同。指纹段对不上，
   就说明知识库或 prompt 变过，这次运行不能和上次比。

2. **prompt 落盘**：把实际发出去的 system prompt **全文 + sha256** 写进
   审计清单文件，而不是留在源码里。源码会改（本项目的档位定义已经从
   两档改成三档、覆盖率约束是后加的），改完之后就再也说不清「当时用的是
   哪版」——这不是假想，v1.3.0 的 169 题现在就处在这个状态。

3. **逐题溯源**：每条候选记录里带上
   `run_id` + `source_unit_sha256` + `source_rendered_sha256`。
   `source_unit_sha256` 是**该知识单元当时的内容指纹**（不是 doc_id），
   哪怕以后重新挖掘、编号全乱，也能凭指纹判断「这道题的源头还在不在、
   变没变」。

═══ 诚实边界：补登不许冒充 ═══════════════════════════════════════

对**已交付的 v1.3.0 这 169 题**，上面三件套一件都没有。本模块提供
`write_backfilled_manifest()` 做事后补登，并严格照抄
`可复现性体系/provenance.py` 的 `backfilled=True` 标准：

> 关键字段一律写 `null`，绝不拿**当前源码**里的 prompt 冒充「当时的 prompt」。
> 那是把一句谎话变成结构化数据，比没有审计链更糟——下一轮复核会直接采信。

而且本项目有**正面证据**证明当前源码的 prompt 不是当时那版（见
`backfill_evidence()`）：已交付的 178 条候选只有 close / hard 两档，
当前 `SYSTEM_PROMPT` 出三档（含 paraphrase）；候选记录里没有
`bigram_coverage` 字段，而当前脚本对每条都写。所以补登清单里的
prompt 字段不是「不知道」，是「已知不是这一版」，两者要分别标注。

═══ 与 provenance.py 的分工 ═══════════════════════════════════════

| | `可复现性体系/provenance.py` | 本模块 |
|---|---|---|
| 对象 | **确定性脚本**产出的文件 | **调大模型**产出的文件 |
| 判据 | 重算代码/输入指纹即可复现 | 模型输出不确定，重跑也不会一致 |
| 因此 | 能证明「产物出自这份代码」 | 只能证明「这次运行吃的是这些输入、
                                这版 prompt」，不能证明输出可复现 |

大模型出题**天然不可复现**，所以审计链的目标不是「重跑得到同一批题」，
而是「说清这批题是在什么条件下产生的」。这个区别必须写在报告里，
不能让评委以为记了 run_id 就等于可复现。
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)

SCHEMA = "eval-generation-audit/1"

# 一次运行的审计清单落在这里。文件名不带 run_id：审计的入口必须是**唯一确定**
# 的一个路径，否则「清单缺失」就退化成「我没找到而已」，缺失即失败这条判据
# 就作废了。历史清单要留档的话按 run_id 归档到 audit_history/ 下。
MANIFEST_NAME = "generation_audit.json"
MANIFEST_PATH = os.path.join(BASE, MANIFEST_NAME)

KNOWLEDGE_FILES = ["cases.jsonl", "workflows.jsonl", "templates.jsonl"]


# ═════════════════════════════════════════════════════ 指纹

def _norm(raw: bytes) -> bytes:
    """行尾归一后再算指纹。理由同 provenance.py：core.autocrlf 不同的两台
    机器算出的指纹会不一样，跨机克隆时会集体误报（本项目已踩过一次）。"""
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(_norm(raw)).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def sha256_unit(unit_raw: dict) -> str:
    """知识单元的内容指纹。

    用 sort_keys 的紧凑 JSON 而不是原始行文本：同一条知识单元被重新
    序列化（键序变了、空格变了）时指纹不该变，变的必须是**内容**。
    这条指纹是逐题溯源的锚——doc_id 会随重新挖掘整体重排，指纹不会。
    """
    return sha256_text(json.dumps(unit_raw, ensure_ascii=False,
                                  sort_keys=True, separators=(",", ":")))


# ═════════════════════════════════════════════════════ run_id

def derive_run_id(input_fingerprints: dict, started_at=None) -> str:
    """派生 run_id：`gen-<UTC时间戳>-<输入指纹前12位>`。

    `input_fingerprints` 应当囊括「换了它结果就该不一样」的一切：
    知识库三个文件的指纹、system prompt 的指纹、出题脚本自身的指纹、
    模型名与采样参数、覆盖率验收区间。

    为什么不用 uuid4：评委重跑一遍，指纹段应当与我们的一致，不一致就说明
    输入变了、两次运行不可比——这个信息是随机 id 给不出来的。
    为什么还要时间戳：模型输出不确定，同样输入跑两次结果不同，纯指纹会把
    两次不同的运行叫成同一个 id。
    """
    started_at = started_at or datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    blob = json.dumps(input_fingerprints, ensure_ascii=False, sort_keys=True)
    return f"gen-{stamp}-{sha256_text(blob)[:12]}"


# ═════════════════════════════════════════════════════ 知识库快照

def knowledge_snapshot(knowledge_dir: str) -> dict:
    """当前知识库的文件级指纹与行数。**两种指纹都记**：

      · `sha256`     —— 行尾归一后算，跨机（core.autocrlf 不同）稳定，
                        本模块内部比对用；
      · `sha256_raw` —— 原始字节，与 `eval_set_frozen.json` 的
                        `knowledge_base_sha256` 同口径，冻结比对用。

    两者**不相等**：本仓库检出为 CRLF，`cases.jsonl` 归一后是 ae213e42…、
    原始字节是 0f41a65c…（后者才是冻结登记里那个）。第一版实现只记了归一
    指纹，还在清单注释里写了一句「它与冻结登记的 sha256 相等」——那句话是
    假的，而且因为没人真去比对，它可以一直假下去。所以两个都记，并让校验
    脚本**真的拿 raw 去比**：断言要么被机器核过，要么就别写。
    """
    files = {}
    for name in KNOWLEDGE_FILES:
        p = os.path.join(knowledge_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            lines = sum(1 for line in f if line.strip())
        with open(p, "rb") as f:
            raw = hashlib.sha256(f.read()).hexdigest()
        files[name] = {"sha256": sha256_file(p), "sha256_raw": raw, "lines": lines}
    return files


def unit_fingerprints(units: list) -> list:
    """把 load_units() 的结果压成审计用的逐单元指纹表。

    `units` 里每项需含 `type` / `raw` / `rendered`（与
    generate_eval_candidates.py 的 load_units() 一致）。
    下标即 doc_id——这层「下标就是编号」的约定本身就是 R3-13 的病根，
    所以这里把它显式写进产物，让它可被核对而不是靠记忆。
    """
    out = []
    for doc_id, u in enumerate(units):
        out.append({
            "doc_id": doc_id,
            "type": u["type"],
            "unit_sha256": sha256_unit(u["raw"]),
            "rendered_sha256": sha256_text(u["rendered"]),
        })
    return out


# ═════════════════════════════════════════════════════ 清单读写

def write_manifest(path, run_id, prompt_block, model_block,
                   knowledge_dir, units, artifacts, generator_block,
                   started_at=None, run_lineage=None):
    """真跑一次出题时写的审计清单（backfilled=False）。

    ═══ run_lineage：断点续跑让「一次运行」这个前提不再成立 ═══════════

    原设计假定三个产物必然出自同一次运行，审计时核对三者 run_id 集合是否
    **相同且只有一个**。2026-08-14 实测这个前提站不住：上游代理不稳，
    86 条跑到一半失败 36 条，靠 `--resume` 补齐——于是候选集里合法地混着
    三个 run_id（159 + 6 + 66 条）。

    这时有两种做法，差别很大：
      · 把 run_id 统一重写成最后一次的 —— **这是造假**，把"分三次出的题"
        说成"一次出的"，而审计链存在的全部意义就是不让人这么干；
      · 如实登记**血缘**：清单里列出所有参与过的 run_id，审计时核对
        "产物里出现的 run_id 集合 ⊆ 清单声明的血缘"。

    取后者。续跑是正当操作，不该逼人在"诚实"和"通过审计"之间二选一——
    该改的是审计判据，不是事实。
    """
    manifest = {
        "schema": SCHEMA,
        "backfilled": False,
        "run_id": run_id,
        # 本次清单对应的全部 run_id（含被续跑接续的历次）。只有一次运行时
        # 它就是 [run_id] 自己；续跑过就把历次都列出来，谁也别想蒙混。
        "run_lineage": sorted(set(run_lineage or []) | {run_id}),
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
        "written_at": datetime.now(timezone.utc).isoformat(),
        "generator": generator_block,
        "model": model_block,
        "prompt": prompt_block,
        "knowledge_base": {
            "dir": os.path.relpath(knowledge_dir, RAG).replace(os.sep, "/"),
            "files": knowledge_snapshot(knowledge_dir),
            "doc_id_space": [0, len(units) - 1] if units else None,
            "unit_count": len(units),
        },
        "units": unit_fingerprints(units),
        "artifacts": artifacts,
    }
    _dump(path, manifest)
    return manifest


def write_backfilled_manifest(path, eval_set_version, evidence,
                              known_gaps, frozen_reason,
                              knowledge_dir, units=None, artifacts=None):
    """事后补登（backfilled=True）。

    ⚠️ 照抄 provenance.py 的诚实标准：`run_id` / `prompt` / `model.params`
    一律 `null`，不用当前源码里的值顶上。补登清单唯一能承载的是
    「事后能从现存文件里**重新算出来**的东西」（比如知识库指纹），
    以及「关于当时状况的、可被证伪的证据」（`evidence`）。

    `frozen_reason` 必填，理由同 provenance.write()：登记一份来历不明的
    产物却不说为什么留着，等于用登记动作给它背书。
    """
    if not frozen_reason:
        raise ValueError(
            "补登审计清单必须写明 frozen_reason（为什么留着这批无审计链的题、"
            "为什么不重跑），否则等于用一次登记动作给来历不明的产物背书")
    if not known_gaps:
        raise ValueError(
            "补登审计清单必须列出 known_gaps（哪些字段是不可知的），"
            "空缺口列表会被下一轮复核读成「补登之后就没问题了」")

    manifest = {
        "schema": SCHEMA,
        "backfilled": True,
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
        "backfilled_note": (
            "事后补登。这批题产出时没有审计链机制，因此**无法证明**它出自哪一次"
            "运行、哪一版 system prompt、哪个模型的哪组参数；run_id / prompt / "
            "model 三处一律留 null，不用当前源码里的值冒充。"
            "本清单唯一能承载的是：①从现存文件重新算得的知识库指纹，"
            "②关于当时状况的、可被独立证伪的证据（evidence 字段）。"),
        "eval_set_version": eval_set_version,
        "run_id": None,
        "generator": {"script": "评测集/generate_eval_candidates.py",
                      "script_sha256": None,
                      "note": "当时用的脚本版本未留存，指纹不可知"},
        "model": {"requested": None, "responded": None, "endpoint": None,
                  "params": None,
                  "note": "eval_set_frozen.json 的 generation_model 字段有一句"
                          "人写的自述，但那是冻结时手填的声明，不是调用时"
                          "机器捕获的记录，两者证据等级不同，不搬到这里"},
        "prompt": None,
        "knowledge_base": {
            "dir": os.path.relpath(knowledge_dir, RAG).replace(os.sep, "/"),
            "files": knowledge_snapshot(knowledge_dir),
            "unit_count": len(units) if units is not None else None,
            "note": "这是**现在**重算的知识库指纹。校验脚本会拿其中的 "
                    "sha256_raw 与 eval_set_frozen.json 的 knowledge_base_sha256 "
                    "逐个比对（同为原始字节口径；归一口径的 sha256 与它不等，"
                    "本仓库检出为 CRLF，别混用）。比对通过只能说明**语料自冻结"
                    "以来没被换过**；它不能说明出题当时读的就是这份——出题早于"
                    "冻结，两者之间还隔着一次人工复核。",
        },
        "units_note": "这是**现在**重算的逐单元指纹，用来给「事后重建」提供锚点。"
                      "它不是出题当时捕获的——出题当时根本没记这个东西。"
                      "不要把它读成「每道题都追得到源单元」。",
        "units": unit_fingerprints(units) if units else None,
        "artifacts": artifacts or {},
        "evidence": evidence,
        "known_gaps": known_gaps,
        "frozen_reason": frozen_reason,
    }
    _dump(path, manifest)
    return manifest


def _dump(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_manifest(path=None):
    path = path or MANIFEST_PATH
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ═════════════════════════════════════════════════════ 补登证据的采集

def backfill_evidence(eval_dir, knowledge_dir):
    """采集「关于 v1.3.0 出题当时状况」的可证伪证据。

    每一条都是从现存文件里**重算**出来的，不是人写的断言——评委照着
    描述跑一遍就能推翻。这是补登清单里唯一有证据力的部分。
    """
    ev = []

    cand = _read_jsonl(os.path.join(eval_dir, "eval_candidates.jsonl"))
    eset = _read_jsonl(os.path.join(eval_dir, "eval_set.jsonl"))
    rawlog = _read_jsonl(os.path.join(eval_dir, "generation_raw_log.jsonl"))
    skipped = _read_jsonl(os.path.join(eval_dir, "generation_skipped.jsonl"))

    # ① 当前源码的 prompt 出三档，已交付候选只有两档 ⇒ 已知不是同一版
    tiers = sorted({c.get("difficulty") for c in cand})
    ev.append({
        "claim": "已交付候选集的档位只有 close / hard 两档，不含 paraphrase",
        "observed": {"difficulty_tiers": tiers,
                     "counts": _count(cand, "difficulty")},
        "how_to_falsify": "统计 eval_candidates.jsonl 的 difficulty 取值集合",
        "implication": "当前源码的 SYSTEM_PROMPT 要求出 close/paraphrase/hard "
                       "三档（R2-03 新增），因此当前源码里的 prompt **确定不是** "
                       "产出这批候选的那一版。补登清单的 prompt 字段不是"
                       "「不知道」，是「已知不是当前这版」。",
    })

    # ② 候选记录缺 bigram_coverage 字段 ⇒ 出题时覆盖率闸门尚不存在
    has_cov = sum(1 for c in cand if "bigram_coverage" in c)
    ev.append({
        "claim": "已交付候选集没有一条带 bigram_coverage 字段",
        "observed": {"with_bigram_coverage": has_cov, "total": len(cand)},
        "how_to_falsify": "统计 eval_candidates.jsonl 里含 bigram_coverage 的行数",
        "implication": "当前脚本对每条候选都写 bigram_coverage（R2-03 的覆盖率"
                       "闸门），全为 0 说明这批候选产出时该闸门还不存在，"
                       "与 ① 相互印证。",
    })

    # ③ generation_raw_log.jsonl 属于上一轮语料，不是这批题的日志
    log_ids = sorted({r.get("doc_id") for r in rawlog if "doc_id" in r})
    cand_ids = sorted({c.get("doc_id") for c in cand if "doc_id" in c})
    verbatim = _verbatim_hits(eset, rawlog)
    unit_total = sum(v["lines"] for v in knowledge_snapshot(knowledge_dir).values())
    ev.append({
        "claim": "generation_raw_log.jsonl 是上一轮（v1.2.0，152 条语料）"
                 "运行的产物，与这 169 题无关",
        "observed": {
            "raw_log_lines": len(rawlog),
            "raw_log_doc_id_range": [log_ids[0], log_ids[-1]] if log_ids else None,
            "candidates_doc_id_range": [cand_ids[0], cand_ids[-1]] if cand_ids else None,
            "current_doc_id_space_max": unit_total - 1 if unit_total else None,
            "frozen_questions_found_verbatim_in_same_doc_id_response": verbatim,
            "frozen_question_count": len(eset),
        },
        "how_to_falsify": "取每道冻结题，在 raw log 中同 doc_id 的 "
                          "raw_response_text 里做子串查找",
        "implication": "0 命中 + doc_id 空间超界（最大 151，当前语料合法区间"
                       "只到 94）⇒ 这份日志不能作为这 169 题的原始调用记录。"
                       "它在交付物表里长期挂着「供可信度审计」，是 R3-13 的核心。",
    })

    # ④ 三个产物的 run_id 集合（评审 §R3-13 给的做法）
    ev.append({
        "claim": "三个产物都没有 run_id 字段，无法核对它们是否出自同一次运行",
        "observed": {
            "eval_candidates.jsonl": _count(cand, "run_id"),
            "generation_raw_log.jsonl": _count(rawlog, "run_id"),
            "generation_skipped.jsonl": _count(skipped, "run_id"),
        },
        "how_to_falsify": "统计三个文件里 run_id 字段的取值分布",
        "implication": "全空 ⇒ 即使当时有一份日志混进了别的运行（③ 正是这种"
                       "情况），也没有任何机制能发现。",
    })

    return ev


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def _count(records, key):
    c = {}
    for r in records:
        v = r.get(key, "<缺失>")
        c[str(v)] = c.get(str(v), 0) + 1
    return c


def backfill_v130(eval_dir=BASE, knowledge_dir=None, path=None):
    """给已交付的 v1.3.0（169 题）补登审计清单。

    这个函数写出来的东西是一份**残缺的**清单，而且它必须看起来就是残缺的。
    第一版实现里我差点直接把当前源码的 SYSTEM_PROMPT 填进去——那会让
    校验脚本当场输出「169 题全部可追溯到 prompt 版本 v2」，而这句话是假的：
    已交付候选只有两档，当前源码出三档，用的根本不是同一版。
    provenance.py 的注释里对这件事的判语是「把一句谎话变成结构化数据，
    比没有审计链更糟」，此处照办。
    """
    knowledge_dir = knowledge_dir or os.path.join(RAG, "知识检索")
    path = path or os.path.join(eval_dir, MANIFEST_NAME)

    # units 只做「事后重建的锚点」用，语义已在 units_note 里说清
    sys.path.insert(0, eval_dir)
    from verify_generation_audit import load_units, reconstruct_report  # noqa: E402
    units = load_units(knowledge_dir)
    eset = _read_jsonl(os.path.join(eval_dir, "eval_set.jsonl"))
    rec = reconstruct_report(eset, knowledge_dir)

    evidence = backfill_evidence(eval_dir, knowledge_dir)
    evidence.append({
        "claim": "169 题的 source_rendered 全部能与当前知识库同 doc_id 的"
                 "渲染结果逐字对上（其中案例题须按 K-2 之前的 150 字截断规则）",
        "observed": rec,
        "how_to_falsify": "python verify_generation_audit.py（看 L1 那一行），"
                          "或按 verify_generation_audit.load_units() 的两种"
                          "render_variant 各渲染一遍逐字比对",
        "implication": "① doc_id → 知识单元 的映射自出题以来没有错位，语料也"
                       "没被换过；② 案例题 53/53 在 150 字规则下一致、在当前"
                       "300 字规则下只有 25 条一致，说明出题时用的是 K-2 之前"
                       "的 render_case。"
                       "⚠️ 这是**事后重建**（L1），不是审计链（L0）：它说不清"
                       "哪次运行、哪版 prompt、哪个模型，也无法排除「当时的"
                       "渲染代码另有其他差异恰好不影响这批题」。",
    })

    return write_backfilled_manifest(
        path,
        eval_set_version="1.3.0",
        evidence=evidence,
        known_gaps=[
            {"field": "run_id",
             "state": "不存在",
             "why": "出题时尚无 run_id 机制；三个产物无从核对是否同一次运行"},
            {"field": "prompt.system_prompt_text / sha256",
             "state": "不可知，且**已知不是当前源码这一版**",
             "why": "SYSTEM_PROMPT 从不落盘（R3-13 称之为「审计日志系统性地"
                    "漏掉了唯一需要被审计的那部分」）。当前源码是三档 + 覆盖率"
                    "闸门的 v2，而已交付候选只有 close/hard 两档、无 "
                    "bigram_coverage 字段，因此当时用的是更早的一版，"
                    "但那一版的全文没有任何留存"},
            {"field": "model / params",
             "state": "只有一句人写的自述，无机器捕获记录",
             "why": "eval_set_frozen.json 的 generation_model 字段是冻结时手填的"
                    "声明；调用时的响应 model 字段没有随这批题落盘"},
            {"field": "逐题 source_unit_sha256",
             "state": "不存在",
             "why": "当时每条候选只记 doc_id（会随重新挖掘整体重排的位置编号）"
                    "与 source_rendered 文本，没有源单元内容指纹"},
            {"field": "generation_raw_log.jsonl",
             "state": "存在，但属于上一轮运行，不覆盖这 169 题",
             "why": "doc_id 空间 0~151（v1.2.0 的 152 条语料），当前语料合法区间"
                    "只到 94；169 题在同 doc_id 响应里逐字命中 0 条。"
                    "它长期挂在交付物表里写着「供可信度审计」，是 R3-13 的核心"},
        ],
        frozen_reason=(
            "重新出题会产出不同的题（模型输出不确定），必须连带重新人工复核、"
            "重新冻结、重新做外部时间戳锚定，且要消耗 API 配额；而 v1.3.0 的 "
            "sha256 已经做过 GitHub issue 外部锚定，锚定链条一旦作废补不回来。"
            "因此本轮选择：保留这 169 题并如实登记「无审计链」，代码侧把三件套"
            "（run_id / prompt 落盘 / 逐题源指纹）补齐，留待下一次整链重跑时闭环。"),
        knowledge_dir=knowledge_dir,
        units=units,
        artifacts={
            "eval_set": "eval_set.jsonl",
            "candidates": "eval_candidates.jsonl",
            "raw_log": "generation_raw_log.jsonl（⚠️ 属于 v1.2.0 那轮，见 known_gaps）",
            "skipped": "generation_skipped.jsonl（⚠️ 同上）",
            "frozen_meta": "eval_set_frozen.json",
        })


def _verbatim_hits(questions, rawlog):
    by_doc = {}
    for r in rawlog:
        by_doc.setdefault(r.get("doc_id"), "")
        by_doc[r["doc_id"]] += r.get("raw_response_text", "")
    hit = 0
    for q in questions:
        text = by_doc.get(q.get("doc_id"), "")
        if q.get("query") and q["query"] in text:
            hit += 1
    return hit


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="出题审计链工具。--backfill 给已交付的 v1.3.0 补登（诚实版）")
    ap.add_argument("--backfill", action="store_true",
                    help="为 v1.3.0 的 169 题写事后补登清单（关键字段留 null）")
    a = ap.parse_args()
    if not a.backfill:
        ap.print_help()
        raise SystemExit(0)

    m = backfill_v130()
    print(f"已写出事后补登清单 → {MANIFEST_PATH}")
    print(f"  backfilled = {m['backfilled']}（run_id / prompt / model 一律 null）")
    print(f"  已登记缺口 {len(m['known_gaps'])} 项，可证伪证据 {len(m['evidence'])} 条")
    print("  下一步：python verify_generation_audit.py —— 它应当报「0 题可追溯」并返回退出码 2")
