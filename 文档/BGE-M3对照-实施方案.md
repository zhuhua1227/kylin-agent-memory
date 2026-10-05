# BGE-M3 对照实验 · 实施方案

> **交给谁**：实现 agent。
> **时间盒**：1 天。
> **依据**：赛题直播 PPT slide 24 Q3 ——「**必须使用指定接口。但可以在对比实验中加入
> BGE-M3 作为对照组进行性能与效果对比。**」

---

## 〇、这是什么 / 不是什么

```
✅ 是   在【完全相同的语料、问题、检索数学】下，把系统 Embedding 接口与 BGE-M3 并排测一次
❌ 不是 用 BGE-M3 替换系统 Embedding
```

**系统的 `kylin-coreai-embedding` 仍是且只能是本作品的生产路径**（slide 13 硬性规定，
不满足视为 0 分）。BGE-M3 只进对照实验，**不得进入任何生产代码路径、不得写入
`rag_knowledge*` 集合、不得改动 `search_knowledge.py` 的默认行为**。

---

## 一、不许动的东西

```
① 8 个 .gitattributes 标 binary 的文件 —— 字节不得变（评测集 3 个 + 语料 3 个 + *.tsv + *.npy）
② eval_set.jsonl / eval_set_frozen.json  —— 227 题一个字不改
③ 语料 86 条（cases/workflows/templates）—— 一条不增不减
④ render_units.py                        —— 冻结，不动
⑤ search_knowledge.py / vector_store.py 的默认行为 —— 不动
```

**BGE 的一切产物写进新文件、新目录 `BGE-M3对照/`，绝不覆盖既有文件。**

---

## 二、实验设计

### 2.1 公平性由 sha256 保证，不靠"我记得是一样的"

`评测集/eval_set.jsonl` 每条带 `source_rendered`（被嵌入的那段文本原文）与
`source_rendered_sha256`。**两侧必须嵌入逐字节相同的字符串**，且这件事可机器验证：

```
对每一条被嵌入的文本：sha256(text) == 该 doc 的 source_rendered_sha256
不相等 → 停下报告，不要"顺手修一下"
```

### 2.2 为什么走 `.npy` 而不是向量库

系统 Embedding 是 **768 维**（见 `知识检索/make_vectors.py`），BGE-M3 是 **1024 维**，
进不了同一个集合。所以两侧都固化成 `.npy`，用**同一份纯 Python 检索代码**算：

```
docs_kylin.npy   (86, 768)      queries_kylin.npy   (227, 768)
docs_bge.npy     (86, 1024)     queries_bge.npy     (227, 1024)
        ↓ 同一个 retrieve.py（余弦、同 top-k、同并列打破规则）
    R@1 / R@3 / R@5 / R@10
```

**附带收益**：BGE 那一侧完全不依赖 KYSDK，**评委在任何机器上都能复现**（L1 级）。

### 2.3 🔴 已知答案自检（这一步不过就不要看 BGE 的数）

Kylin 那侧的 `naive` 档**部分**已锚定。**逐条核过 `claims.json` 后的实际状况**：

| 指标 | 锚定情况 | 权威值 |
|---|---|---|
| **R@1** | ✅ `C-265` 独立主张 | **0.5242**（52.4%） |
| R@3 | ❌ **无任何主张** | —— |
| R@5 | ⚠️ 只在 `C-265` 的 `note` 里提及，非独立主张 | 0.8238（82.4%） |
| **R@10** | ✅ `C-266` 独立主张 | **0.8634**（86.3%，196/227） |

**所以已知答案自检只能用 R@1 与 R@10 这两个**——它们是有主张、有 anchor、
`reproduce_L1.py` 会真去核的。

```
R@5   参考 C-265 的 note，对不上先查，但它不是硬门
R@3   没有出处 —— 【算出来是多少就是多少】，不要拿任何"记得是 76.2"之类的数去对
```

⚠️ 本项目此前发生过一次「凭印象写 R@k、把跨版本对照表的列混掉」的错误
（写出 R@5 > R@10 这种不可能的组合）。**任何 R@k 落纸前，先回 `claims.json` 查有没有主张，
再查单调性。**

所以：**先用新写的 `retrieve.py` 跑 Kylin 向量，看 R@1 与 R@10 能不能逐位复现出上表两个锚定值。**

