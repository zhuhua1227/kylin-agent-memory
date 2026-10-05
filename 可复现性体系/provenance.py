#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
provenance.py —— 产物血缘：内容级的「这份产物是不是当前代码 + 当前输入产出的」
（第四轮独立复核，§四·三"附带：mtime 类级检查被 touch 绕过"）

═══ 为什么必须换掉 mtime ═══════════════════════════════════════════

第三轮给出的判据是 `mtime(产物) < mtime(脚本)` —— 产物比脚本旧就说明
"代码改了、产物没重跑"。第四轮实测：三个知识产物的 mtime 被 touch 到
08-10 18:03，五对比较**全部 ok**，而内容仍是旧的（`cases.jsonl` 27 条 vs
当前代码产出的 15 条）。检查空过。

> **时间戳是元数据，不是内容证据。** 任何人一次 `touch`（哪怕不是有意的，
> 比如复制目录、解压、同步工具、编辑器保存）就能让它失去全部证据力。

正确做法是把"这份产物出自哪份代码、哪份输入"**写进产物旁边**，检查时
重算并比对：内容变了指纹必变，touch 无效。

═══ 与冻结守卫的分工（别混为一谈）═════════════════════════════════

| | `知识检索/freeze_guard.py` | 本模块 |
|---|---|---|
| 时机 | **写之前** | **写之后** |
| 问题 | "我能不能覆写它" | "它是谁产出的、还新不新" |
| 手段 | 比对评测集冻结登记的 sha256 | 比对伴生 `.provenance.json` 里的代码/输入指纹 |
| 缺失时 | 登记表读不到 = 硬失败 | 血缘文件缺失 = **失败，不是跳过** |

两者都必要：守卫防"手滑毁掉冻结链"，血缘防"改了代码忘了重跑"。
第四轮的 18 个阻塞项里，8 个出自后者。

═══ 血缘文件长什么样 ═══════════════════════════════════════════════

产物 `知识检索/cases.jsonl` 旁边放 `知识检索/cases.jsonl.provenance.json`：

    {
      "artifact": "cases.jsonl",
      "artifact_sha256": "0f41a65c…",
      "script": "知识检索/mine_cases.py",
      "script_sha256": "…",
      "inputs": {"多源数据整合/dataset/memory_source_records.jsonl": "…"},
      "generated_at": "2026-08-05T…Z",
      "frozen_reason": "……"        ← 有意冻结时必须写清理由
    }

`frozen_reason` 是**有意保留旧产物**时的唯一合法出口：写了理由，检查就从
"失败"降级为"已登记的落差"，并要求《代码产物落差登记表.md》里有对应行。
没写理由而指纹对不上 = 失败。

