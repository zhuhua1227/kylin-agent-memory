#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_cases.py —— 案例挖掘器：从「失败→修复」中挖「历史案例」知识

═══ 一次真实的方法修正（先读这段）═══════════════════════════════════

最初的启发式是「is_error=true 之后，同会话 5 步内出现同工具的成功调用」，
估算出 461 对。**这个数字被证明基本是噪声**，人工抽查后发现：

  · **91.3% 的"出错"记录（5174 条中的 4725 条）根本不是技术故障**，
    而是「用户拒绝了工具调用」「auto mode 分类器拒绝了危险操作」——
    这类记录没有"怎么解决"这回事，压根不该进历史案例库；
  · 剩下的技术故障里，"下一条同工具调用"也不一定是在修复同一个问题——
    可能只是换个方向的另一次探索，同样被误判成了"修复"。

真实的技术故障占比只有 **2.4%（125 条）**。这是本脚本重新设计后的地基。

═══ 新设计：三步过滤（第三步是二次修正）══════════════════════════════

第一步：**从错误输出的文本内容判断是不是真故障**，而不是只看 is_error 标记。
        权限拒绝类的文本特征（"user doesn't want to proceed"、"denied by"等）
        与技术故障的文本特征（"command not found"、"Traceback"等）截然不同，
        可以用规则区分——规则本身写在这里，可自行核对合理性。

第二步：**修复判定看"后续调用的输出是否不再含同类错误信号"**，
        而不是简单地看 is_error 字段是否变成 false——
        因为标记为成功的下一条调用，完全可能只是另一次无关的探索。

第三步（★人工抽样后新增★）：**要求失败命令与修复命令有实质文本重叠**。

  第一、二步做完后，对 115 条候选案例做了系统性人工抽样（25 条），
  发现残留噪声：约 60% 的"修复"其实是**下一步无关操作**，而非针对同一问题的重试——
  例如「SSH 权限被拒」之后，下一条同工具调用是「查 Windows 磁盘剩余空间」，
  这不是修复，是转向去做别的事了。

  真正的修复（如 #7/#8 样例）有一个共同特征：**修复命令与失败命令共享大量文本**
  （同一个目标主机、同一个文件路径、同一段脚本），只是改了关键的一小处
  （加了 `-i 密钥文件`、换了认证方式）。而"无关的下一步"命令与失败命令
  几乎没有文本重叠。

  因此加一道相似度过滤：用**最长公共子串占比**衡量两条命令的重叠程度，
  低于阈值的判定为"不是同一问题的修复"，丢弃。这不是精确的语义判断，
  但足以把"改了一处重试"和"转头做别的事"区分开。

═══ 第四次修正（2026-08-04）：「91.3% 权限拒绝」这个统计本身被单日污染 ═══

用户追问"数据规模够不够用"时，深挖了这个头部统计的来源，发现：

  **5174 条出错记录中，4663 条（90.1%）来自 2026-07-27 这一天、仅 2 个会话**，
  内容是一次批处理/自动化任务（日志显示"串行校验开始，待处理14个"），
  产出了约 4628 条几乎逐字相同的拒绝文本
  （"The user doesn't want to proceed with this tool use..."）。

  这不是分类规则误判——这些记录**确实是真实的权限拒绝事件**，classify_error()
  判断正确。问题在于：**用包含这一天的聚合统计（91.3%）去代表"典型情况"是误导的**，
  它掩盖了"正常日子"的真实分布。排除 07-27 后重新统计：

  ```
  含 07-27（旧头部数字）：permission 91.3% / technical 2.4% / unknown 6.3%
  排除 07-27（正常日子）：permission 18.8% / technical 20.5% / unknown 60.7%
  ```

  正常日子里，**60.7% 的出错记录连分类器都认不出来是什么类型**——这是一个
  比"权限噪声"更值得处理的真实缺口。人工抽样这批"未知"记录（20+20条两轮），
  确认了至少 8 类被规则遗漏的真实信号：中文"找不到命令"变体、编码异常
  （UnicodeEncodeError）、结构化错误（InputValidationError/API Error 4xx）、
  小写/中文的语法错误措辞、"timed out"（区别于"timeout"）、终端要求
  （TTY 相关）、"请求了权限但未授权"（待批准状态）、以及全新的一类——
  **基础设施故障**（"claude-sonnet-5[1m] is temporarily unavailable,
  so auto mode cannot determine the safety..."，模型服务波动导致安全分类器
  判断不了操作安全性，这既不是权限拒绝也不是命令本身的技术问题，是平台
  瞬时故障，因此单列为第四类 `infra`，同样不产出案例）。

  规则据此扩充，见下方 PERMISSION_PATTERNS / INFRA_FAILURE_PATTERNS /
  TECH_FAILURE_PATTERNS 里标注"2026-08-04 新增"的部分。

  规则扩充后候选案例从 65 条涨到 84 条，但人工抽样（20条）又发现三个新问题：
  ①第三步相似度过滤挡不住"pscp/plink 共享连接字符串前缀、实际传输不同文件"
  这类噪声——加了第四步过滤（见 `is_shared_template_not_fix()`），剔除 28 条；
  ②发现分类规则本身还有一个真实缺口："Blocked: xxx followed by" 这种不含
  "is"的安全拦截措辞没被 `is blocked` 规则捕获，导致一条安全拦截记录被误判为
  技术故障混进了案例——已扩大 PERMISSION_PATTERNS 里的拦截匹配范围修复。
  ③（生成评测集反向出题时发现，见下方 `deduplicate_shared_fix()`）：连续多次
  相似失败后只跟着一次真实重试时，那次重试会被算法复用给前面每一次失败，
  制造出人为重复的案例——61 条里有 10 条（16.4%）受影响，加了第五步去重。

═══ 产出的知识单元格式 ═══════════════════════════════════════════

