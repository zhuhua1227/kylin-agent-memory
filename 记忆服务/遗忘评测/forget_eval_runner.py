#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forget_eval_runner.py —— 精准遗忘评测流程（《精准遗忘-实施方案.md》§3.4）

前提：记忆服务已用**独立评测账本**启动（不污染生产账本，§2.1）：

    python3 记忆服务/memory_service.py --config all \\
        --forget-ledger 记忆服务/遗忘评测/forget_ledger_eval.jsonl

跑法（麒麟机上，需要 embedding 服务；前台跑，不要 bash/setsid/nohup&——
netctl 按进程祖先链拦联网，只认前台 `ssh host "python3 x.py"`，见
《RAG三个对照实验.md》M-2xx 踩过的坑）：

    ssh device_c "cd ~/RAG/记忆服务/遗忘评测 && python3 forget_eval_runner.py"

流程（§3.4）：
  ① 清空评测账本——若上次异常中断留了残余，自愈 unforget 干净，不依赖
     操作者手动删文件。
  ② 跑 227 题基线 → hits_before[q]（top-k 集合），只跑一次，所有任务共用。
  ③ 对每条 F-xx：
     a. POST /forget {instruction, k}                 → 第一步·定位，记 top-1
     b. POST /forget {instruction, confirm_doc_id}     → 第二步·强制按 gold 确认
        （评测的是"忘了以后检索表现"，不是"定位错了以后怎么办"，两个能力
        分开测——定位错了走确认流程本来就会被人拦下，见 §3.4 备注）
     c. 重跑 227 题 → hits_after[q]
     d. 落盘：每题一行到 runs_forget.jsonl（含 before/after 集合）
     e. POST /unforget，复位，进入下一条任务
  ④ 定位结果单独落 locate_log.jsonl（每任务一行）——两份文件都是纯落盘
     数据，score_forget.py 只读它们、不重新跑检索，是 L1（评委任何机器
     可复现）；本脚本本身因为要调 embedding 服务，是 L2。
