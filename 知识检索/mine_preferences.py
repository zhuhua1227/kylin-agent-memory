#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_preferences.py —— 偏好提取器：从多源数据里挖"这个用户在什么情境下
倾向怎么做"，并用留出期真实行为检验预测准不准。

═══ 这个文件解决什么问题 ═══════════════════════════════════════════

赛题要求"构建偏好记忆动态捕捉模块……实现用户操作习惯、输出风格、安全策略
等偏好的自动提取与版本化管理，实现跨场景偏好适配与回溯"。

核心设计（偏好提取-设计方案.md §〇）：**标准答案是留出期的真实行为记录，
不是人的判断**——零人工标注、零循环论证，这是本模块相对知识冲突模块
（n=18、需人工裁决）的结构优势。按时间切分：前 60% 提取偏好，后 40%
验证——提取器能不能预测留出期的真实选择，直接算准确率，不需要请示任何人
"这算不算偏好"。

═══ 口径（设计方案 §一，2026-08-15 已定）═══════════════════════════

"用户偏好"的操作性定义：该用户在其 Agent 交互轨迹中**稳定表现出**的
操作模式与约束表达，**不区分**具体动作由用户直接指定还是由 Agent 在
用户授权下选择。三条依据（详见设计方案，须写进报告）：
  1. 赛题背景原文把"工具调用执行结果"列为记忆数据源之一
  2. 记忆系统服务的是"用户+Agent"交互整体，拆分对系统能力无影响
  3. 下游价值在于"下次遇到同类情境能否复用既有模式"，与动作主体无关

═══ 三类偏好的信号来源，及一次真实的假设推翻 ═══════════════════════

  操作习惯：同一情境下的工具/命令选择（inner_verb() 归一化后按语义分类）
  输出风格：产出文件的格式分布（.md/.txt/.json）
  安全策略：`execution_status=="user_rejected"` 的操作类型分布

