#!/bin/bash
# restore_env.sh —— 银河麒麟 V11 磐石不可变系统：KYSDK Embedding 环境一键恢复脚本
#
# 【为什么需要这个脚本】
#   磐石不可变系统上，通过 unshare -m 通道装进 /usr 的软件（sshd、embedding SDK 全套）
#   属于临时覆盖层，**整机重启后会全部退回出厂状态**（实测正常重启与异常断电均如此，
#   且 PVE 快照备份也救不回来——因为这些改动根本没落到磁盘上）。
#   本脚本把完整恢复流程固化下来，重启后跑一遍即可恢复（约 5~10 分钟）。
#
# 【存放位置】
#   必须放在 /home 或 /root 等持久分区（本脚本设计为 ~/restore_env.sh），
#   这些分区不受 /usr 重置影响，脚本本身不会丢。
#
# 【用法】
#   首次（SSH 尚未恢复时）：在 Kylin 本机图形控制台运行  bash ~/restore_env.sh
#   之后（SSH 已恢复）：可远程 ssh 进来运行
#
# 【本脚本已固化的踩坑经验】
#   1. /usr 挂载状态要看 /proc/mounts 的**最后一行**（前面那行 ro 是中间层，会误判）
#   2. 装包必须走 unshare -m（磐石 guard 只放行这个通道）
#   3. kylin-ai-subsystem 元包在 dpkg 认为"版本已正确"时只重装自己、不碰 71 个依赖，
#      必须逐个 --reinstall 强制重装
#   4. 真正的病根是模型包 kylin-gte-base-model 的 model_description.yaml 缺
#      others_info 字段（含 channel_info/embd_type/output_size），必须重装模型包
#   5. sshd 的 systemd 单元有问题（报 ssh.socket not found），要用 SysV 脚本启动
#   6. kysec 执行控制会拦截自编译程序，测试前需临时放行
#   7. 【2026-08-16 新增】ostree admin unlock --hotfix 每次都会新建一个部署，
#      旧部署不会自动清理——反复重启+跑本脚本堆出过 11 层部署、吃掉 30GB 根分区
#      （根分区仅 55GB，堆到 100%/0 可用）。本脚本现在每次解锁后都会自动清理旧部署。

set -u
# sudo 口令不再硬编码——通过环境变量传入，或留空走交互输入。
# （历史教训：早期版本把口令写死在这里，随源码一起进了 git 历史，
#  详见《独立评审报告与整改清单.md》§1。）
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
log "1/7 检查并解锁 /usr 为可写"
# ============================================================
# 注意：必须取最后一行，/usr 会有多行 overlay 记录，第一行恒为 ro（中间层）
USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
if [ "$USR_MODE" = "rw" ]; then
    ok "/usr 已经可写，跳过解锁"
else
    echo "  /usr 当前为只读，执行 hotfix 解锁..."
    echo "$PW" | sudo -S ostree admin unlock --hotfix 2>&1 | tail -3
    USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
    if [ "$USR_MODE" = "rw" ]; then
        ok "解锁成功，/usr 现在可写"
    else
        err "/usr 仍为只读（当前=$USR_MODE）"
        err "可能遇到『假解锁』死结（flag 显示已解锁但实际只读）。"
        err "解法见《前置步骤手册》2.1 节：需 ostree admin deploy 新部署 + 重启。"
        exit 1
    fi
fi

# ============================================================
log "2/7 清理旧的 hotfix 部署（防止磁盘再次被堆满）"
# ============================================================
# 背景：ostree admin unlock --hotfix 每次都会新建一个部署，旧的不会自动清理。
# 只保留：当前启动项(*)、rollback、pending、出厂 Pinned:yes 基线，其余全部 undeploy。
# 解析异常（例如格式变了、找不到唯一的当前项/pinned项）时宁可跳过，不冒险误删。
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
log "3/7 刷新软件源 + 修复残留的破损依赖"
# ============================================================
echo "$PW" | sudo -S apt update 2>&1 | tail -2
ok "软件源已刷新"

# 【重要】重启后 dpkg 可能残留"未满足的依赖关系"（实测见过
# kylin-ai-abstract-models 依赖 kylin-speech-tts-model 未装的情况）。
# apt 在这种破损状态下会**拒绝执行任何新的安装**，导致后续装 sshd、
# 装 SDK 全部失败。所以必须先修复，再往下走。
echo "  检查并修复破损依赖..."
echo "$PW" | sudo -S unshare -m sh -c \
    'DEBIAN_FRONTEND=noninteractive apt --fix-broken install -y' 2>&1 | tail -3
ok "依赖关系已修复"

# ============================================================
log "4/7 恢复 SSH 服务（远程连接的前提）"
# ============================================================
echo "$PW" | sudo -S unshare -m sh -c \
    'DEBIAN_FRONTEND=noninteractive apt install -y --reinstall openssh-server' 2>&1 | tail -3

