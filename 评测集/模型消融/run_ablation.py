#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_ablation.py —— 模型消融 4×2 本地档运行器（回答型 / 零 LLM 裁判）

依据《模型消融4x2-实验方案.md》：
  - 每题 × {无记忆, 有记忆} × {2B, 4B, 9B}，共 13×2×3 = 78 次生成
  - 无记忆组 = 完全不调 /retrieve；有记忆组 = 调 :8077/retrieve 注入 top-k
  - system prompt / 任务文本 / 生成参数 六格逐字相同，唯一变量：注入块在不在
  - 温度 0、seed 固定、每题独立会话、每格每题只跑一次
  - wall_ms 只用于三档之间相对比较，不与云端 API 延迟并列

自带 llama-server 生命周期：一次只加载一个 GGUF（机器 4 vCPU / 10 GB）。
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

HOME = os.path.expanduser("~")
LLAMA_DIR = os.path.join(HOME, "llama-cpp", "llama-b10850")
LLAMA_BIN = os.path.join(LLAMA_DIR, "llama-server")
MODELS = {
    "2b": (os.path.join(HOME, "models/qwen35/Qwen3.5-2B-Q4_K_M.gguf"), "qwen3.5-2b-q4km"),
    "4b": (os.path.join(HOME, "models/qwen35/Qwen3.5-4B-Q4_K_M.gguf"), "qwen3.5-4b-q4km"),
    "9b": (os.path.join(HOME, "models/qwen35/Qwen3.5-9B-Q4_K_M.gguf"), "qwen3.5-9b-q4km"),
}
LLAMA_PORT = 8080
MEMORY_URL = "http://127.0.0.1:8077"
THREADS = 4
CTX = 4096
MAX_TOKENS = 256
SEED = 42
K = 5

SYSTEM_PROMPT = (
    "你是运行在银河麒麟操作系统上的命令行助手。用户会给你描述当前遇到的一个报错或状况。"
    "请直接给出你接下来会执行的具体命令或做法，控制在一到三句话以内，写出可直接运行的命令。"
    "不要复述背景，不要解释原理，不要执行任何操作。"
)

