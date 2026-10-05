#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_service.py —— 记忆服务（麒麟机上常驻的 HTTP 查询接口）

依据《记忆服务与pi对照实验-设计方案.md》§一 实现：

  POST /retrieve   {"query": "任务描述文本", "k": 5}
                   → {"units": [{"doc_id","type","text","score"}...],
                      "config": "...", "elapsed_ms": ...}
  GET  /health     → {"ok": true, "index_size": ..., "config": "...", "backend": "..."}

依据《精准遗忘-实施方案.md》新增（不删向量、只加账本 + 查询时过滤，与
supersede 同形）：

  POST /forget     {"instruction": "...", "k": 5}
                   → 第一步·定位：instruction 当 query 走现有检索链，
                     返回候选，不写账本
                   {"instruction": "...", "confirm_doc_id": 12}
                   → 第二步·确认：doc_id 须在第一步候选内，写账本
  POST /unforget   {"stable_key": "..."}    撤销
  GET  /forgotten                          列出当前生效的遗忘

硬约束落实：
  ① config 禁止 meta_oracle —— 启动时直接拒绝（标签泄漏，那是上界不是能力）。
  ② config 不写入代码，走 --config 启动参数；报告声明系统用哪档，服务就跑哪档。
  ③ 纯标准库 http.server，不引框架、不装任何包；只监听 127.0.0.1。
  ④ 检索管线复用 measure_recall.retrieve_ablation / vector_store /
     embedding_client，不重新发明；原文由 DocStore 取回。
  ⑤ 启动预热完整跑一次检索链（KYSDK 首次调用可能在加载模型，只预热
     encode 不够，首笔完整检索仍会有一次性开销）。

单线程 HTTPServer 是刻意的：emb_serverd/ve_serverd 的 stdin/stdout 管道协议
不是并发安全的（每条管道的读写字节序由调用方维护），并发请求会错位。
检索 P95 ≈ 112ms，单线程完全覆盖演示与实验流量。
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from forget_ledger import ForgetLedger, apply_forget_filter, compute_stable_key

ALLOWED_CONFIGS = ("naive", "meta", "mmr", "bm25", "supersede", "all")
DEFAULT_PORT = 8077


def find_knowledge_dir():
    """在 ~/RAG 下按内容找知识检索目录（含 cases.jsonl 的那个）——
    不把中文目录名写进代码，绕开终端/ssh 传输中文目录名编码损坏的老坑。"""
    home = os.path.expanduser("~/RAG")
    for d in sorted(os.listdir(home)):
        p = os.path.join(home, d)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "cases.jsonl")):
            return p
    raise SystemExit("找不到知识检索目录（~/RAG 下含 cases.jsonl 的子目录）")


def setup_imports(knowledge_dir):
    """measure_recall.py 的模块级代码用 RAG_KNOWLEDGE_DIR 环境变量解析知识目录，
    必须在 import 之前设置。"""
    os.environ["RAG_KNOWLEDGE_DIR"] = knowledge_dir
    sys.path.insert(0, knowledge_dir)
    sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
    rag_root = os.path.dirname(knowledge_dir)
    sys.path.insert(0, os.path.join(rag_root, "评测集"))
    sys.path.insert(0, os.path.join(rag_root, "可复现性体系"))


