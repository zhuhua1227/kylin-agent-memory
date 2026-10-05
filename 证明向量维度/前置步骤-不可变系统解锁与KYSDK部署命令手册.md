# 前置步骤：银河麒麟 V11 磐石不可变系统 —— 解锁与 KYSDK Embedding 部署命令手册

> **适用机型**：银河麒麟桌面 V11（2603，磐石 OSTree 不可变系统）
> **目标**：在只读的不可变系统上，一步步跑通官方文本 embedding SDK，为《证明向量维度-768-代码执行报告》提供可运行环境。
> **记录**：麒麟 RAG 项目组　（本手册命令均为真机实测）

---

## 0. 先建立心智模型：这台机器有"两道锁"

普通 Linux `apt install` 即可；磐石不可变系统不行，它有两道防护，安装前必须理解，否则会被报错卡住：

| 锁 | 是什么 | 表现 | 开锁方式 |
|----|--------|------|----------|
| **锁① 安装守卫** | dpkg 钩子 `ostree-pkgs-guard`，normal 模式拦截写系统操作 | 报「当前模式禁止执行(unpack)操作」 | 走 `unshare -m` 通道 |
| **锁② `/usr` 只读** | 磐石基底 `/usr` 为只读挂载 | 报「只读文件系统」 | `ostree admin unlock --hotfix` 挂可写覆盖层 |

**排错口诀**：遇到装不上，先判断是哪道锁在拦——「模式禁止」是锁①，「只读文件系统」是锁②。

> ⚠️ 重要限制（务必先读第 6 节）：本手册的安装方式**在当前开机会话内有效，但不保证跨整机重启存活**。请先看文末"持久化说明"。

---

## 1. 认识当前部署状态（只读操作，先看后动）

```bash
sudo ostree admin status
```

- 输出中带 `*` 的是当前启动的部署槽；标记可能有 `pending` / `rollback` / `Unlocked`。
- 目的：确认是"干净单部署"还是已有待启动槽，避免后续解锁/重启踩坑。
- 注意：`ostree admin` 系列命令都需 `sudo`，否则报「权限不够」。

---

## 2. 解锁不可变系统（开锁②：让 `/usr` 可写）

这是整个前置的核心一步。

```bash
sudo ostree admin unlock --hotfix
```

**作用**：给 `/usr` 挂一层**可写覆盖层（overlayfs）**，进入 hotfix 模式。

**成功标志**（输出关键行）：
```
Hotfix mode enabled.  A writable overlayfs is now mounted on /usr for this booted deployment.
```

**验证 `/usr` 是否已可写**：
```bash
grep ' /usr ' /proc/mounts
# 期望结尾出现 rw（overlay ... rw）；若仍为 ro 则未解锁成功
```

**两种解锁模式的区别（选 hotfix）**：

| 命令 | 特点 |
|------|------|
| `ostree admin unlock` | 开发模式，临时，重启即失效 |
| `ostree admin unlock --hotfix` | 挂持久覆盖层（本手册采用） |

**可能的连带现象**：解锁后 `ostree admin status` 可能新增一个 `.2 (pending)` 干净克隆槽。它与原 `.0` 同一 commit、是冗余副本。