⚠️ **行尾归一化**：指纹计算前把 CRLF 折成 LF（同 `rescrub_strings.py` 的
R4-10）。否则 `core.autocrlf` 不同的两台机器算出的血缘不同，检查会在跨机
克隆时集体误报——这个坑本项目已经踩过一次（见《勘误与修正记录》第九节）。
"""

import hashlib
import json
import os
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)

PROVENANCE_SUFFIX = ".provenance.json"


def _norm_bytes(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_of(path) -> str:
    """内容指纹。行尾先归一，理由见文件头。"""
    with open(path, "rb") as f:
        return hashlib.sha256(_norm_bytes(f.read())).hexdigest()


def provenance_path(artifact_path) -> str:
    return artifact_path + PROVENANCE_SUFFIX


def write(artifact_path, script_path, input_paths,
          frozen_reason=None, backfilled=False):
    """产出产物之后调用，把血缘写在它旁边。

    ⚠️ `backfilled=True` 是给**已经存在、且已知不是当前代码产出**的老产物
    事后补登用的，此时 `script_sha256` / 输入指纹一律写 `null`。

    这个参数不是可选的礼貌，是必需的诚实。第一版实现里我给三个已知过期的
    冻结产物直接调了 `write()`，于是它把**当前**脚本的指纹写了进去，
    `check()` 当场返回三个 `ok`——**一份事后补的血缘，让一个已知过期的产物
    拿到了"出自当前代码"的机器可读背书**。这比没有血缘更糟：它把一句谎话
    变成了结构化数据，下一轮复核会直接采信。
    补登就要长得像补登：写 null，让 `check()` 只能给出 "frozen"，
    并在说明里写清"无法证明它出自哪版代码"。
    """
    prov = {
        "artifact": os.path.basename(artifact_path),
        "artifact_sha256": sha256_of(artifact_path),
        "script": os.path.relpath(script_path, RAG).replace(os.sep, "/"),
        "script_sha256": None if backfilled else sha256_of(script_path),
        "inputs": ({} if backfilled else {
            os.path.relpath(p, RAG).replace(os.sep, "/"): sha256_of(p)
            for p in input_paths if os.path.exists(p)
        }),
        "generated_at": None if backfilled else datetime.now(timezone.utc).isoformat(),
    }
    if backfilled:
        prov["backfilled_at"] = datetime.now(timezone.utc).isoformat()
        prov["backfilled_note"] = (
            "事后补登。本文件产出时没有血缘机制，因此**无法证明**它出自哪一版"
            "代码与输入；script_sha256 与 inputs 故意留空，不用当前指纹冒充。")
        if not frozen_reason:
            raise ValueError("补登血缘必须同时写明 frozen_reason，"
                             "否则等于登记了一份来历不明的产物却不说为什么留着")
    if frozen_reason:
        prov["frozen_reason"] = frozen_reason
    with open(provenance_path(artifact_path), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return prov


def load(artifact_path):
    p = provenance_path(artifact_path)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def check(artifact_path, script_path, input_paths):
    """核对一份产物的血缘。返回 (状态, 说明)。

    状态取值：
      · "ok"        —— 产物、代码、输入三者指纹全对得上
      · "frozen"    —— 指纹对不上，但血缘里写了 `frozen_reason`（有意冻结）
      · "stale"     —— 指纹对不上且**没有**理由 ⇒ 失败
      · "missing"   —— 没有血缘文件 ⇒ 失败（不是跳过！）

    ⚠️ "missing" 判失败而不是跳过，是这个模块存在的半个理由。旧的 mtime
    判据在文件不全时也是静默通过的，于是"没查"和"查过了"长得一样。
    """
    if not os.path.exists(artifact_path):
        return "missing", f"产物本身不存在：{artifact_path}"

    prov = load(artifact_path)
    if prov is None:
        return "missing", (
            f"缺少血缘文件 {os.path.basename(provenance_path(artifact_path))}"
            f"——无法判断这份产物出自哪版代码。"
            f"缺失按失败处理：不然「没查」和「查过了」在输出上一模一样。")

    problems = []
    if prov.get("artifact_sha256") != sha256_of(artifact_path):
        problems.append("产物内容与血缘里记的不一致（产物被改过）")

    # 事后补登的血缘不承载"出自哪版代码"这个信息，也**不许**假装承载。
    # 它唯一能证明的是"产物自补登以来没被改过"，其余一律按未知处理。
    if prov.get("script_sha256") is None and prov.get("backfilled_at"):
        if problems:
            return "stale", (f"{'；'.join(problems)}。"
                             f"（血缘为事后补登，本就不能证明代码来源；"
                             f"现在连产物自身都与补登时不一致了。）")
        return "frozen", (
            f"血缘为事后补登，**无法证明这份产物出自哪版代码或哪份输入**——"
            f"只能证明它自补登以来未被改动。已登记的有意冻结："
            f"{prov.get('frozen_reason')}")

    if os.path.exists(script_path):
        if prov.get("script_sha256") != sha256_of(script_path):
            problems.append(f"生成脚本已改动（{os.path.basename(script_path)}）")
    recorded_inputs = prov.get("inputs") or {}
    for p in input_paths:
        if not os.path.exists(p):
            continue
        rel = os.path.relpath(p, RAG).replace(os.sep, "/")
        if rel not in recorded_inputs:
            problems.append(f"输入未登记：{rel}")
        elif recorded_inputs[rel] != sha256_of(p):
            problems.append(f"输入已改动：{rel}")

    if not problems:
        return "ok", "代码与输入指纹均与产物血缘一致"
    reason = prov.get("frozen_reason")
    if reason:
        return "frozen", f"{'；'.join(problems)}｜已登记的有意冻结：{reason}"
    return "stale", (
        f"{'；'.join(problems)}。"
        f"血缘里没有 frozen_reason ⇒ 这是「改了代码忘了重跑」，不是有意冻结。"
        f"要么重跑产物，要么在血缘里写明冻结理由并在"
        f"《代码产物落差登记表.md》登记。")


# ── 本项目的产物依赖图：谁由谁产出、吃哪些输入 ─────────────────────
#
# 写在这里而不是散在各脚本里，是为了让"还有哪些产物没纳入血缘检查"
# 这个问题有一个**唯一的、可数的**答案。第四轮的判语是"未做类级收口"，
# 收口的前提就是先有一张完整的表。
ARTIFACT_GRAPH = [
    {
        "artifact": "知识检索/cases.jsonl",
        "script": "知识检索/mine_cases.py",
        "inputs": ["多源数据整合/dataset/memory_source_records.jsonl"],
    },
    {
        "artifact": "知识检索/workflows.jsonl",
        "script": "知识检索/mine_workflows.py",
        "inputs": ["多源数据整合/dataset/memory_source_records.jsonl"],
    },
    {
        "artifact": "知识检索/templates.jsonl",
        "script": "知识检索/mine_templates.py",
        "inputs": ["知识检索/cases.jsonl", "知识检索/workflows.jsonl"],
    },
    {
        # mine_conflicts.py 的 T2 候选（S3_case_signature）是靠内部重新
        # 调用 mine_cases.mine(recs) 现算的，不读盘上已有的 cases.jsonl
        # ——所以这里的输入只有原始数据集一项，与 write_provenance() 调用
        # 处登记的一致（血缘对得上，不能多写一个没被实际读取的文件）。
        "artifact": "知识检索/conflicts.jsonl",
        "script": "知识检索/mine_conflicts.py",
        "inputs": ["多源数据整合/dataset/memory_source_records.jsonl"],
    },
    {
        "artifact": "知识检索/preferences.jsonl",
        "script": "知识检索/mine_preferences.py",
        "inputs": ["多源数据整合/dataset/memory_source_records.jsonl"],
    },
]


def check_all():
    """按依赖图逐个核对。返回 [(产物, 状态, 说明)]。"""
    out = []
    for item in ARTIFACT_GRAPH:
        art = os.path.join(RAG, *item["artifact"].split("/"))
        script = os.path.join(RAG, *item["script"].split("/"))
        inputs = [os.path.join(RAG, *p.split("/")) for p in item["inputs"]]
        status, detail = check(art, script, inputs)
        out.append((item["artifact"], status, detail))
    return out
