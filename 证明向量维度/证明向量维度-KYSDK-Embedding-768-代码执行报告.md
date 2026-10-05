# 银河麒麟 V11 KYSDK 文本 Embedding —— 向量维度 768 证明与代码执行报告

> **目标机**：VM 102 @ `192.168.100.50`，银河麒麟桌面操作系统 V11（2603，磐石 OSTree 不可变系统，内核 6.6，x86_64）
> **模型**：`ensemble-embd_gte-base_uint8-text`（GTE-base 多语言 ONNX）
> **结论**：调用**官方 KYSDK C 接口**（`libkylin-coreai-embedding`），端到端真实输出 **768 维**句向量；且"模型自身声明维度"与"实际输出向量长度"两处独立证据**一致为 768**。
> **执行日期**：2026-07-15（首次）；2026-08-01（全新部署复现）；2026-08-03（脚本化恢复复现）　　详见第十一节
> **记录**：麒麟 RAG 项目组

---

## 一、结论先行

在银河麒麟 V11 桌面系统上，编写 C 程序调用官方文本向量化 SDK，成功将一段中文文本向量化，程序打印：

```
向量维度 : 768
```

该维度值**并非人工假设，而是由官方接口 `embedding_result_get_vector_length()` 在运行时返回**，属权威来源。至此，"银河麒麟 embedding SDK 输出 768 维句向量"这一事实得到端到端验证。

---

## 二、证明目标与思路

**目标**：证明题目要求调用的"银河麒麟国产桌面操作系统 embedding SDK"，其文本向量化输出的向量维度为 **768**。

**为什么需要"证明"而不是"查文档"**：文档标称值不等于真机实测值。只有让官方 SDK 在真机上**真实跑出一条向量、并由官方接口报告其长度**，这个 768 才是可信、可复现、可写进测试报告的硬证据。

**证明思路（双重独立证据）**：

| 证据 | 来源接口 | 含义 |
|------|----------|------|
| 证据①：模型声明维度 | `embedding_model_info_get_model_dim()` | 模型元数据自报的输出维度 = **768** |
| 证据②：实际输出长度 | `embedding_result_get_vector_length()` | 真实向量化一句话后，返回的向量数组长度 = **768** |

两条来自不同环节的独立数据**互相印证、均为 768**，排除偶然与误读，构成完整证据链。本报告的代码聚焦证据②（端到端实测），证据①在部署阶段已由 `get_model_dim()` 查得并记录。

---

## 三、测试环境与关键组件版本

| 项目 | 值 |
|------|----|
| 操作系统 | 银河麒麟桌面 V11（2603，磐石不可变系统） |
| 文本 embedding 模型 | `ensemble-embd_gte-base_uint8-text`（GTE-base 多语言） |
| **句向量维度** | **768** |
| embedding SDK 包 | `libkylin-coreai-embedding` / `-dev`　1.2.0.0 |
| 推理运行时 `kylin-ai-runtime` | 1.2.0.4 |
| 底层推理引擎 `kytensor` | ok7k0.14 |
| SDK↔后端协议版本 | **1.3.0（init_v2）** |
| 推理引擎 socket | `/tmp/.kylin-ai-runtime-unix/1000/core-textembedding.sock` |
| SDK 头文件 | `/usr/include/kylin-ai/coreai/embedding/embedding.h` |

---

## 四、证明代码 `emb_test.c`

完整源码见同目录 `emb_test.c`（带逐行注释）。核心调用流程严格依据官方头文件 `embedding.h`：

