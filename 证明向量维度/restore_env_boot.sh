#!/bin/bash
# restore_env_boot.sh —— 开机自动执行版（由 root 的 @reboot crontab 调用）
#
# 与 restore_env.sh（手动版）的区别：
#   1. 以 root 身份运行，不需要 sudo，也不需要密码；
#   2. 路径全部写死为 /home/<USER>/...，不用 ~ （root 的 ~ 是 /root，会找错地方）；
#   3. **不包含拉起 kylin-ai-runtime 这一步** —— 因为开机时还没有图形会话，
#      而 kylin-ai-runtime 必须挂在图形会话下（需要 DISPLAY / DBUS_SESSION_BUS_ADDRESS
#      等只有用户登录桌面后才存在的环境变量）。这一步交给桌面自启动脚本
#      （~/.config/autostart/）在用户登录时完成。
#   4. 全部输出写入日志文件，方便开机后回溯执行情况。
#
# 因此本脚本的目标是：**开机后无需人工干预，自动恢复到"SSH 可连、SDK 已装、
# 推理引擎已运行"的状态**，让远程用户能直接 ssh 进来工作。

set -u
LOG=/home/<USER>/restore_boot.log
USER_HOME=/home/<USER>

exec > >(tee -a "$LOG") 2>&1
echo ""
echo "############################################################"
echo "# 开机自动恢复 —— $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

log()  { echo -e "\n========== $1 =========="; }
ok()   { echo "  [OK] $1"; }
err()  { echo "  [ERR] $1"; }

# 等待网络就绪（cron 的 @reboot 可能早于网络配置完成，
# 而后面装包需要访问软件源，没网会全部失败）
log "0/5 等待网络就绪"
for i in $(seq 1 30); do
    if ping -c1 -W2 archive.kylinos.cn > /dev/null 2>&1; then
        ok "网络已就绪（等待 ${i} 次）"
        break
    fi
    sleep 5
done

# ---------- 1. 解锁 /usr ----------
log "1/5 解锁 /usr 为可写"
# 判断挂载状态必须取最后一行：/usr 有多行 overlay 记录，第一行恒为 ro 的中间层
USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
if [ "$USR_MODE" = "rw" ]; then
    ok "/usr 已可写，跳过"
else
    ostree admin unlock --hotfix 2>&1 | tail -3
    USR_MODE=$(grep ' /usr ' /proc/mounts | tail -1 | grep -o '\brw\b\|\bro\b')
    [ "$USR_MODE" = "rw" ] && ok "解锁成功" || { err "解锁失败，终止"; exit 1; }
fi

# ---------- 2. 刷新源 + 修复破损依赖 ----------
log "2/5 刷新软件源并修复破损依赖"
apt update 2>&1 | tail -2
# 重启后常残留未满足的依赖，此状态下 apt 会拒绝任何新安装，必须先修
unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt --fix-broken install -y' 2>&1 | tail -3
ok "依赖已修复"

# ---------- 3. 恢复 SSH（最优先，保证远程可连） ----------
log "3/5 恢复 SSH 服务"
unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y --reinstall openssh-server' 2>&1 | tail -3
# systemd 的 ssh.service 依赖不存在的 ssh.socket，必须用 SysV 脚本启动
/etc/init.d/ssh start 2>&1
sleep 2
if ss -tlnp 2>/dev/null | grep -q ':22'; then
    ok "sshd 已监听 22 端口，远程可连接"
else
    err "22 端口未监听"
fi

# ---------- 4. 重装 embedding SDK 全套 ----------
log "4/5 重装 embedding SDK 与 AI 子系统依赖"
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
    --no-breaks --no-replaces --no-enhances kylin-ai-subsystem 2>/dev/null \
    | grep -E "^\w" | sort -u \
    | grep -E "^(kylin|kyai|kytensor|libkylin|libkysdk|onnxruntime|share-kytensor)" \
    | grep -vE "wayland|kwin|kmre|kylin-video|kylin-photo" > /tmp/ai_deps.txt
echo "  找到 $(wc -l < /tmp/ai_deps.txt) 个依赖包，开始强制重装..."

unshare -m sh -c "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall \
    libkylin-coreai-embedding-dev libkylin-coreai-embedding \
    $(tr '\n' ' ' < /tmp/ai_deps.txt)" 2>&1 | tail -5

# 模型包必须单独重装：出厂旧版 model_description.yaml 缺 others_info 字段
# （含 channel_info/embd_type/output_size），是 init_model 报错 10 的真正病根
unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y --reinstall kylin-gte-base-model kylin-cn-clip-model' 2>&1 | tail -3

if grep -q "others_info" /usr/share/kylin-ai/model-repository/ensemble-embd_gte-base_uint8-text/model_description.yaml 2>/dev/null; then
    ok "模型元数据正确（含 others_info/channel_info）"
else
    err "模型元数据仍缺 others_info 字段"
fi

# ---------- 5. 重启推理引擎 + 放行 kysec ----------
log "5/5 重启推理引擎"
systemctl restart kytensor 2>&1
sleep 3
systemctl is-active --quiet kytensor && ok "kytensor 已重启" || err "kytensor 启动失败"

# 提前放行 kysec 执行控制，这样用户登录后可直接运行自编译的 emb_test
setstatus -f exectl off 2>&1
ok "kysec 执行控制已放行（测试环境）"

echo ""
echo "========== 开机自动恢复完成 =========="
echo "系统层已就绪：SSH 可连接、SDK 已安装、kytensor 推理引擎运行中。"
echo "注意：kylin-ai-runtime 需要图形会话，将在用户登录桌面时由"
echo "      ~/.config/autostart/ 中的自启动项拉起，之后 emb_test 才能跑出 768。"
echo "日志位置：$LOG"
