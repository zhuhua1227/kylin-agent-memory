#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_diff.py —— 批次一验收：两组 payload 逐字比对 + runs.jsonl 摘要"""
import glob
import json
import os

os.chdir(os.path.expanduser("~/RAG/memory_service/payload_log"))
files = sorted(glob.glob("*.json"))
off = json.load(open(files[0]))          # 最早 = OFF 组
on = json.load(open(files[-1]))          # 最晚 = ON 组（注入成功那次）

so = off["messages"][0]["content"]
sn = on["messages"][0]["content"]
print("① system prompt 逐字相同:", so == sn, f"(长度 {len(so)} vs {len(sn)})")
print("② off 组 roles:", [m["role"] for m in off["messages"]])
print("   on 组 roles:", [m["role"] for m in on["messages"]])
print("③ 用户任务两组成一字相同:",
      off["messages"][1]["content"] == on["messages"][2]["content"])
print("④ on 组注入块（插在任务之前的消息）:")
print("-" * 60)
print(on["messages"][1]["content"][:800])
print("-" * 60)

print("\nruns.jsonl 摘要:")
for line in open(os.path.expanduser("~/RAG/memory_service/runs.jsonl")):
    d = json.loads(line)
    print(f"  {d['ts']} memory={d['memory']} config={d['retrieval_config']} "
          f"doc_ids={d['injected_doc_ids']} err={d.get('error', '-')}")