```c
#include <stdio.h>
#include <stdbool.h>
#include "embedding.h"

int main(void) {
    // ① 创建会话（获取 SDK 会话句柄）
    TextEmbeddingSession *session = text_embedding_create_session();
    if (session == NULL) { printf("创建 session 失败\n"); return 1; }

    // ② 初始化会话（返回 0 = 成功）
    int rc = text_embedding_init_session(session);
    if (rc != 0) { printf("init_session 失败，错误码=%d\n", rc); return 1; }

    // ③ 加载指定模型
    const char *model_name = "ensemble-embd_gte-base_uint8-text";
    rc = text_embedding_init_model(session, model_name);
    if (rc != 0) { printf("init_model 失败，错误码=%d\n", rc); return 1; }

    // ④ 将一句中文文本向量化（result 出参，先置空再传 &result）
    EmbeddingResult *result = NULL;
    bool ok = text_embedding(session, "怎么重启网络服务", &result);
    if (!ok || result == NULL) { printf("text_embedding 调用失败\n"); return 1; }

    // ⑤ 先检查向量化本身是否出错（协议不匹配等错误会在此暴露）
    int err = embedding_result_get_error_code(result);
    if (err != 0) {
        printf("向量化出错，错误码=%d，信息=%s\n",
               err, embedding_result_get_error_message(result));
        return 1;
    }

    // ⑥ ★核心证据★ 由官方接口读取"向量维度"与"向量数据"
    int dim = embedding_result_get_vector_length(result);   // 权威维度来源
    float *vec = embedding_result_get_vector_data(result);
    printf("=============== 真实结果 ===============\n");
    printf("模型名   : %s\n", model_name);
    printf("向量维度 : %d\n", dim);
    printf("前 5 个值: %f  %f  %f  %f  %f\n", vec[0], vec[1], vec[2], vec[3], vec[4]);
    printf("=======================================\n");

    // ⑦ 释放资源
    embedding_result_destroy(&result);
    text_embedding_destroy_session(&session);
    return 0;
}
```

**API 调用序列**（对应上面 ①~⑦）：
`create_session → init_session → init_model → text_embedding → get_error_code → get_vector_length / get_vector_data → destroy`。

---

## 五、编译与运行

**编译命令**（`-I` 指向 SDK 头文件目录，`-l` 链接官方库）：

```bash
gcc ~/emb_test.c -o ~/emb_test \
    -I/usr/include/kylin-ai/coreai/embedding \
    -lkysdk-coreai-embedding
```

**运行命令**：

```bash
~/emb_test
```

---

## 六、执行结果（真机原始输出）

```
[1784115289821] info  Connection established successfully to  unix:path=/tmp/.kylin-ai-runtime-unix/1000/core-textembedding.sock
[1784115289821] info  Runtime version is  1.3.0 InitEngine call new init_v2
[1784115289901] info  Proxy init success,sessionId is: 1432500288
[1784115289902] info  Proxy init model success,model: ensemble-embd_gte-base_uint8-text sessionId: 1432500288
=============== 真实结果 ===============
模型名   : ensemble-embd_gte-base_uint8-text
向量维度 : 768
前 5 个值: -0.061744  0.067857  -0.087588  0.019701  -0.013820
=======================================
```

---

## 七、结果分析：为什么这能"证明"768 维

逐行拆解上面的输出，每一句都是一环证据：

1. **`Connection established ... core-textembedding.sock`**
   程序通过本地 socket 成功连上官方推理后端 —— 证明走的是**官方 SDK 通路**，而非自造实现。

2. **`Runtime version is 1.3.0 ... init_v2`**
   SDK 与后端完成**协议 1.3.0** 握手（init_v2）。这一点尤为关键：麒麟出厂镜像存在"SDK 比后端新、协议 1.0.0↔1.3.0 不匹配"的问题（详见第八节），协议成功升到 1.3.0 才使 `text_embedding()` 得以正常返回结果。

3. **`Proxy init model success, model: ensemble-embd_gte-base_uint8-text`**
   官方 GTE-base 模型加载成功。

4. **`向量维度 : 768`**
   **核心证据**。该数字由官方接口 `embedding_result_get_vector_length()` 返回，即真实向量数组的元素个数 = 768。与部署阶段 `embedding_model_info_get_model_dim()` 查得的模型声明维度 768 **完全一致**（双重印证）。