class RetrievalCore:
    """常驻部件：emb_serverd 连接、向量引擎连接、DocStore、消融管线所需结构。"""

    def __init__(self, config, backend, collection,
                 mmr_lambda=0.7, hybrid_alpha=0.7, docs_path=None,
                 forget_ledger_path=None):
        knowledge_dir = find_knowledge_dir()
        setup_imports(knowledge_dir)

        from vector_store import create_store, DocStore
        from embedding_client import EmbeddingClient
        from measure_recall import retrieve_ablation
        self._retrieve_ablation = retrieve_ablation

        self.config = config
        self.backend_name = backend
        self.mmr_lambda = mmr_lambda
        self.hybrid_alpha = hybrid_alpha

        _dp = docs_path or os.path.join(knowledge_dir, "knowledge_docs.jsonl")
        if not os.path.isabs(_dp):
            _dp = os.path.join(knowledge_dir, _dp)
        self.docs = DocStore(_dp)
        if len(self.docs) == 0:
            raise SystemExit(f"{_dp} 是空的——请先跑 build_knowledge_index.py")

        self.edges = None
        if config in ("supersede", "all"):
            from mine_conflicts import build_supersede_edges
            self.edges = build_supersede_edges()

        self.bm25 = None
        if config in ("bm25", "all"):
            from search_knowledge import BM25
            ids = sorted(self.docs._docs.keys())
            self.bm25 = BM25(ids, [self.docs.get(i)["text"] for i in ids])

        self.client = EmbeddingClient(
            serverd_path=os.path.expanduser("~/rag/serverd/emb_serverd"))

        kwargs = {"collection": collection} if backend == "kylin" else \
                 {"data_dir": os.path.join(knowledge_dir, "knowledge_store_numpy")}
        self.store = create_store(backend, **kwargs)
        # ⚠️ 只读场景：绝不调用 store.create()（它会清空集合），索引归
        # build_knowledge_index.py 管；重启服务是重建索引后的刷新手段。

        n = self.store.count()
        if n == 0:
            raise SystemExit(
                f"向量集合 {collection!r} 是空的——请先跑 build_knowledge_index.py --backend {backend}")
        self.index_size = n

        # 遗忘账本：不删向量、只加账本 + 查询时过滤（《精准遗忘-实施方案.md》）。
        # 默认与本脚本同目录（部署时与 runs.jsonl/payload_log 同层），评测跑
        # 独立账本时用 --forget-ledger 指到别处，不污染生产账本。
        ledger_path = forget_ledger_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "forget_ledger.jsonl")
        self.forget_ledger = ForgetLedger(ledger_path)

        # 预热：KYSDK 首次调用可能在加载模型，设计文档点名 errCode:24 Timeout
        # 是正常现象。只预热 encode 不够——实测首笔完整检索仍要 500ms+（BM25
        # 分词、MMR 候选拉取等首路径都有一次性开销），所以预热直接完整跑一次
        # 检索链，把所有首调开销消化在启动期，保证第一笔真实请求就 <500ms。
        self.retrieve("预热：完整跑一次检索链", 3)

    def retrieve(self, query, k):
        t0 = time.perf_counter()
        qvec = self.client.encode(query)

        # 遗忘过滤必须在截断到 k 之前——否则忘掉 top-1 后只剩 k-1 条，行为不
        # 一致（《精准遗忘-实施方案.md》§2.3）。
        #
        # 🔴 按需扩窗，不按账本总数扩窗：retrieve_ablation 内部的候选池大小
        # 是 max(max_k*5, 50)——按 max_k 分档，不是连续量。第一版实现直接用
        # "k + 账本总遗忘数" 当 max_k，实测在 k=10（本项目 eval 用的档位，
        # 10*5 恰好卡在 50 这个档位边界上）时，只要账本里有任意一条遗忘
        # （哪怕跟这次查询完全无关），max_k 就从 10 顶到 11、候选池从 50
        # 跳到 55——BM25 混合分数靠 `_minmax_normalize` 对整个候选池做
        # 全局归一化，池子一变大，连没被遗忘、根本不挨着的文档排名都跟着
        # 抖——旁观题被殃及，副作用率从预期的 100% 砸到了 50% 出头（见
        # 遗忘评测/预登记.md 的踩坑记录）。
        #
        # 正确做法：先按原始 k 查一次（账本为空时这就是唯一一次查询，
        # max_k 与遗忘功能上线前逐位相同，候选池大小也不变——真正的
        # "零行为差异"保证，不是只在账本空的时候凑巧成立）。只有当过滤
        # 之后数量不够 k 条（说明这次查询的候选窗口里真实撞上了被遗忘的
        # 单元），才按缺口大小再查一次更大的 max_k 补位——候选池扩张只发生
        # 在"确实撞上"的查询上，没撞上的查询连 fetch_k 都不会变。
        request_k = k
        while True:
            hit_ids = self._retrieve_ablation(
                self.config, query, qvec, self.store, self.docs, request_k,
                edges=self.edges, bm25=self.bm25,
                mmr_lambda=self.mmr_lambda, hybrid_alpha=self.hybrid_alpha)
            filtered = apply_forget_filter(hit_ids, self.docs, self.forget_ledger)
            deficit = k - len(filtered)
            if deficit <= 0 or len(hit_ids) < request_k or request_k >= len(self.docs):
                # 凑够 k 条了；或者语料已经见底（这次没比上次多要到东西，
                # 再要也不会更多）；或者已经把整个语料都问了一遍。
                break
            request_k += deficit
        hit_ids = filtered[:k]
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # score 只作为参考值返回：对 naive 是排序依据本身；对带重排的配置，
        # 它是"向量层"相似度、不构成最终排序依据，调用方勿当排名字段解释。
        score_map = {did: s for did, s in
                     self.store.search(qvec, top_k=max(k * 5, 50))}
        units = []
        for did in hit_ids[:k]:
            doc = self.docs.get(did)
            if not doc:
                continue
            units.append({
                "doc_id": did,
                "type": doc["meta"].get("type", "unknown"),
                "text": doc["text"],
                "score": score_map.get(did),
            })
        return units, elapsed_ms


