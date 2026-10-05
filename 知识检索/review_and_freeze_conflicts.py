#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_and_freeze_conflicts.py —— 人工（Claude Code）复核 mine_conflicts.py
产出的候选池，逐条手写裁决，冻结成知识冲突处理的评测集。

═══ 为什么裁决必须手写在这里，不能让 adjudicate() 自己标自己 ═══════════

`知识冲突处理-设计方案.md` §三讲得很清楚：**规则裁决器 `mine_conflicts.
adjudicate()` 不能是它自己评测集的来源**——不然"裁决正确率 88%"这个数字
就是"用规则去对规则自己产出的答案"，跟 E-1 评测集踩过的"出题方绑架指标"
是同一个坑。所以这份评测集的裁决**不调用 `adjudicate()`**，是逐条通读
`conflicts.jsonl` 里的原始证据后手写的决定——与 `评测集/review_and_
freeze.py` 的 `REMOVALS` 是同一个模式：批量脚本，不是交互式 CLI，
理由写进源码接受审计。

═══ ⚠️ 必须诚实披露的一点：裁决者是谁 ═══════════════════════════════

`ADJUDICATIONS` 里的判断由 Claude Code（本次会话）逐条通读 conflicts.
jsonl 的原始证据后写出，**用户没有逐条复核**。这与设计方案理想中的
"人工裁决"有真实差距，必须写进报告不回避（见《知识冲突处理说明报告.md》
"自指声明"一节）。但它与"L2 LLM 自动裁决"有本质不同，不是同一类风险：

  · 不是被测系统本身：被测的是 `mine_conflicts.adjudicate()`（一段
    确定性规则代码），本文件的判断不调用它，两者是完全独立的代码路径；
  · 每条判断都可回溯：`stable_key` 指向原始记录，任何人（包括用户）都
    能重新核对这份判断，不是黑箱结论；
  · 判断依据写清楚了具体引用了原文哪句话，不是泛泛的"看起来像冲突"。

这份披露本身就是"诚实边界"的一部分——不写清楚裁决者是谁，比裁决本身
不完美更损害可信度。

═══ 正负例的三种来源（对应 §六.2 的负例要求）══════════════════════════

  1. `ADJUDICATIONS`——真冲突（T1~T4），全部来自 mine_conflicts.py 结构层
     标记为 `structural_label="candidate"` 的 19 条，逐条读原文后裁决。
  2. `CONFIRMED_NOT_CONFLICT`——结构层已经判"不是候选"的负例
     （append_only / same_sitting），从 conflicts.jsonl 里按 candidate_id
     抽样引用，**不重新编内容**，直接读原始记录；外加 1 条结构层判定为
     "候选"但人工复核后否决的（K-0067，纯措辞精简无信息差异——见下方
     "结构层判定为候选、人工复核后否决"一节，这条最能体现"信号只能提示
     值得看一眼，真正判断要看原文"，评测集/说明报告.md:239-244 那句话
     引用的就是这个方法论）。
  3. `MANUAL_UNRELATED_PAIRS`——两条真实存在、但话题完全无关的知识手动
     配对，基础对照组。**这两条是构造的配对，但两条知识本身都是真实的**
     （设计方案 §八要求的措辞）。

用法：
    python review_and_freeze_conflicts.py