5. **`前 5 个值: -0.061744 ...`**
   打印真实的浮点向量数据，证明拿到的是**有效的稠密向量**（而非空/占位），768 维名副其实。

**结论**：768 维这一结果来源权威（官方 API 实测）、证据双重（模型声明 + 实际输出一致）、过程可复现，成立。

---

## 八、达标前提：不可变系统上的关键适配（踩坑纪要）

要在磐石不可变系统上让上述程序真正跑通 768，须先跨过若干适配障碍。这既是本证明的前提，也是"适配测试报告"的一手素材：

1. **不可变系统写保护**：`/usr` 只读、dpkg 钩子 `ostree-pkgs-guard` 拦截安装。经 `ostree admin unlock --hotfix` 打开可写覆盖层后方可安装 SDK 开发包。
2. **SDK↔后端协议不匹配（核心阻塞）**：出厂 `kylin-ai-runtime` 为 1.1.0.1（仅讲协议 1.0.0），而 embedding SDK 1.2.0.0 要求后端协议≥1.3.0，接口报 403。经安装 AI 子系统元包 `kylin-ai-subsystem`（协同升级 runtime 至 1.2.0.4）解决。
3. **组件需协同升级**：元包不会自动补齐 `kytensor`；须手动将 `kytensor-client/server/python` 升到 **ok7k0.14**，为新 runtime 提供所需符号，服务方能启动、协议升至 1.3.0。
4. **kysec 安全执行控制**：自编译二进制默认被拒执行（EPERM）。将执行控制临时置为放行（`setstatus -f exectl off`）后可运行，验证完毕恢复 `warning`。

以上适配全部完成后，`emb_test` 才输出第六节的结果。

---

## 九、复现步骤

```bash
# 0) 前提：已按第八节完成不可变系统适配、协议升至 1.3.0、后端服务在运行
# 1) 编写 emb_test.c（见同目录源码）
# 2) 编译
gcc ~/emb_test.c -o ~/emb_test -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
# 3) （如遇 EPERM）临时放行执行控制
sudo setstatus -f exectl off
# 4) 运行，观察输出中的 "向量维度 : 768"
~/emb_test
# 5) 恢复安全策略
sudo setstatus -f exectl warning
```

---

## 十、结论

在银河麒麟 V11 桌面系统上，通过官方 KYSDK C 接口对中文文本完成向量化，程序由官方接口 `embedding_result_get_vector_length()` 实测报告向量维度为 **768**，并与模型元数据声明维度 `embedding_model_info_get_model_dim()` = 768 相互印证。证据链完整、来源权威、过程可复现。

**本报告证明：银河麒麟 embedding SDK（模型 `ensemble-embd_gte-base_uint8-text`）输出的句向量维度为 768。** 该结论为后续 RAG 向量库设计（768 维索引）与检索管线提供了确定的基础参数。

---

## 十一、独立复现记录（第二次，2026-08-01）

首次验证环境（2026-07-15，见第六节）后经历一次非计划的整机重启，磐石不可变系统的 `/usr` 覆盖层内容随之失效（详见《前置步骤-不可变系统解锁与KYSDK部署命令手册》第 7 节持久化说明）。为验证第一节的证明结论并非偶然、且方法可在独立环境中复现，于 2026-08-01 在**全新的 OSTree 部署**（`ostree admin deploy` 新建，与首次验证时物理位置相同、但为全新的干净系统副本）上完整重走部署流程，再次编译运行 `emb_test`，真机原始输出：

```
[...] Runtime version is  1.3.0 InitEngine call new init_v2
[...] Proxy init success,sessionId is: 1555151671
[...] Proxy init model success,model: ensemble-embd_gte-base_uint8-text sessionId: 1555151671
=============== 真实结果 ===============
模型名   : ensemble-embd_gte-base_uint8-text
向量维度 : 768
前 5 个值: -0.061744  0.067857  -0.087588  0.019701  -0.013820
=======================================
```