```
复现得上  → 检索代码是对的，BGE 那侧的数才可信
复现不上  → 是新代码错了，不是 BGE 的问题。停下修，不要继续
```

**写任何 R@k 之前先检查单调性**：R@1 ≤ R@3 ≤ R@5 ≤ R@10。不单调就是算错了。

### 2.4 对比在哪个档上做

```
主对比   naive（纯向量检索）   ← 只有它能隔离出 embedding 本身的差异
附报     系统档                 端到端影响（元数据先验/MMR/BM25 会稀释 embedding 的贡献）
```

**headline 用 naive。**

### 2.5 配对比较，不是两个独立区间

227 题两侧完全相同 → **McNemar**（配对）。

❌ **不许**把两侧的 Wilson 区间并排画然后说"区间重叠所以没差异"——这是方法错误，
本项目此前已明确过一次。

**且必须写明**：227 题只覆盖 **77 个不同的 gold doc**（约 2.9 题/文档），
**有效样本量远小于 227**，Wilson 区间偏乐观。

---

## 三、三个会静默毁掉这次对比的坑

这三条任何一条错了，得出的差异都是假的，而且**不会报错**。

### 3.1 归一化

先查 Kylin 向量是否已 L2 归一化（算一批向量的模长看是不是 ≈1）。

**两侧都在算余弦前显式 L2 归一化**，并在报告里写明做了这件事。
不要假设任何一侧已经归一化过。

### 3.2 BGE-M3 只用 dense

BGE-M3 有 **dense / sparse / colbert** 三种输出。**只用 dense。**

用多向量混合 = 拿一个集成方法去比一个单一 dense 编码器，**比的不是同一种东西**。

```python
model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
```

### 3.3 截断长度

BGE-M3 支持 8192 token，系统 Embedding 的上限大概率远小于此（**先实测，别猜**）。

**先统计 86 条渲染文本的长度分布**，数清楚有几条超过系统 Embedding 的上限：

```
一条都不超   → 无此问题，正常做，报告里写明"无截断"
有超的       → 两个口径都报：
               ① 同输入：两侧都截到系统 Embedding 的上限   ← headline（隔离编码器）
               ② 原生：各用各的上限                        ← 附报（衡量实际部署）
```

**不写截断统计就出结论 = 不知道自己在比什么。**

### 3.4 顺带：BGE-M3 不需要指令前缀

BGE-v1.5 系列需要 `"为这个句子生成表示以用于检索文章："` 前缀，**BGE-M3 不需要**。
加了反而掉点。查一下当前 `FlagEmbedding` 版本的文档确认，并在报告里写明用没用前缀。

---

## 四、执行步骤

### 4.1 在哪跑

```
Kylin 向量   必须在麒麟机（device_c）上出 —— 只有那儿有 kylin-coreai-embedding
BGE 向量     Ubuntu VM 100.3 的 /data，或笔记本 GPU
             ⚠️ 模型约 2.3 GB，100.3 系统盘池 98% 满，必须落 /data
检索与计分   纯 numpy，哪儿都行
```

### 4.2 顺序

```
① 导出 86 条渲染文本 + 227 条 query 到 texts.jsonl（带 sha256）
② 麒麟侧：调 embedding 服务 → docs_kylin.npy / queries_kylin.npy
③ 🔴 用 retrieve.py 跑 Kylin 向量，核对 §2.3 的已知答案。不过不继续
④ 装 FlagEmbedding，下 BAAI/bge-m3 到 /data
⑤ BGE 侧：同一份 texts.jsonl → docs_bge.npy / queries_bge.npy（逐条校 sha256）
⑥ 同一份 retrieve.py 算两侧 R@1/3/5/10
⑦ McNemar（配对，227 题）
⑧ 写报告 + 接 claims
```

### 4.3 产出目录

```
BGE-M3对照/
  texts.jsonl              两侧共用的输入（带 sha256）
  make_vectors_bge.py      BGE 侧向量生成
  retrieve.py              两侧共用的检索与计分（纯 numpy）
  docs_kylin.npy  queries_kylin.npy
  docs_bge.npy    queries_bge.npy
  results.md               三张表：主对比 / 截断口径 / McNemar
  说明报告.md              见 §六
```

---

## 五、🔴 跑前预登记（写完再跑，不许跑完再写）

在 `BGE-M3对照/预登记.md` 里**先写死**三种结果各自怎么解释：