"""

import hashlib
import json
import os
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES_PATH = os.path.join(BASE, "conflicts.jsonl")
OUT_EVAL_SET = os.path.join(BASE, "conflicts_eval.jsonl")
OUT_FROZEN_META = os.path.join(BASE, "conflicts_eval_frozen.json")
OUT_REVIEW_LOG = os.path.join(BASE, "conflicts_review_decisions.json")

VERSION = "1.3.0"  # 变更史见下
#
# ⚠️ v1.2.0 → v1.3.0（2026-08-28）：**真实的裁决改判**，含1条内容变化。
#   D2 排查发现 mine_conflicts.py 的 _find_origin() 把"同 session 内的 Read"
#   误当作起源，导致所有"先 Read 再 Edit"的修订被打成 same_sitting 负例。
#   K-0052（server-pve-network.md 笔记本联网路径那条）受此影响：原判断依据
#   "old/new 仅隔11秒、同session"本身是错的，真实起源在2天前的一次 Edit。
#   已修复 _find_origin()（两轮搜索，Write/Edit 优先、全无才退回 Read），并
#   对冻结的36对逐条盲测复查（协议见《冲突判定标注规范.md》v1.0，2026-08-27
#   完成，诱饵3/3命中，18条负例里15条维持原判、3条重新核实后仍维持
#   not_conflict、K-0052改判conflict/T1/old_was_false）。K-0052 从
#   CONFIRMED_NOT_CONFLICT_SAMPLE_IDS 移入 ADJUDICATIONS。18正18负→19正17负，
#   如实记录，未挪用其他数据凑对称。修复版对本文件36对重跑仍是35/36（唯一
#   不一致仍是已知的 K-0067 语义边界），is_conflict()/adjudicate() 判定逻辑
#   一行未改，见《冻结修订记录 #1》。
#
# ⚠️ v1.0.0 → v1.1.0（2026-08-27）：**确定性修复**，不含任何裁决内容变化。
#   v1.0.0 时 build_eval_set() 用 datetime.now() 给每一行 eval 记录盖
#   adjudicated_at，导致 conflicts_eval.jsonl 的 sha256 **每次重跑都变**
#   （2026-08-16 首冻 b03056e6…、08-19 重跑 8d0c6f26…、08-27 再跑又一个值）。
#   对一个"整份文件按 sha256 冻结 + 外部锚定"的产物，运行时刻墙钟时间进
#   文件字节是缺陷（《冲突规则冻结-执行方案.md》§一 判据：能用一句不提这
#   条具体数据的规则说清 = 真 bug → 修）。改为固定常量 ADJUDICATED_AT。
#
# ⚠️ v1.1.0 → v1.2.0（2026-08-27，冻结当天稍晚）：**脱敏补漏**，不含任何
#   裁决内容变化。冻结后发现 conflicts.jsonl / conflicts_eval.jsonl 里有
#   4 类标识没脱敏（一个虚机主机名、既有用户名映射的大小写变体、两个 QQ
#   机器人 AppID），均只出现在 memory/*.md 派生记录里、不在 cases/workflows
#   语料链上。已给 identity_map.txt 补 4 条、rescrub 源数据集、重跑
#   mine_conflicts.py，得到的 conflicts.jsonl **除这 4 类 token 的一致替换外
#   逐字节不变**（149 行、candidate_id 顺序不变、实测 4 条记录变化且全是
#   token 替换）。18 正 18 负、label/type/reason_kind 全部与 v1.1.0 逐字相同，
#   M-302(35/36) / M-303 / M-304 的任何数字都不受影响。
#   （具体是哪 4 个真实值——见 identity_map.txt，那张表本身 gitignore，
#    受跟踪文件里一律用描述性措辞，不复述值，避免在防泄漏检查器眼皮下泄漏。）

ADJUDICATED_BY = "claude_code_review"  # ⚠️ 不写 "human"，理由见文件头披露

# 人工裁决实际发生的时刻（历史事实，不是"现在"）。2026-08-19 那轮
# review_and_freeze 跑出来的 conflicts_eval.jsonl 里每行盖的就是这个值；
# 常量化后重跑得到逐字节相同的文件。以后若真的重新逐条裁决（换了候选池
# / 改了判据），把这里更新成新的裁决时刻并升 VERSION。
#
# ⚠️ v1.3.0（2026-08-28）：K-0052 真的被重新裁决了（起源查找修复 + 08-27
# 盲测复查确认），这是本常量第一次因为"内容真的变了"而不是"脱敏/确定性"
# 这类元数据原因移动。取值为本次代码变更落地的时刻，不是 08-27 盲测复查
# 讨论发生的精确时刻（那次没有记录到秒级时间戳）——两者相差不到一天，
# 不影响任何时间跨度类判据。整份文件（含未变化的 35 条）的 adjudicated_at
# 字段会统一变成这个新值，这是本脚本的既有设计（版本代表"截至此刻的裁决
# 状态"，不是逐条独立加盖时间戳），不是本次新引入的行为。
ADJUDICATED_AT = "2026-08-28T00:00:00.000000+00:00"

# 评测集冻结的时刻（历史事实，不是"现在"）。同 ADJUDICATED_AT 的道理
# （v1.1.0 确立的原则）：conflicts_eval_frozen.json 是按 sha256 锚定、进
# MANIFEST 的产物，往里塞运行时刻的墙钟时间 = 每次重跑都换一个 sha256，
# 是缺陷。v1.3.0 的评测集内容（conflicts_eval.jsonl，sha cb6f60bc…）自
# 2026-08-28 起逐字节未变；此后为补充「开发集/留出集分界线」这类**描述性
# 元信息字段**而重跑本脚本，不构成"重新冻结"，frozen_at 仍是 v1.3.0 评测集
# 真正定稿的时刻。真正重新逐条裁决时（换候选池/改判据）连同 VERSION 一起更新。
FROZEN_AT = "2026-08-28T11:56:29.349100+00:00"

# ═════════════════════════════════════════ 开发集 / 留出集分界线（按数据日期，不按行号）
#
# ⚠️ v1.3.0（2026-08-28）起，分界线由「conflicts.jsonl 行号」改为「候选的
#   数据日期」。理由：起源查找修复后重挖，候选行号全部重排（第 150 行不再
#   是同一条记录），「第 150 行起 = 留出集」这个定义在任何一次重挖之后就
#   失效了。数据日期不随挖掘器版本变化——D1 每日采集从 2026-08-26 开始，
#   在此之前的 memory/*.md 修订都是历史数据（规则在其上开发），此后的是
#   D1~D7 采集窗口（规则从未见过）。判据取候选 new 侧的断言时间：
#       开发集 = new.asserted_at <  2026-08-26
#       留出集 = new.asserted_at >= 2026-08-26
#   见《冻结修订记录 #1》§6 与《冲突规则冻结记录.md》§三。
HOLDOUT_BOUNDARY_DATE = "2026-08-26"


def _new_date_of(candidate_row):
    """取候选记录 new 侧的断言日期（YYYY-MM-DD）。S4 用户信号没有 old/new
    结构，退回它自己的 timestamp。取不到返回 ""（计入 undated）。"""
    new = candidate_row.get("new") or {}
    ts = (new.get("asserted_at") or new.get("timestamp")
          or candidate_row.get("timestamp") or "")
    return ts[:10]


def split_dev_holdout(candidate_rows):
    """按 HOLDOUT_BOUNDARY_DATE 把候选池切成 (开发集, 留出集, 无日期)。
    重挖多少次都稳定——不依赖行号。"""
    dev, holdout, undated = [], [], []
    for r in candidate_rows:
        d = _new_date_of(r)
        if not d:
            undated.append(r)
        elif d < HOLDOUT_BOUNDARY_DATE:
            dev.append(r)
        else:
            holdout.append(r)
    return dev, holdout, undated


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_candidates():
    by_id = {}
    with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                c = json.loads(line)
                by_id[c["candidate_id"]] = c
    return by_id


# ═════════════════════════════════════════ 正例：结构层候选，逐条手写裁决
#
# 每条：candidate_id 定位 conflicts.jsonl 里的原始证据；verdict_type 是
# T1~T4（见设计方案 §二类型学）；reason_kind 区分"旧结论当时为假"还是
# "旧结论前提消失"（设计方案 §四第 3 点，两者裁决结果都是新的生效，但
# 知识价值不同，必须分开标注，不能都写"旧的错了"）；scope_of_old 是旧
# 知识的适用边界（不是可选字段——旧知识通常不是错的，是有边界的）。
ADJUDICATIONS = [
    {
        "candidate_id": "K-0060", "verdict_type": "T2",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——旧结论本身是查证不足下的误判，不存在其成立的版本/环境前提",
        "reason": ("旧记录明确写'PTTime 是 ratioless（不要求做种）'；新记录（次日查证后）"
                   "开头即写'先前PTTime是ratioless、不用做种的说法是错的（那是我2026-07-13"
                   "的错误结论，别再信）'——这是记录本身对自己下的推翻判断，不是本次裁决"
                   "外推的，是设计方案 §一.1 与 §二引用的教科书案例。"),
    },
    {
        "candidate_id": "K-0100", "verdict_type": "T2",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——07-15 的'固化完成、重启会正确进.1、成果保留'结论未经真实断电验证就下了定论",
        "reason": ("旧记录称固化(CP4)已完成、'重启会正确进.1，成果保留'；13天后新记录标题即为"
                   "'重大教训(2026-07-28验证)'，正文写'unshare -m 装的东西是临时层，重启全部"
                   "蒸发……写入进了临时层而非持久的usr-upper'。旧结论是在未经真实验证的前提下"
                   "过早下的，后续验证证伪，属于'旧结论当时为假'而非环境变化。"),
    },
    {
        "candidate_id": "K-0106", "verdict_type": "T1",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "2026-08-01~08-03 之间，'前置工作已做但尚未重启验证'这一待定状态成立",
        "reason": ("与 K-0100 同一条持久化调查线的后续节点：旧记录是'已做前置工作，等待用户"
                   "手动重启来最终确认'（待定状态），新记录是'❌持久化问题已有定论：验证完毕'"
                   "（确定状态）。这是待验证到已验证的正常推进，不是判断被推翻，标 premise_"
                   "disappeared（等待的前提——尚未验证——不再成立）比 old_was_false 更准确。"),
    },
    {
        "candidate_id": "K-0123", "verdict_type": "T2",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——旧结论是当时对'免费种是否计入下载量'的误判",
        "reason": ("旧记录的下一步计划写'下载28.92GB考核项需另下50%/普通种(免费不计)'；次日新"
                   "记录写'下载项：免费种其实照样计入下载量(下载从1.08→13.38G，Godless/"
                   "Lionheart都算了)，之前免费不计下载判断有误'——记录原文自认判断有误，不是"
                   "环境变化导致的。"),
    },
    {
        "candidate_id": "K-0024", "verdict_type": "T3",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "做种盘（500G物理盘，SATA直通）物理存在期间（至2026-07-30）成立",
        "reason": ("旧记录是当时的整体架构总览（VM100+阿里云反向隧道等）；新记录标题即'2026-"
                   "07-30/31重大变化：做种盘已物理拔除，PT做种链路事实上停摆'，正文明确写"
                   "'下面这份当前整体架构总览里描述的物理盘500G已经不存在了……架构总览记录的是"
                   "拔盘前状态'——记录本身就在做 scope_of_old 标注，设计方案 §一.1 引用的原始"
                   "例子。旧架构描述本身没错（当时确实那样搭建），是运行它的硬件前提消失了，"
                   "标 premise_disappeared 而非 old_was_false。"),
    },
    {
        "candidate_id": "K-0025", "verdict_type": "T1",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "2026-07-29 磁盘仍插在系统里、状态为'自检失败疑似翻新盘'时成立",
        "reason": ("旧记录是磁检诊断的中间结论（'自检失败/147不可纠正错误，疑翻新盘维权'）；"
                   "两天后新记录是诊断的最终处置结果（'✅已物理拔除退役(SMART本判死刑+PVE断电"
                   "SATA失联)'）。不是旧诊断错了，是磁盘状态本身从'待处理的故障盘'变成'已处置'"
                   "，事实槽位（磁盘当前状态）的值变了。"),
    },
    {
        "candidate_id": "K-0137", "verdict_type": "T1",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "同 K-0025——与之是同一现实事件在另一个文件(seagate-j-drive-failing.md"
                        "的description字段)里的记录，非独立事件",
        "reason": ("与 K-0025 是同一次磁盘拔除事件在不同文件/字段里的记录（K-0025 在 MEMORY.md"
                   "索引行，本条在 seagate-j-drive-failing.md 自己的 description 字段），"
                   "内容高度重合但取材独立（不同 stable_key），按设计方案的候选定义各自成立，"
                   "报告里会注明两者对应同一现实事件，不当作独立发现的两次冲突计数。"),
    },
    {
        "candidate_id": "K-0088", "verdict_type": "T3",
        "reason_kind": "old_was_false",
        "scope_of_old": "2026-07-17~07-31 期间，该服务处于用户默认接受的'已建成'状态时成立",
        "reason": ("旧记录标题'开机自动进Jellyfin(2026-07-17建成并实测通过)'；新记录改为"
                   "'❌2026-07-31用户明确表示不再需要，已停用'，正文写'此前记录写的已enable是"
                   "过时状态……用户明确答复不需要了'。是用户主动决策撤回需求（环境/偏好变化），"
                   "但记录本身用'过时状态'定性旧记录，介于两种reason之间，取更贴近'用户决策"
                   "变化'本质的 old_was_false（旧记录所称的当前状态不再为真）。"),
    },
    {
        "candidate_id": "K-0099", "verdict_type": "T1",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "2026-07-15 fstrim 回收操作之前，thin pool 94.5%占用时成立",
        "reason": ("旧记录'thin pool已用~94.5%,仅剩~8.9G……随时io-error冻VM'是当时的存储告急"
                   "状态；次日新记录'给VM100开discard+fstrim回收了约40G，现pool 69%(~48G空闲)'"
                   "是用户执行操作后的新状态。事实槽位（磁盘占用率）因操作改变，非判断错误。"),
    },
    {
        "candidate_id": "K-0044", "verdict_type": "T1",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——旧描述是 Claude 对人设的初次理解，未经用户确认",
        "reason": ("旧记录里 mako 的人设描述是《千恋万花》常陆茉子'前期人设'（笼统写法）；次日"
                   "新记录明确标注'正确人设（用户2026-07-05亲自改定，别用错）'并给出更具体准确"
                   "的描述（朝武芳乃的护卫/见习忍者等）。'别用错'这个措辞直接表明旧描述被认定"
                   "为不准确，不是环境变化。"),
    },
    {
        "candidate_id": "K-0048", "verdict_type": "T1",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "2026-07-07~07-09 期间，agent.model 配置为 maxthinking 时成立",
        "reason": ("旧记录'模型maxthinking'，两天后新记录'模型 catie-sukaka-v1/gcli-gemini-"
                   "3.1-pro-preview（2026-07-09从maxthinking换成中等档pro-preview）'——显式"
                   "记录了一次配置变更操作，不是旧记录错了，是配置这个事实槽位的值被主动改了。"),
    },
    {
        "candidate_id": "K-0076", "verdict_type": "T2",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——是 Claude 对用户诉求的理解偏差，不存在其成立的前提",
        "reason": ("旧记录写用户'不需要PT站/Radarr全自动流水线'（Claude 的推断）；同日新记录"
                   "标注'2026-07-17用户亲自纠正了上面这段的两处理解偏差，务必按新的来：……他要"
                   "的就是库存多！'——用户明确否定了 Claude 先前的理解，是 Claude 侧的方法/"
                   "理解被推翻，符合 T2'同一问题，后者否定前者的解法'。"),
    },
    {
        "candidate_id": "K-0078", "verdict_type": "T1",
        "reason_kind": "old_was_false",
        "scope_of_old": "07-17 上午基于初步测算给出的粗略结论，当晚补做电视端实测前有效",
        "reason": ("旧记录基于网盘吞吐测算给出'1080P/普通4K流畅无压力；4K蓝光原盘会卡，别下'"
                   "的粗略结论；同日晚些新记录补了电视端实测校准数据，标题即'这是硬数据，别再"
                   "拍脑袋'——用词直接暗示旧结论是拍脑袋的推断，用精确实测数据取代，是判断"
                   "精度不足被更好的证据推翻，不是环境变化。"),
    },
    {
        "candidate_id": "K-0026", "verdict_type": "T4",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "作为该文件在07-26时点的状态摘要仍准确，当前索引已指向07-31之后的更新摘要",
        "reason": ("MEMORY.md 索引行随项目进展滚动改写：旧摘要'瑞萨USB芯片故障史；500G盘应拆盒"
                   "直连板载SATA'，新摘要补充'+2026-07-31意外断电NVMe一度检测不到(重插修复)，"
                   "USB口意外恢复，别在开机时碰硬件'。不是旧信息错了，是文件内容随新事件推进，"
                   "属于设计方案 T4'段落粒度，新的生效'的常规情形，未见强烈否定措辞。"),
    },
    {
        "candidate_id": "K-0020", "verdict_type": "T4",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "作为该文件在07-26~07-28之间的摘要仍准确，当前索引已指向07-28之后的更新摘要",
        "reason": ("MEMORY.md 里 pttime-seedbox.md 的索引摘要两天内两次滚动更新（迁移完成→"
                   "架构总览定稿），都是随实际工程进展推进的摘要改写，不含对旧摘要的否定措辞，"
                   "是 T4 的典型情形（旧的没错，新的更新）。"),
    },
    {
        "candidate_id": "K-0021", "verdict_type": "T4",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "作为该文件在07-26~07-28之间的摘要仍准确",
        "reason": ("jellyfin-baidu-media.md 的 MEMORY.md 索引摘要更新：原尾部'PT打工号(500G盘/"
                   "qbit-pt就绪)撞上联通CGNAT大内网'这一子话题已转移到独立的 pttime-seedbox.md"
                   "自己的索引行（同一时期新增），本条摘要改为补充字幕预热自动化的新进展，是"
                   "话题分流+摘要滚动更新，非否定。"),
    },
    {
        "candidate_id": "K-0023", "verdict_type": "T4",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "作为该文件在07-28~07-31之间的摘要仍准确",
        "reason": ("同一索引行的再次滚动更新，这次汇总了 K-0088（tv-autostart停用）与磁盘拔除"
                   "事件（K-0024/K-0025同源）造成的库内容变化，是对下游两个已单独裁决的真实"
                   "事件做的摘要汇总，本条自身按 T4 摘要更新处理。"),
    },
    {
        "candidate_id": "K-0124", "verdict_type": "T3",
        "reason_kind": "premise_disappeared",
        "scope_of_old": "在确认 TexHoo QN10 转接线兼容性之前，'接板载SATA'是可行计划",
        "reason": ("旧记录的下一步计划是'拆盘接板载SATA→迁做种目录'；次日新记录标题'改用笔记本"
                   "当临时做种机'，正文解释'N100板载SATA是小型专用口(TexHoo QN10，标准SATA线"
                   "插不进)，转接线难买→暂时把500G盘接笔记本'——是发现硬件接口不兼容后被迫改"
                   "变的应急方案，原计划的物理前提不成立，属环境约束触发的方案调整。"),
    },
    {
        # 🔴 冻结修订 #1（2026-08-28）：本条原在 CONFIRMED_NOT_CONFLICT_SAMPLE_IDS
        # 里标 same_sitting（"同会话内间隔仅11秒的自我更正"），起源查找修复后
        # 发现该判断依据本身是错的——详见 reason 与《冻结修订记录 #1》。
        "candidate_id": "K-0052", "verdict_type": "T1",
        "reason_kind": "old_was_false",
        "scope_of_old": "无——'不经过软路由/代理'这一表述本身不够精确，不存在其成立的版本/环境前提",
        "reason": ("起源查找修复前，old 侧被误判为同 session 同天（08-07 12:24:43 的 Read 快照，"
                   "与 new 侧仅隔 11 秒），据此裁决为 same_sitting'同一次坐下来的自我更正'。修复"
                   "后追溯到真实起源是 08-05T09:51:13 的 Edit——同一个跨天长驻 session(2fd0b580)"
                   "在 08-07 的那次 Read 只是重新观测到两天前已写下的内容，不是新创建，真实时间"
                   "跨度约 2 天 2.5 小时。新记录明确写'之前\"不经过软路由/代理\"的表述不准确'——"
                   "是对 08-05 就已经不够精确的表述做澄清纠正（笔记本本地代理与软路由代理的区别"
                   "当时就存在，只是行文时未加区分），不是 08-05 到 08-07 之间环境发生了变化，故"
                   "标 old_was_false 而非 premise_disappeared。这是 mine_conflicts.py 的 "
                   "_find_origin() 同 session Read 遮蔽起源这一 bug（2026-08-28 发现并修复）"
                   "导致的一起误判，不是原裁决逻辑本身的错误——按当时能看到的（错误）起源数据，"
                   "判 same_sitting 是自洽的。"),
    },
]

# 结构层判定为"候选"、人工复核后否决的一条——是最能体现"信号只能提示值得
# 看一眼、真正判断要看原文"这条方法论的例子（评测集/说明报告.md:239-244
# 那句话引用的证据）。
DEMOTED_TO_NOT_CONFLICT = [
    {
        "candidate_id": "K-0067",
        "reason": ("mine_conflicts.py 的结构层判据（old_text 不是 new_text 的子串）判定为"
                   "候选，但通读原文后两条内容实质相同：旧文写'music-sweep每30分钟对全库跑"
                   "一遍organize+lyrics，有变化才刷Jellyfin'，新文只是删掉了'organize+lyrics'"
                   "这几个字（'每30分钟对全库跑一遍，有变化才刷Jellyfin'），没有任何信息增删"
                   "或立场变化，是纯粹的措辞精简。人工判定：不构成'旧知识被新知识取代'，不算"
                   "冲突。这一条本身就是'结构信号≠语义判断'的实证，报告里会专门引用。"),
    },
]

# ═════════════════════════════════════ 负例①：结构层已判定"不是候选"的样本
#
# 从 conflicts.jsonl 里按 candidate_id 直接抽样引用（不重新编内容），覆盖
# append_only（纯追加）与 same_sitting（同会话同天）两种设计方案 §六.2
# 要求的负例类型。选取时兼顾文件多样性，避免负例全部挤在同一个文件里。
CONFIRMED_NOT_CONFLICT_SAMPLE_IDS = {
    # append_only：旧文本整段原样保留在新文本里，没有替换/否定
    "K-0001": "append_only：openclaw-gateway.service 描述后面接着写配置文件路径，旧句一字未改",
    "K-0013": "append_only：MEMORY.md 索引在已有一行后面新增一行（Z-Image-Turbo），旧行原样保留",
    "K-0045": "append_only：mako 的语义记忆说明后面新增'干活模式'一段，旧句原样保留",
    "K-0050": "append_only：server-pve-network.md 排障提示后面新增文件收件箱功能说明，旧句原样保留",
    "K-0053": "append_only：PVE开机自启这行标题后面新增了完整的手动加片流程章节，旧标题原样保留",
    "K-0091": "append_only：butler-anime-bot.md 的exec说明后面新增'点歌'能力介绍，旧句原样保留",
    "K-0101": "append_only：kylin-rag-project.md 风险清单标题后插入了一段新的部署成功记录，旧标题原样保留",
    "K-0117": "append_only：tg-pan-index.md 一个小节标题后新增死链问题详细分析，旧标题原样保留",
    "K-0135": "append_only：pve-usb-renesas-dead.md 关联链接行后新增断电修复瑞萨USB的记录，旧句原样保留",
    # same_sitting：同一 session_id 且同一天，多半是起草/润色，不是跨会话的知识推翻
    "K-0002": "same_sitting：同一会话内(07-05)对openclaw简介的两次连续编辑，属起草过程",
    "K-0011": "same_sitting：同一会话内(07-05)MEMORY.md索引连续两次编辑，6秒间隔，属起草过程",
    "K-0042": "same_sitting：同一会话内(07-05)QQ机器人从'待办'更新为'已完成接入'，29分钟内、"
              "同一次配置操作的记录延续，不是跨会话的知识推翻",
    # K-0052 已移出（冻结修订 #1，2026-08-28）：起源查找修复后发现原判断依据
    # （"仅隔11秒"）本身是同 session Read 遮蔽起源导致的误判，真实跨度约2天，
    # 已改判 conflict/T1，见 ADJUDICATIONS 列表。
    "K-0073": "same_sitting：同一会话内(07-17)电视端APK说明29秒后补充登录方式，属起草过程",
    "K-0098": "same_sitting：同一会话内(07-15)持久化状态从'未做固化'更新为'固化已完成'，是"
              "当天工作进展的记录延续（这条后续在07-28被K-0100真正推翻，那次是跨天的）",
}

# ═════════════════════════════════════ 负例②：两条真实但话题无关的知识手动配对
#
# 基础对照组。**配对是人工构造的，但两条知识本身都是真实数据**（设计方案
# §八要求的措辞）。取材：两条完全不相关话题的真实历史片段。
MANUAL_UNRELATED_PAIRS = [
    {
        "pair_id": "U-0001",
        "a_ref": "K-0135:old", "a_text_summary": "pve-usb-renesas-dead.md 与其他两篇笔记的关联链接",
        "b_ref": "K-0139:new", "b_text_summary": "claude-code-gemini-proxy.md 关于 thought signatures 的技术笔记",
        "reason": "USB硬件故障笔记 与 LLM代理提示词工程笔记，话题完全无关，仅作基础对照。",
    },
    {
        "pair_id": "U-0002",
        "a_ref": "K-0053:old", "a_text_summary": "jellyfin-baidu-media.md 的 PVE 开机自启标题行",
        "b_ref": "K-0091:new", "b_text_summary": "butler-anime-bot.md 新增的点歌能力介绍",
        "reason": "Jellyfin开机自启配置 与 QQ机器人点歌功能，话题完全无关，仅作基础对照。",
    },
]


def _claim(record_side, kind):
    """从 conflicts.jsonl 的 old/new 字段构造 §四 schema 要求的 claim 摘要。

    ⚠️ file_path 单独存一份（不要求调用方从 stable_key 反解）：
    stable_key 的格式是 "doc:<file_path>:<timestamp>"，而 Windows 路径
    本身含冒号（`C:\\Users\\...`），时间戳也含冒号（`13:35:25`），两种
    冒号混在一起没有无歧义的切分方式。"是否同文件"是 is_conflict() 判定
    准确率要用的关键信号，必须存成显式字段，不能指望下游解析字符串。
    """
    text = record_side.get("text", "")
    return {
        "stable_key": record_side["stable_key"],
        "kind": kind,
        "session_id": record_side.get("session_id"),
        "asserted_at": record_side.get("timestamp") or record_side.get("failed_at"),
        "file_path": record_side.get("file_path"),
        "claim": text[:300],
    }


def build_eval_set(candidates_by_id):
    # 固定常量，不是 datetime.now()——见 ADJUDICATED_AT 处的说明（v1.1.0
    # 确定性修复）。变量名保留 now 以缩小 diff。
    now = ADJUDICATED_AT
    eval_rows = []

    for adj in ADJUDICATIONS:
        c = candidates_by_id[adj["candidate_id"]]
        eval_rows.append({
            "conflict_id": adj["candidate_id"],
            "label": "conflict",
            "type": adj["verdict_type"],
            "old": _claim(c["old"], c["kind"]),
            "new": _claim(c["new"], c["kind"]),
            "verdict": "new_supersedes_old",
            "scope_of_old": adj["scope_of_old"],
            "reason_kind": adj["reason_kind"],
            "reason": adj["reason"],
            "retrievable": {"old": True, "new": True},
            "default_return": "new",
            "adjudicated_by": ADJUDICATED_BY,
            "adjudicated_at": now,
        })

    for demo in DEMOTED_TO_NOT_CONFLICT:
        c = candidates_by_id[demo["candidate_id"]]
        eval_rows.append({
            "conflict_id": demo["candidate_id"],
            "label": "not_conflict",
            "type": "NOT_CONFLICT",
            "old": _claim(c["old"], c["kind"]),
            "new": _claim(c["new"], c["kind"]),
            "verdict": "not_conflict",
            "reason": demo["reason"],
            "adjudicated_by": ADJUDICATED_BY,
            "adjudicated_at": now,
            "note": "structural_label=candidate（结构层判定为候选），人工复核后否决",
        })

    for cid, reason in CONFIRMED_NOT_CONFLICT_SAMPLE_IDS.items():
        c = candidates_by_id[cid]
        eval_rows.append({
            "conflict_id": cid,
            "label": "not_conflict",
            "type": "NOT_CONFLICT",
            "old": _claim(c["old"], c["kind"]),
            "new": _claim(c["new"], c["kind"]),
            "verdict": "not_conflict",
            "reason": reason,
            "adjudicated_by": ADJUDICATED_BY,
            "adjudicated_at": now,
            "note": "structural_label=%s（结构层本身已判定为非候选）" % c["structural_label"],
        })

    for pair in MANUAL_UNRELATED_PAIRS:
        a_id, a_side = pair["a_ref"].split(":")
        b_id, b_side = pair["b_ref"].split(":")
        a_rec = candidates_by_id[a_id][a_side]
        b_rec = candidates_by_id[b_id][b_side]
        eval_rows.append({
            "conflict_id": pair["pair_id"],
            "label": "not_conflict",
            "type": "NOT_CONFLICT",
            "old": _claim(a_rec, candidates_by_id[a_id]["kind"]),
            "new": _claim(b_rec, candidates_by_id[b_id]["kind"]),
            "verdict": "not_conflict",
            "reason": pair["reason"],
            "adjudicated_by": ADJUDICATED_BY,
            "adjudicated_at": now,
            "note": "manual_unrelated_pair：配对是人工构造的，两条知识本身均取自真实记录",
        })

    return eval_rows


def main():
    candidates_by_id = _load_candidates()

    missing = [a["candidate_id"] for a in ADJUDICATIONS
               if a["candidate_id"] not in candidates_by_id]
    missing += [d["candidate_id"] for d in DEMOTED_TO_NOT_CONFLICT
                if d["candidate_id"] not in candidates_by_id]
    missing += [cid for cid in CONFIRMED_NOT_CONFLICT_SAMPLE_IDS
                if cid not in candidates_by_id]
    if missing:
        raise SystemExit(
            "拒绝执行：以下 candidate_id 在当前 conflicts.jsonl 里找不到：%s\n"
            "  多半是 mine_conflicts.py 重新跑过、候选池变了——裁决记录是针对"
            "特定一次挖掘结果手写的，源变了必须重新核对每一条裁决是否还成立，"
            "不能对着新候选池硬套旧裁决。" % missing)

    eval_rows = build_eval_set(candidates_by_id)
    n_conflict = sum(1 for r in eval_rows if r["label"] == "conflict")
    n_not = sum(1 for r in eval_rows if r["label"] == "not_conflict")

    print(f"正例（conflict）：{n_conflict} 条")
    print(f"负例（not_conflict）：{n_not} 条")
    print(f"合计：{len(eval_rows)} 条")
    if len(eval_rows) < 50:
        print(f"\n⚠️ 样本量 n={len(eval_rows)} < 50——本数据集的真实矿脉产出量不支持"
              f"设计方案估算的'30~70对'，已如实缩小规模而非注水凑数，"
              f"报告会明确标注 Wilson 区间与'不足以支撑88%达标判定'的声明。")

    for i, r in enumerate(eval_rows):
        r["eval_id"] = f"E-{i+1:03d}"

    with open(OUT_EVAL_SET, "w", encoding="utf-8", newline="\n") as f:
        for r in eval_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(OUT_REVIEW_LOG, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "reviewed_at": ADJUDICATED_AT,   # 历史事实，非"现在"——同 build_eval_set
            "reviewed_by": ADJUDICATED_BY,
            "candidates_total_in_pool": len(candidates_by_id),
            "adjudications": ADJUDICATIONS,
            "demoted_to_not_conflict": DEMOTED_TO_NOT_CONFLICT,
            "confirmed_not_conflict_sample": CONFIRMED_NOT_CONFLICT_SAMPLE_IDS,
            "manual_unrelated_pairs": MANUAL_UNRELATED_PAIRS,
        }, f, ensure_ascii=False, indent=2)

    with open(OUT_EVAL_SET, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    conflicts_pool_sha256 = sha256_of(CANDIDATES_PATH)
    conflicts_pool_lines = sum(1 for _ in open(CANDIDATES_PATH, encoding="utf-8") if _.strip())

    _dev, _holdout, _undated = split_dev_holdout(list(candidates_by_id.values()))

    frozen_meta = {
        "version": VERSION,
        "frozen_at": FROZEN_AT,  # 固定常量，不是 datetime.now()——见 FROZEN_AT 处说明
        "file": os.path.basename(OUT_EVAL_SET),
        "sha256": sha256,
        "pair_count": len(eval_rows),
        "by_label": dict(Counter(r["label"] for r in eval_rows)),
        "by_type": dict(Counter(r["type"] for r in eval_rows)),
        "adjudicated_by": ADJUDICATED_BY,
        "conflicts_pool_source": "知识检索/conflicts.jsonl（%d 条，见 mine_conflicts.py）" % conflicts_pool_lines,
        "conflicts_pool_sha256": conflicts_pool_sha256,
        "conflicts_pool_lines": conflicts_pool_lines,
        "dev_holdout_boundary": {
            "basis": "candidate.new.asserted_at（数据日期，不是行号）",
            "boundary_date": HOLDOUT_BOUNDARY_DATE,
            "dev_set": "new.asserted_at < %s（历史数据，规则在其上开发）" % HOLDOUT_BOUNDARY_DATE,
            "holdout_set": "new.asserted_at >= %s（D1~D7 采集窗口，规则未见过）" % HOLDOUT_BOUNDARY_DATE,
            "at_freeze": {"dev": len(_dev), "holdout": len(_holdout), "undated_s4_signals": len(_undated)},
            "rationale": "行号定义不耐重挖：起源查找修复后候选行号全部重排，"
                         "「第 150 行起 = 留出集」在任何一次重挖后即失效。详见《冻结修订记录 #1》§6。",
        },
        "sample_size_note": (
            "n=%d < 50：设计方案估算 S2 矿脉可产出 30~70 对真冲突，实测（两轮"
            "方法修正后）S2 结构候选 19 条（1条复核后否决为非冲突），S3 全部"
            "5 组因签名过粗（Exit code/Traceback 样板文本聚类了不相关事件）"
            "被文本重叠度过滤，未产出正例。如实按小样本处理，不注水凑数。"
        ) % len(eval_rows),
    }

    for field in ("by_label", "by_type"):
        _sum = sum(frozen_meta[field].values())
        if _sum != len(eval_rows):
            raise SystemExit(f"拒绝执行：{field} 各档之和 {_sum} ≠ 总数 {len(eval_rows)}")

    if os.path.exists(OUT_FROZEN_META):
        with open(OUT_FROZEN_META, encoding="utf-8") as _f:
            try:
                _prev = json.load(_f)
            except json.JSONDecodeError:
                _prev = None
        if _prev and _prev.get("sha256") != sha256 and _prev.get("version") == VERSION:
            raise SystemExit(
                f"拒绝执行：内容已变但版本号仍是 {VERSION}，请先升版本号"
                f"（同 评测集/review_and_freeze.py 的同类防呆检查）。")

    with open(OUT_FROZEN_META, "w", encoding="utf-8", newline="\n") as f:
        json.dump(frozen_meta, f, ensure_ascii=False, indent=2)

    print(f"\n已冻结：{OUT_EVAL_SET}")
    print(f"版本 {VERSION}，SHA256={sha256}")
    print(f"元信息：{OUT_FROZEN_META}")
    print(f"复核记录：{OUT_REVIEW_LOG}")

    print("\n" + "=" * 60)
    print("可用于外部锚定的文本（发到 GitHub issue 评论，是否执行、")
    print("发到哪个仓库需另行确认）：")
    print("=" * 60)
    print(f"麒麟RAG知识冲突评测集 v{VERSION} 冻结\n"
          f"文件：{os.path.basename(OUT_EVAL_SET)}\n"
          f"SHA256：{sha256}\n"
          f"正例{n_conflict}条 / 负例{n_not}条，合计{len(eval_rows)}条\n"
          f"候选池来源：conflicts.jsonl（{conflicts_pool_lines}条，sha256={conflicts_pool_sha256[:12]}…）\n"
          f"冻结时间：{frozen_meta['frozen_at']}")


if __name__ == "__main__":
    main()
