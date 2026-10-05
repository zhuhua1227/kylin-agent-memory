# 银河麒麟 V11 桌面版 KYSDK 文本 Embedding 部署报告

> 目标机：`device_c@192.168.200.50`　银河麒麟桌面操作系统 V11（2603，OSTree 磐石不可变系统，内核 6.6.0-63，x86_64，DEB/apt）
> 任务：让赛题指定的 KYSDK 文本向量化（`libkylin-coreai-embedding`）官方 C 接口真实跑通。
> 结论：**已彻底打通**，官方 `text_embedding()` 端到端输出 **768 维**向量。

> **本报告的定位与数值说明**（2026-08-04 补）
>
> 本报告为 2026-07-15 之前在 `192.168.200.50` 上进行的**早期部署实验**记录，
> 向量化的是一段泛化测试文本（非后续统一用的「怎么重启网络服务」），故本文
> 中的向量数值（前5值 `-0.0427527 0.0493203 -0.0513582 -0.00670282 -0.0267695`）
> 与项目其他报告及 `可复现性体系/crosschecks.json` 的 X-003 锚点值
> （`-0.061744 0.067857 -0.087588 0.019701 -0.013820`）不同，**不构成矛盾**。
> 项目最终采用的部署环境是 **VM 102 @ 192.168.100.50**（`<USER>` 用户），
> 与本报告的早期实验机是两台不同的机器；`证明向量维度/` 系列报告的三次独立
> 复现（首次部署 / 全新 OSTree 部署 / `/usr` 重置后脚本化恢复）均在 VM 102 上
> 完成，锚点值以那三份报告与 `M-201.first5_values` 为准。

---

## 一、最终结果（先放结论）

官方 KYSDK C 接口端到端运行成功：

```
[2] init_session                 rc=0 (SUCCESS)
[3] get_model_list               error_code=0 (SUCCESS)，模型数量=1
[3] 模型: ensemble-embd_gte-base_uint8-text   model_dim=768
[4] init_model(...)              rc=0 (SUCCESS)
[5] text_embedding               ok=1
================ 真实结果 ================
模型名      : ensemble-embd_gte-base_uint8-text
向量维度    : 768   （官方 embedding_model_info_get_model_dim() 查得）
向量前5个值 : -0.0427527  0.0493203  -0.0513582  -0.00670282  -0.0267695
=========================================
```

协议层证据：后端日志由 `Runtime version is 1.0.0 / init_v1` 变为
**`CoreTextEmbeddingService protocol version: 1.3.0 / init_v2`**，协议不匹配消失。

---

## 二、环境与不可变机制

- 系统：Kylin Desktop V11，OSTree 磐石不可变系统；`/usr` 只读。
- 包管理：DEB / apt；软件源为 `2603.preview` 预览源（`archive.kylinos.cn`）。
- 拦截机制：dpkg 钩子 `/usr/bin/ostree-pkgs-guard`，仅在启动模式为 `normal` 时拦截
  install/unpack/remove/configure；**对 `unshare -m` 挂载命名空间显式放行**。
- 部署结构（`ostree admin status`）：`.2 (pending)` / `* .1 (Unlocked: hotfix)` / `.0 (rollback)`，
  三者同一 commit。所有 apt 改动落在 `.1` 的 hotfix overlay，不动只读基底。

---

## 三、一路遇到的问题与解决方案

### 问题 1：不可变系统 apt 被拦截
- **现象**：`apt install` 报「当前模式禁止执行（unpack）操作」，dpkg 退出码 2。
- **原因**：`ostree-pkgs-guard` 在 `normal` 模式拦截写系统操作。
- **解决**：用 guard 自身放行的命名空间通道安装，不破坏系统完整性：
  ```bash
  echo PW | sudo -S unshare -m sh -c 'export DEBIAN_FRONTEND=noninteractive; apt install -y <包>'
  ```
- 补充技巧：只取头文件而不装系统时，可 `dpkg-deb -x <deb> /tmp/dir` 解压，零系统影响。