class Handler(BaseHTTPRequestHandler):
    core = None          # 由 main() 注入
    server_version = "rag-memory-service/1.0"

    # 走访问日志到 stdout（nohup 落盘），便于演示时指认"agent 查了什么"。
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + (fmt % args), flush=True)

    def _reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, {
                "ok": True,
                "index_size": self.core.index_size,
                "config": self.core.config,
                "backend": self.core.backend_name,
            })
        if self.path == "/forgotten":
            return self._reply(200, {
                "forgotten": self.core.forget_ledger.list_forgotten(),
                "count": len(self.core.forget_ledger.forgotten_keys),
            })
        return self._reply(404, {"error": "unknown path", "path": self.path})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            raise ValueError("bad Content-Length")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self):
        try:
            req = self._read_json_body()
        except (ValueError, UnicodeDecodeError):
            return self._reply(400, {"error": "invalid JSON body"})

        if self.path == "/retrieve":
            return self._handle_retrieve(req)
        if self.path == "/forget":
            return self._handle_forget(req)
        if self.path == "/unforget":
            return self._handle_unforget(req)
        return self._reply(404, {"error": "unknown path", "path": self.path})

    def _handle_retrieve(self, req):
        query = req.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._reply(400, {"error": "query must be a non-empty string"})
        k = req.get("k", 5)
        if not isinstance(k, int) or k < 1 or k > 50:
            return self._reply(400, {"error": "k must be int in [1,50]"})

        try:
            units, elapsed_ms = self.core.retrieve(query, k)
        except Exception as e:                      # noqa: BLE001
            # 让演示/实验方拿到可读错误，而不是连接被掐断
            return self._reply(500, {"error": f"{type(e).__name__}: {e}",
                                     "config": self.core.config})

        print(f"[{time.strftime('%H:%M:%S')}] retrieve k={k} "
              f"{elapsed_ms:.1f}ms hits={[u['doc_id'] for u in units]} "
              f"q={query[:40]!r}", flush=True)
        self._reply(200, {
            "units": units,
            "config": self.core.config,
            "elapsed_ms": round(elapsed_ms, 2),
        })

    def _handle_forget(self, req):
        """两步确认（《精准遗忘-实施方案.md》§2.2）：
        不带 confirm_doc_id → 第一步·定位，只查不写；
        带 confirm_doc_id   → 第二步·确认，doc_id 须落在按同一 instruction
        重新算出的候选集合内才写账本——无状态设计，不需要服务端记住"第一步
        返回过什么"，检索本身是确定性的，重算一遍即可核验。
        """
        instruction = req.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            return self._reply(400, {"error": "instruction must be a non-empty string"})
        k = req.get("k", 5)
        # 上限比 /retrieve 松（50→1000）：/retrieve 的 k 是"给人看几条"，
        # 这里的 k 还兼职"确认步的候选窗口"——评测跑§3.4(b)时要能对
        # gold_doc_id "强制确认"，与定位准不准是两件事分开测（见该函数
        # docstring），候选窗口就不能被 50 这个"展示用"上限卡住语料稍大
        # 一点（如本项目 86 条）就漏掉 gold 的情况。
        if not isinstance(k, int) or k < 1 or k > 1000:
            return self._reply(400, {"error": "k must be int in [1,1000]"})

        try:
            units, _ = self.core.retrieve(instruction, k)
        except Exception as e:                       # noqa: BLE001
            return self._reply(500, {"error": f"{type(e).__name__}: {e}"})
        candidate_ids = {u["doc_id"] for u in units}

        confirm_doc_id = req.get("confirm_doc_id")
        if confirm_doc_id is None:
            candidates = []
            for u in units:
                doc = self.core.docs.get(u["doc_id"])
                candidates.append({
                    "doc_id": u["doc_id"],
                    "stable_key": compute_stable_key((doc or {}).get("meta")),
                    "type": u["type"],
                    "text": u["text"][:200],
                })
            return self._reply(200, {
                "candidates": candidates,
                "note": "未执行遗忘，请用 confirm_doc_id 确认",
            })

        if not isinstance(confirm_doc_id, int):
            return self._reply(400, {"error": "confirm_doc_id must be an int"})
        if confirm_doc_id not in candidate_ids:
            return self._reply(400, {
                "error": "confirm_doc_id 不在本次 instruction 的候选内，拒绝写账本",
                "candidate_doc_ids": sorted(candidate_ids),
            })

        doc = self.core.docs.get(confirm_doc_id)
        stable_key = compute_stable_key((doc or {}).get("meta"))
        if not doc or stable_key is None:
            return self._reply(400, {
                "error": "该单元类型算不出 stable_key，暂不支持精准遗忘",
                "doc_id": confirm_doc_id,
            })

        self.core.forget_ledger.forget(stable_key, confirm_doc_id, instruction, doc["text"])
        print(f"[{time.strftime('%H:%M:%S')}] forget doc_id={confirm_doc_id} "
              f"stable_key={stable_key} instruction={instruction[:40]!r}", flush=True)
        self._reply(200, {
            "forgotten": {"doc_id": confirm_doc_id, "stable_key": stable_key},
            "ledger_size": len(self.core.forget_ledger.records),
        })

    def _handle_unforget(self, req):
        stable_key = req.get("stable_key")
        if not isinstance(stable_key, str) or not stable_key.strip():
            return self._reply(400, {"error": "stable_key must be a non-empty string"})
        self.core.forget_ledger.unforget(stable_key, reason=req.get("reason"))
        print(f"[{time.strftime('%H:%M:%S')}] unforget stable_key={stable_key}", flush=True)
        self._reply(200, {
            "unforgotten": stable_key,
            "ledger_size": len(self.core.forget_ledger.records),
        })