**关键验证点**：前 5 个向量值与 2026-07-15 首次运行结果**逐位完全一致**（`-0.061744 0.067857 -0.087588 0.019701 -0.013820`）。由于 embedding 模型推理为确定性计算（相同输入、相同模型权重），两次独立环境下对同一输入文本得到逐位相同的输出，构成了比"维度数字相同"更强的证据——**证明两次运行调用的是同一套官方模型与推理逻辑，结果真实可信，而非巧合或占位数据**。

本次复现过程中额外发现两处此前未记录的深层问题（不影响 768 这一结论，但属于不可变系统工程适配的重要经验，已补充进部署手册）：
1. AI 子系统元包在 dpkg 数据库记录满足版本要求时，**不会真正重装其全部依赖**，需改用递归依赖清单批量强制重装（手册 4.5 节）；
2. 模型包 `kylin-gte-base-model` 的出厂旧版元数据文件缺少 `others_info`/`channel_info` 字段，是导致 `init_model` 报错的真正根因（手册 4.6 节）。

详细排查过程与修复命令见同目录《前置步骤-不可变系统解锁与KYSDK部署命令手册》。

---

## 十二、独立复现记录（第三次，2026-08-03）—— 脚本化恢复验证

### 12.1 本次复现的背景与特殊意义

2026-08-01 验证完成后，目标机先后经历了**家庭电路跳闸导致的整机硬断电**和**正常重启**两种情况，`/usr` 覆盖层内容均告失效。经三轮实证（含从 PVE 快照备份恢复的尝试），最终确认：**磐石不可变系统上 `/usr` 的改动无法跨重启保留，这是系统设计使然，而非操作失误**（完整结论见部署手册第 7 节）。

据此，本项目将部署流程完全脚本化，产出一键恢复脚本 `restore_env.sh`（见同目录）。**本次复现的目的，不仅是再次验证 768 这一结论，更是验证"脚本化恢复方案"本身是否真正可用**——即：在一台 `/usr` 已被重置为出厂状态的机器上，能否仅凭一条命令，在可接受的时间内完整恢复 embedding 能力。

### 12.2 复现过程与真机输出

在 `/usr` 已完全重置（`emb_test` 报 `undefined symbol`、`sshd` 二进制不存在）的状态下，运行：

```bash
bash ~/restore_env.sh
```

脚本自动完成解锁、依赖修复、SDK 全套重装、模型元数据修复、后端服务重启，全程约 5~10 分钟。随后运行 `emb_test`，真机原始输出：

```
[1785728256817] info  Proxy init model success,model: ensemble-embd_gte-base_uint8-text sessionId: 1359561142
=============== 真实结果 ===============
模型名   : ensemble-embd_gte-base_uint8-text
向量维度 : 768
前 5 个值: -0.061744  0.067857  -0.087588  0.019701  -0.013820
=======================================
```

### 12.3 结论

**前 5 个向量值与 2026-07-15、2026-08-01 两次运行结果逐位完全一致**（`-0.061744 0.067857 -0.087588 0.019701 -0.013820`）。至此，该结论已在**三次相互独立的环境状态下**（首次部署、全新 OSTree 部署、`/usr` 重置后的脚本化恢复）得到一致验证。

本次复现同时证明了两件事：

1. **768 维这一结论稳定可靠**——跨越三次独立部署、两种重启方式，官方 SDK 对同一输入始终返回逐位相同的 768 维向量；
2. **脚本化恢复方案切实可用**——面对不可变系统"无法持久化"这一硬约束，本项目给出的工程解法（完全脚本化、可复现的部署流程）已通过实战验证，可在数分钟内从零恢复完整的 embedding 运行环境。这一点对项目的实际意义在于：**团队不再需要依赖"装好后不敢重启"的脆弱状态，环境随时可重建**，也为答辩中"面向不可变操作系统的端侧部署适配"这一议题提供了完整的实证链条。