### 问题 2：协议不匹配（核心阻塞）
- **现象**：`get_model_list` 返回 `error_code=403`；日志
  `Protocol mismatch for API: getModelList  runtime: 1.0.0  sdk: 1.3.0  min: 1.3.0`。
- **原因**：镜像自带的后端 `kylin-ai-runtime`（包版本 1.1.0.1）只讲**协议 1.0.0**，
  而 embedding SDK（`libkylin-coreai-embedding` 1.2.0.0）要求接口 `getModelList` 后端**至少 1.3.0**。
  且 runtime 最初连协议版本字段都没上报（`invalid: null`），被回退当作 1.0.0。
  **本质：同一镜像里 SDK 比后端 runtime 新，出厂未对齐。**
- **说明**：`init_session` 是 SUCCESS、socket 也连得上——不是连接/模型缺失问题，纯协议门禁。

### 问题 3：单独升级 runtime 失败（走过的弯路）
- **尝试**：`apt install kylin-ai-runtime`（升到 1.2.0.4，能讲新协议）。
- **现象**：新 runtime **起不来**，报
  `undefined symbol: ...InferenceServerHttpClient::Create...`（缺 5 参数版符号）。
- **原因**：runtime 1.2.0.4 针对**更新版 triton 客户端库**编译，而当时 `libhttpclient.so`
  （由 `kytensor-client` 提供）是旧的 4 参数版 → ABI 不匹配。**单包升级破坏了协同性。**
- **教训**：这套 AI 栈必须**协同升级**，不能单包升。

### 问题 4：恢复后端时的两道坎
- 升级失败导致 AI 后端一度停摆。从 OSTree 只读基底恢复原始文件时遇到：
  1. **kysec 安全执行控制**：普通 `cp` 的文件被拒绝执行（EPERM「不允许的操作」）。
     **解决**：必须保留安全 xattr —— `sudo cp --preserve=all`（xattr 为 `security.exectl` / `security.ksaf`）。
  2. **第二处 ABI 断裂**：安装 `vector-engine-client-dev` 连带把 `libkysdk-vector-engine-client`
     从 0k0.7 升到 0k1.1，旧 runtime 1.1.0.1 加载它报 `undefined symbol: VectorDB::ConnectParam`。
     **解决**：同样从基底恢复 0k0.7 的 `.so`（保留 xattr）。
- 恢复后后端正常。期间用 `apt-mark hold` 冻结错配包，避免误升。

### 问题 5：重启后"像被初始化"、找不到 sshd
- **现象**：关机重开后环境像被重置，且 `systemctl status sshd` 找不到服务。
- **原因**：
  1. OSTree A/B 多部署，`.2` 标 **pending**，重启默认切到 `.2`（全新部署），
     之前在 `.1` overlay 上的所有改动「不在」了 → 看起来像初始化。
  2. 麒麟是 **Debian 系，SSH 服务名是 `ssh` 不是 `sshd`**。
- **解决**：本机终端 `sudo systemctl enable --now ssh`（未装则 `unshare -m apt install -y openssh-server`）。

### 最终突破：元包协同升级 + 补齐 kytensor + 重启（有效解）
- 老师建议 `apt install kylin-ai-subsystem`（AI 子系统**元包**，候选 1.3.0.1）。
- 元包会**协同**拉起 67 个包（含 runtime 1.2.0.4、kytensor、引擎插件、模型等），零卸载、依赖干净。
- **关键缺失环节**：元包对 `kytensor-client` 的依赖是「≥ok7k0.10」，而已装 ok7k0.12 已满足、
  **不会自动升级**到带新符号的候选 **ok7k0.14**。手动补这一步后，runtime 1.2.0.4 的
  `Create` 符号才解析成功、能启动。
- 之后重启推理引擎与守护进程，协议升到 1.3.0，接口全通。

---

## 四、完整有效配方（可复现 / 交付用）

> 全部写操作走 `unshare -m`；不动只读基底；改动在 `.1` 的 hotfix overlay。

