#!/bin/bash
# start_memory_service.sh —— 记忆服务启动脚本（麒麟机常驻）
#
# 磐石不可变系统上没有可持久的 systemd 单元（/usr 重启回滚），用 nohup 常驻。
# 依赖：KYSDK embedding + 官方向量引擎已恢复（重启后先跑一次 ~/restore_env.sh，
#       见《证明向量维度/前置步骤-不可变系统解锁与KYSDK部署命令手册.md》）。
#
# 用法：
#   bash start_memory_service.sh                 # 默认 config=all backend=kylin
#   CONFIG=naive bash start_memory_service.sh    # 换检索档（须与报告声明一致）
#   COLLECTION=rag_knowledge DOCS=... bash start_memory_service.sh
#
# 停止：pkill -f 'memory_service.py --config'   （注意别用会匹配到本脚本的模式）
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8077}"
CONFIG="${CONFIG:-all}"
BACKEND="${BACKEND:-kylin}"
COLLECTION="${COLLECTION:-rag_knowledge}"
LOG="${LOG:-$HERE/service.log}"

# 前置自检：emb_serverd 能返回 768 维
if ! python3 - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))
from embedding_client import EmbeddingClient
v = EmbeddingClient(serverd_path=os.path.expanduser("~/rag/serverd/emb_serverd")).encode("自检")
sys.exit(0 if len(v) == 768 else 1)
PY
then
  echo "❌ KYSDK embedding 未就绪——先跑 ~/restore_env.sh（磐石重启后 /usr 会回滚）"
  exit 1
fi

pkill -f 'memory_service.py --config' 2>/dev/null
sleep 1

ARGS=(--config "$CONFIG" --backend "$BACKEND" --collection "$COLLECTION" --port "$PORT")
[ -n "${DOCS:-}" ] && ARGS+=(--docs "$DOCS")

mv "$LOG" "$LOG.$(date +%Y%m%d%H%M%S)" 2>/dev/null
( setsid nohup python3 "$HERE/memory_service.py" "${ARGS[@]}" > "$LOG" 2>&1 & )

echo "启动中，等待预热（首笔完整检索会消化 KYSDK 首调开销）..."
for i in $(seq 1 40); do
  sleep 2
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok": true'; then
    echo "✅ 已就绪：$(curl -s http://127.0.0.1:$PORT/health)"
    echo "   日志：$LOG"
    exit 0
  fi
done
echo "❌ 40 次探测后仍未就绪，看 $LOG"
exit 1
