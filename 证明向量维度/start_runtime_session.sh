#!/bin/bash
# start_runtime_session.sh —— 登录桌面时自动拉起 kylin-ai-runtime
#
# 为什么需要单独一个脚本？
#   kylin-ai-runtime 与 kytensor 不同，它没有 systemd 单元，是由图形会话
#   （ukui-session）在用户登录时拉起的，运行时需要 DISPLAY、DBUS_SESSION_BUS_ADDRESS
#   等只有图形会话中才存在的环境变量。因此它无法在开机阶段（还没有人登录时）启动，
#   必须等到用户登录桌面后才能拉起 —— 这就是本脚本的职责。
#
# 配合关系：
#   restore_env_boot.sh  开机时跑（root/cron），负责系统层：解锁、装包、起 kytensor、开 SSH
#   start_runtime_session.sh  登录时跑（当前用户/桌面自启动），负责会话层：拉起 runtime
#
# 安装位置：~/.config/autostart/ 下需要一个对应的 .desktop 文件来调用本脚本

LOG=/home/<USER>/runtime_session.log
echo "" >> "$LOG"
echo "===== 会话启动 runtime —— $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"

# 等待桌面环境完全就绪（登录瞬间 DBus/X 可能还没准备好）
sleep 15

# 如果已经在跑就不重复启动
if pgrep -f kylin-ai-runtime > /dev/null; then
    echo "kylin-ai-runtime 已在运行，跳过" >> "$LOG"
    exit 0
fi

# 用完整的会话环境变量拉起 runtime
setsid env LD_LIBRARY_PATH=/usr/lib/kylin-ai/depends \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    DISPLAY=:1 \
    kylin-ai-runtime >> /tmp/runtime.log 2>&1 < /dev/null &
disown

sleep 5
if pgrep -f kylin-ai-runtime > /dev/null; then
    echo "kylin-ai-runtime 启动成功" >> "$LOG"
else
    echo "kylin-ai-runtime 启动失败，详见 /tmp/runtime.log" >> "$LOG"
fi