| 结果 | 怎么解释 | 怎么写 |
|---|---|---|
| **BGE 明显更好** | 系统 Embedding 是当前检索精度的瓶颈之一 | 如实报，作为赛题要求的「明确优化方向」。**合规上仍必须用系统接口**，这不影响作品成立 |
| **基本持平** | 系统 Embedding 已达开源 SOTA 水平 | 正面结论，可直接支撑「用系统接口不牺牲效果」 |
| **系统 Embedding 更好** | 先别急着下结论 | **回头逐条查 §三 三个坑**（归一化/dense-only/截断）确认不是 BGE 用法错了，再写 |

**第三种要格外小心**：一个开源 SOTA 模型输给系统接口，先验上更可能是我们用错了，
而不是真的赢了。**"结果对我们有利"恰恰是最该自查的时候。**

---

## 六、报告要写到的六件事

`BGE-M3对照/说明报告.md`：

```
1. 定位：对照实验，不是替换方案；系统 Embedding 仍是唯一生产路径（引 slide 13 + slide 24 Q3）
2. 公平性怎么保证的：同一份 texts.jsonl、sha256 逐条校验、同一份 retrieve.py
3. 已知答案自检：Kylin 侧逐位复现 C-265 (R@1) 与 C-266 (R@10)（给出两组数对照）；
   并说明 R@3 在 claims.json 中无主张、本次为新增测量
4. 三个坑各自怎么处理的：归一化做了、只用 dense、截断统计（含超限条数）
5. 结果：主对比表 + McNemar 不一致对的条数与方向
6. 局限：227 题只覆盖 77 个 gold doc，有效样本量远小于 227；单一语料、单一领域，
   不构成对 BGE-M3 通用能力的评价
```

---

## 七、接可复现性体系

```
① claims.json 按 8 字段 schema 补主张（anchor 指向本次 commit）：
   - BGE 侧 R@1/3/5/10
   - Kylin 侧复现值（与 **C-265 / C-266** 逐位一致）
   - McNemar 不一致对条数
② 判定 L1 / L2：
   BGE 侧与 retrieve.py 全部是纯 Python + numpy → L1（评委可复现）
   Kylin 向量生成需 KYSDK → L2
③ generate_manifest.py 重算
④ reproduce_L1.py --check-only 不得新增失败
```

---

## 八、验收

- [ ] 生产路径未被触碰：`search_knowledge.py` / `vector_store.py` / `render_units.py` 无改动
- [ ] 8 个 binary 文件 sha256 与改动前一致
- [ ] 两侧嵌入文本的 sha256 **逐条**匹配 `source_rendered_sha256`（不是抽查）
- [ ] §2.3 已知答案自检通过：Kylin 侧逐位复现 **C-265 (R@1=0.5242)** 与 **C-266 (R@10=0.8634)**
- [ ] R@3 未与任何未经核实的数字对照（它在 claims.json 里没有主张）
- [ ] R@1 ≤ R@3 ≤ R@5 ≤ R@10（两侧都查）
- [ ] 归一化：两侧都显式 L2，报告写明
- [ ] BGE 仅用 dense（`return_sparse=False, return_colbert_vecs=False`）
- [ ] 截断统计已出；若有超限条，两个口径都报
- [ ] 是否用指令前缀已确认并写明
- [ ] McNemar 已算，且**没有**用两个独立 Wilson 区间下"无差异"结论
- [ ] 「227 题覆盖 77 个 gold doc」这条局限已写进报告
- [ ] `预登记.md` 的时间戳早于结果文件
- [ ] claims 已补、manifest 已重算、`reproduce_L1.py --check-only` 无新增失败

---

## 九、不要做的事

- ❌ 不要让 BGE-M3 进入任何生产路径或默认配置
- ❌ 不要改 `render_units.py`、评测集、语料
- ❌ 不要覆盖任何既有 `.npy` / `.tsv`
- ❌ 不要用 BGE 的 sparse / colbert 向量参与主对比
- ❌ 不要在两侧用不同的归一化、不同的 top-k、不同的并列打破规则
- ❌ 不要跳过 §2.3 的已知答案自检直接看 BGE 的数
- ❌ 不要拿两个独立 Wilson 区间说"没有显著差异"
- ❌ 不要因为结果对我们有利就不查 §三 那三个坑
- ❌ 若 Kylin 侧复现不出锚定值，不要调参数凑——停下报告
