#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_forget_ledger.py —— 遗忘账本 + 过滤器的本地单元测试

零依赖：不需要 device_c、不需要 embedding 服务、不需要 KYSDK。跑法：

    python3 记忆服务/test_forget_ledger.py

覆盖：
  1. compute_stable_key —— case / workflow / 不支持的类型
  2. ForgetLedger —— 追加写、生效状态取最后一条、unforget 撤销、重启重放
  3. apply_forget_filter —— 账本为空是 no-op；按 stable_key 精准滤掉
  4. RetrievalCore.retrieve —— 调的是真实方法本体（手工搭 stub 绕开
     __init__ 对 device_c/embedding 的硬依赖，不是另外复刻一份逻辑）：
     验证"遗忘过滤在截断之前"——忘掉 top-1 后仍返回满 k 条；账本为空时
     传给 _retrieve_ablation 的 max_k 与遗忘功能上线前完全一致。
"""
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
# compute_stable_key 的 case 分支要 import mine_conflicts——正常运行时这条
# 路径由 memory_service.setup_imports() 在服务启动时加好；这里是独立单测，
# 不走那条启动路径，手动补上（mine_conflicts.py 自己会再补 mine_cases 的路径）。
sys.path.insert(0, os.path.join(os.path.dirname(BASE), "知识检索"))

from forget_ledger import ForgetLedger, apply_forget_filter, compute_stable_key  # noqa: E402
import memory_service  # noqa: E402


def _case_meta(session_id="s1", failed_at="t1", tool_name="Bash"):
    return {"type": "case",
            "raw": {"session_id": session_id, "failed_at": failed_at, "tool_name": tool_name}}


def _workflow_meta(session_id="s1", intent_at="t1"):
    return {"type": "workflow", "raw": {"session_id": session_id, "intent_at": intent_at}}


class TestComputeStableKey(unittest.TestCase):
    def test_case(self):
        key = compute_stable_key(_case_meta("abc", "2026-01-01T00:00:00Z", "Bash"))
        self.assertEqual(key, "case:abc:2026-01-01T00:00:00Z:Bash")

    def test_workflow(self):
        key = compute_stable_key(_workflow_meta("abc", "2026-01-01T00:00:00Z"))
        self.assertEqual(key, "workflow:abc:2026-01-01T00:00:00Z")

    def test_unsupported_type_returns_none(self):
        self.assertIsNone(compute_stable_key({"type": "template", "raw": {"a": 1}}))

    def test_missing_meta_returns_none(self):
        self.assertIsNone(compute_stable_key(None))
        self.assertIsNone(compute_stable_key({"type": "case", "raw": "not-a-dict"}))


class TestForgetLedger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.path)     # ForgetLedger 应能从"文件不存在"起步

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_forget_then_unforget(self):
        ledger = ForgetLedger(self.path)
        self.assertEqual(ledger.forgotten_keys, set())
        ledger.forget("case:a:b:c", 5, "忘掉这条", "原文")
        self.assertEqual(ledger.forgotten_keys, {"case:a:b:c"})
        ledger.unforget("case:a:b:c", reason="误操作")
        self.assertEqual(ledger.forgotten_keys, set())

    def test_reload_from_disk_replays_last_op(self):
        ledger = ForgetLedger(self.path)
        ledger.forget("k1", 1, "i1", "t1")
        ledger.unforget("k1")
        ledger.forget("k2", 2, "i2", "t2")
        reloaded = ForgetLedger(self.path)      # 模拟服务重启后重新加载账本
        self.assertEqual(reloaded.forgotten_keys, {"k2"})

    def test_append_only_never_rewrites(self):
        ledger = ForgetLedger(self.path)
        ledger.forget("k1", 1, "i1", "t1")
        ledger.unforget("k1")
        with open(self.path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertEqual(len(lines), 2)          # 两条历史都在，没有被改写/合并


class _FakeDocs:
    def __init__(self, docs):
        self._docs = docs

    def get(self, doc_id):
        return self._docs.get(doc_id)

    def __len__(self):
        return len(self._docs)


class TestApplyForgetFilter(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.path)
        self.docs = _FakeDocs({
            1: {"meta": _case_meta("s1", "t1", "Bash")},
            2: {"meta": _case_meta("s2", "t2", "Bash")},
            3: {"meta": _workflow_meta("s3", "t3")},
        })

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_noop_when_ledger_empty(self):
        ledger = ForgetLedger(self.path)
        hit_ids = [1, 2, 3]
        self.assertEqual(apply_forget_filter(hit_ids, self.docs, ledger), hit_ids)

    def test_filters_only_forgotten_stable_key(self):
        ledger = ForgetLedger(self.path)
        ledger.forget("case:s1:t1:Bash", 1, "忘掉1", "text")
        self.assertEqual(apply_forget_filter([1, 2, 3], self.docs, ledger), [2, 3])

    def test_unforget_restores_visibility(self):
        ledger = ForgetLedger(self.path)
        ledger.forget("case:s1:t1:Bash", 1, "忘掉1", "text")
        ledger.unforget("case:s1:t1:Bash")
        self.assertEqual(apply_forget_filter([1, 2, 3], self.docs, ledger), [1, 2, 3])


class TestRetrieveFilterBeforeTruncate(unittest.TestCase):
    """直接调用 memory_service.RetrievalCore.retrieve 这个真实方法（不复刻
    一份），手工搭一个最小 stub 对象绕开 __init__ 里对 device_c/embedding 的
    硬依赖——测的是真实检索管线接线，不是测试自己写的另一份逻辑。
    """

    def _make_core(self, ledger, ranked_ids, k_seen):
        core = object.__new__(memory_service.RetrievalCore)
        core.config = "naive"
        core.edges = None
        core.bm25 = None
        core.mmr_lambda = 0.7
        core.hybrid_alpha = 0.7
        core.forget_ledger = ledger
        core.docs = _FakeDocs({
            i: {"meta": _case_meta(f"s{i}", f"t{i}", "Bash"), "text": f"text-{i}"}
            for i in ranked_ids
        })

        class _FakeClient:
            def encode(self, query):
                return [0.0]
        core.client = _FakeClient()

        class _FakeStore:
            def search(self, qvec, top_k):
                return [(i, 1.0 / (i + 1)) for i in ranked_ids[:top_k]]
        core.store = _FakeStore()

        def _fake_retrieve_ablation(config, query, qvec, store, docs, max_k, **kw):
            k_seen.append(max_k)
            hits = store.search(qvec, top_k=max_k)
            return [h[0] for h in hits]
        core._retrieve_ablation = _fake_retrieve_ablation
        return core

    def test_empty_ledger_identical_to_pre_forget_behavior(self):
        """账本为空：f=0，传给 _retrieve_ablation 的 max_k 必须与旧行为
        （直接传 k）完全一致——这是"不引入任何行为差异"的硬指标。"""
        ledger = ForgetLedger(tempfile.mktemp(suffix=".jsonl"))
        k_seen = []
        core = self._make_core(ledger, ranked_ids=[10, 11, 12, 13, 14], k_seen=k_seen)
        units, _ = memory_service.RetrievalCore.retrieve(core, "q", 3)
        self.assertEqual(k_seen, [3])
        self.assertEqual([u["doc_id"] for u in units], [10, 11, 12])

    def test_forgetting_top1_still_returns_k_results(self):
        """忘掉排名第一的单元后，仍应返回满 k 条——这正是 §2.3 的核心断言：
        过滤发生在截断之前，不是"忘了就少一条"。按需扩窗：第一次按原始 k=3
        查，撞上被遗忘的单元、缺 1 条，第二次才按 k=4 补位——不是一次性
        按账本总数加缓冲（那种做法在真实语料上炸过，见 memory_service.py
        retrieve() 里的注释）。"""
        ledger = ForgetLedger(tempfile.mktemp(suffix=".jsonl"))
        ledger.forget("case:s10:t10:Bash", 10, "忘掉10", "text-10")
        k_seen = []
        core = self._make_core(ledger, ranked_ids=[10, 11, 12, 13, 14], k_seen=k_seen)
        units, _ = memory_service.RetrievalCore.retrieve(core, "q", 3)
        self.assertEqual(k_seen, [3, 4])          # 先原始 k，撞上了才扩到 k+缺口
        self.assertEqual([u["doc_id"] for u in units], [11, 12, 13])   # 仍是3条，10被滤掉

    def test_untouched_query_never_expands_fetch_window(self):
        """账本非空，但本次查询的候选窗口里根本没撞上被遗忘的单元——不该
        触发第二次查询、不该改变候选池大小。这是本轮修复的核心断言：旧版
        "按账本总数加缓冲"的实现会让*任何*查询都跟着扩窗，哪怕跟被遗忘的
        单元毫不相关，致使 BM25 归一化范围跟着变、殃及无关排名（真实语料
        实测：副作用率从预期 100% 砸到 50% 出头，见 遗忘评测/预登记.md）。
        """
        ledger = ForgetLedger(tempfile.mktemp(suffix=".jsonl"))
        ledger.forget("case:s99:t99:Bash", 99, "忘掉一个跟这次查询无关的单元", "text-99")
        k_seen = []
        core = self._make_core(ledger, ranked_ids=[10, 11, 12, 13, 14], k_seen=k_seen)
        units, _ = memory_service.RetrievalCore.retrieve(core, "q", 3)
        self.assertEqual(k_seen, [3])             # 只查了一次，候选池大小不受影响
        self.assertEqual([u["doc_id"] for u in units], [10, 11, 12])


if __name__ == "__main__":
    unittest.main(verbosity=2)