SMOKE_TASKS = {"T-01", "T-02", "T-03"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_json(url, payload=None, timeout=600):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_health(url, max_wait):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            d = http_json(url + "/health", timeout=5)
            if d.get("status") == "ok" or d.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def format_block(units):
    lines = [f"=== 相关记忆（来自本机知识库，共 {len(units)} 条） ==="]
    for i, u in enumerate(units, 1):
        txt = " ".join(str(u.get("text", "")).split())[:400]
        lines.append(f"[{i}] ({u.get('type','?')}) {txt}")
    lines.append("=== 记忆结束（以上是历史经验，供参考；与当前任务冲突时以当前任务为准） ===")
    return "\n".join(lines)


def retrieve(query):
    d = http_json(f"{MEMORY_URL}/retrieve", {"query": query, "k": K}, timeout=60)
    return d.get("units", []), d.get("config"), d.get("elapsed_ms")


def generate(user_content):
    payload = {
        "model": "q",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "seed": SEED,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    t0 = time.perf_counter()
    d = http_json(f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions", payload, timeout=900)
    wall_ms = (time.perf_counter() - t0) * 1000
    msg = d["choices"][0]["message"]
    return {
        "answer_text": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content"),
        "wall_ms": round(wall_ms, 1),
        "gen_tokens": d.get("usage", {}).get("completion_tokens"),
        "prompt_tokens": d.get("usage", {}).get("prompt_tokens"),
        "finish_reason": d["choices"][0].get("finish_reason"),
    }


# 每档 llama-server 参数：9B 大、机器 10GB，必须锁单 slot + 缩上下文，
# 否则 auto 会开 2 个 slot、KV 缓存翻倍，跑十几题后 OOM 静默死掉（首轮实测）。
LLAMA_ARGS = {
    "2b": ["-c", "4096"],
    "4b": ["-c", "4096"],
    "9b": ["-c", "3072", "-np", "1", "--no-context-shift"],
}


def start_llama(gguf, size):
    env = dict(os.environ, LD_LIBRARY_PATH=LLAMA_DIR)
    # 磁盘慢（~40MB/s）：先把权重顺序读进 page cache，llama-server 再 mmap 就快
    t0 = time.time()
    subprocess.run(["cat", gguf], stdout=subprocess.DEVNULL, check=False)
    log(f"  预热 {os.path.basename(gguf)} 进缓存，{time.time()-t0:.0f}s")
    logf = open(os.path.join(os.path.dirname(RUNS_PATH), f"llama_{time.strftime('%H%M%S')}.log"), "w")
    p = subprocess.Popen(
        [LLAMA_BIN, "-m", gguf, "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
         "-t", str(THREADS), "--reasoning", "off", *LLAMA_ARGS[size]],
        stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True,
    )
    return p, logf


def stop_llama(p, logf):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        p.wait(timeout=20)
    except Exception:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
    try:
        logf.close()
    except Exception:
        pass


RUNS_PATH = ""


def main():
    global RUNS_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="2b,4b,9b")
    ap.add_argument("--smoke", action="store_true", help="只跑 T-01/02/03（三档各跑）")
    ap.add_argument("--time-9b-only", action="store_true",
                    help="只用 9B 跑一题（含注入）测单题耗时，不写 runs")
    ap.add_argument("--tasks", default="tasks.jsonl")
    ap.add_argument("--out", default="runs.jsonl")
    args = ap.parse_args()

    RUNS_PATH = os.path.abspath(args.out)
    tasks = [json.loads(l) for l in open(args.tasks, encoding="utf-8") if l.strip()]
    if args.smoke:
        tasks = [t for t in tasks if t["task_id"] in SMOKE_TASKS]

    # 断点续跑：已成功落库的 (size, task_id, memory) 跳过（error 记录不算成功，会重跑）
    done = set()
    if os.path.exists(RUNS_PATH):
        for l in open(RUNS_PATH, encoding="utf-8"):
            l = l.strip()
            if not l:
                continue
            r = json.loads(l)
            if not r.get("error") and r.get("answer_text") is not None:
                done.add((r["size"], r["task_id"], r["memory"]))
        if done:
            log(f"续跑：已完成 {len(done)} 格，将跳过")

    # memory service 必须在
    h = http_json(f"{MEMORY_URL}/health", timeout=10)
    log(f"memory service: {h}")
    assert h.get("config") != "meta_oracle"

    sizes = args.sizes.split(",")

    if args.time_9b_only:
        gguf, label = MODELS["9b"]
        p, logf = start_llama(gguf, "9b")
        try:
            if not wait_health(f"http://127.0.0.1:{LLAMA_PORT}", 300):
                log("9B 加载超时"); return
            t = tasks[0]
            units, cfg, rms = retrieve(t["prompt"])
            content = format_block(units) + "\n\n" + t["prompt"]
            r = generate(content)
            log(f"9B 单题(含注入 {len(content)} 字符, prompt_tokens={r['prompt_tokens']}) "
                f"wall={r['wall_ms']}ms gen_tokens={r['gen_tokens']}")
            log(f"  answer: {r['answer_text'][:200]!r}")
        finally:
            stop_llama(p, logf)
        return

    # 9B 在 10GB 机器上跑十几题后 llama-server 会 OOM 静默死；每 N 格重启一次兜住。
    # v2 首轮 restart_every=6 仍在第 17 格崩，收紧到 4。
    RESTART_EVERY = {"9b": 4}

    def run_one_cell(t, mem):
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task_id": t["task_id"], "model": label, "size": size,
            "memory": mem, "retrieval_config": None,
            "injected_doc_ids": [], "inject_chars": 0,
            "answer_text": None, "wall_ms": None, "gen_tokens": None,
            "prompt_tokens": None, "finish_reason": None, "error": None,
        }
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
        g = generate(content)
        rec.update(answer_text=g["answer_text"], wall_ms=g["wall_ms"],
                   gen_tokens=g["gen_tokens"], prompt_tokens=g["prompt_tokens"],
                   finish_reason=g["finish_reason"])
        if g["reasoning"]:
            rec["reasoning_leak"] = g["reasoning"][:200]
        return rec

    for size in sizes:
        gguf, label = MODELS[size]
        pending = [(t, m) for t in tasks for m in (False, True)
                   if (size, t["task_id"], m) not in done]
        if not pending:
            log(f"==== {size} : 全部已完成，跳过 ===="); continue
        log(f"==== {size} ({label}) : 待跑 {len(pending)} 格 ====")
        restart_every = RESTART_EVERY.get(size, 999)
        wait = 900 if size == "9b" else 200
        p = logf = None
        since_restart = 10 ** 9  # 强制首次启动
        try:
            for t, mem in pending:
                # 需要（重）启 llama-server：首次 / 达到重启阈值 / 上次探测已死
                need = p is None or since_restart >= restart_every
                if not need:
                    try:
                        need = not http_json(f"http://127.0.0.1:{LLAMA_PORT}/health",
                                             timeout=5).get("status") == "ok"
                    except Exception:
                        need = True
                if need:
                    if p is not None:
                        stop_llama(p, logf)
                        time.sleep(3)
                    p, logf = start_llama(gguf, size)
                    if not wait_health(f"http://127.0.0.1:{LLAMA_PORT}", wait):
                        log(f"{size} 加载超时，跳过本档剩余"); break
                    log(f"{size} llama-server ready")
                    since_restart = 0

                rec = None
                for attempt in (1, 2):
                    try:
                        rec = run_one_cell(t, mem)
                        break
                    except Exception as e:
                        msg = f"{type(e).__name__}: {e}"
                        log(f"{size} {t['task_id']} m{int(mem)} 尝试{attempt} 失败 {msg}")
                        if attempt == 1:
                            # 多半是 llama-server 挂了：重启后重试一次
                            stop_llama(p, logf); time.sleep(3)
                            p, logf = start_llama(gguf, size)
                            if not wait_health(f"http://127.0.0.1:{LLAMA_PORT}", wait):
                                break
                            since_restart = 0
                        else:
                            rec = {
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "task_id": t["task_id"], "model": label, "size": size,
                                "memory": mem, "answer_text": None, "wall_ms": None,
                                "injected_doc_ids": [], "error": msg,
                            }
                if rec is None:
                    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "task_id": t["task_id"], "model": label, "size": size,
                           "memory": mem, "answer_text": None, "error": "llama 无法就绪"}
                if not rec.get("error"):
                    log(f"{size} {t['task_id']} m{int(mem)} wall={rec.get('wall_ms')}ms "
                        f"tok={rec.get('gen_tokens')} ans={(rec.get('answer_text') or '')[:80]!r}")
                with open(RUNS_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                since_restart += 1
        finally:
            if p is not None:
                stop_llama(p, logf)
            log(f"==== {size} : llama-server 已停 ====")
            time.sleep(2)

    log(f"完成，runs 落在 {RUNS_PATH}")


if __name__ == "__main__":
    main()