# systemd 的 ssh.service 依赖一个不存在的 ssh.socket，会报错；用 SysV 脚本启动
echo "$PW" | sudo -S /etc/init.d/ssh start 2>&1
sleep 2
if ss -tlnp 2>/dev/null | grep -q ':22'; then
    ok "sshd 已启动，22 端口正在监听"
else
    err "22 端口未监听，SSH 恢复失败，请检查上方输出"
fi

# ============================================================
log "5/7 重装 embedding SDK 与 AI 子系统全套依赖"
# ============================================================
# 关键：不能只装元包 kylin-ai-subsystem（它在 dpkg 认为版本已正确时只会重装自己
# 那 4.7KB 的空壳，71 个依赖一个都不碰，而全新 /usr 上这些依赖全是出厂旧文件）。
# 必须取出完整递归依赖清单，逐个强制 --reinstall。
echo "  正在解析 kylin-ai-subsystem 的完整依赖清单..."
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
    --no-breaks --no-replaces --no-enhances kylin-ai-subsystem 2>/dev/null \
    | grep -E "^\w" | sort -u \
    | grep -E "^(kylin|kyai|kytensor|libkylin|libkysdk|onnxruntime|share-kytensor)" \
    | grep -vE "wayland|kwin|kmre|kylin-video|kylin-photo" > /tmp/ai_deps.txt

DEP_COUNT=$(wc -l < /tmp/ai_deps.txt)
echo "  找到 $DEP_COUNT 个相关包，开始强制重装（体积较大，请耐心等待）..."

echo "$PW" | sudo -S unshare -m sh -c \
    "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall \
     libkylin-coreai-embedding-dev libkylin-coreai-embedding \
     $(tr '\n' ' ' < /tmp/ai_deps.txt)" 2>&1 | tail -5

# 单独确保模型包被重装 —— 这是最终病根所在：
# 出厂旧版 model_description.yaml 缺 others_info 字段（内含 channel_info、
# embd_type:TEXT、output_size:768），新版 runtime 读不到就报
# "Failed to find model group 'TEXT'" → init_model 返回 errorCode 10
echo "  强制重装模型包（修复 model_description.yaml 缺 others_info 的问题）..."
echo "$PW" | sudo -S unshare -m sh -c \
    'DEBIAN_FRONTEND=noninteractive apt install -y --reinstall kylin-gte-base-model kylin-cn-clip-model' 2>&1 | tail -3

# 验证模型元数据是否正确
if grep -q "others_info" /usr/share/kylin-ai/model-repository/ensemble-embd_gte-base_uint8-text/model_description.yaml 2>/dev/null; then
    ok "模型元数据正确（含 others_info/channel_info 字段）"
else
    err "模型元数据仍缺 others_info 字段，embedding 可能无法工作"
fi

# ============================================================
log "6/7 重启后端服务"
# ============================================================
# kytensor 是 systemd 服务（推理引擎，真正跑模型的）
echo "$PW" | sudo -S systemctl restart kytensor 2>&1
sleep 3
if systemctl is-active --quiet kytensor; then
    ok "kytensor 推理引擎已重启"
else
    err "kytensor 未能启动"
fi

# kylin-ai-runtime 挂在图形会话下、没有 systemd 单元，需手动拉起
echo "$PW" | sudo -S pkill -9 -f kylin-ai-runtime 2>/dev/null
sleep 2
setsid env LD_LIBRARY_PATH=/usr/lib/kylin-ai/depends \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    DISPLAY=:1 kylin-ai-runtime > /tmp/runtime.log 2>&1 < /dev/null &
disown
sleep 4
if pgrep -f kylin-ai-runtime > /dev/null; then
    ok "kylin-ai-runtime 已启动（日志：/tmp/runtime.log）"
else
    err "kylin-ai-runtime 未能启动，请查看 /tmp/runtime.log"
fi

# ============================================================
log "7/7 验证 embedding 是否恢复（应输出 768 维）"
# ============================================================
# kysec 执行控制会拦截自编译程序，临时放行
echo "$PW" | sudo -S setstatus -f exectl off 2>&1

# 若可执行文件不存在则重新编译（源码在 /home，是持久的）
if [ ! -x ~/emb_test ] && [ -f ~/emb_test.c ]; then
    echo "  重新编译 emb_test..."
    gcc ~/emb_test.c -o ~/emb_test \
        -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
fi

if [ -x ~/emb_test ]; then
    echo "  运行 emb_test："
    ~/emb_test 2>&1 | tail -6
else
    err "找不到 ~/emb_test，请确认源码 ~/emb_test.c 是否存在"
fi

# 恢复安全策略
echo "$PW" | sudo -S setstatus -f exectl warning 2>&1

echo ""
echo -e "\033[1;32m========== 恢复流程执行完毕 ==========\033[0m"
echo "若上方显示『向量维度 : 768』，说明环境已完全恢复。"
echo "若 SSH 已恢复，之后可直接远程连接：ssh <USER>@192.168.100.50"