"""
import argparse
import json
import os
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))                  # 记忆服务/遗忘评测
RAG = os.path.dirname(os.path.dirname(BASE))                        # 仓库根
EVAL_SET_PATH = os.path.join(RAG, "评测集", "eval_set.jsonl")
TASKS_PATH = os.path.join(BASE, "forget_tasks.jsonl")
TOP_K = 10                                                           # §3.3：top-10 集合比较


# 180s：确认步用大 k（覆盖整个语料）触发 MMR 重排,该函数是 O(corpus^3) 级
# 的贪心多样性计算（`search_knowledge._mmr_rerank`,冻结代码不能碰）,实测
# 86 条语料下单次确认请求约 49s——30s 默认超时会导致*客户端*提前放弃，
# 但*服务端*仍在算、算完照样写账本，制造"账本里出现了客户端以为失败的
# 那条遗忘"的假象（本轮踩过：doc_id=0 就是这么被误遗忘的，reset_ledger()
# 能清，但不如从根上给够超时）。
def _post(port, path, body, timeout=180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(port, path, timeout=30):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def run_full_eval_set(port, eval_set, k):
    """跑一遍全部 227 题，返回 {question_id: [doc_id,...]}（top-k 有序命中列表）。"""
    hits = {}
    for i, q in enumerate(eval_set, 1):
        res = _post(port, "/retrieve", {"query": q["query"], "k": k})
        hits[q["question_id"]] = [u["doc_id"] for u in res["units"]]
        if i % 50 == 0 or i == len(eval_set):
            print(f"    {i}/{len(eval_set)}", flush=True)
    return hits


def reset_ledger(port):
    forgotten = _get(port, "/forgotten")["forgotten"]
    for rec in forgotten:
        _post(port, "/unforget", {"stable_key": rec["stable_key"], "reason": "reset before eval"})
    print(f"  reset: 清掉 {len(forgotten)} 条残余记录" if forgotten else "  reset: 账本本就干净",
          flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--k", type=int, default=TOP_K)
    ap.add_argument("--locate-k", type=int, default=5,
                    help="第一步·定位时的候选数（算定位准确率只看 top-1，不需要很大）")
    ap.add_argument("--confirm-k", type=int, default=200,
                    help="第二步·确认时的候选窗口（须覆盖整个语料，"
                         "否则 gold 排名靠后时会被误判成'候选外'而拒绝写账本——"
                         "评测的是遗忘本身，不是定位，见 §3.4(b)）")
    ap.add_argument("--out", default=os.path.join(BASE, "runs_forget.jsonl"))
    ap.add_argument("--locate-out", default=os.path.join(BASE, "locate_log.jsonl"))
    args = ap.parse_args()

    health = _get(args.port, "/health")
    print(f"health: {health}", flush=True)
    assert health["ok"], "记忆服务未就绪"

    eval_set = load_jsonl(EVAL_SET_PATH)
    tasks = load_jsonl(TASKS_PATH)
    print(f"eval_set: {len(eval_set)} 题；forget_tasks: {len(tasks)} 条", flush=True)

    print("① 清空评测账本", flush=True)
    reset_ledger(args.port)

    print("② 跑 227 题基线", flush=True)
    hits_before = run_full_eval_set(args.port, eval_set, k=args.k)

    # newline=""：仓库 eol=lf，Windows 上落盘要避免文本模式悄悄换成 CRLF；
    # device_c 是 Linux，这里统一写法两边都对。
    runs_f = open(args.out, "w", encoding="utf-8", newline="")
    locate_f = open(args.locate_out, "w", encoding="utf-8", newline="")

    n_located = 0
    for ti, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        instruction = task["instruction"]
        gold_doc_id = task["gold_doc_id"]
        gold_stable_key = task["gold_stable_key"]
        print(f"③ [{ti}/{len(tasks)}] {task_id}: {instruction[:30]}...", flush=True)

        # a. 第一步·定位
        locate = _post(args.port, "/forget", {"instruction": instruction, "k": args.locate_k})
        candidates = locate.get("candidates", [])
        top1_doc_id = candidates[0]["doc_id"] if candidates else None
        located_correctly = (top1_doc_id == gold_doc_id)
        n_located += int(located_correctly)
        locate_f.write(json.dumps({
            "task_id": task_id, "instruction": instruction, "gold_doc_id": gold_doc_id,
            "top1_doc_id": top1_doc_id, "located_correctly": located_correctly,
            "candidate_doc_ids": [c["doc_id"] for c in candidates],
        }, ensure_ascii=False) + "\n")
        locate_f.flush()

        # b. 第二步·强制按 gold 确认（不管 a 是否命中——见文件头说明）。
        #    k 用 --confirm-k（大窗口），不是 --locate-k：确认步要覆盖到
        #    gold_doc_id 本身，跟"定位准不准"是两件事分开测。
        confirm = _post(args.port, "/forget",
                        {"instruction": instruction, "confirm_doc_id": gold_doc_id,
                         "k": args.confirm_k})
        forgotten = confirm.get("forgotten") or {}
        assert forgotten.get("stable_key") == gold_stable_key, (
            f"{task_id}: 确认后账本里的 stable_key 与预期不符：{forgotten} vs {gold_stable_key}")

        # c. 重跑 227 题
        hits_after = run_full_eval_set(args.port, eval_set, k=args.k)

        # d. 逐题落盘（含 before/after 集合，供 score_forget.py 纯 Python 重算指标）
        for q in eval_set:
            qid = q["question_id"]
            runs_f.write(json.dumps({
                "task_id": task_id, "question_id": qid, "gold_doc_id": q["doc_id"],
                "target_doc_id": gold_doc_id,
                "hits_before": hits_before[qid], "hits_after": hits_after[qid],
            }, ensure_ascii=False) + "\n")
        runs_f.flush()

        # e. 复位
        _post(args.port, "/unforget", {"stable_key": gold_stable_key, "reason": "eval task done"})

    runs_f.close()
    locate_f.close()
    print(f"\n④ 完成：runs_forget.jsonl → {args.out}", flush=True)
    print(f"          locate_log.jsonl → {args.locate_out}", flush=True)
    print(f"   定位命中 {n_located}/{len(tasks)}（粗看，正式数字由 score_forget.py 算）", flush=True)


if __name__ == "__main__":
    main()