> ⚠️ **不要用 `undeploy` 清理这个多余克隆槽！** 这是一处实测踩过的坑：`undeploy` 属于"修改部署列表"的操作，疑似会触发下文 2.1 节描述的 libostree 已知 bug（[RH Bugzilla #1346335](https://bugzilla.redhat.com/show_bug.cgi?id=1346335) / 上游 ostree issue #339），导致后续重启后进入"假解锁"死结。**如需固定默认启动项，改用 `ostree admin set-default <index>`，它只改默认指针、不删除任何部署，更安全。** 实在要清理，也应先做好整机快照再动手。

### 2.1 ⚠️ 已知持久化陷阱："假解锁"死结（重要，先读再动手）

这是一个**真实存在、有编号的 libostree 上游 bug**，不是操作失误：当已有 hotfix 部署的系统上产生新部署（如 `undeploy`/`upgrade`）时，新／剩余部署可能**错误地继承 `Unlocked: hotfix` 标记，但并未真正挂载可写覆盖层**。

**症状**（重启后再执行 `sudo ostree admin status` 出现）：
```
* kylin ...   Unlocked: hotfix        ← 标记显示已解锁
```
但 `grep ' /usr ' /proc/mounts` 结尾却是 `ro`；此时再跑 `unlock --hotfix` 会报：
```
error: 部署 'hotfix' 已处于解锁状态
```
——**拒绝解锁，而 `ostree admin` 又没有 `lock` 子命令可以清掉这个假状态**，形成死结，所有后续 apt 安装都会报"只读文件系统"。

**验证方法**：不要只看 `Unlocked` 标记，务必用 `/proc/mounts` 里的**最后一行**判定（`/usr` 常会出现两行 overlay 记录，第一行是中间层、恒为 `ro`，只有最后一行才是实际生效的挂载）：
```bash
grep ' /usr ' /proc/mounts | tail -1
# 结尾 rw 才是真的可写；只看第一行会误判为只读（我们曾在此踩过坑）
```

**唯一确认有效的解法**：不要试图在这个"脏"部署上硬解，而是重新部署一份**全新、从未被碰过**的干净副本，重启切换过去，在处女地上再解锁：
```bash
# ① 查当前 refspec（在 ostree admin status 输出的 origin refspec 一行）
sudo ostree admin status

# ② 用同一 refspec 重新部署一份全新副本（不会重启、不影响当前运行）
sudo ostree admin deploy <origin-refspec>
# 例：sudo ostree admin deploy kylin:kylin/desktop/v11/2603/xc/general/all/release/amd64

# ③ 确认新部署排在最前（index 0）且不带 Unlocked 标记
sudo ostree admin status

# ④ 用系统自身命令干净重启（不要用外部强制断电/重置）
sudo reboot

# ⑤ 重启后落在新部署上，此时再解锁应能真正成功
sudo ostree admin unlock --hotfix
grep ' /usr ' /proc/mounts | tail -1   # 期望 rw
```
本手册第 3 节以后的所有安装步骤，都应在这份**新解锁成功**的部署上执行。

---

## 3. 安装 embedding 开发包（过锁①：走 `unshare -m`）

`/usr` 可写后，安装仍需绕过安装守卫，用 `unshare -m` 通道。

```bash
# 3.1 刷新软件源（写 /var，可写，无需 unshare）
sudo apt update

# 3.2 安装 embedding SDK 开发包（头文件 + 可链接 .so）
sudo unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y libkylin-coreai-embedding-dev'
```

**命令拆解**：
- `unshare -m` = 开独立挂载命名空间，守卫放行此通道；
- `DEBIAN_FRONTEND=noninteractive` = 让 apt 不弹交互、自动用默认，避免脚本卡住；
- 装 `-dev` 包是因为编译测试程序需要**头文件**（`embedding.h`）与**可链接 .so**。

**验证头文件落地**：
```bash
find /usr/include/kylin-ai -name 'embedding.h'
# 期望：/usr/include/kylin-ai/coreai/embedding/embedding.h
```

**常见报错对照**：
- 「当前模式禁止执行(unpack)」→ 没走 unshare（锁①）；
- 「只读文件系统」→ `/usr` 未解锁（回第 2 步）。

---

## 4. 修复 SDK↔后端协议不匹配（核心阻塞）

麒麟出厂镜像常见"SDK 比后端新"：embedding SDK 1.2.0.0 要求后端协议 ≥1.3.0，而出厂 `kylin-ai-runtime` 仅 1.1.0.1（协议 1.0.0），直接调用会报 403。

```bash
# 4.1 先侦察当前后端版本
dpkg -l | grep -iE 'kylin-ai-runtime|kytensor'
#   典型故障态：runtime 1.1.0.1、kytensor ...ok7k0.12

# 4.2 安装 AI 子系统"元包"，协同升级整套后端（runtime → 1.2.0.4 等）
sudo unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y kylin-ai-subsystem'

# 4.3 ★关键★ 手动补齐 kytensor 到 ok7k0.14
#     元包对 kytensor 的依赖只写"≥ok7k0.10"，已装的 ok7k0.12 已满足 → 不会自动升；
#     但新 runtime 1.2.0.4 需要 ok7k0.14 才有的符号，缺则起不来。必须手动补：
sudo unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y kytensor-client kytensor-server kytensor-python'

# 4.4 复核版本（runtime 应 1.2.0.4；kytensor 应 ...ok7k0.14）
dpkg -l | grep -iE 'kylin-ai-runtime|kytensor'
```

**教训**：这套 AI 栈必须**协同升级**，切勿单包升级（会 ABI 崩），也切勿全量 `apt upgrade`（预览源易引入新的不一致）。

### 4.5 ⚠️ 更深的坑：元包"以为装过"就不真的重装依赖

在**全新部署**（见 2.1 节）上，会遇到一个此前未记录的现象：`kylin-ai-subsystem` 元包依赖版本号早已满足（dpkg 数据库残留自旧部署的记录），**apt 判断"无需变动"，只重装元包自身（几 KB），完全不碰它的 71 个依赖**，而这份新 `/usr` 里那些依赖实际全是**出厂旧文件（日期 2 月 12 日）**——数据库记录与磁盘实际内容脱节，是这套 ostree+dpkg 混合架构的固有特性。

**表现**：`libkylin-coreai-embedding` 等包 `dpkg -l` 显示"已安装、版本正确"，但实际调用时报 `undefined symbol`，或运行报 `Proxy init failed errorCode: 3`（连接错误）、`errorCode: 10`（模型未找到）等，且文件日期停留在 2 月 12 日。

**排查方法**：怀疑某个功能不对时，先看文件真实日期，2 月 12 日即为疑点：
```bash
ls -la $(dpkg -L <可疑包名> | grep '\.so')   # 日期是 2月12日 → 出厂旧文件，需强制重装
```

**根治方法**：不要依赖元包，直接取出它的完整依赖清单，全部强制重装（`--reinstall`），一次性解决，不要逐个猜：
```bash
# 取出元包的完整递归依赖清单（71个包，含所有模型包/引擎插件）
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
  --no-breaks --no-replaces --no-enhances kylin-ai-subsystem 2>/dev/null \
  | grep -E "^\w" | sort -u > /tmp/subsystem_deps.txt

# 全部强制重装（体积较大，含模型文件，视网速需要几分钟）
sudo unshare -m sh -c "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall $(cat /tmp/subsystem_deps.txt | tr '\n' ' ')"
```

### 4.6 ★最终病根★：模型元数据文件缺字段（导致 init_model 报错 10）

即使协议、runtime、kytensor 全部正确，仍可能卡在 `text_embedding_init_model()` 返回错误码 10（`Text model not found`）。根本原因：**模型包 `kylin-gte-base-model` 提供的 `model_description.yaml` 是出厂旧版，缺少新版 runtime 必需的 `others_info` 字段**（内含 `channel_info`、`embd_type`、`output_size` 等模型分组/维度信息）。旧版 yaml 里完全没有这一行，新版 runtime 解析不到就报 `Failed to find model group 'TEXT' in the model info`，进而导致模型判定为"未找到"。

**诊断**：
```bash
cat /usr/share/kylin-ai/model-repository/ensemble-embd_gte-base_uint8-text/model_description.yaml
# 若只有 model_deb_name/model_name/model_capability/model_is3rdModel/model_internal 五行、
# 没有 others_info 一行 → 就是这个坑
```

**修复**：强制重装模型包本身（连同姊妹的 CN-CLIP 模型包）：
```bash
sudo unshare -m sh -c "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall kylin-gte-base-model kylin-cn-clip-model"
# 重装后 yaml 会新增一行，形如：
# others_info: "{...,\"embd_type\":\"TEXT\",\"channel_info\":[{...,\"output_size\":768,...}]}"
```
重装后需按第 5 节重启 `kytensor` 与 `kylin-ai-runtime` 让新元数据生效。

---

## 5. 重启后端服务，让新协议生效

装包只改磁盘文件，**内存里运行的旧进程仍用旧代码**，必须重启后端才让协议升到 1.3.0。

```bash
# 5.1 重启推理引擎（systemd 服务）
sudo systemctl restart kytensor
systemctl status kytensor --no-pager | head -8      # 期望 active (running)

# 5.2 重启 kylin-ai-runtime
#     注意：它由图形会话（ukui-session）登录时拉起，无 systemd 单元。
#     最干净的做法是：在图形界面【注销】再重新登录（不是关机重启整机！）。
#     登录回来后确认它重新起来（PID 会变）：
ps -ef | grep -i kylin-ai-runtime | grep -v grep
```

**成功后**，调用程序日志应出现：
```
Runtime version is  1.3.0 InitEngine call new init_v2
```

---

## 6. 运行自编译程序：放行 kysec 执行控制

麒麟 kysec 安全执行控制默认拒绝运行"陌生"（自编译）二进制，表现为 `不允许的操作`（EPERM）。

```bash
# 6.1 查看安全状态，确认 exec control 当前档位
getstatus
#   常见：exec control : warning（warning 也会拦截未受信任程序）

# 6.2 临时放行"执行控制"，让自编译程序可运行
sudo setstatus -f exectl off

# 6.3 运行验证程序（见《证明向量维度-768-代码执行报告》）
gcc ~/emb_test.c -o ~/emb_test -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
~/emb_test          # 期望输出 "向量维度 : 768"

# 6.4 验证完毕，恢复安全策略
sudo setstatus -f exectl warning
```

`setstatus` 用法速查：
```
setstatus -f < exectl | netctl > < off | enforcing | warning >
```

---

## 7. ★核心结论★ 持久化问题的最终定论与解决方案（2026-08-03 实证）

### 7.1 结论：`/usr` 覆盖层无法跨重启保留，这是系统设计使然

经多轮实证（详见 7.2 验证过程），可以确定地给出结论：

> **在银河麒麟 V11 磐石不可变系统上，通过 `unshare -m` 通道安装到 `/usr` 的一切软件（sshd、embedding SDK、runtime、kytensor 等），在整机重启后必定全部丢失，退回出厂状态。这不是操作失误或偶发故障，而是不可变系统"保护系统分区不被持久修改"这一设计目标的必然结果。**

需要特别澄清两个曾经的误判：

1. **"全新部署 + 重新解锁"并不能解决持久化**。libostree 官方文档对 `unlock --hotfix` 的描述是"reboots will retain any changes"，但在本机型（Kylin V11 磐石）上实测并非如此——即使严格按照"新建部署 → 干净重启 → 在处女地上解锁"的流程操作，重启后 `/usr` 依然回到只读的出厂状态。
2. **虚拟机快照备份救不回来**。曾尝试用 PVE 的 `vzdump` 备份（快照模式）+ `qmrestore` 恢复，结果恢复后 `/usr/sbin/sshd` 依然不存在。原因是：`unshare -m` 写入的是内存中的临时挂载层，**这些改动从未落到磁盘**，所以磁盘快照里根本就没有它们。

### 7.2 验证过程（两种重启方式均已验证）

| 验证场景 | 结果 |
|---------|------|
| 异常断电（家庭跳闸，整机硬断电） | `/usr` 回到只读，`emb_test` 报 `undefined symbol`，sshd 消失 |
| 正常重启（`reboot` 干净关机） | 同上，现象完全一致 |
| 从 vzdump 备份恢复 | `/usr` 内容仍是出厂状态，备份中本就不含这些改动 |

三次验证结论一致，可以排除"偶发损坏"或"关机方式不当"的可能。

### 7.3 采用的解决方案：一键恢复脚本

既然无法持久化，就把"恢复"这件事做到**快速、可靠、可复现**。本项目最终采用的方案是一键恢复脚本 `restore_env.sh`（见同目录），存放在 `/home` 持久分区（该分区不受 `/usr` 重置影响）。

**每次整机重启后，运行一次即可在约 5~10 分钟内完整恢复环境：**

```bash
bash ~/restore_env.sh
```

脚本按顺序自动完成本手册第 2~6 节的全部工作，并固化了所有已知坑的规避方法（详见脚本内注释）：

| 步骤 | 内容 | 固化的踩坑经验 |
|------|------|--------------|
| 1 | 解锁 `/usr` | 判断挂载状态必须取 `/proc/mounts` **最后一行**（第一行恒为 `ro` 的中间层，会误判） |
| 2 | 刷新源 + **修复破损依赖** | 重启后常残留未满足的依赖（如 `kylin-ai-abstract-models` 依赖 `kylin-speech-tts-model` 未装），此状态下 apt 会**拒绝任何新安装**，必须先 `--fix-broken` |
| 3 | 恢复 sshd | `systemctl start ssh` 会报 `ssh.socket not found`，须改用 `/etc/init.d/ssh start` |
| 4 | 重装 SDK 全套 | 不能只装元包（它只重装自己那 4.7KB 空壳），须用 `apt-cache depends --recurse` 取出 72 个依赖**逐个 `--reinstall`**；并单独重装模型包修复 `others_info` 字段（见 4.6 节） |
| 5 | 重启后端服务 | `kytensor` 用 systemd；`kylin-ai-runtime` 无 systemd 单元，需带完整会话环境变量手动拉起 |
| 6 | 验证 768 | 临时放行 kysec 执行控制，跑 `emb_test`，验证完毕恢复安全策略 |

**⚠️ 使用注意事项**：

- **所有写 `/usr` 的 apt 操作都必须套 `unshare -m`**，包括 `apt --fix-broken install`。漏掉就会报"当前模式禁止执行"。
- **首次运行 `emb_test` 可能报 `errCode: 24 Timeout was reached`**：这是模型文件（339MB）仍在后台加载，**不是故障**，等待数十秒后重新运行即可。
- **恢复后需检查默认网关**：`ip route` 的 `default via` 应为 PVE 地址（本项目为 `192.168.100.2`）而非 `.1`。若网关错误，表现为"能 ping 通 PVE、但外部无法 SSH 连入"。修复：`sudo nmcli con modify "有线连接 1" ipv4.gateway <PVE地址> && sudo nmcli con up "有线连接 1"`。
- **首次恢复时（SSH 尚未可用）需在本机图形控制台操作**。若使用 PVE 的 noVNC 控制台，**无法粘贴文本**，需通过 U 盘等物理介质传入脚本。

### 7.4 进阶：配置为开机自动恢复（免人工干预）

手动运行脚本仍需人工介入。为进一步降低运维成本，可将恢复流程配置为**开机自动执行**。由于恢复工作分属两个不同的运行环境，需要拆成两个脚本、用两种机制分别触发：

| 脚本 | 触发机制 | 运行身份 | 负责部分 | 为什么这样拆 |
|------|---------|---------|---------|------------|
| `restore_env_boot.sh` | root 的 `@reboot` crontab | root | 解锁 `/usr`、修依赖、装 SDK、起 sshd 与 kytensor | 开机阶段无人登录，只能做不依赖图形会话的事；用 root 身份可免去密码问题 |
| `start_runtime_session.sh` | 桌面自启动 `~/.config/autostart/*.desktop` | 当前登录用户 | 拉起 `kylin-ai-runtime` | 该进程**必须**挂在图形会话下（需要 `DISPLAY`、`DBUS_SESSION_BUS_ADDRESS` 等只有登录后才存在的变量），无法在开机阶段启动 |

**为什么用 crontab 而不是 systemd 服务**：systemd 单元文件通常放在 `/etc/systemd/system/`，而本机 `/etc` 也是 overlay 挂载（其 upperdir 位于 `sysroot` 下的部署专属目录），跨重启/跨部署切换的持久性存在不确定性；而 crontab 数据存放于 `/var/spool/cron/crontabs`，`/var` 是**独立的 ext4 分区**（`/dev/sda5`），确定持久。同理，脚本本体存放于 `/home`（`/dev/sda6`，独立分区）。

**配置方法**：

```bash
# ① 注册开机自启（root crontab）
sudo bash -c '(crontab -l 2>/dev/null | grep -v restore_env_boot; \
    echo "@reboot /home/<USER>/restore_env_boot.sh") | crontab -'
sudo crontab -l          # 验证

# ② 注册登录自启（桌面 autostart）
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/kylin-ai-runtime.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Kylin AI Runtime Restore
Exec=/home/<USER>/start_runtime_session.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
```

**设计要点**（已固化在脚本中）：

- **开机脚本需先等待网络就绪**：cron 的 `@reboot` 可能早于网络配置完成，而后续所有装包操作都要访问软件源。脚本会轮询 ping 软件源域名，最多等待约 150 秒。
- **root 身份下不能用 `~`**：root 的 `~` 是 `/root`，而脚本要操作的是 `/home/<USER>` 下的文件，因此开机版脚本中所有路径均写死为绝对路径。
- **执行日志**：开机脚本输出写入 `/home/<USER>/restore_boot.log`，会话脚本写入 `/home/<USER>/runtime_session.log`，便于开机后回溯自动恢复是否成功。

**能达到的效果**：开机后无需任何人工操作，机器即自动恢复到「SSH 可远程连接、SDK 已安装、kytensor 推理引擎运行中」的状态；用户登录图形桌面后，`kylin-ai-runtime` 自动补齐，此时 `emb_test` 即可跑出 768。

### 7.5 这一发现对项目的价值

该持久化限制本身，是"面向不可变操作系统的端侧部署适配"这一工程难点的**一手实证素材**，可直接写入适配测试报告：

- 它揭示了不可变操作系统在"AI 能力端侧部署"场景下的一个真实矛盾：**系统安全性设计（防止系统分区被篡改）与 AI 组件部署需求（需要安装大量依赖到系统目录）之间存在结构性冲突**；
- 本项目给出的应对方案——**将部署过程完全脚本化、可复现化**——是面对这类不可变系统的通用工程实践，比"装好后不敢重启"的脆弱状态更具工程价值，也更贴近真实生产环境的运维要求。

---

## 8. 命令速查

### 8.0 【日常使用推荐】重启后恢复环境 —— 一条命令

由于 `/usr` 无法持久（见第 7 节），**每次整机重启后**只需运行恢复脚本，无需再手动执行下面 8.1 的完整流程：

```bash
bash ~/restore_env.sh        # 约 5~10 分钟，自动完成全部步骤并验证 768
```

若 `emb_test` 报 `errCode: 24 Timeout`，等待数十秒后重新运行 `~/emb_test` 即可（模型仍在加载）。
若外部 SSH 连不进来，检查网关：`ip route` 的 `default via` 应指向 PVE 而非 `.1`。

### 8.1 手动流程（首次部署 / 脚本失效时排查用，按序汇总，含全部已知坑修复）

```bash
# ① 看状态，记下 origin refspec
sudo ostree admin status

# ② 若怀疑处于"假解锁"死结（Unlocked标记但/usr为ro），先重新部署一份干净副本
sudo ostree admin deploy <origin-refspec>
sudo reboot
# 重启后确认落在新部署（* 号在新 index 且无 Unlocked 标记），再继续

# ③ 解锁 /usr 可写（用 tail -1 判定，别只看第一行）
sudo ostree admin unlock --hotfix
grep ' /usr ' /proc/mounts | tail -1     # 期望 rw

# ④ 装 embedding 开发包
sudo apt update
sudo unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y libkylin-coreai-embedding-dev'

# ⑤ 不要只信任元包，直接批量强制重装其全部依赖（71个包，含模型/引擎插件）
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
  --no-breaks --no-replaces --no-enhances kylin-ai-subsystem 2>/dev/null \
  | grep -E "^\w" | sort -u > /tmp/subsystem_deps.txt
sudo unshare -m sh -c "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall $(cat /tmp/subsystem_deps.txt | tr '\n' ' ')"

# ⑥ 重启后端服务
sudo systemctl restart kytensor
sudo pkill -9 -f kylin-ai-runtime
setsid env LD_LIBRARY_PATH=/usr/lib/kylin-ai/depends XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus DISPLAY=:1 kylin-ai-runtime \
  > /tmp/runtime.log 2>&1 < /dev/null &
disown

# ⑦ 放行执行控制并运行验证程序
sudo setstatus -f exectl off
gcc ~/emb_test.c -o ~/emb_test -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
~/emb_test          # 期望输出 "向量维度 : 768"
sudo setstatus -f exectl warning

# ⑧ 若仍报 errorCode 10（Text model not found），逐一排查模型 yaml 是否缺 others_info 字段（见 4.6 节）
cat /usr/share/kylin-ai/model-repository/ensemble-embd_gte-base_uint8-text/model_description.yaml
sudo unshare -m sh -c "DEBIAN_FRONTEND=noninteractive apt install -y --reinstall kylin-gte-base-model kylin-cn-clip-model"
# 重装后重复步骤⑥重启服务、重新验证
```
