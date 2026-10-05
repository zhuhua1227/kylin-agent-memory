#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forget_ledger.py —— 自然语言指令驱动的精准遗忘：账本 + 查询时过滤器

依据《精准遗忘-实施方案.md》：不删向量，只加账本——沿用 supersede 的
查询时过滤模式（`知识检索/search_knowledge.py:apply_supersede_filter`）。

  · ForgetLedger   追加写 JSONL，生效状态 = 按 stable_key 取最后一条的 op。
  · compute_stable_key  按单元类型算稳定标识，重挖不变（doc_id 会变，这个不会）。
  · apply_forget_filter 与 apply_supersede_filter 同形：滤掉已遗忘的单元。

不改 `知识检索/mine_conflicts.py`：case 类型直接复用它的 `stable_key()`，
不发明第二套；workflow 类型那份文件没有对应公式（它只支持 case /
doc_revision 两种 kind），这里按同样的「内容自然键」思路（
`build_knowledge_index.py` 记录的 raw 字段里，session_id + 不随挖掘器
版本变化的时间戳字段）单独补一个，不是重新发明 case 已有的那套。
"""

import hashlib
import json
import os
import time


def compute_stable_key(meta):
    """按 unit type 算 stable_key；算不出就返回 None（不强凑）。

    meta 形如 `{"type": "case"|"workflow"|..., "raw": {...}}`，与
    `build_knowledge_index.py:load_units()` 写入 `DocStore` 的结构一致。
    """
    if not meta:
        return None
    raw = meta.get("raw")
    if not isinstance(raw, dict):
        return None

    t = meta.get("type")
    if t == "case":
        from mine_conflicts import stable_key as _mc_stable_key
        try:
            return _mc_stable_key(raw, "case")
        except (KeyError, ValueError):
            return None

    if t == "workflow":
        sid, at = raw.get("session_id"), raw.get("intent_at")
        if sid is None or at is None:
            return None
        return f"workflow:{sid}:{at}"

    # template / conflict 目前没有内容自然键公式（mine_conflicts.stable_key
    # 也不支持），刻意不用哈希临时凑一个——凑出来的 key 没人能独立验证它
    # 稳不稳定，还不如老实说"这类暂不支持精准遗忘"。
    return None


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


class ForgetLedger:
    """遗忘账本：追加写，永不改写既有行；生效状态 = 按 stable_key 取最后一条的 op。"""

    def __init__(self, path):
        self.path = path
        self.records = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.records.append(json.loads(line))

    def _append(self, record):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.records.append(record)

    @property
    def forgotten_keys(self):
        """当前生效的已遗忘 stable_key 集合（按 stable_key 取最后一条记录的 op）。"""
        last_op = {}
        for r in self.records:
            last_op[r["stable_key"]] = r["op"]
        return {k for k, op in last_op.items() if op == "forget"}

    def is_forgotten(self, stable_key):
        return stable_key in self.forgotten_keys

    def forget(self, stable_key, doc_id, instruction, text):
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        record = {
            "ts": _now_iso(), "op": "forget", "stable_key": stable_key,
            "doc_id": doc_id, "instruction": instruction,
            "text_sha256": text_sha256, "confirmed": True,
        }
        self._append(record)
        return record

    def unforget(self, stable_key, reason=None):
        record = {"ts": _now_iso(), "op": "unforget", "stable_key": stable_key,
                   "reason": reason}
        self._append(record)
        return record

    def list_forgotten(self):
        """当前生效的遗忘记录（每个 stable_key 最后一条 forget 记录本身）。"""
        last = {}
        for r in self.records:
            last[r["stable_key"]] = r
        return [r for r in last.values() if r["op"] == "forget"]


def apply_forget_filter(hit_ids, docs, ledger):
    """滤掉已遗忘的知识单元——与 `apply_supersede_filter` 同形。

    `hit_ids` 是 doc_id 有序列表（不是 (id, score) 元组，`retrieve_ablation`
    的返回值就是这个形状），`docs` 提供 `.get(doc_id) -> {"meta": {...}}`。
    """
    forgotten = ledger.forgotten_keys
    if not forgotten:
        return hit_ids
    out = []
    for did in hit_ids:
        doc = docs.get(did)
        meta = (doc or {}).get("meta")
        key = compute_stable_key(meta)
        if key is not None and key in forgotten:
            continue
        out.append(did)
    return out
