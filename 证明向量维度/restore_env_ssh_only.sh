#!/bin/bash
# restore_env_ssh_only.sh —— 银河麒麟 V11 磐石不可变系统：SSH 快速恢复脚本（阉割版）
#
# 【用途】
#   录制复现演示视频专用。完整版 restore_env.sh 要走 7 步（刷新软件源、修复破损依赖、
#   72 个 AI 子系统依赖逐个 --reinstall、重启 kytensor/kylin-ai-runtime、跑 emb_test 验证），
#   耗时 5~10 分钟，步骤太多不适合直接入镜。
#   本脚本假定 embedding SDK / kytensor / kylin-ai-runtime 已经处于正常状态
#   （录制前已用完整版 restore_env.sh 恢复过一次环境），只负责把 SSH 服务重新拉起来，
#   让录制在几秒内完成，直接进入"远程连接演示"环节。
#
# 【不做的事】
#   不刷新软件源、不修复破损依赖、不重装 AI 子系统依赖、不重启 kytensor/runtime、
#   不跑 emb_test。如果 embedding 验证（应输出 768 维）失败，请改用完整版 restore_env.sh。
#
# 【用法】
#   首次（SSH 尚未恢复时）：在 Kylin 本机图形控制台运行  bash ~/restore_env_ssh_only.sh

set -u
# sudo 口令不硬编码——通过环境变量传入，或留空走交互输入
# （历史教训见《独立评审报告与整改清单.md》§1：早期版本把口令写死进了 git 历史）
if [ -n "${KYLIN_SUDO_PW:-}" ]; then
    PW="$KYLIN_SUDO_PW"
else
    read -r -s -p "sudo 口令: " PW
    echo
fi

log()  { echo -e "\n\033[1;36m========== $1 ==========\033[0m"; }
ok()   { echo -e "  \033[1;32m[OK]\033[0m $1"; }
err()  { echo -e "  \033[1;31m[ERR]\033[0m $1"; }

# ============================================================
log "1/3 检查 /usr 可写状态（sshd 二进制在 /usr 下）"
# ============================================================
# 注意：必须取最后一行，/usr 会有多行 overlay 记录，第一行恒为 ro（中间层）
USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
if [ "$USR_MODE" = "rw" ]; then
    ok "/usr 已可写，跳过解锁"
else
    echo "  /usr 当前只读，执行 hotfix 解锁..."
    echo "$PW" | sudo -S ostree admin unlock --hotfix 2>&1 | tail -3
    USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
    if [ "$USR_MODE" = "rw" ]; then
        ok "解锁成功，/usr 现在可写"
    else
        err "/usr 仍为只读（当前=$USR_MODE），无法继续。"
        err "请改用完整版 restore_env.sh 排查『假解锁』死结。"
        exit 1
    fi
fi

# ============================================================
log "2/3 清理旧的 hotfix 部署（防止磁盘再次被堆满）"
# ============================================================
# 背景：ostree admin unlock --hotfix 每次都会新建一个部署，旧的不会自动清理，
# 反复重启+跑脚本堆出过 11 层部署、吃掉 30GB 根分区（曾把根分区堆到 100%/0 可用）。
# 只保留：当前启动项(*)、rollback、pending、出厂 Pinned:yes 基线，其余全部 undeploy。
# 解析异常时宁可跳过，不冒险误删。
DEPLOY_STATUS=$(echo "$PW" | sudo -S ostree admin status 2>/dev/null)
REMOVE_IDX=$(echo "$DEPLOY_STATUS" | awk '
    BEGIN { idx = -1 }
    /^\*?[[:space:]]*kylin[[:space:]]/ {
        idx++
        cur[idx]  = ($0 ~ /^\*/) ? 1 : 0
        roll[idx] = ($0 ~ /\(rollback\)/) ? 1 : 0
        pend[idx] = ($0 ~ /\(pending\)/) ? 1 : 0
        pin[idx]  = 0
        next
    }
    /Pinned:[[:space:]]*yes/ { pin[idx] = 1 }
    END {
        for (i = 0; i <= idx; i++) {
            keep = cur[i] || roll[i] || pend[i] || pin[i]
            if (!keep) print i
        }
    }
')
HAS_CURRENT=$(echo "$DEPLOY_STATUS" | grep -c '^\*')
HAS_PINNED=$(echo "$DEPLOY_STATUS" | grep -c 'Pinned:[[:space:]]*yes')

if [ -z "$REMOVE_IDX" ]; then
    ok "没有多余的旧部署需要清理"
elif [ "$HAS_CURRENT" -ne 1 ] || [ "$HAS_PINNED" -lt 1 ]; then
    err "ostree 部署列表解析异常（未找到唯一的当前启动项或出厂 Pinned 基线），跳过清理以策安全"
else
    COUNT=$(echo "$REMOVE_IDX" | wc -l)
    echo "  发现 $COUNT 个可清理的旧部署，从高索引到低索引依次移除..."
    for idx in $(echo "$REMOVE_IDX" | sort -rn); do
        echo "$PW" | sudo -S ostree admin undeploy "$idx" 2>&1 | tail -1
    done
    ok "旧部署清理完成"
fi
df -h / 2>/dev/null | tail -1

# ============================================================
log "3/3 启动 SSH 服务"
# ============================================================
# systemd 的 ssh.service 依赖一个不存在的 ssh.socket，会报错；固定用 SysV 脚本启动
echo "$PW" | sudo -S /etc/init.d/ssh start 2>&1
sleep 2
if ss -tlnp 2>/dev/null | grep -q ':22'; then
    ok "sshd 已启动，22 端口正在监听"
else
    err "22 端口未监听，SSH 恢复失败。"
    err "若 openssh-server 本身缺失（未事先跑过完整版），请改用 restore_env.sh。"
    exit 1
fi

echo ""
echo -e "\033[1;32m========== SSH 已就绪 ==========\033[0m"
echo "可远程连接：ssh device_b@[REDACTED_IP]"
