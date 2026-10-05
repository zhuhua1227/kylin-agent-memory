#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_krow.py —— 模型消融 4×2 的 K 行（云端对照）

云端模型 = Gemini 3.1 Pro（maxthinking），经 catie 中转 OpenAI 兼容接口。
其余一切与本地档三行逐字相同：同 tasks.jsonl、同 memory_service:8077 检索、
同 system prompt、同注入格式、同 temperature 0。

⚠️ 与本地三行的差异（必须写进报告）：
  - thinking 模式：本地三行「关」，K 行「maxthinking（开，最大预算）」——用户指定。
  - 评分只看最终 answer（content），不看 reasoning_content（模型草稿，非答案）。
  - wall_ms 是网络往返，**不得与本地 CPU wall_ms 并列**。
  - catie 硬限流 10 次/分钟 → 每次请求间隔 ≥ 8s + 429 退避重试。
"""
import argparse
import json
import os
import time
import urllib.request
import urllib.error

BASE = "https://catiecli.sukaka.top/v1"
KEY = os.environ.get("CATIE_KEY", "")
MODEL = "gcli-gemini-3.1-pro-preview-maxthinking"
MODEL_LABEL = "gemini-3.1-pro-maxthinking"
MEMORY_URL = "http://127.0.0.1:8077"
K = 5
MIN_GAP_S = 13.0         # 限流：catie 10/分钟；共享池高峰更狠，压到 ~4.5/分钟
MAX_TOKENS = 2048        # maxthinking 会占 token，给足；answer 本身仍 1~3 句
RETRY_429 = 6            # 每次 429 退避 min(90*n, 600)s；6 次 ≈ 30 分钟
STALL_MAX_S = 4 * 3600   # catie 长时间不恢复时，单格最多耗这么久就记 error 放弃

SYSTEM_PROMPT = (
    "你是运行在银河麒麟操作系统上的命令行助手。用户会给你描述当前遇到的一个报错或状况。"
    "请直接给出你接下来会执行的具体命令或做法，控制在一到三句话以内，写出可直接运行的命令。"
    "不要复述背景，不要解释原理，不要执行任何操作。"
)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def http_json(url, payload=None, headers=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h,
                                method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def retrieve(q):
    d = http_json(f"{MEMORY_URL}/retrieve", {"query": q, "k": K}, timeout=60)
    return d.get("units", []), d.get("config"), d.get("elapsed_ms")


def format_block(units):
    lines = [f"=== 相关记忆（来自本机知识库，共 {len(units)} 条） ==="]
    for i, u in enumerate(units, 1):
        txt = " ".join(str(u.get("text", "")).split())[:400]
        lines.append(f"[{i}] ({u.get('type','?')}) {txt}")
    lines.append("=== 记忆结束（以上是历史经验，供参考；与当前任务冲突时以当前任务为准） ===")
    return "\n".join(lines)


_last = [0.0]


def cloud_generate(user_content):
    # 限流间隔
    gap = MIN_GAP_S - (time.time() - _last[0])
    if gap > 0:
        time.sleep(gap)
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
    }
    hdr = {"Authorization": f"Bearer {KEY}"}
    started = time.time()
    attempt = 0
    while True:
        attempt += 1
        t0 = time.perf_counter()
        try:
            d = http_json(f"{BASE}/chat/completions", payload, hdr, timeout=240)
            _last[0] = time.time()
            msg = d["choices"][0]["message"]
            return {
                "answer_text": msg.get("content") or "",
                "reasoning": msg.get("reasoning_content"),
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                "gen_tokens": d.get("usage", {}).get("completion_tokens"),
                "prompt_tokens": d.get("usage", {}).get("prompt_tokens"),
                "finish_reason": d["choices"][0].get("finish_reason"),
                "attempts": attempt,
            }
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            _last[0] = time.time()
            if e.code == 429 and time.time() - started < STALL_MAX_S:
                wait = min(90 * attempt, 600)
                log(f"  429 第{attempt}次，退避 {wait}s（{body}）")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:  # 网络抖动等
            _last[0] = time.time()
            if time.time() - started < STALL_MAX_S:
                log(f"  {type(e).__name__} 第{attempt}次，退避 60s（{str(e)[:120]}）")
                time.sleep(60)
                continue
            raise


def main():
    assert KEY, "设 CATIE_KEY 环境变量"
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="tasks.jsonl")
    ap.add_argument("--out", default="runs_krow.jsonl")
    args = ap.parse_args()
    tasks = [json.loads(l) for l in open(args.tasks, encoding="utf-8") if l.strip()]
    out = os.path.abspath(args.out)

    done = set()
    if os.path.exists(out):
        for l in open(out, encoding="utf-8"):
            if not l.strip():
                continue
            r = json.loads(l)
            if not r.get("error") and r.get("answer_text") is not None:
                done.add((r["task_id"], r["memory"]))
        if done:
            log(f"续跑：已完成 {len(done)} 格")

    h = http_json(f"{MEMORY_URL}/health", timeout=10)
    log(f"memory service: {h}")
    assert h.get("config") != "meta_oracle"

    for t in tasks:
        for mem in (False, True):
            if (t["task_id"], mem) in done:
                continue
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task_id": t["task_id"], "model": MODEL_LABEL, "size": "k",
                "memory": mem, "retrieval_config": None, "injected_doc_ids": [],
                "inject_chars": 0, "answer_text": None, "wall_ms": None,
                "gen_tokens": None, "prompt_tokens": None, "finish_reason": None,
                "error": None, "thinking": "maxthinking",
            }
            try:
                if mem:
                    units, cfg, rms = retrieve(t["prompt"])
                    rec["retrieval_config"] = cfg
                    rec["injected_doc_ids"] = [u["doc_id"] for u in units]
                    rec["retrieval_ms"] = round(rms, 1) if rms else None
                    block = format_block(units)
                    content = block + "\n\n" + t["prompt"]
                    rec["inject_chars"] = len(block)
                else:
                    content = t["prompt"]
                g = cloud_generate(content)
                rec.update(answer_text=g["answer_text"], wall_ms=g["wall_ms"],
                           gen_tokens=g["gen_tokens"], prompt_tokens=g["prompt_tokens"],
                           finish_reason=g["finish_reason"], attempts=g["attempts"])
                if g["reasoning"]:
                    rec["reasoning_content"] = g["reasoning"]
                log(f"K {t['task_id']} m{int(mem)} wall={g['wall_ms']}ms "
                    f"tok={g['gen_tokens']} ans={g['answer_text'][:90]!r}")
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
                log(f"K {t['task_id']} m{int(mem)} ERROR {rec['error']}")
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    log(f"完成 -> {out}")


if __name__ == "__main__":
    main()
