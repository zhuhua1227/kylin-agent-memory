#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_service.py —— 记忆服务冒烟验证（设计方案批次一 §九 验收）

验收点：
  1. GET /health 正常回包，index_size > 0
  2. 每条手工查询：返回结构合法、带原文（text 非空）、config 回声一致
  3. 单次 /retrieve 延迟 < 500ms（硬约束④）
  4. 首笔请求预热后不再出现 errCode:24 超时
用法：python3 smoke_service.py [--port 8077] [--config all]
"""
import argparse
import json
import sys
import urllib.request

QUERIES = [
    "把系统日志里大于100MB的旧日志文件归档到备份目录",
    "为项目一键生成提交信息并推送到远程仓库的工作流程",
    "定期清理临时目录释放磁盘空间的自动化办法",
    "查看当前容器运行状态并汇总资源占用",
    "生成一份今天服务器巡检报告的模板",
]


def call(port, query, k=5):
    body = json.dumps({"query": query, "k": k}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/retrieve", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--config", default="all")
    args = ap.parse_args()

    with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=10) as r:
        h = json.loads(r.read().decode("utf-8"))
    print(f"[PASS] /health: {h}")
    assert h["ok"] and h["index_size"] > 0 and h["config"] == args.config

    max_ms = 0.0
    for q in QUERIES:
        res = call(args.port, q)
        assert res["config"] == args.config, f"config 回声不一致: {res['config']}"
        units = res["units"]
        assert units, f"查询无命中: {q}"
        for u in units:
            assert u["text"].strip(), f"空原文: {u['doc_id']}"
        ms = res["elapsed_ms"]
        max_ms = max(max_ms, ms)
        flag = "PASS" if ms < 500 else "FAIL>500ms"
        top = units[0]
        print(f"[{flag}] {ms:7.1f}ms {len(units)}条 top={top['doc_id']}({top['type']}) "
              f"score={top['score']:.3f} | {q[:20]}... → {top['text'][:50]}...")
        if ms >= 500:
            sys.exit(f"延迟超预算: {ms:.1f}ms >= 500ms")

    print(f"\n==> 冒烟通过：{len(QUERIES)} 条查询全部 < 500ms（最大 {max_ms:.1f}ms），"
          f"结构与原文合法，config 回声一致")


if __name__ == "__main__":
    main()