⚠️ 安全策略这一类，设计方案原本判断"证据强度三类里最高"（引用"4,664条
user_rejected，占33.2%"）。**实测这个数字对不上**：真实 `execution_
status=="user_rejected"` 记录是 865 条（占 tool_execution 的 9.34%），
不是 4664/33.2%——很可能是设计时把 B2 案例挖掘那次被识别、后来判定为
单日污染的旧统计（"5174条出错记录4663条来自07-27"）错记成了这里的数字，
两个数字确实接近但指的不是同一件事。

更进一步深挖发现：**865 条里 798 条（92.3%）来自同一天同一会话**
（2026-07-27，session=bada921e），与 `mine_cases.py` 文档字符串里记录的
"90.1%来自单日两个会话的批处理任务"是**同一枚地雷**，用
`detect_outlier_days()` 一验就中。排除该异常日后，正常日子的拒绝率只有
0.8%（66/8230），且**没有任何命令类型的拒绝率有统计支持**（`rm` 这类
真正有风险的操作拒绝率反而只有 3.6%，`ip`/`df` 这类只读命令拒绝率虚高
到 60~85% 全部是异常日的产物）。安全策略这一类因此实测信号很弱，如实
报告，不强行凑出"高证据强度"的结论——这是本模块的 S3（详见冲突模块
`mine_conflicts.py` 的 §2.5 同类教训，同一方法论第三次在本项目验证）。

用法：
    python mine_preferences.py                  # 提取 → preferences.jsonl
    python mine_preferences.py --out 试.jsonl    # 实验，不碰冻结产物
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(RAG, "可复现性体系"))

from measure import wilson_ci  # noqa: E402

# 剥壳复用 mine_cases.py 的 inner_verb()（命令首动词提取，第三/四轮复核已
# 验证到类级通用）与 detect_outlier_days()（单日污染检测），不重新发明。
from mine_cases import inner_verb, detect_outlier_days  # noqa: E402
# 偏好的版本变更就是一种知识冲突（设计方案 §5.1：用户中途改了规则，
# 旧偏好被新偏好推翻——这就是 T2 方法推翻）。版本化/回溯不重新发明，
# 直接复用知识冲突模块已建成的裁决器与回溯接口。
import mine_conflicts  # noqa: E402

DATASET = os.environ.get("RAG_DATASET") or os.path.join(
    RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")


# ═════════════════════════════════════════════════════ 稳定标识

def stable_key(category: str, trigger_key: str) -> str:
    """偏好的稳定标识：与产出顺序无关，重挖不变（沿用冲突模块 §0.1 的
    教训：doc_id 是 enumerate 下标，重挖即重排）。"""
    return "pref:%s:%s" % (category, trigger_key)


# ═════════════════════════════════════════════════════ 操作习惯：verb → intent 归一化

# ⚠️ 这张表是从真实数据的 inner_verb() 分布（Bash/PowerShell/exec 共
# 9266 条 tool_execution 里跑出来的前 995 个不同动词）里挑高频、有明确
# 语义类的词手工归类得出，不是拍脑袋列的。同一意图下必须有 ≥2 个真实
# 出现过的候选，否则这张表里的分类本身就没有被验证过——见下方
# INTENT_MIN_ALTERNATIVES 相关注释。
VERB_INTENT_MAP = {
    # 内容检索/文本处理
    "grep": "content_search", "rg": "content_search", "findstr": "content_search",
    "select-string": "content_search", "ag": "content_search",
    "sed": "text_transform", "awk": "text_transform", "tr": "text_transform",
    # 文件查看
    "cat": "file_view", "get-content": "file_view", "type": "file_view", "bat": "file_view",
    "head": "file_view_partial", "tail": "file_view_partial",
    # 文件列举
    "ls": "file_list", "get-childitem": "file_list", "dir": "file_list", "ll": "file_list",
    # 文件查找
    "find": "file_search", "where": "file_search",
    # 文件删除
    "rm": "file_delete", "remove-item": "file_delete", "del": "file_delete",
    # 文件复制/移动
    "cp": "file_copy", "copy-item": "file_copy", "copy": "file_copy",
    "mv": "file_move", "move-item": "file_move", "move": "file_move", "rename": "file_move",
    # 目录创建
    "mkdir": "dir_create", "new-item": "dir_create",
    # 进程查看/管理
    "ps": "process_list", "get-process": "process_list",
    "kill": "process_kill", "pkill": "process_kill", "stop-process": "process_kill",
    "taskkill": "process_kill",
    # 网络探测/传输
    "ping": "network_probe", "netstat": "network_probe", "ss": "network_probe",
    "test-netconnection": "network_probe", "ip": "network_probe", "ifconfig": "network_probe",
    "curl": "network_fetch", "wget": "network_fetch",
    # 磁盘/系统信息查看
    "df": "disk_usage", "du": "disk_usage",
    "lsblk": "disk_list", "get-volume": "disk_list", "get-psdrive": "disk_list",
    # 服务控制
    "systemctl": "service_control", "service": "service_control",
    # 日志查看
    "journalctl": "log_view", "dmesg": "kernel_log",
    # 脚本执行
    "python": "script_exec", "python3": "script_exec", "node": "script_exec",
    # 包管理
    "apt": "package_manage", "apt-get": "package_manage", "pip": "package_manage",
    "pip3": "package_manage", "winget": "package_manage", "npm": "package_manage",
    # 版本控制
    "git": "vcs",
    # 权限/提权
    "chmod": "permission_change", "chown": "permission_change",
    # 等待
    "sleep": "wait", "start-sleep": "wait",
    # 输出
    "echo": "output", "write-output": "output", "write-host": "output",
}

# 一个 intent 类里如果只出现过一个真实候选（比如"网络抓取"目前只见过
# curl，从没见过 wget），提取出来的"偏好"其实是"没有别的选项可选"，不是
# "在多个选项里选了这个"——这种情况下 support.ratio 恒为 1.0，看着像
# 强偏好，实为"唯一解"。评测阶段（§evaluate）仍然可以预测它（下次遇到
# 同情境，唯一选项也是最佳预测），所以不在挖掘阶段剔除，只在 schema 里
# 用 `n_alternatives` 如实标注"当时有没有别的真实候选"，交给报告解释，
# 不假装"唯一解"和"竞争后胜出"是同一类证据。


def classify_intent(verb: str):
    return VERB_INTENT_MAP.get(verb)


_VALID_VERB_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{0,30}$")


def _looks_like_real_verb(verb: str) -> bool:
    """过滤 inner_verb() 在复杂多行脚本上产生的噪声——变量赋值
    （`$pw=...`）、纯标点/数字片段（`2>&1`、`31879`）、被截断的字符串
    片段。这些不是真正的命令首动词，混进去会在 VERB_INTENT_MAP 里查不到
    （因为不会被人工收录），本来就不产出偏好，但过滤掉能让统计更干净、
    调试更容易看清真实分布。"""
    return bool(_VALID_VERB_RE.match(verb)) and "$" not in verb


def extract_operation_habits(tool_exec_recs):
    """情境=intent类别，选择=inner_verb()归一化后的动词。"""
    groups = defaultdict(list)
    for r in tool_exec_recs:
        if r.get("tool_name") not in ("Bash", "PowerShell", "exec"):
            continue
        cmd = (r.get("tool_input") or {}).get("command")
        if not cmd:
            continue
        verb = inner_verb(cmd)
        if not verb or not _looks_like_real_verb(verb):
            continue
        verb = verb.lower()
        intent = classify_intent(verb)
        if not intent:
            continue
        groups[intent].append({
            "choice": verb, "session_id": r.get("session_id"),
            "timestamp": r.get("timestamp"), "project": r.get("project"),
        })
    return groups


# ═════════════════════════════════════════════════════ 输出风格：产出文件格式分布

# 只看设计方案 §2.2 明确点名的三种"整理输出结果"格式——.md/.txt/.json。
# .py/.sh 这类代码文件是任务的直接产物（写脚本本身就是在完成任务），
# 不是"用什么形式呈现结果"的候选项。
#
# ⚠️ 实测踩过一次：最初把 .html/.yaml/.csv 也混进这个桶，六选一分母
# 变大，.md 的占比从 73.4%（58/79）被稀释到 55.8%（58/104），刚好卡在
# min_ratio=0.65 门槛之下、产出量为 0。深查发现 .yaml/.html 大多是配置
# 文件/网页 demo，不是"整理结果"的候选形态，混进来是范围定义错了，不是
# 门槛该调松——收窄回设计方案原定的三种后，真实占比回到 73.4%，过线。
# 这是"产出为0先查范围定义、不要先调松门槛"的一次真实示范。
DOC_STYLE_EXTENSIONS = {".md", ".txt", ".json"}


def extract_output_style(tool_exec_recs):
    """情境=单一类别"写文档类产出"，选择=文件扩展名。"""
    items = []
    for r in tool_exec_recs:
        if r.get("tool_name") != "Write":
            continue
        fp = (r.get("tool_input") or {}).get("file_path") or ""
        ext = os.path.splitext(fp)[1].lower()
        if ext not in DOC_STYLE_EXTENSIONS:
            continue
        items.append({
            "choice": ext, "session_id": r.get("session_id"),
            "timestamp": r.get("timestamp"), "project": r.get("project"),
        })
    return {"doc_format": items} if items else {}


# ═════════════════════════════════════════════════════ 安全策略：操作类型的拒绝率

def extract_safety_policy(tool_exec_recs, stats):
    """情境=inner_verb()归一化后的动词，选择="rejected"/"allowed"。

    ⚠️ 必须先排除异常日——见文件头的实测记录：不排除的话，07-27 那天
    单一会话的批处理拒绝会把几乎所有命令类型的"拒绝率"都推高到失真
    水平（df 59%/dmesg 67%/ip 86%），排除后正常拒绝率只有 0.8%，且没有
    任何命令类型有统计支持的高拒绝率。这不是可以跳过的步骤，是这一类
    信号能不能用的前提。
    """
    rejected_all = [r for r in tool_exec_recs if r.get("execution_status") == "user_rejected"]
    outliers = detect_outlier_days(rejected_all)
    stats["safety_outlier_days"] = [o["day"] for o in outliers]
    outlier_days = {o["day"] for o in outliers}
    if outliers:
        stats["safety_outlier_excluded"] = sum(o["error_count"] for o in outliers)

    groups = defaultdict(list)
    for r in tool_exec_recs:
        if r.get("tool_name") not in ("Bash", "PowerShell", "exec"):
            continue
        if (r.get("timestamp") or "")[:10] in outlier_days:
            continue
        cmd = (r.get("tool_input") or {}).get("command")
        if not cmd:
            continue
        verb = inner_verb(cmd)
        if not verb or not _looks_like_real_verb(verb):
            continue
        verb = verb.lower()
        status = r.get("execution_status")
        if status not in ("user_rejected", "success", "execution_failed"):
            continue
        choice = "rejected" if status == "user_rejected" else "allowed"
        groups[("op_risk", verb)].append({
            "choice": choice, "session_id": r.get("session_id"),
            "timestamp": r.get("timestamp"), "project": r.get("project"),
        })
    return groups


# ═════════════════════════════════════════════════════ 跨场景：windows / kylin

def detect_scenario(project) -> str:
    """从 user_input.project 字段推断场景（设计方案 §5.2）。老数据是
    Windows 路径（`C:\\Users\\...`、`I:\\SillyTavern-release`），新数据
    是麒麟机上的 Linux 路径（`/home/device_b/...`）。"""
    if not project:
        return "unknown"
    if re.match(r"^[A-Za-z]:[\\/]", project):
        return "windows"
    if project.startswith("/"):
        return "kylin"
    return "unknown"


def _scenario_of_group(items):
    """一个触发组里各 scenario 的分布——items 里没有 project 字段的
    （比如 tool_execution 记录本身不带 project，只有同会话的 user_input
    才带）查不到就计入 unknown，不假装知道。"""
    counter = Counter(detect_scenario(it.get("project")) for it in items)
    return dict(counter)


def _attach_project_from_session(tool_exec_recs, all_recs):
    """tool_execution 记录本身没有 project 字段（那是 user_input 独有的），
    用同一 session_id 里最早一条 user_input 的 project 值回填——同一会话
    通常对应同一个工作目录/项目，这是能拿到的最好近似，查不到就是
    unknown（不编造）。"""
    session_project = {}
    for r in all_recs:
        if r.get("record_type") != "user_input":
            continue
        sid = r.get("session_id")
        if sid and sid not in session_project:
            session_project[sid] = r.get("project")
    out = []
    for r in tool_exec_recs:
        r2 = dict(r)
        r2["project"] = session_project.get(r.get("session_id"))
        out.append(r2)
    return out


# ═════════════════════════════════════════════════════ 统一入口

DEFAULT_MIN_TRIGGER = 8
DEFAULT_MIN_RATIO = 0.65
DEFAULT_MIN_SESSIONS = 2


def _build_preference(category, trigger_key, trigger_desc, items, statement_fmt):
    """把一个 (trigger, items) 分组变成一条 §三 schema 的偏好记录（不含
    门槛判定，门槛在 extract() 里统一做，这里只负责把已经过关的组装出来）。
    """
    choice_counter = Counter(it["choice"] for it in items)
    top_choice, n_choice = choice_counter.most_common(1)[0]
    n_trigger = len(items)
    ratio = n_choice / n_trigger
    top_items = [it for it in items if it["choice"] == top_choice]
    sessions = set(it["session_id"] for it in top_items)
    timestamps = sorted(it["timestamp"] for it in items if it.get("timestamp"))

    return {
        "pref_id": None,   # main() 统一编号
        "category": category,
        "statement": statement_fmt.format(choice=top_choice),
        "trigger": {"kind": category, "match": trigger_key, "desc": trigger_desc},
        "choice": {
            "observed": top_choice,
            "alternatives": sorted(c for c in choice_counter if c != top_choice),
        },
        "support": {
            "n_trigger": n_trigger, "n_choice": n_choice, "ratio": round(ratio, 4),
            "n_sessions": len(sessions),
            "first_seen": timestamps[0] if timestamps else None,
            "last_seen": timestamps[-1] if timestamps else None,
        },
        "n_alternatives": len(choice_counter) - 1,
        "scenario": {"n_by_scenario": _scenario_of_group(top_items)},
        "version": 1,
        "supersedes": None,
        "superseded_by": None,
        "stable_key": stable_key(category, trigger_key),
    }


def extract(records, min_trigger=DEFAULT_MIN_TRIGGER, min_ratio=DEFAULT_MIN_RATIO,
           min_sessions=DEFAULT_MIN_SESSIONS):
    """从多源记录里提取稳定的情境-选择偏好。返回 (prefs, stats)。

    三道门槛缺一不可（设计方案 §四）：
      min_trigger  情境出现太少，比例没有意义（4/4=100% 说明不了什么）
      min_ratio    低于此值说明这个情境下并无稳定倾向，是随机的
      min_sessions 只在一个会话里出现的是一次性做法，不是习惯
                   （同 mine_templates.py 的跨会话判据，R2-22 bug③踩过
                    "用出现次数冒充会话数"的坑，这里用 set(session_id)
                    而不是 len(items) 计数，同一套修法）
    """
    tool_exec = [r for r in records if r.get("record_type") == "tool_execution"]
    tool_exec = _attach_project_from_session(tool_exec, records)

    stats = {
        "tool_exec_total": len(tool_exec),
        "rejected_by_min_trigger": 0,
        "rejected_by_min_ratio": 0,
        "rejected_by_min_sessions": 0,
        "rejected_by_required_choice": 0,
        "extracted_operation_habit": 0,
        "extracted_output_style": 0,
        "extracted_safety_policy": 0,
    }

    prefs = []

    def _consider(category, trigger_key, items, statement_fmt, trigger_desc, required_choice=None):
        """required_choice：只在"这个特定选项才是产出偏好的诉求"时用
        （目前只有 safety_policy 用到，见下方调用处的说明）——不传就是
        "看谁是多数就报谁"（operation_habit/output_style 的语义）。
        """
        n_trigger = len(items)
        if n_trigger < min_trigger:
            stats["rejected_by_min_trigger"] += 1
            return
        choice_counter = Counter(it["choice"] for it in items)
        top_choice, n_choice = choice_counter.most_common(1)[0]
        if required_choice is not None and top_choice != required_choice:
            stats["rejected_by_required_choice"] += 1
            return
        ratio = n_choice / n_trigger
        if ratio < min_ratio:
            stats["rejected_by_min_ratio"] += 1
            return
        top_sessions = set(it["session_id"] for it in items if it["choice"] == top_choice)
        if len(top_sessions) < min_sessions:
            stats["rejected_by_min_sessions"] += 1
            return
        pref = _build_preference(category, trigger_key, trigger_desc, items, statement_fmt)
        prefs.append(pref)
        stats["extracted_" + category] += 1

    habit_groups = extract_operation_habits(tool_exec)
    for intent, items in habit_groups.items():
        _consider("operation_habit", intent, items,
                  intent + " 任务倾向用 {choice}", intent)

    style_groups = extract_output_style(tool_exec)
    for kind, items in style_groups.items():
        _consider("output_style", kind, items,
                  "写文档类产出时倾向用 {choice} 格式", kind)

    # ⚠️ safety_policy 与另外两类语义不同：required_choice="rejected"——
    # "这类操作大多被放行"不是一条有信息量的安全策略（在这批数据里几乎
    # 对所有操作类型都成立，只是复读了全局 0.8% 的低拒绝率基线，见文件
    # 头的实测记录）。只有"这类操作被拒绝"才是真正的策略信号，所以只统计
    # rejected 是多数的组，"allowed 是多数"的组一律不产出偏好——不是因为
    # 它们支持度不够，是因为它们本来就不该被当成"策略"报出来。
    safety_groups = extract_safety_policy(tool_exec, stats)
    for (kind, verb), items in safety_groups.items():
        _consider("safety_policy", verb, items,
                  "对 " + verb + " 这类操作倾向{choice}", verb,
                  required_choice="rejected")

    for i, p in enumerate(prefs):
        p["pref_id"] = "P-%04d" % (i + 1)

    stats["preferences_total"] = len(prefs)
    return prefs, stats


# ═════════════════════════════════════════════════════ 评测：时间切分行为预测

def split_by_time_keeping_sessions(records, train_ratio=0.6):
    """按会话起始时间排序，整个会话归入同一侧，不切断会话。

    ⚠️ 不能纯按 record 的 timestamp 切——本数据集里会话可跨天恢复
    （知识冲突模块挖掘时实测：最长一个会话横跨 23 天，`session_id=
    1cd1ff9f`，2026-07-13→08-13）。拦腰切断会让同一会话的前半段预测
    自己的后半段，情境几乎相同、任务连续，这是切分泄漏，会把准确率
    虚抬——与知识冲突模块 §6.2 撞见的是同一个数据集特性，处理方式也
    完全一致（按会话完整性切，不按时间戳切）。

    时间戳格式是 normalize_time.py 产出的 ISO8601 UTC
    （2026-07-31T09:31:13.404000+00:00），字典序即时间序，可直接排序。
    """
    starts = {}
    for r in records:
        s = r.get("session_id")
        t = r.get("timestamp", "")
        if s and (s not in starts or t < starts[s]):
            starts[s] = t
    ordered = sorted(starts, key=lambda s: starts[s])
    cut = int(len(ordered) * train_ratio)
    train_sess, test_sess = set(ordered[:cut]), set(ordered[cut:])
    return ([r for r in records if r.get("session_id") in train_sess],
            [r for r in records if r.get("session_id") in test_sess])


def _raw_trigger_groups(records):
    """三类偏好各自的 {trigger_key: items} 分组，附带 category 标签。
    不做任何门槛过滤——供 extract()（过滤后产出偏好）与 evaluate()
    （同时需要过滤后的 P 与未过滤的类别级分布，见下方多数基线的说明）
    共用，不重复写一遍分组逻辑。
    """
    tool_exec = [r for r in records if r.get("record_type") == "tool_execution"]
    tool_exec = _attach_project_from_session(tool_exec, records)
    groups_by_category = {
        "operation_habit": {intent: items for intent, items
                            in extract_operation_habits(tool_exec).items()},
        "output_style": {kind: items for kind, items
                         in extract_output_style(tool_exec).items()},
    }
    # safety_policy 分组的 key 是 (kind, verb) 二元组，统一成 verb 字符串，
    # 与 extract() 里的 trigger_key 保持一致（否则 P 里的 stable_key 与
    # evaluate() 这边对不上）。
    safety_stats_sink = {}
    groups_by_category["safety_policy"] = {
        verb: items for (kind, verb), items
        in extract_safety_policy(tool_exec, safety_stats_sink).items()
    }
    return groups_by_category, tool_exec


def evaluate(records, min_trigger=DEFAULT_MIN_TRIGGER, min_ratio=DEFAULT_MIN_RATIO,
            min_sessions=DEFAULT_MIN_SESSIONS, train_ratio=0.6):
    """按会话完整性切分，用 T1 提取的偏好预测 T2 的真实选择，同时报
    多数基线与提升幅度（设计方案 §六）。返回含 accuracy/baseline/lift/
    n_trigger/wilson_ci 等字段的字典，按 category 分别报（不合成一个数，
    §六.3 与 §九第2点的要求）。

    ═══ "多数基线"的口径，写清楚避免歧义 ═══════════════════════════
    "总是选该情境下最高频的选项"如果把"情境"理解成与提取器完全相同的
    trigger 粒度，基线预测值会与提取器的 `choice.observed` 定义完全
    相同（两者都是"T1 里这个 trigger 下最常见的选择"），在提取器覆盖到
    的 trigger 上两者永远预测一致、提升幅度恒为 0，这个数字就测不出
    任何东西。

    本模块的解读：**基线的"情境"取 category 这一级颗粒度，不看具体
    trigger**——即"不管当前是哪个具体意图，只看这一大类里 T1 整体最
    常见的选择是什么"，这是真正的、更弱的、与提取器可能给出不同预测的
    基线，对应标准机器学习评测里的"忽略特征、只猜全局众数"这一基线。
    两者在同一个 T2 子集（trigger 属于 P 覆盖范围的出现次数）上比较，
    保证公平（不是不同分母的两个数）。
    """
    train_recs, test_recs = split_by_time_keeping_sessions(records, train_ratio)
    prefs, extract_stats = extract(train_recs, min_trigger, min_ratio, min_sessions)
    prefs_by_key = {(p["category"], p["trigger"]["match"]): p for p in prefs}

    train_groups, _ = _raw_trigger_groups(train_recs)
    category_majority = {}
    for category, groups in train_groups.items():
        all_choices = Counter()
        for items in groups.values():
            for it in items:
                all_choices[it["choice"]] += 1
        if all_choices:
            category_majority[category] = all_choices.most_common(1)[0][0]

    test_groups, _ = _raw_trigger_groups(test_recs)

    results = {}
    for category in ("operation_habit", "output_style", "safety_policy"):
        n = extractor_hits = baseline_hits = 0
        for trigger_key, items in test_groups.get(category, {}).items():
            pref = prefs_by_key.get((category, trigger_key))
            if pref is None:
                continue    # 提取器在这个 trigger 上没有偏好，不进入比较（两边都无从谈起）
            baseline_choice = category_majority.get(category)
            for it in items:
                n += 1
                if it["choice"] == pref["choice"]["observed"]:
                    extractor_hits += 1
                if baseline_choice is not None and it["choice"] == baseline_choice:
                    baseline_hits += 1
        if n == 0:
            results[category] = {"n_trigger": 0, "accuracy": None, "baseline": None, "lift": None}
            continue
        acc = extractor_hits / n
        base = baseline_hits / n
        acc_lo, acc_hi = wilson_ci(extractor_hits, n)
        base_lo, base_hi = wilson_ci(baseline_hits, n)
        # lift 的区间用两个独立 Wilson 区间的差直接相减得到——这是保守
        # 估计（两者其实算在同一批 n 次试验上，天然相关，真实区间只会更
        # 窄），不是精确解，但方向安全：不会把本不显著的提升幅度包装成
        # 显著。区间跨 0 就如实说"不能证明提取器优于多数基线"
        # （设计方案 §九第4点的要求）。
        lift_lo, lift_hi = acc_lo - base_hi, acc_hi - base_lo
        results[category] = {
            "n_trigger": n,
            "extractor_hits": extractor_hits,
            "baseline_hits": baseline_hits,
            "accuracy": round(acc, 4),
            "accuracy_wilson_ci": [round(acc_lo, 4), round(acc_hi, 4)],
            "baseline": round(base, 4),
            "baseline_wilson_ci": [round(base_lo, 4), round(base_hi, 4)],
            "lift": round(acc - base, 4),
            "lift_ci_conservative": [round(lift_lo, 4), round(lift_hi, 4)],
        }

    return {
        "by_category": results,
        "train_sessions": len(set(r.get("session_id") for r in train_recs)),
        "test_sessions": len(set(r.get("session_id") for r in test_recs)),
        "preferences_extracted": len(prefs),
    }


# ═════════════════════════════════════════════════════ 版本化：偏好变更即知识冲突

def detect_preference_conflicts(records, train_ratio=0.6, **extract_kwargs):
    """同一 stable_key 的偏好，在训练期（前 60%）与留出期（后 40%）两个
    时间窗口下 `choice.observed` 不同 → 一次偏好版本变更（设计方案
    §5.1：用户中途改了规则，旧偏好被新偏好推翻，这就是 T2 方法推翻）。

    ⚠️ 不重新发明版本化/裁决机制——直接调用知识冲突模块已建成、已被
    18 条真实冲突测过准确率的 `adjudicate()`，把 kind 定成 "case"
    （对应 adjudicate() 里"同一问题、后者否定前者的解法"这条路径，
    语义上偏好选择的改变正是这一类，不是套壳凑数）。
    """
    train_recs, test_recs = split_by_time_keeping_sessions(records, train_ratio)
    early_prefs, _ = extract(train_recs, **extract_kwargs)
    late_prefs, _ = extract(test_recs, **extract_kwargs)
    early_by_key = {p["stable_key"]: p for p in early_prefs}
    late_by_key = {p["stable_key"]: p for p in late_prefs}

    conflicts = []
    for key in sorted(set(early_by_key) & set(late_by_key)):
        old, new = early_by_key[key], late_by_key[key]
        if old["choice"]["observed"] == new["choice"]["observed"]:
            continue
        pair = {
            "kind": "case",
            "old": {"stable_key": old["stable_key"], "text": old["statement"],
                    "timestamp": old["support"]["last_seen"]},
            "new": {"stable_key": new["stable_key"], "text": new["statement"],
                    "timestamp": new["support"]["last_seen"]},
        }
        verdict = mine_conflicts.adjudicate(pair)
        conflicts.append({
            "stable_key": key,
            "old_choice": old["choice"]["observed"], "new_choice": new["choice"]["observed"],
            "old_support": old["support"], "new_support": new["support"],
            **verdict,
        })
    return conflicts


def main():
    import argparse
    from freeze_guard import add_arguments, guard, write_provenance

    args = add_arguments(argparse.ArgumentParser(
        description="偏好提取器")).parse_args()
    out_path = guard(os.path.join(BASE, "preferences.jsonl"),
                     force=args.force, out_override=args.out)

    recs = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    prefs, stats = extract(recs)

    print("=" * 70)
    print("偏好提取统计")
    print("=" * 70)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print()
    by_category = Counter(p["category"] for p in prefs)
    print(f"提取合计 {len(prefs)} 条，按类别：{dict(by_category)}")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for p in prefs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"\n已写：{out_path}")

    write_provenance(__file__, out_path, [DATASET])

    if not args.out:   # --out 是实验路径，不连带冻结评测（同冲突模块的约定）
        _freeze_evaluation(recs, out_path)


def _freeze_evaluation(recs, prefs_path):
    """冻结切分点 + T1 偏好集合 + 语料哈希（设计方案 §6.4）。

    ⚠️ 偏好提取不需要人工裁决（标准答案是留出期真实行为，见文件头），
    所以这里没有 review_and_freeze_conflicts.py 那种手写 ADJUDICATIONS
    的环节——"冻结"要证明的是另一件事：**切分点不是看着结果反向挑的**。
    锚定切分点（会话列表哈希）+ 语料哈希，任何人都能核对"这就是那次
    评测用的切分"，不能事后换一个切分点凑数字。
    """
    import hashlib
    from datetime import datetime, timezone

    result = evaluate(recs)
    train_recs, test_recs = split_by_time_keeping_sessions(recs)
    train_sessions = sorted(set(r.get("session_id") for r in train_recs if r.get("session_id")))
    test_sessions = sorted(set(r.get("session_id") for r in test_recs if r.get("session_id")))
    split_hash = hashlib.sha256(
        json.dumps({"train": train_sessions, "test": test_sessions}).encode("utf-8")
    ).hexdigest()

    with open(prefs_path, "rb") as f:
        prefs_sha256 = hashlib.sha256(f.read()).hexdigest()
    with open(DATASET, "rb") as f:
        corpus_sha256 = hashlib.sha256(f.read()).hexdigest()

    frozen = {
        "version": "1.0.0",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "split_train_ratio": 0.6,
        "split_session_list_sha256": split_hash,
        "split_train_session_count": len(train_sessions),
        "split_test_session_count": len(test_sessions),
        "preferences_jsonl_sha256": prefs_sha256,
        "corpus_sha256": corpus_sha256,
        "corpus_file": "多源数据整合/dataset/memory_source_records.jsonl",
        "eval_result": result,
    }
    frozen_path = os.path.join(BASE, "preferences_eval_frozen.json")
    with open(frozen_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(frozen, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"已冻结评测：{frozen_path}")
    print(f"切分点哈希：{split_hash[:16]}…")


if __name__ == "__main__":
    main()
