import json
import subprocess
import sys
import time

sys.path.insert(0, "/home/<USER>/rag/serverd")
from embedding_client import EmbeddingClient


def rss_of(pid):
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out) if out else 0
    except Exception:
        return 0


def find_pids(pattern):
    out = subprocess.check_output(["pgrep", "-f", pattern]).decode().strip()
    return [int(p) for p in out.splitlines() if p.strip()]


def total_rss(pattern):
    return sum(rss_of(p) for p in find_pids(pattern))


result = {}

result["kytensor_rss_before_kb"] = total_rss("^/usr/bin/kytensor ")
result["kylin_ai_runtime_rss_before_kb"] = total_rss("^kylin-ai-runtime$")

t0 = time.time()
with EmbeddingClient() as client:
    startup_s = time.time() - t0
    emb_pid = client._proc.pid
    v = client.encode("怎么重启网络服务")
    result["vector_dim"] = len(v)
    result["startup_seconds"] = round(startup_s, 2)
    result["emb_serverd_rss_kb"] = rss_of(emb_pid)
    result["kytensor_rss_after_kb"] = total_rss("^/usr/bin/kytensor ")
    result["kylin_ai_runtime_rss_after_kb"] = total_rss("^kylin-ai-runtime$")

result["kytensor_delta_kb"] = result["kytensor_rss_after_kb"] - result["kytensor_rss_before_kb"]
result["kylin_ai_runtime_delta_kb"] = (result["kylin_ai_runtime_rss_after_kb"]
                                       - result["kylin_ai_runtime_rss_before_kb"])
result["total_attributable_kb"] = (result["emb_serverd_rss_kb"]
                                    + max(0, result["kylin_ai_runtime_delta_kb"])
                                    + max(0, result["kytensor_delta_kb"]))

print(json.dumps(result, ensure_ascii=False, indent=2))
with open("/home/<USER>/rag/serverd/e2e_memory_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