def main():
    ap = argparse.ArgumentParser(description="RAG 记忆服务（只读、127.0.0.1 本地监听）")
    ap.add_argument("--config", default="all",
                    help="检索配置，必须与报告声明的系统配置一致（报告默认 all）")
    ap.add_argument("--backend", choices=["kylin", "numpy"], default="kylin",
                    help="向量后端（报告声明部署默认 kylin 官方引擎；numpy 为一键切换档）")
    ap.add_argument("--collection", default="rag_knowledge")
    ap.add_argument("--docs", default=None,
                    help="knowledge_docs jsonl 路径（默认知识目录下 knowledge_docs.jsonl；"
                         "相对路径按知识目录解析）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--forget-ledger", default=None,
                    help="遗忘账本路径（默认与本脚本同目录 forget_ledger.jsonl）；"
                         "评测跑独立账本时指到别处，不污染生产账本")
    args = ap.parse_args()

    # 🔴 硬约束①：meta_oracle 用标准答案自身的 knowledge_type 做过滤，
    # 是标签泄漏、是上界不是能力。启动即拒绝，不给运行态开关。
    if args.config == "meta_oracle":
        raise SystemExit("拒绝启动：config=meta_oracle 属于标签泄漏（上界实验配置），"
                         "记忆服务只接受真实可查的配置。")
    if args.config not in ALLOWED_CONFIGS:
        raise SystemExit(f"未知 config={args.config!r}，可选：{ALLOWED_CONFIGS}")

    core = RetrievalCore(config=args.config, backend=args.backend,
                         collection=args.collection, docs_path=args.docs,
                         forget_ledger_path=args.forget_ledger)
    Handler.core = core

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"记忆服务已启动  http://127.0.0.1:{args.port}  "
          f"config={args.config} backend={args.backend} index_size={core.index_size}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