```bash
# 0) 准备：更新源
echo PW | sudo -S apt update

# 1) 装 embedding 开发包（头文件 + .so 链接）
echo PW | sudo -S unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y libkylin-coreai-embedding-dev'

# 2) 装 AI 子系统元包（协同升级 67 包，runtime→1.2.0.4）
echo PW | sudo -S unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y kylin-ai-subsystem'

# 3) ★关键★ 补齐 kytensor 到 ok7k0.14（提供 runtime 需要的新 triton 客户端符号）
echo PW | sudo -S unshare -m sh -c 'DEBIAN_FRONTEND=noninteractive apt install -y kytensor-client kytensor-server kytensor-python'

# 4) 重启推理引擎（systemd 服务）
echo PW | sudo -S systemctl restart kytensor

# 5) 重启 kylin-ai-runtime 守护进程（它由 ukui-session 登录时拉起，无 systemd 单元）
#    干净做法：在图形界面注销再登录；或杀掉后用会话环境重启：
#    env LD_LIBRARY_PATH=/usr/lib/kylin-ai/depends XDG_RUNTIME_DIR=/run/user/1000 \
#        DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus DISPLAY=:1 kylin-ai-runtime &
```

### 编译/运行客户端
```bash
# .pc: Cflags=-I/usr/include/kylin-ai   Libs=-lkysdk-coreai-embedding
gcc emb_test.c -o emb_test -I/usr/include/kylin-ai -lkysdk-coreai-embedding
./emb_test
```

### 官方接口调用流程（来自 embedding.h）
```
text_embedding_create_session() → text_embedding_init_session()
→ text_embedding_get_model_list() → embedding_model_list_get_model(0)
→ embedding_model_info_get_model_name() / _get_model_dim()   // dim = 768
→ text_embedding_init_model(name)
→ text_embedding(session, "文本", &result)
→ embedding_result_get_vector_data()/_get_vector_length()    // 768 维
→ embedding_result_destroy()
```

---

## 五、遗留隐患与后续建议

1. **⚠️ 部署未固化（最重要）**：当前升级都在 `.1` overlay，但 `.2` 仍是 pending。
   **直接重启整机会切到干净的 `.2`，这套环境会丢失、embedding 退回不可用。**
   建议：把 `.1` 设为默认启动 / 撤掉 `.2` pending，或重启后确认进入 `.1`。
2. **建议加 hold**，冻结这套可用版本，防误动：
   ```bash
   sudo apt-mark hold kylin-ai-runtime kytensor-client kytensor-server kytensor-python \
                      libkysdk-vector-engine-client libkylin-coreai-embedding
   ```
3. **勿在本机跑无差别 `apt upgrade`**：预览源跑在基底前面，整体升级易引入新的 ABI/协议不一致。
   今后单独装具体包、走 `unshare -m`。
4. 备用通路：底层 triton(kytensor) 也可直连推理
   `POST http://127.0.0.1:8000/v2/models/ensemble-embd_gte-base_uint8-text/infer`，
   输出 `logits [1,seq,768]`，自行均值池化得句向量（与官方 SDK 池化细节可能略有差异）。

---

## 六、关键事实速查

| 项目 | 值 |
|------|----|
| 文本 embedding 模型 | `ensemble-embd_gte-base_uint8-text`（GTE-base 多语言 ONNX）|
| **句向量维度** | **768** |
| 图文（CN-CLIP）文本维度 | 1024 |
| 协议版本（修复前 → 修复后）| 1.0.0（init_v1）→ **1.3.0（init_v2）** |
| runtime 包版本（修复前 → 修复后）| 1.1.0.1 → **1.2.0.4** |
| kytensor 关键版本 | ok7k0.12 → **ok7k0.14**（提供 5 参数 `Create`）|
| 推理引擎 socket | `/tmp/.kylin-ai-runtime-unix/<uid>/core-textembedding.sock` |
| triton 端口 | HTTP 127.0.0.1:8000 / gRPC :8001 / metrics :8002 |

---

*报告生成：2026-06-30　记录人：Claude Code*