{
  "kind": "case",
  "tool_name": "...",
  "failed_input": {...},       # 失败时的调用参数
  "error_text": "...",         # 报错内容（截断）
  "fixed_input": {...},        # 修复时的调用参数
  "command_similarity": 0.72,  # 失败/修复命令的最长公共子串占比
  "steps_between": 2,          # 中间隔了几步
  "session_id": "...",
}
"""

import difflib
import json
import os
import re
import shlex
import sys

# Windows 默认控制台是 GBK，print() 里的 ⚠️ 等符号会直接抛 UnicodeEncodeError
# 让脚本崩掉（独立评审 K-6 复现过：说明报告 §8 的复现步骤写的正是
# `python mine_cases.py`，读者照做就会看到崩溃而不是核对结果）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATASET = os.environ.get("RAG_DATASET") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "多源数据整合", "dataset", "memory_source_records.jsonl")

# ── 第一步：区分「权限拒绝」与「技术故障」──────────────────────────
# 规则写在这里而不是藏在正则字符串里，方便读者核对是否合理。

PERMISSION_PATTERNS = re.compile(
    r"user doesn.?t want to proceed"
    r"|was rejected"
    r"|denied by"
    r"|requires approval"
    r"|requires permission"
    r"|not want to proceed"
    # 以下几条是 2026-08-04 复查「未知类型」桶及第四步人工抽样时新增的
    # （见下方大段说明）：
    # 「请求了权限但未授权」是待批准状态，不是技术故障
    r"|requested permissions.{0,40}haven.?t granted"
    # 安全护栏拦截（如阻止删除系统路径、阻止危险的 sleep+脚本组合）：
    # 没有"用户怎么解决"这回事，性质等同权限类。
    # 踩过的坑：最初只写了"is blocked"，漏掉了"Blocked: xxx followed by"这种
    # 不含"is"的裸露写法，导致一条安全拦截记录被误判成技术故障混进了案例库
    # （详见第四步过滤前的人工抽样，样例：sleep 60 + 脚本被拦截）。
    r"|\bblocked\b|protected from removal",
    re.IGNORECASE,
)

# 2026-08-04 新增：基础设施/平台故障。
#
# 这不是"权限被拒"，也不是"命令本身有技术问题"——是 Claude Code 平台自身
# 暂时不可用（模型服务波动），导致安全分类器判断不了操作是否安全，进而整个
# 请求失败。这类记录同样没有"用户是怎么解决的"这回事（重试与用户操作无关），
# 因此单独分一类，和 permission 一样不进入案例挖掘的候选池。
#
# 为什么不直接归进 permission：语义上完全不同（一个是"人/系统主动拒绝"，
# 一个是"平台基础设施故障"），归错类会让统计报告的措辞产生误导。
INFRA_FAILURE_PATTERNS = re.compile(
    r"temporarily unavailable|auto mode cannot determine",
    re.IGNORECASE,
)

TECH_FAILURE_PATTERNS = re.compile(
    r"command not found|未找到命令|找不到命令|not recognized"
    r"|No such file|没有那个文件|cannot access|无法访问|cannot find|does not exist"
    r"|Traceback|SyntaxError|NameError|TypeError|ValueError|KeyError"
    r"|[Ss]yntax [Ee]rror|未预期的记号|意外的符号"
    r"|undefined symbol"
    r"|Permission denied|不允许的操作"
    r"|ModuleNotFoundError|ImportError"
    r"|connection refused|Connection refused|拒绝连接"
    r"|timeout|Timeout|超时|timed out"
    r"|terminal is required"
    # 以下四条同样是 2026-08-04 新增：
    r"|Unicode\w*Error"                # 编码异常（UnicodeDecodeError/UnicodeEncodeError）
    r"|InputValidationError"           # 结构化参数校验失败
    r"|API Error:?\s*\d{3}"            # 上游服务返回 4xx/5xx
    r"|error encountered",             # curl 等工具的通用错误措辞
)


def command_text(ti) -> str:
    """把 tool_input 归一成一段用于相似度比较的文本。"""
    if not isinstance(ti, dict):
        return str(ti)
    return str(ti.get("command") or ti.get("file_path") or ti)


# 第二轮独立复核 R2-01：探查动词表原本只查 cmd.split()[0]，而本数据集里
# 绝大多数命令是 plink/pscp 包装形式（`plink -ssh host "echo pw | sudo -S
# ls -la /mnt/seed"` 或 `& D:\putty\plink.exe ...`），首词恒为 plink/&，
# 永远命中不了探查动词表——第一轮整改加了这张表却没有剥壳，假案例率因此
# 从 21.8% 只动到 22.2%，几乎没有改善。
#
# ═══ 🔴 第四轮独立复核 R4-12：三张表只改了一张 ═══════════════════════
#
# 第三轮把 `PROBE_ONLY` 做成了类级（按语义类别铺开，不再追着被点名的词补），
# 复核确认那一改是成功的——**15 种从未被任何轮次点名过的新形态 100% 通过、
# 对照组 10 条真实修复零误杀**。但同一个函数依赖的另外两张表
# （`VALUE_FLAGS` / `WRAPPER_TOKENS`）**仍然是"遇到一个补一个"的实例清单**，
# 于是第四轮又测出 4 类残余失败（本地已逐条复现）：
#
#   ssh -J jump@h1 user@h2 systemctl …  → 返回 user@h2   （`-J` 不在取值表）
#   sshpass -f /tmp/pw ssh user@h apt … → 返回 user@host （`-f` 不在取值表）
#   env LANG=C ssh user@h df -h         → 返回 env       （`env` 不在包装表）
#   nohup ssh user@h tail -f … &        → 返回 nohup     （`nohup` 不在包装表）
#
# 本次按 `PROBE_ONLY` 同样的思路重做这两张表：**不再问"我们见过哪些词"，
# 改问"这个 token 在命令行里扮演什么角色"**。角色只有有限几种，穷举角色
# 比穷举词汇可收敛。
#
# ① 透明执行包装（EXEC_WRAPPERS）：只给后面那条命令套一层执行环境
#    （改工作目录/环境变量/超时/优先级/会话），自己不是被执行的命令。
#    这是一个**语义类别**，不是"nohup 出过问题所以补 nohup"。
EXEC_WRAPPERS = {
    "nohup", "env", "time", "timeout", "stdbuf", "nice", "ionice",
    "setsid", "unbuffer", "chrt", "taskset", "doas", "sudo", "su",
}

# ② 远程执行包装：语法是 `<wrapper> [选项…] <主机> <要执行的命令>`，
#    **主机位那个裸词不是命令**。上一版没有这层认识，`plink -ssh host "…"`
#    会直接把 `host` 当成内层动词返回（R3-07 实测 4/6 形态出错的主因之一）。
REMOTE_WRAPPERS = {"ssh", "sshpass", "plink", "plink.exe",
                   "pscp", "pscp.exe", "scp", "rsh", "telnet"}

# ③ shell 包装：`bash -c "真命令"` / `powershell -Command "真命令"`
SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ash", "ksh",
                  "powershell", "powershell.exe", "pwsh", "cmd", "cmd.exe"}

# ④ 容器内执行：`docker exec <容器> <真命令>` 与 `ssh <主机> <真命令>` 是
#    **同一种结构**——包装词 + 一个"在哪儿执行"的位置参数 + 真正的命令。
#    第四轮 R4-13 点名的假案例 `docker exec qbit-pt netstat` 之所以漏过
#    探查过滤，一半原因就是这层壳没被剥：`inner_verb()` 返回的是 `docker`，
#    而 `("docker","exec")` 不在探查子命令表里，于是两个口子叠加放行。
#    按结构归类之后，它自动走 REMOTE_WRAPPERS 那条路，容器名占掉"主机位"。
CONTAINER_EXEC_CMDS = {"docker", "podman", "kubectl", "nerdctl", "lxc"}

# ⑤ 其余零碎的连接/分隔符号，本身不是命令
PUNCT_TOKENS = {"&", "&&", "||", "|", ";", "-c", "-s", "/c", "-command"}

WRAPPER_TOKENS = EXEC_WRAPPERS | REMOTE_WRAPPERS | SHELL_WRAPPERS | PUNCT_TOKENS


# 取值型选项：后面紧跟一个独立 token 作为值（不是 -x=value 这种粘连写法），
# 那个值必须一并跳过，否则会被误当成"内层动词"取走——这是把审阅报告的
# inner_verb() 建议原样实现后，本文件自己的变异测试当场抓到的真实 bug：
# `echo 密码 | sudo -S ls -la ...` 剥壳后返回的是"密码"这个词，不是 ls，
# 因为 echo 的参数（密码本身）没被识别成"需要跳过的值"，只跳过了 echo
# 这个词本身。VALUE_FLAGS 里的 "echo" 不是选项但同样是"下一个 token 是
# 值不是命令"的情况，一并处理。
#
# 🔴 R4-12：这张表原来是一个**跨命令混用的扁平集合**（`{"-p","-i","-l",…}`），
# 病根在于 `-p` 对 ssh 是端口、对 mysql 是密码、对 find 是 `-print`——同一个
# 短选项在不同命令下语义完全不同，混在一张表里必然既漏又误。第三轮修
# `anonymize.py` 的 `-p` 时已经得出过同一条结论（"`-p` 没有普适语义，必须
# 按命令锚定"），但**当时只改了脱敏那边，没回头改这里**——这正是"修实例
# 不修类"的又一个实例，而且是跨文件的同型。
#
# 现在按命令分表，且每张表**照 man page 抄全**，不是"遇到一个补一个"：
VALUE_FLAGS_BY_CMD = {
    # ssh(1) 里所有"后跟独立取值"的短选项
    "ssh": {"-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L",
            "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w"},
    # sshpass(1)：-p 明文口令 / -f 口令文件 / -d 文件描述符 / -P 提示串
    "sshpass": {"-p", "-f", "-d", "-P"},
    # plink/pscp（PuTTY 系）
    "plink": {"-pw", "-pwfile", "-i", "-l", "-P", "-load", "-m",
              "-D", "-L", "-R", "-nc", "-sshlog", "-logoverwrite"},
    "pscp": {"-pw", "-pwfile", "-i", "-l", "-P", "-load", "-sshlog"},
    "scp": {"-i", "-l", "-o", "-P", "-c", "-F", "-J", "-S"},
    "sudo": {"-u", "-g", "-p", "-C", "-h", "-r", "-t", "-U"},
    "su": {"-c", "-s", "-g", "-G"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p", "-P"},
    "stdbuf": {"-i", "-o", "-e"},
    "curl": {"-u", "-H", "-d", "-o", "-X", "-A", "-b", "-e", "-F"},
    "wget": {"-O", "-o", "--user", "--password", "-P"},
    "mysql": {"-u", "-h", "-P", "-D", "-e"},
    "docker": {"-e", "-v", "-p", "-w", "--name", "--network"},
}

# 与具体命令无关、语义稳定的取值型选项（长选项基本都是自解释的）
GENERIC_VALUE_FLAGS = {"--password", "--user", "--username", "--port",
                       "--host", "--key", "--identity", "--option"}

# `echo 密码 | sudo -S ls` ——`echo` 不是选项，但同样属于"下一个 token 是
# 值不是命令"。这是把审阅报告的 inner_verb() 建议原样实现后，本文件自己的
# 变异测试当场抓到的真实 bug：剥壳后返回的是"密码"这个词而不是 ls。
VALUE_TAKING_VERBS = {"echo", "printf"}

# 兼容旧引用：别处（含测试）可能还在 import VALUE_FLAGS
VALUE_FLAGS = (set().union(*VALUE_FLAGS_BY_CMD.values())
               | GENERIC_VALUE_FLAGS | VALUE_TAKING_VERBS)

# ── 结构性 token 分类器：靠 token 自身的形状判定角色，不靠词表 ──
#
# 这三条是本次类级改造里最"可收敛"的部分：它们描述的是 shell 语法事实，
# 不会随着我们遇到新命令而需要扩充。
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")   # env LANG=C …
_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")        # timeout 30 …


def _is_env_assignment(tok: str) -> bool:
    """`LANG=C` 这类环境变量赋值——是执行环境的一部分，不是命令。"""
    return bool(_ENV_ASSIGN_RE.match(tok))


def _is_hostspec(tok: str) -> bool:
    """`user@host` 形式的主机说明。含 `@` 就足以判定——命令名里不会有 `@`。

    为什么要单独判：`ssh -J jump@h1 user@h2 cmd` 里有**两个**主机说明，
    而"跳过一个主机位"的旧逻辑只跳一个，第二个就被当成命令返回了（R4-12）。
    """
    return "@" in tok and not tok.startswith("-")


def inner_verb(cmd: str, _depth: int = 0) -> str:
    """剥掉远程执行/提权/引号包装，取真正被执行的那个命令的首词。

    🔴 第三轮独立复核 R3-07 指出上一版的两个 bug——它们合起来让第一轮就
    点名、第二轮又点名的那条假案例（`mysql: command not found` →
    `ssh "docker ps"`）**活到了第三轮**，而 `docker ps` 明明就写在本文件
    自己的 PROBE_SUBCOMMANDS 表里：

      **bug 甲｜引号提取先于 token 扫描，且取最长**
        上一版先 `max(引号内容, key=len)` 取最长引号串。而
        `sshpass -p "<23字符密码>" ssh host "docker ps"` 里，
        密码(23字符) 比 `docker ps`(9字符) 长 → 返回的是**密码**。
        `VALUE_FLAGS` 里虽然有 `-p`，但它只在后面的 token 扫描阶段生效，
        而引号提取发生在**之前**，先手就走岔了。
        修法：先按 token 扫描剔除 VALUE_FLAGS 及其取值，再在剩下的部分里
        识别被引号包住的子命令——**顺序不能反**。

      **bug 乙｜子命令匹配拿裸 token 比对，引号粘连导致永不匹配**
        `PROBE_SUBCOMMANDS` 那边用 `cmd.split()` 的裸 token 与首词比对，
        而实际 token 是 `"docker`（引号粘在词头）→ 永远匹配不上 →
        该表**对所有引号包装形态整体失效**。
        修法：统一 `strip('"\\'')` 后再比对（本函数返回值也保证已剥引号）。

    用 shlex 而不是 str.split()：shlex 认识引号，`"docker ps"` 会被当成
    **一个** token 交回来，正好让下面"这个 token 里还有空格 ⇒ 它本身是
    一条被包起来的子命令 ⇒ 递归再剥一层"这个判断成立。
    """
    if _depth > 4:                      # 防御性上限，正常最多两三层
        return ""
    try:
        tokens = shlex.split(cmd, posix=False)
    except ValueError:                  # 引号不配对等畸形输入，退回朴素切分
        tokens = cmd.split()

    i = 0
    saw_remote_wrapper = False          # 见过 ssh/plink 之后，第一个裸词是主机
    host_consumed = False
    # R4-12：当前处在哪个包装命令的选项区——决定用哪张取值型选项表。
    # `-p` 对 ssh 是端口、对 mysql 是密码、对 sshpass 是明文口令，
    # 一张扁平表判不了，必须带上下文。
    current_cmd = None
    while i < len(tokens):
        raw = tokens[i]
        t = raw.strip("\"'").lower()
        if not t:
            i += 1
            continue

        # ── 取值型选项：连它的值一起跳过 ──
        # 先查当前命令自己的表，再查跨命令通用表，最后是 echo/printf 这类
        # "下一个 token 是数据不是命令"的动词。
        cmd_flags = VALUE_FLAGS_BY_CMD.get(current_cmd, set())
        if t in cmd_flags or t in GENERIC_VALUE_FLAGS or t in VALUE_TAKING_VERBS:
            i += 2
            continue
        if t.startswith("-"):           # 其余选项：只跳它自己
            i += 1
            continue

        # ── 结构性判定：这些形状本身就说明"不是命令" ──
        if _is_env_assignment(t):       # env LANG=C ssh …
            i += 1
            continue
        if saw_remote_wrapper and _is_hostspec(t):
            # `ssh -J jump@h1 user@h2 cmd` 里有**两个** user@host。旧逻辑
            # 只留了一个"主机位"名额，第二个就被当成命令返回了（R4-12）。
            # 含 `@` 的一律按主机说明跳过。
            #
            # ⚠️ 必须同时占掉下面那个"裸主机"名额，否则真命令会被当成主机
            # 吃掉。第一版漏了这一句，20 条用例里 10 条回归（`ssh user@host
            # docker ps` 返回 `ps`、`env … df -h` 返回空），比改之前还差——
            # 这类"新写的代码复刻自己正在修的错误"正是四轮复核的元问题，
            # 所以这一版的验证是**新旧形态一起跑 20 条**，不是只跑新增的 4 条。
            host_consumed = True
            i += 1
            continue

        base = os.path.basename(t)
        # `docker exec <容器> <真命令>`：只有跟着 exec 才是"在别处执行"，
        # `docker ps` / `docker run` 是 docker 自己在干活，不能一并剥掉。
        if base in CONTAINER_EXEC_CMDS and i + 1 < len(tokens) \
                and tokens[i + 1].strip("\"'").lower() == "exec":
            saw_remote_wrapper = True   # 容器名占"主机位"，与 ssh 同构
            current_cmd = base
            i += 2
            continue
        if base in WRAPPER_TOKENS:
            if base in REMOTE_WRAPPERS:
                saw_remote_wrapper = True
            if base in EXEC_WRAPPERS or base in REMOTE_WRAPPERS \
                    or base in SHELL_WRAPPERS:
                current_cmd = base      # 后续选项按这个命令的表来解释
            # `timeout 30 ssh …`：timeout 的第一个位置参数是时长，不是命令。
            # 用"形状像时长"判定，而不是把 "timeout" 塞进取值型选项表——
            # 后者会让 `timeout` 这个词在任何位置都吞掉下一个 token。
            if base == "timeout" and i + 1 < len(tokens) \
                    and _DURATION_RE.match(tokens[i + 1].strip("\"'")):
                i += 2
                continue
            i += 1
            continue

        inner = raw.strip("\"'")
        # 这个 token 自己就是一条带空格的子命令（被引号包着的远程命令），
        # 递归再剥一层
        if " " in inner:
            return inner_verb(inner, _depth + 1)
        # 远程包装后的第一个裸词是**主机**（`ssh host cmd`），跳过它再往后
        # 找真正的命令；只跳一次，避免把命令本身也吃掉
        if saw_remote_wrapper and not host_consumed:
            host_consumed = True
            i += 1
            continue
        return os.path.basename(inner.lower())
    return ""


def _subcommand_after_verb(cmd: str, verb: str):
    """取 `verb` 后面紧跟的那个子命令词（如 `docker` → `ps`）。

    🔴 R3-07 bug 乙的修复点：必须**先剥引号再比对**。真实命令是
    `plink … "docker exec c1 cat /log"`，朴素 split 得到的 token 是
    `"docker`（引号粘连），与 verb `docker` 永不相等，导致
    PROBE_SUBCOMMANDS 对所有引号包装形态整体失效。

    这里用与 inner_verb 相同的 shlex + strip 处理，并把嵌套引号里的
    子命令也展开，保证"看得到"引号内部。
    """
    if not verb:
        return None
    try:
        tokens = shlex.split(cmd, posix=False)
    except ValueError:
        tokens = cmd.split()
    # 把带空格的引号 token 再摊平一层，这样 `"docker exec c1"` 里的
    # docker/exec 都能被单独看到
    flat = []
    for tk in tokens:
        inner = tk.strip("\"'")
        flat.extend(inner.split() if " " in inner else [inner])
    for idx_w, w in enumerate(flat):
        if os.path.basename(w.strip("\"'").lower()) == verb:
            rest = flat[idx_w + 1:]
            if rest:
                nxt = rest[0].strip("\"'").lower()
                if not nxt.startswith("-"):
                    return nxt
            return None
    return None


# pscp 的进度条格式固定为 "<文件名> | <大小> | <速率> | ETA: hh:mm:ss | 100%"，
# 出现这一行就说明文件已经完整传输——即便外层命令因为后续操作（往往是通过
# && 拼接的远程执行部分）返回非零退出码而被标记为 is_error，"pscp 传输本身
# 失败了"这个前提是假的。复核员人工核对 idx 0/6/7/8/11/12/13 共 7 条：
# 全部是"pscp 报 Exit code 1，但输出显示 100% 传输已完成"→"改用 plink
# 执行刚上传的脚本"，这是工作流的下一步而非修复动作，不该算作案例。
_PSCP_TRANSFER_DONE = re.compile(r"\|\s*ETA:\s*[\d:]+\s*\|\s*100%")


def is_spurious_pscp_failure(cmd: str, out_text: str) -> bool:
    return "pscp" in cmd.lower() and bool(_PSCP_TRANSFER_DONE.search(out_text))


def command_similarity(a: str, b: str) -> float:
    """两条命令文本的重叠程度：最长公共子串长度 / 较短命令的长度。

    用最长公共子串而不是整体编辑距离，是因为我们关心的是
    "有没有一大段完全相同的内容"（同一主机、同一路径、同一段脚本），
    而不是字符级别的整体相似度——一条长命令里改了一个参数，
    整体编辑距离可能很大，但公共子串占比依然很高，能正确识别为"同一操作的重试"。
    """
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = matcher.find_longest_match(0, len(a), 0, len(b))
    return match.size / min(len(a), len(b))


# 相似度低于此阈值，判定为"下一步的无关操作"而非"针对同一问题的修复"。
# 阈值取 0.4 是人工抽样后校准的结果：真实修复案例（如换认证方式、改路径写法）
# 普遍在 0.5~0.9 之间，无关操作普遍低于 0.2，中间地带很窄。
SIMILARITY_THRESHOLD = 0.4


def is_shared_template_not_fix(a: str, b: str, tol: int = 15) -> bool:
    """判断两条命令是不是"共享一段模板前缀，但实际内容完全不同"。

    ═══ 第三步相似度过滤本身也会漏判一类噪声，这是第四步 ═══════════════

    2026-08-04 对 84 条候选案例做人工抽样，发现相似度过滤（第三步）挡不住
    一类具体噪声：**pscp/plink 这类命令天然带很长的连接字符串前缀**
    （主机、密码、路径开头），传输**不同文件**、执行**不同远程命令**时，
    这段共享前缀本身就能让最长公共子串占比冲到 0.4~0.6，通过第三步的门槛，
    但实际上后半段内容（真正决定这是不是"同一个问题的修复"的部分）完全不同。

    真实修复与这类噪声的关键区别在于**最长公共子串落在哪个位置**：

      真实修复（如 `python` → `python3`、`timeout 30` → `timeout 60`）：
        改动通常在中间某一处，公共子串会**跨过改动点、延伸到命令结尾**——
        因为改动前后的内容都还是一样的。

      噪声（模板前缀相同、后续内容不同）：
        公共子串**从开头算起，但到不了结尾**——只有"模板头"相同，
        "内容尾"完全不搭边。

    因此规则是：最长公共子串若从两条命令的开头同时起步（`match.a==0` 且
    `match.b==0`），且**够不到任何一方的结尾**（留 tol 个字符的容差），
    判定为"共享模板、内容不同"，不算修复。

    实测：对 84 条候选案例应用此规则，剔除 28 条（人工抽样确认这 28 条
    确实是"传输不同文件"类噪声），保留的 56 条人工抽样质量显著更高。
    """
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = matcher.find_longest_match(0, len(a), 0, len(b))
    if match.size == 0:
        return True   # 完全没有公共子串，显然不是修复
    starts_at_zero = (match.a == 0 and match.b == 0)
    reaches_a_end = (match.a + match.size) >= len(a) - tol
    reaches_b_end = (match.b + match.size) >= len(b) - tol
    return starts_at_zero and not reaches_a_end and not reaches_b_end


def classify_error(text: str) -> str:
    """判断一条出错记录的类型：permission / infra / technical / unknown。

    优先判定 permission——权限拒绝的文本有时也会顺带出现 "Error" 字样，
    但那不是技术故障，必须先排除掉，否则会被 TECH_FAILURE_PATTERNS 误抓。
    infra 同理，也要在 technical 之前判定。
    """
    if PERMISSION_PATTERNS.search(text):
        return "permission"
    if INFRA_FAILURE_PATTERNS.search(text):
        return "infra"
    if TECH_FAILURE_PATTERNS.search(text):
        return "technical"
    return "unknown"


def detect_outlier_days(errs, share_threshold: float = 0.3, max_sessions: int = 3):
    """找出"异常日"：单日贡献了过高比例的出错记录、且只集中在极少数会话里。

    ═══ 为什么要有这个检测，而不是留在文件头注释里手工提一句 ═══════════

    2026-08-04 发现：5174 条出错记录里，4663 条（90.1%）来自单独一天
    （2 个会话内一次批处理任务产生的近乎逐字相同的拒绝记录）。用包含这一天的
    聚合统计代表"典型情况"具有误导性——真实的错误类型分布因此被完全掩盖
    （权限拒绝占比从真实的 18.8% 被推高到 91.3%）。

    做成通用检测而不是硬编码排除某个日期，是因为：数据集会持续增长，
    以后可能出现新的异常日、也可能不再出现——写死日期只能治一次，
    通用检测能长期生效，且判定依据（占比 + 会话数）写在这里，可自行核对
    阈值是否合理，不是"我们主观觉得那天不算数"。

    判据：单日出错记录占全部出错记录的比例超过 share_threshold，
    且当天涉及的会话数不超过 max_sessions——高占比但会话分散（真实多次
    交互产生的，比如恰好那天开发任务多）不算异常；高占比且会话集中
    （像批处理脚本在少数几个会话里疯狂产生错误）才算。
    """
    from collections import defaultdict

    total = len(errs)
    if total == 0:
        return []

    by_day = defaultdict(list)
    for r in errs:
        day = (r.get("timestamp") or "")[:10]
        if day:
            by_day[day].append(r)

    outliers = []
    for day, day_errs in by_day.items():
        share = len(day_errs) / total
        sessions = len(set(r.get("session_id") for r in day_errs))
        if share >= share_threshold and sessions <= max_sessions:
            outliers.append({
                "day": day, "error_count": len(day_errs),
                "share_pct": round(share * 100, 1), "sessions": sessions,
            })
    return sorted(outliers, key=lambda o: -o["error_count"])


def load_dataset():
    recs = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return recs


def mine(recs, lookahead: int = 5):
    tools = [r for r in recs if r.get("record_type") == "tool_execution"]

    by_session = {}
    for r in tools:
        by_session.setdefault(r.get("session_id"), []).append(r)
    for rs in by_session.values():
        rs.sort(key=lambda r: r.get("timestamp") or "")

    stats = {"total_errors": 0, "permission": 0, "infra": 0, "technical": 0,
             "unknown": 0, "candidates_before_similarity": 0,
             "rejected_by_similarity": 0, "rejected_by_template_check": 0,
             "rejected_by_identical_cmd": 0, "rejected_by_probe_only": 0,
             "cases_found": 0}
    cases = []

    for sid, rs in by_session.items():
        for i, r in enumerate(rs):
            if not r.get("is_error"):
                continue
            stats["total_errors"] += 1

            out_text = str(r.get("tool_output", ""))
            kind = classify_error(out_text)
            stats[kind] += 1

            if kind != "technical":
                continue   # 权限拒绝/无法判断类型的，不构成"历史案例"

            failed_cmd_early = command_text(r.get("tool_input"))
            if is_spurious_pscp_failure(failed_cmd_early, out_text):
                # 第二轮独立复核 R2-01：pscp 报非零退出码，但输出显示文件
                # 已 100% 传完——"传输失败"这个前提是假的，退出码来自 &&
                # 拼接的后续远程执行部分，不该被当成"pscp 命令本身出错"去
                # 配对"修复"。人工核对过 7 条（idx 0/6/7/8/11/12/13），全是
                # 这个模式，"修复"实际是工作流下一步（执行刚传上去的脚本），
                # 不是针对同一问题的修复动作。
                stats.setdefault("rejected_by_spurious_pscp", 0)
                stats["rejected_by_spurious_pscp"] += 1
                continue

            # 在后续 lookahead 步内找「修复」：同工具、且输出不再含同类错误信号，
            # 且该步本身不是错误——不是只看 is_error 字段本身单独判定，也不是只看
            # classify_error 单独判定，是两者的合取。
            #
            # 为什么必须合取（独立评审 K-1 指出的漏洞）：正常日子里 41.5% 的错误
            # 连分类器都认不出类型（`unknown`，见 M-107 的 normal_days_unknown），
            # 原判定 `classify_error(next_out) != "technical"` 里，"不是
            # technical" 这个条件同时包含了"真的修复成功"和"分类器认不出的
            # 未知错误"两种情况——后者混进来会被误判成"修复"。加上
            # `not next.get("is_error")` 的显式否定后，只有 next 明确标记为
            # 成功的调用才可能被算作修复，堵住了这个漏检率转化成误判率的通道。
            for step, nxt in enumerate(rs[i + 1:i + 1 + lookahead], start=1):
                if nxt.get("tool_name") != r.get("tool_name"):
                    continue
                nxt_text = str(nxt.get("tool_output", ""))
                is_fixed = (not nxt.get("is_error")) and classify_error(nxt_text) != "technical"
                if not is_fixed:
                    continue   # 同类错误还在，或分类器认不出但确实还在报错，都不算修复
                stats["candidates_before_similarity"] += 1

                failed_cmd = command_text(r.get("tool_input"))
                fixed_cmd = command_text(nxt.get("tool_input"))

                # 第 3.5 步（独立评审 K-1 新增）：排除"修复命令与失败命令
                # 完全相同"的伪案例。为什么第三步（相似度）和第四步（模板前缀）
                # 都拦不住它：两个字符串完全相同时，相似度 = 1.0 远超阈值 0.4；
                # 最长公共子串就是整串，必然"跨过改动点延伸到结尾"，因此
                # is_shared_template_not_fix() 判定为"真修复"。两道过滤的设计
                # 前提都是"两条命令有差异"，同一条命令是它们共同的盲区。
                if failed_cmd.strip() == fixed_cmd.strip():
                    stats["rejected_by_identical_cmd"] += 1
                    continue

                # 探查动词表（独立评审 K-1 新增，R2-01 补齐剥壳+词表）：这些
                # 命令是"看一眼发生了什么"，不是"修复"，在失败后极常出现，
                # 且往往与失败命令有高相似度之外的实质关联（比如都在同一
                # 目录下操作），容易被后面的相似度/模板过滤放过。
                #
                # ⚠️ 原实现只查 fixed_cmd.split()[0]——本数据集里绝大多数
                # 命令形如 `plink -ssh host "echo pw | sudo -S ls -la ..."`，
                # 首词恒为 plink，永远命中不了这张表。改用 inner_verb() 剥壳
                # 取内层真正被执行的命令。df/lsblk 是复核员实测新发现的探查
                # 动词（idx 19/20），补进表里。
                # 🔴 第三轮独立复核 R3-18/R3-19：上一轮只补了被点名的
                # `df`/`lsblk` 两个——恰好是复核报告里标注了"idx 19/20"的
                # 那两条，代码注释还把这一点写死了。结果 R3-19 实测出现
                # **"打掉一个实例、同型实例当场顶上"**：旧的 `ls→lsblk` 被
                # 过滤掉后，新的 idx 14 变成了 `ls→blkid`（同会话、同性质）。
                # 这是"修实例不修类"最直观的一次现形。
                # 本轮改为按**语义类别**补齐只读探查命令，而不是继续追着
                # 复核报告点名的那几个词打。
                PROBE_ONLY = {
                    # 网络探查
                    # ⚠️ 有意**不**收 `ss`：本数据集里存在一条真实的
                    # `netstat -tuln` → `ss -tuln` 修复（工具不存在，换等价
                    # 工具），而下面这道检查看的是**修复侧**动词，所以只要
                    # `ss` 不在表里，那条真案例就保得住。
                    #
                    # 🔴 第四轮 R4-13 顺带纠正一处上一轮的过度保守：上一版
                    # 把 `netstat` 也一起排除了，理由写的是"会误杀那条真
                    # 案例"——**这个理由不成立**。那条案例里 netstat 是
                    # 失败侧、ss 是修复侧，判据只看修复侧，收进 netstat
                    # 伤不到它。多排除的这一个词恰好留了个口子：R4-13 点名的
                    # `docker exec qbit-pt netstat`（修复侧是 netstat）
                    # 因此逃过探查过滤，成了本轮的假案例。
                    "ping", "arp", "netstat", "ipconfig", "ifconfig",
                    "traceroute", "nslookup", "dig",
                    # 文件/目录探查
                    "ls", "dir", "cat", "type", "find", "stat", "file",
                    "head", "tail", "wc", "tree", "readlink", "realpath",
                    # 磁盘/块设备探查
                    "df", "du", "lsblk", "blkid", "mount", "findmnt",
                    # 进程/资源探查
                    "ps", "top", "htop", "free", "uptime", "vmstat", "iostat",
                    # 环境/身份探查
                    "which", "where", "whereis", "echo", "pwd", "whoami",
                    "id", "hostname", "uname", "env", "printenv", "date",
                }
                # ⚠️ 曾试过"失败与修复的内层动词都在探查表里就豁免"这条
                # 补偿规则（想同时收进 ss/netstat 又保住那条真修复），实测
                # **过宽**：它顺带放行了 `ls→lsblk`（正是 R3-19 点名的那条）、
                # `ls→find`、`cat→ls`。改回上面的做法——直接不收 ss/netstat，
                # 判据更简单，也不会误放其他探查对。
                fixed_verb = inner_verb(fixed_cmd)
                if fixed_verb in PROBE_ONLY:
                    stats["rejected_by_probe_only"] += 1
                    continue

                # docker/systemctl/journalctl 不能整体归为探查动词——docker
                # run/start 是真实操作，但 port/ps/logs/inspect 这些只读
                # 子命令确实是"看一眼"（idx 21 就是 `docker port qbit-pt`），
                # 因此单独按"动词+子命令"两个词一起判断，不影响其余合法用法。
                PROBE_SUBCOMMANDS = {
                    ("docker", "port"), ("docker", "ps"), ("docker", "logs"),
                    ("docker", "inspect"), ("docker", "images"),
                    ("docker", "stats"), ("docker", "version"),
                    ("systemctl", "status"), ("systemctl", "is-active"),
                    ("journalctl", None), ("git", "status"), ("git", "log"),
                    ("git", "diff"), ("kubectl", "get"), ("kubectl", "describe"),
                }
                # 🔴 R3-07 bug 乙：上一版用 `fixed_cmd.replace("|"," ").split()`
                # 切出的**裸 token** 与 fixed_verb 比对，而真实 token 是
                # `"docker`（引号粘在词头），`os.path.basename(...)` 也去不掉
                # 引号 → 永不相等 → 这张表**对所有引号包装形态整体失效**，
                # 等于白写。改用 _subcommand_after_verb() 统一剥引号后再比。
                fixed_sub = _subcommand_after_verb(fixed_cmd, fixed_verb)
                if (fixed_verb, fixed_sub) in PROBE_SUBCOMMANDS or (fixed_verb, None) in PROBE_SUBCOMMANDS:
                    stats["rejected_by_probe_only"] += 1
                    continue

                # 第三步：要求失败命令与修复命令有实质文本重叠，
                # 排除"同工具但转头做别的事"这类误判（人工抽样后新增，见文件头说明）
                sim = command_similarity(failed_cmd, fixed_cmd)
                if sim < SIMILARITY_THRESHOLD:
                    stats["rejected_by_similarity"] += 1
                    continue

                # 第四步：排除"共享模板前缀、实际内容不同"的假修复（见函数文档）
                if is_shared_template_not_fix(failed_cmd, fixed_cmd):
                    stats["rejected_by_template_check"] += 1
                    continue

                cases.append({
                    "kind": "case",
                    "tool_name": r.get("tool_name"),
                    "failed_input": r.get("tool_input"),
                    "error_text": out_text[:300],
                    "fixed_input": nxt.get("tool_input"),
                    "fixed_output": nxt_text[:200],
                    "command_similarity": round(sim, 3),
                    "steps_between": step,
                    "session_id": sid,
                    "failed_at": r.get("timestamp"),
                })
                stats["cases_found"] += 1
                break

    cases, dedup_removed = deduplicate_shared_fix(cases)
    stats["cases_found"] = len(cases)
    stats["deduped_shared_fix"] = dedup_removed

    return cases, stats


def deduplicate_shared_fix(cases):
    """第五步（2026-08-04，生成评测集时发现）：合并"共享同一个修复动作"的案例。

    ═══ 起因 ═══════════════════════════════════════════════════════════

    用 `generate_eval_candidates.py` 反向出题时，人工核对发现 `doc_id=45/46/47`
    三条案例问法高度相似（都是"用plink查qBittorrent.conf报错没有那个文件或目录
    怎么办"），查真实数据发现：这是**同一次真实修复动作被重复配对了三次**——
    用户连续三次用不同的 `grep` 关键词（path/port/webui）读同一个不存在的配置
    文件，都失败了，随后**只有一次**真实的重试（换成 `tail -n 20`）。而挖掘算法
    是"每个失败独立地往后 5 步内找修复"，三次失败各自的向后搜索都找到了同一个
    后续调用，于是产出了三条"案例"，实际只有一次真实的解决尝试。

    这不是评测集生成阶段的问题，是**挖掘算法本身的漏洞**：连续多次相似失败后
    跟着一次重试时，那次重试会被复用给前面每一次失败。放着不修，会同时污染
    知识库（同一条知识被重复收录三次，检索结果被同质条目占满）和评测集
    （同一批问题实际上有多个"正确答案"，检索到任何一个都该算对，但标注只记了
    一个 doc_id，会冤枉一个本来答对的系统）。

    实测：61 条案例里有 10 条（16.4%，分 4 组）受影响。

    ═══ 处理方式 ═══════════════════════════════════════════════════════

    按 `(tool_name, 修复动作)` 分组，同组内只保留 **`steps_between` 最小**
    （离那次真实修复最近）的一条，其余视为算法产生的人为重复，丢弃。
    选"最近的那条"而不是"最早的那条"，是因为越靠近修复动作的失败，越可能是
    促成用户真正采取那次行动的直接原因；较早的几次失败更像是同一次排查过程
    里的探索性尝试。
    """
    def fix_signature(c):
        fi = c["fixed_input"] or {}
        return (c["tool_name"], str(fi.get("command") or fi))

    groups = {}
    for c in cases:
        groups.setdefault(fix_signature(c), []).append(c)

    kept = []
    removed = 0
    for sig, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        group.sort(key=lambda c: c["steps_between"])
        kept.append(group[0])
        removed += len(group) - 1

    return kept, removed


def render_text(case: dict) -> str:
    """把一个案例渲染成可向量化、可展示给人看的检索文本。

    ⚠️ 这份是控制台预览用的副本，真正用于建索引的是
    `build_knowledge_index.py` 里同名逻辑的 `render_case()`——两处的
    error_text 截断长度必须保持一致（300字，理由见那边的注释），
    否则控制台看到的预览文本会跟实际建进索引的文本不一样，误导排查。
    """
    ti = case["failed_input"] or {}
    intent = ti.get("description") or ti.get("command") or json.dumps(ti, ensure_ascii=False)
    return (f"【历史案例】使用 {case['tool_name']} 执行「{str(intent)[:150]}」时出错："
            f"{case['error_text'][:300]}。"
            f"修复方式：{json.dumps(case['fixed_input'], ensure_ascii=False)[:150]}")


def main():
    # 🔴 第四轮 R4-05：冻结守卫，见 freeze_guard.py 文件头（三个挖掘器共用一份）。
    # 本地已复现该风险：副本里直接跑本脚本，cases.jsonl 静默 27→15、
    # sha256 从 0f41a65c… 变成 46b792c9…，而这个哈希正被评测集冻结登记。
    import argparse
    from freeze_guard import add_arguments, guard, write_provenance
    args = add_arguments(argparse.ArgumentParser(
        description="历史案例挖掘器")).parse_args()
    # 守卫放在**算之前**：判定只看路径与哈希，不依赖挖掘结果。放在写盘前
    # 会让人白等两分钟的挖掘才被告知拒跑——拒跑要快，这是守卫好不好用的一部分。
    out_path = guard(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "cases.jsonl"),
                     force=args.force, out_override=args.out)

    recs = load_dataset()

    # 异常日检测：先于其他统计输出，让读者一开始就知道数字有没有被单日污染
    tools = [r for r in recs if r.get("record_type") == "tool_execution"]
    errs_raw = [r for r in tools if r.get("is_error")]
    outliers = detect_outlier_days(errs_raw)
    if outliers:
        print("⚠️ 检测到异常日（单日贡献过高比例的出错记录、且集中在极少数会话内）：")
        for o in outliers:
            print(f"  {o['day']}：{o['error_count']} 条错误 "
                  f"（占全部错误的 {o['share_pct']}%），仅 {o['sessions']} 个会话")
        print("  下方统计默认包含这些天（保证与真实数据一致）；")
        print("  同时给出排除异常日后的对比，避免被单日污染的聚合数字误导。\n")

    cases, stats = mine(recs)

    print(f"出错记录总数：{stats['total_errors']}")
    print(f"  权限/审批拒绝：{stats['permission']} "
          f"({stats['permission']/stats['total_errors']*100:.1f}%)")
    print(f"  基础设施故障：{stats['infra']} "
          f"({stats['infra']/stats['total_errors']*100:.1f}%) —— 平台服务波动，非用户/命令问题")
    print(f"  技术故障：{stats['technical']} "
          f"({stats['technical']/stats['total_errors']*100:.1f}%)")
    print(f"  无法识别类型：{stats['unknown']} "
          f"({stats['unknown']/stats['total_errors']*100:.1f}%)")
    print(f"  （以上三类均不构成技术案例，仅 technical 类进入下一步）")

    if outliers:
        outlier_days = {o["day"] for o in outliers}
        normal_recs = [r for r in recs if
                       r.get("record_type") != "tool_execution"
                       or (r.get("timestamp") or "")[:10] not in outlier_days]
        _, normal_stats = mine(normal_recs)
        te = normal_stats["total_errors"]
        print(f"\n排除异常日后（{te} 条错误，供对比参考）：")
        print(f"  权限/审批拒绝：{normal_stats['permission']} ({normal_stats['permission']/te*100:.1f}%)")
        print(f"  基础设施故障：{normal_stats['infra']} ({normal_stats['infra']/te*100:.1f}%)")
        print(f"  技术故障：{normal_stats['technical']} ({normal_stats['technical']/te*100:.1f}%)")
        print(f"  无法识别类型：{normal_stats['unknown']} ({normal_stats['unknown']/te*100:.1f}%)")

    print(f"\n候选修复对（同工具+错误信号消失+is_error显式为否）：{stats['candidates_before_similarity']} 条")
    print(f"  第3.5步-命令完全相同剔除（独立评审K-1新增）：{stats['rejected_by_identical_cmd']} 条")
    print(f"  探查动词剔除（独立评审K-1新增）：{stats['rejected_by_probe_only']} 条")
    print(f"  第三步-相似度过滤剔除（判定为无关的下一步操作）：{stats['rejected_by_similarity']} 条")
    print(f"  第四步-模板前缀过滤剔除（共享连接串但内容不同）：{stats['rejected_by_template_check']} 条")
    print(f"最终案例：{stats['cases_found']} 条 "
          f"（占技术故障的 {stats['cases_found']/stats['technical']*100:.1f}%）")

    with open(out_path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n已写出：{out_path}")
    write_provenance(__file__, out_path, [DATASET])

    if cases:
        print("\n样例（前 3 条）：")
        for c in cases[:3]:
            print(f"  · {render_text(c)[:160]}")


if __name__ == "__main__":
    main()
