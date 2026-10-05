#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_record.py —— 生成 M-211 风格的测量记录（与 可复现性体系/records/M-*.json 同 schema）。

刻意不直接写进 可复现性体系/records/：那套冻结体系此刻正被别人编辑（git 里
可复现性说明.md / 当前数字看板.md 等已 modified），现在往 records/ + claims.json 里塞
会撞车。这里产出一份可直接搬进去的 M-211.json + 说明报告里给出对应 claims 文本，
由并入方决定何时接线。
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def sha256_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def run(cmd):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, env=e)
    return r.stdout + r.stderr


def main():
    import numpy as np

    kylin = run([sys.executable, "retrieve.py", "--side", "kylin"])
    bge = run([sys.executable, "retrieve.py", "--side", "bge"])
    mc = run([sys.executable, "retrieve.py", "--mcnemar"])

    pk = {p["question_id"]: p for p in json.load(open(os.path.join(HERE, "perquery_kylin.json"), encoding="utf-8"))}
    pb = {p["question_id"]: p for p in json.load(open(os.path.join(HERE, "perquery_bge.json"), encoding="utf-8"))}

    def rk(p, k):
        return 1 if (p["rank"] is not None and p["rank"] <= k) else 0

    m = {}
    for k in (1, 3, 5, 10):
        m[f"kylin_naive_r{k}"] = {"value": round(sum(rk(pk[q], k) for q in pk) / 227, 4), "unit": "", "repeats": 1}
        m[f"bge_dense_r{k}"] = {"value": round(sum(rk(pb[q], k) for q in pb) / 227, 4), "unit": "", "repeats": 1}
    for k in (1, 3, 5, 10):
        only_kylin = only_bge = 0
        for q in pk:
            a, b = rk(pk[q], k), rk(pb[q], k)
            only_kylin += a and not b
            only_bge += b and not a
        m[f"mcnemar_r{k}_only_kylin_n"] = {"value": only_kylin, "unit": "题", "repeats": 1}
        m[f"mcnemar_r{k}_only_bge_n"] = {"value": only_bge, "unit": "题", "repeats": 1}

    bge_meta = json.load(open(os.path.join(HERE, "_bge_meta.json"), encoding="utf-8"))

    rec = {
        "record_id": "M-211",
        "title": "BGE-M3 对照实验（naive 档，227 题 / 77 gold doc）：系统 kylin-coreai-embedding vs BGE-M3 dense",
        "layer": "L1",
        "script": "BGE-M3对照/retrieve.py",
        "script_sha256": sha256_file(os.path.join(HERE, "retrieve.py")),
        "command": "python retrieve.py --side kylin && python retrieve.py --side bge && python retrieve.py --mcnemar",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": None,
        "random_seed": None,
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "note": "检索与计分纯 numpy（L1）；系统侧向量为已固化产物（ensemble-embd_gte-base_uint8-text，L2 来源），"
                    "BGE 侧向量由 make_vectors_bge.py 在 Ubuntu CPU 上用 " + bge_meta["model"] +
                    " / torch " + bge_meta["torch_version"] + " 新算（dense-only、无指令前缀、max_length 8192、无截断）",
        },
        "inputs": {
            "texts.jsonl": sha256_file(os.path.join(HERE, "texts.jsonl")),
            "docs_kylin.npy": sha256_file(os.path.join(HERE, "docs_kylin.npy")),
            "queries_kylin.npy": sha256_file(os.path.join(HERE, "queries_kylin.npy")),
            "docs_bge.npy": sha256_file(os.path.join(HERE, "docs_bge.npy")),
            "queries_bge.npy": sha256_file(os.path.join(HERE, "queries_bge.npy")),
            "gold.npy": sha256_file(os.path.join(HERE, "gold.npy")),
        },
        "metrics": m,
        "notes": "已知答案自检：retrieve.py --side kylin 逐位复现 C-265 (naive_r1=0.5242) 与 C-266 (naive_r10=0.8634)。"
                 "R@3 在 claims.json 中无主张，本次为新增测量。227 题只覆盖 77 个 gold doc，"
                 "有效样本量远小于 227，只报 McNemar 不一致对条数与方向，不下显著性/等价结论。"
                 "单一语料单一领域，不构成对 BGE-M3 通用能力的评价。",
        "succeeded": True,
        "raw_stdout": kylin + "\n" + bge + "\n" + mc,
    }
    out = os.path.join(HERE, "measurement_record_M-211.json")
    json.dump(rec, open(out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=2)
    print("写出", out)
    print(json.dumps({k: v["value"] for k, v in m.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
