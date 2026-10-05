#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anonymize.py —— 递归脱敏模块

背景：本项目调查真实数据源（Claude Code 会话日志、OpenClaw agent 日志）时，
      曾在检查 OpenClaw 配置文件（openclaw.json）时用"只查顶层字段名"的方式
      过滤敏感字段，结果漏检了嵌套在 memorySearch.remote.apiKey 里的一个
      真实 API key（因为顶层字段叫 memorySearch，本身不含"key"关键字，
      而它内部嵌套的 apiKey 字段没有被递归检查到）。

      这个事故直接决定了本模块的核心设计要求：
      **必须递归遍历任意深度的 dict/list 结构，不能只处理顶层字段**。

用法：
    from anonymize import anonymize
    clean_data = anonymize(raw_data)   # raw_data 可以是 dict / list / str / 其他基本类型
"""

import os
import re
import sys

# 强制标准输出用 UTF-8，避免在 Windows(GBK) 终端下打印中文/特殊符号时报错。
# 用 reconfigure()（Python 3.7+）原地重配置，而不是重新包装 sys.stdout ——
# 重新包装（sys.stdout = io.TextIOWrapper(...)）在被多个脚本 import 时会导致
# 旧包装对象被垃圾回收时关闭底层缓冲区，引发 "I/O operation on closed file"。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 规则一：字段名本身就很敏感 —— 只要 key 名包含这些词，不管值是什么，整体打码
# （大小写不敏感，中英文都覆盖，任意嵌套层级都生效）
# ---------------------------------------------------------------------------
SENSITIVE_KEY_PATTERNS = [
    "key", "token", "secret", "password", "passwd", "credential",
    "密钥", "密码", "口令",
]

# ---------------------------------------------------------------------------
# 关键词+上下文规则用的正则与替换函数——单独定义在 REGEX_RULES 之前，
# 因为需要按"分隔符是否含标点"做条件判断，静态正则表达式做不到，
# 用函数式替换（re.sub 的 repl 参数传函数）。
#
# ⚠️ 第二轮独立复核 R2-08 用对抗集实测出这条规则的误伤：分隔符字符类里
# 混了空白与标点，"password policy document"这种自然语言散文里"password"
# 后面紧跟一个空格再接一个 6 字符以上的英文单词（policy），一样满足
# "关键词+分隔符+6位以上非空白串"这个形状，被错当成"password 后面跟着
# 密钥值"整体打码，破坏了正常知识内容。真实的赋值写法（冒号/等号/引号/
# 方括号）和自然语言提及（纯空白分隔）要分开对待：分隔符里出现过标点，
# 判定为真实赋值，维持原有宽松匹配；分隔符只有纯空白、没有任何标点，
# 则额外要求"值"里至少带一个数字——真实密码/口令几乎总是字母数字混合，
# 而"policy""document"这类正常英文单词不含数字，用这一点区分，不放过
# 真正的口语化提及型泄漏（p15"口令是SomeKouLingValue1"这类零长度分隔符
# 的情况不受影响，因为分隔符为空字符串，不满足"非空且纯空白"的条件，
# 走的还是原有的宽松匹配分支）。
# ---------------------------------------------------------------------------
# 🔴 第三轮：执行 R3-03 的全量 rescrub 之前做安全检查，实测这条规则在真实
# 语料上还会误伤 279 处，形态与 R2-08 那批不同、上一版的"纯空白分隔符要求
# 带数字"这条判据挡不住：
#     `credentials.json`          → 关键词 credential + 后续 `s.json`（文件名）
#     `Tokenizer` / `tokenizer}`  → 关键词 token + `izer`（正常技术词）
#     `密码设成一样的"},`          → 正常中文散文
#     `Tokenizer\nG:\ComfyUI\...` → 值里恰好有 `ComfyUI` 这种 6 位以上英文串
# 共同点：**分隔符为空**——关键词后面直接连着字母，说明它只是某个更长的
# 单词/文件名的一部分，不是"关键词 + 分隔符 + 密钥值"的赋值结构。
#
# 判据因此升级为：**不再区分分隔符形态，一律要求"值"本身具备密钥形态**。
# 中间试过"分隔符里有赋值标点就宽松放行"的两分支写法，实测反而更糟——
# 为了让 `密码：MyChineseColonPw`（全角冒号）走进宽松分支，把全角冒号和
# 反引号加进了分隔符类，结果 `密码： 网盘文件数:`、`api_key=\` ——请求任意
# 一条轨...`、`password: YES)`、``password` 永久密码，否则`` 这些正常文本
# 全部被当成赋值命中，真实语料上误伤从 279 涨到 1109。
#
# 教训与 R3-02 的 `-p` 完全同型：**判据应该落在"值长什么样"这个本质特征
# 上，而不是"它前面跟了什么标点"这个表面特征上。** 标点类判据总能被真实
# 文本里的标点噪声绕开，而"这段值是不是一串足够长的 ASCII 凭据"是稳定的。
#
# 🔴 第四轮独立复核 R4-19（三条真实漏检之一）：中文「口令是<全角空格>值」
# 整条漏掉。根因在**分隔符字符类只认半角**：
#     `口令是　Ab3xYz99`（`　` 是 U+3000 全角空格）
#   · 关键词 `口令` 命中；
#   · 下一个字符是 `是`，不在 `[\s:="'\\\]\}]` 里 → 分隔符按零长度匹配；
#   · 于是 `(\S{6,})` 必须从 `是` 开始连吃 6 个非空白字符，可它后面紧跟的
#     U+3000 **在 Python 的 Unicode 语义下算 `\s`**，`\S{6,}` 只吃到 1 个就断，
#     整条规则不成立、静默放过。
# 换句话说：全角空格本身是被 `\s` 覆盖的，真正卡住的是**中文的系动词与全角
# 标点没有被当成"赋值分隔符"**——中文里"口令是X"「密钥：X」才是自然写法，
# 而这份字符类是照着 `key=value` 这种英文/配置文件写法设计的。
#
# 补法：把中文里承担"接下来是值"这一语义的字符补进分隔符（是/为、全角冒号
# 与等号、成对引号）。
#
# ⚠️⚠️ 加宽分隔符**第二次**把误伤做大了（第三轮那次是 279→1109），实测留档：
#
# 本轮第一版把 CJK 字符直接并进原来那个半角字符类，写成
#     (?:[\s:="'\\\]\}]|[是为：＝「」『』<四个弯引号>])*
# 并在注释里写下"真实语料 36,999 条全量实测：加宽前后输出差异 0 处"。
# **那个数字是错的、且从未真正跑过**——独立复验实测：**36,999 条里 36 处被
# 新打了码，逐条看全是误伤**，无一真实凭据（0.0973%，比第四轮好不容易压到
# 的 1/2000 = 0.05% 还差了一倍）。36 处是同一个形态，sudo 的中文提示语：
#     `[sudo] user_a 的密码： === openclaw-gateway 状态 ===\nactive\n…`
#     `[sudo] user_a 的密码： [2026-07-19 04:42:02.692 +00:00] [INF] …`
#     `[sudo] user_a 的密码： {"0":"{\"subsystem\":\"agents/tool-policy\"}"…`
# sudo 提示语后面**根本没有口令**（口令从不回显），紧跟的是命令自己的输出。
# 旧版侥幸安全的原因很具体：`：` 不在半角类里 → 分隔符零长度匹配 → `\S{6,}`
# 必须从 `：` 起步，而它后面是空格，只吃到 1 个字符就断。一旦把 `：` 并进那个
# 带 `*` 的类，分隔符就能**连着跨过全角冒号和它后面的空白**，`\S{6,}` 于是
# 落到下一段正文上，把整行日志/整个 JSON 当成"密钥值"整段吃掉。
#
# 根因（与前几轮同型：**加宽了入口却没同步收紧判据**）：
#   · `_looks_secretish()` 用的是 `.search()`——值里**任何位置**有一段 16 位
#     以上的字母数字串、或"6 位以上且含数字"，整个值就算像密钥。
#     `openclaw-gateway`（正好 16 位）、`CloudDrive2`、`[2026-07-19` 全部轻松
#     过关。它挡得住"正常英文单词"，挡不住"一整行日志"。只要分隔符能跨过
#     空白把起点推到下一段正文上，这层形态判定就形同虚设。
#
# 现在的写法（实测 36,999 条全量误伤回到 0 处）：
#   1. 分隔符改成**并列的两个分支**，而不是把两类字符混进同一个字符类：
#          (?:[\s:="'\\\]\}]*|[<CJK>]+\s*)
#      半角分支与旧版**逐字符相同**，旧路径行为因此可证明零变化；新增的
#      CJK 分支只允许"一段 CJK 赋值标记 + 其后的空白"，中间不能再夹半角
#      标点，也就无法像上面那样连跨 `： ===` 把起点推到正文上。
#   2. **只对走了 CJK 分支的匹配**额外要求「值以 ASCII 字母/数字开头」
#      （见 `_redact_keyword_context`）。真实凭据几乎必然以字母数字开头，
#      而那 36 处的"值"开头是 `=` `[` `{` `/` 反引号——全是结构符号，是
#      "下一段结构的起头"而不是"一个值"。这条只加在新分支上，半角分支
#      一个字符都不碰，因此不牺牲任何既有召回（labeled_redaction_testset
#      的 27 条正例仍 27/27 全中，19 条负例仍 0 误伤）。
# 教训与前四轮完全同型，写死在这里：**加宽入口的同一次改动里必须同步收紧
# 判据，且必须在真实语料上跑完再写下结论**——上一版正是"改完直接在注释里
# 写了个没跑过的 0"，而真实数字是 36。
#
# 写法说明：四个中文弯引号（U+201C/201D/2018/2019）用 `\uXXXX` 转义而不是直接写
# 字面量——本项目约定 Python 源码里不出现中文弯引号（历史上贴中文文本时
# 混进弯引号导致过 SyntaxError）。raw string 不解释 `\u`，所以这一段单独用
# 普通字符串拼接。其余 CJK 字符（是为：＝「」『』）不是引号，直接写字面量。
_CJK_CURLY_QUOTES = "\u201c\u201d\u2018\u2019"   # 中文弯双引号与弯单引号（不写字面量）
# 承担"接下来是值"这一语义的 CJK 字符。**不含顿号/逗号/分号**——`、``，` 在
# 中文里是"并列下一项"而不是"接下来是值"，`密钥、密码、账号 xxx` 这种枚举句
# 里补了它们就会把枚举后面的正常内容当成值。
_CJK_SEP_CHARS = "是为：＝「」『』" + _CJK_CURLY_QUOTES
_KEYWORD_SEP_CLASS = (r"(?:[\s:=\"'\\\]\}]*|["
                      + _CJK_SEP_CHARS
                      + r"]+\s*)")
_KEYWORD_CTX_RE = re.compile(
    r"(?i)(" + "|".join(
        ["密钥", "密码", "口令", "token", "api[_ ]?key",
         "secret", "password", "passwd", "credential"]) +
    r")(" + _KEYWORD_SEP_CLASS + r")(\S{6,})")

# "看起来像密钥"的三种最小形态（任一成立即可）——都是对**值本身**的判断。
# 先试过"≥8 位且大小写混合""≥12 位"这两条更宽的判据，真实语料上仍会把
# `Credential Materialization]`（15 位、含大写）、`exist_ok=True` 这类正常
# 英文词句判成密钥。收紧后的三条：
#   · 含数字且 ≥6 位          —— 绝大多数真实口令/token（qqBotSecretValue16）
#   · ≥16 位                  —— 足够长的纯字母串（BacktickWrappedPw 17 位）
#   · ≥8 位且有 ≥3 处驼峰转折 —— 随机生成的混合大小写（MyChineseColonPw）
# 关键区分点是**驼峰转折次数**：随机凭据到处是 `小写→大写` 的跳变，而正常
# 英文单词（Materialization / Tokenizer / Credential / ComfyUI）最多只有
# 词首一个大写、0~1 处转折。这比单纯"含大写字母"稳健得多。
_HAS_DIGIT_RE = re.compile(r"(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{6,}")
_LONG_RUN_RE = re.compile(r"[A-Za-z0-9_\-]{16,}")
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9_\-]{8,}")


def _looks_secretish(value: str) -> bool:
    if _HAS_DIGIT_RE.search(value) or _LONG_RUN_RE.search(value):
        return True
    # 驼峰转折 ≥3：随机凭据的典型特征，正常英文词做不到
    for run in _ASCII_RUN_RE.findall(value):
        humps = sum(1 for a, b in zip(run, run[1:]) if a.islower() and b.isupper())
        if humps >= 3:
            return True
    return False


def _redact_keyword_context(m):
    sep, value = m.group(2), m.group(3)
    # 走 CJK 分隔符分支时的**附加**判据：值必须以 ASCII 字母/数字开头。
    # 理由与实测见 `_KEYWORD_SEP_CLASS` 上方那段——CJK 分支能跨过全角标点
    # 后面的空白，起点会落到"下一段结构"上（`=== 标题 ===`、`[时间戳`、
    # `{JSON`、`/路径`、反引号代码块），而 `_looks_secretish()` 用 `.search()`
    # 挡不住这类整行文本。真实凭据几乎必然以字母数字开头，用"值的第一个
    # 字符"就能把这两类分开。**只作用于 CJK 分支**：半角分支一个字符都不
    # 加限制，因此不动任何既有召回。
    if any(ch in _CJK_SEP_CHARS for ch in sep):
        head = value[:1]
        if not (head.isascii() and head.isalnum()):
            return m.group(0)
    if not _looks_secretish(value):
        return m.group(0)
    return m.group(1) + "[REDACTED_SECRET]"


# ---------------------------------------------------------------------------
# 🔴 第四轮独立复核 R4-19（三条真实漏检之二）：AWS Secret Access Key
# （形如 `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`，40 位、base64 字符集）
# 此前**两条防线同时不覆盖**：
#   · 没有专用规则（只有 Access Key ID 那条 `AKIA[0-9A-Z]{16}`，那是**账号
#     标识**，不是口令，两者是不同的东西，抓到一个不等于抓到另一个）；
#   · 32 位长串兜底规则的字符类是 `[A-Za-z0-9_\-]`，**不含 `/` 和 `+`**，
#     所以带斜杠的 secret 会被 `\b` 切成几段短串，每段都够不到 32 位阈值。
#
# ⚠️ 补这条规则最大的风险是"把 `/` 放进字符类"——真实语料里 40 位左右、
# 含斜杠的串遍地都是：文件路径、URL、PATH 环境变量、base64 片段。
# 先做了实测再写规则（这是 R3-02 那条 `-p` 教训的做法）：
#   · 只用「40 位定长 + 含 `/`」这一个条件，全量 36,999 条真实语料命中
#     **299 处，逐条看全是路径与 URL**（`/media/CloudDrive/Media/Movies/…`、
#     `/c/Windows/System32/WindowsPowerShell/v1.0`、CSDN 博客链接…），
#     误伤面比 R3-02 那条 `-p` 还典型；
#   · 叠上下面 `_looks_aws_secret()` 的五条结构判据后，同一份语料命中
#     **0 处**。既然真实语料上一处不碰，这条规则的误伤面就是可实测的 0。
#
# 判据为什么是这五条（每条都对着一类具体的良性串）：
#   1. 必须含 `/` 或 `+`：纯字母数字的 40 位串归 32 位长串规则管，那条已经
#      有 hex 豁免（git commit / md5 / sha256）。**这一条同时是哈希铁律的
#      保险**——40 位 hex 里不可能出现 `/` 或 `+`，本规则连试都不会试。
#   2. 不含 `//`：`https://`、`file:///`、被折叠的路径分隔全在这一类里。
#   3. 首尾不是 `/` 或 `+`：绝对路径以 `/` 开头、目录以 `/` 结尾。
#   4. 斜杠不超过 3 个：AWS secret 是 30 字节随机数据的 base64，40 个字符里
#      斜杠的期望个数只有 0.6 个，出现 4 个以上的概率不到 0.2%；而路径里
#      4 个以上斜杠是常态。用概率差而不是"有没有斜杠"来分。
#   5. 大写、小写、数字三者齐全：随机 base64 几乎必然三者都有；而路径
#      （`/usr/share/bash-completion/completions/mysql`）通常全小写、
#      也常常一个数字都没有。
# 五条都是对**值本身形态**的判断，不依赖它前面跟了什么——与 R3-02、R3-03
# 反复确认过的那条原则一致：判据落在值上才稳，落在上下文标点上一定被绕开。
_AWS_SECRET_BARE_RE = re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])")


def _looks_aws_secret(s: str) -> bool:
    if "/" not in s and "+" not in s:
        return False                      # 判据 1
    if "//" in s:
        return False                      # 判据 2
    if s[0] in "/+" or s[-1] in "/+":
        return False                      # 判据 3
    if s.count("/") > 3:
        return False                      # 判据 4
    return (any(c.islower() for c in s)   # 判据 5
            and any(c.isupper() for c in s)
            and any(c.isdigit() for c in s))


def _redact_aws_secret_bare(m):
    return "[REDACTED_SECRET]" if _looks_aws_secret(m.group(0)) else m.group(0)


# 有关键词伴随时（`aws_secret_access_key = …`）就不需要上面那五条结构判据了
# ——关键词本身已经足够决定性，只要求"40 位 base64 字符集"即可，这样连
# 那 0.2% 的多斜杠 secret 也不会漏。
#
# ⚠️ 为什么现成的关键词+上下文规则抓不到它（这正是漏检的成因）：那条规则的
# 关键词表里有 `secret`，但 `aws_secret_access_key` 里 `secret` 后面紧跟的是
# `_access_key`，分隔符类不含下划线与字母 → 分隔符按零长度匹配，取到的"值"
# 是 `_access_key`，`_looks_secretish()` 判它不像密钥（无数字、11 位、无驼峰）
# → 整条放过，真正的 40 位口令在更后面，根本没进入判定。所以必须**把完整
# 字段名当成一个关键词**单独写，不能指望通用关键词规则顺手抓到。
_AWS_SECRET_CTX_RE = re.compile(
    r"(?i)((?:aws[_\-]?)?secret[_\-]?access[_\-]?key|aws[_\-]?secret[_\-]?key)"
    r"(" + _KEYWORD_SEP_CLASS + r")"
    r"([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])")


# ---------------------------------------------------------------------------
# 规则二：文本内容里如果出现这些"长得像敏感信息"的模式，即使字段名看起来无害，
# 也要在字符串层面做正则替换
# ---------------------------------------------------------------------------
REGEX_RULES = [
    # 常见 API key 前缀（OpenAI/SiliconFlow 等常用 sk- 开头）。
    # ⚠️ 2026-08-04 修：字符集原本不含 `-`/`_`、阈值 16 位——独立审查实测
    # 发现一个真实泄漏的 key 恰好在第 6 位就出现连字符，命中不了旧正则，
    # 25 位就穿透了 16 位阈值检查（该 key 已随本次修复吊销）。字符集补上
    # `-`/`_`，阈值降到 12（新式 key 常见形如 `sk-xxxx-yyyy`，分段更短）。
    # ⚠️ 第二轮独立复核 R2-08：原正则没有左词边界，"task-xxx"这类词里的
    # "sk-"子串（task-的后三个字符）会被误当成key前缀打码成"ta[REDACTED_SECRET]"
    # ——`\b`在这里不够，因为`-`本身不是\w字符，`\b`在字母和`-`之间不会拒绝
    # 匹配，必须显式要求"sk-"前面不是字母/数字/下划线/连字符。
    (re.compile(r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{12,}"), "[REDACTED_SECRET]"),
    # 主流云厂商/SaaS的定长前缀token——没有关键词伴随、长度也不一定过32位
    # 长随机串阈值，靠通用规则抓不到，独立复核用对抗集实测出的真实漏检
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_SECRET]"),          # AWS Access Key ID
    # R4-19：AWS Secret Access Key（配套的**口令**，与上面的 ID 是两回事）。
    # 关键词伴随式在前、纯结构式在后：前者不需要结构判据，后者靠
    # `_looks_aws_secret()` 的五条判据把路径/URL/base64 挡在外面（说明见上）。
    (_AWS_SECRET_CTX_RE, r"\1\2[REDACTED_SECRET]"),
    (_AWS_SECRET_BARE_RE, _redact_aws_secret_bare),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SECRET]"),  # Slack token
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"), "[REDACTED_SECRET]"),  # GitHub PAT
    # Bearer token
    (re.compile(r"Bearer\s+[A-Za-z0-9\-\._~\+/]{16,}=*", re.IGNORECASE), "Bearer [REDACTED_SECRET]"),
    # 一长串随机字母数字（很可能是 token/密钥），至少20位连续字母数字或-_
    #
    # ⚠️ 2026-08-05 D-8/D-9实测发现的真实误伤：标准UUID（8-4-4-4-12，36字符含
    # 连字符）命中这条规则被打码——本项目自己的 session_id 就是UUID格式，
    # 误伤会切断"用户意图↔工具调用"的关联链路（同类型问题第二版数据整合时
    # 就踩过一次，见说明报告第四节教训）。加一条UUID格式排除：只有形如
    # 8-4-4-4-12的连字符分段字符串才豁免，不豁免任何其他形态的长随机串
    # （不放过真实密钥，只放过明确是UUID标准格式的这一种）。
    #
    # ⚠️ 第二轮独立复核 R2-08 用独立构造的对抗集（25正例/12负例）实测出这条
    # 规则误伤比自测集显示的更严重——64位SHA256被打码成[REDACTED_LONG_TOKEN]，
    # 而**本项目的外部锚定、评测集冻结校验、可复现性记录全部依赖SHA256**，
    # 对含哈希的产物跑这条规则等于自毁证据链；40位git commit hash同理被误伤；
    # 语义化技术包名（kylin-ai-vector-engine-client-devel，本项目核心包名）
    # 整体被打码，破坏文档可读性。补两类豁免：纯hex的32/40/64位串（md5/git
    # commit/sha256），以及多段小写连字符拼接的包名格式。两类豁免都只放过
    # "看起来确实是那类良性长串"的具体形态，不放松对真实随机密钥的判定——
    # 真实密钥不会恰好是纯hex或恰好符合"至少3段的小写连字符拼接"这种规律。
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
     lambda m: m.group(0) if (
         re.fullmatch(
             r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
             m.group(0))
         or re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", m.group(0))
         or re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+){2,}", m.group(0))
         # 🔴 2026-08-14：出题审计链的 run_id（形如
         # `gen-20260814T085700Z-0b8e6c2382e5`，37 位）命中了本条兜底，
         # 整链重跑后 `generation_raw_log.jsonl` 的 86 条 run_id **全部**
         # 被打成 [REDACTED_LONG_TOKEN]，审计链当场断掉——
         # 而 run_id 恰恰是审计链存在的全部意义。
         # 它有明确的结构（前缀 + UTC 时间戳 + 输入指纹），不是随机凭据，
         # 与 sha256/UUID 属同一类"良性长串"，按同样方式豁免。
         # ⚠️ 只放过这个精确形态，不放宽对真实随机密钥的判定。
         or re.fullmatch(r"gen-\d{8}T\d{6}Z-[0-9a-f]{12}", m.group(0))
     ) else "[REDACTED_LONG_TOKEN]"),
    # 中/英文"密钥/密码/token/password"类关键词紧跟着一段非空白串——独立审查
    # 发现的真实漏检：QQ机器人 client secret（16位）、qBittorrent口令（9位）、
    # 上传目录TOKEN（16位）全部低于上面 32 位长随机串规则的阈值，但都是
    # 紧跟在明确的关键词后面出现的，用关键词+上下文而不是长度来抓。
    #
    # ⚠️ 2026-08-05 复查时又漏了一次：第一版这条规则只写了密钥/token/secret，
    # 漏了 password/密码/pw——结果两条真实密码（一条是不含分隔符的短口令，
    # 一条是含感叹号+年份的组合）在重新提取一轮后依然原样留在数据集里（这类
    # 值出现在字符串里的类JSON文本中，不经过 anonymize() 的字段名递归判定，
    # 只能靠这条正则内容匹配兜底）。**⚠️ 具体值不写在这里**——第二轮独立
    # 复核指出，第一版这条注释直接抄了两条真实密码明文并推送到远端，是和
    # 最初P0发现完全同型的错误在同一个文件里复发；具体词根见 gitignore 的
    # secrets_blocklist.txt，此处只描述形态，不复制原文。这类问题
    # 本身就在教训列表里出现过不止一次，再次印证"光记录教训不够，写新规则
    # 时要主动回查已有教训"这条说明报告里写过的话——这次干脆把关键词表
    # 直接对齐 SENSITIVE_KEY_PATTERNS（字段名判定用的那份清单），避免同一类
    # 关键词在两条不同规则里各写一半、各漏一半。
    # ⚠️ 不能加 \b 词边界：真实字段名几乎全是下划线拼接（web_ui_password、
    # client_secret），下划线在正则里算 \w 字符，`\b` 卡在 `_password` 前面
    # 不会成立，加了 \b 这条规则会对着最想抓的那类名字直接失效（复查时踩过
    # 这个坑，第一次加 \b 版本本地测试通过是因为测的是裸词"password"，
    # 没测下划线拼接的真实字段名）。"pw" 不放进这个列表——不带边界限制时
    # 会命中"upward"这类正常英文词中间的"pw"，误伤概率比它能多抓到的场景更高，
    # 已有的 `-pw` 值规则和 `$pw=` 变量规则足够覆盖这个缩写的实际出现形式。
    # 分隔符字符类要够宽：真实数据里关键词和值之间夹的不只是空格/冒号/引号，
    # 还会出现 Python 字典写法（`cfg["password"] = "xxx"`，中间有 `] = `）、
    # 多层转义的 JSON（`password\": \"xxx`，中间有反斜杠）——复查时发现原来
    # 只写 `[\s:=\"']*` 挡不住这两种，上面提到的那条含感叹号+年份的真实
    # 密码就是这么漏过的（具体值不写在这里，同上）。
    # ⚠️ 2026-08-05 D-8/D-9实测发现：这份关键词表与 SENSITIVE_KEY_PATTERNS
    # （字段名判定用的那份）不一致——后者有"credential"，这份内容匹配用的
    # 没有，导致"credential: xxx"这类内容级出现（不是作为dict key，而是
    # 嵌在字符串正文里）漏检。补上，两份表恢复一致，不再各写一半。
    (_KEYWORD_CTX_RE, _redact_keyword_context),
    # 手机号（中国大陆11位，1开头）
    #
    # ⚠️ 2026-08-05 D-8/D-9实测发现的真实漏检：Python的 `\b` 基于 `\w`判定，
    # 而Python 3默认Unicode模式下汉字算 `\w` 字符——"电话13812345678"里
    # "话"和"1"之间没有\w/非\w的过渡，`\b`不成立，规则直接不触发。原有
    # 自测用例恰好在数字前留了个空格（"手机号 13812345678"）才侥幸测出
    # "通过"，掩盖了这个问题；真实数据里"电话:xxx"/"IP是xxx"这类紧贴写法
    # 很常见。改用显式的"前后不是ASCII字母数字"断言，不依赖\w的Unicode语义。
    (re.compile(r"(?<![0-9A-Za-z_])1[3-9]\d{9}(?![0-9A-Za-z_])"), "[REDACTED_PHONE]"),
    # 邮箱
    (re.compile(r"(?<![0-9A-Za-z_])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![0-9A-Za-z_])"), "[REDACTED_EMAIL]"),
    # IPv4 地址（同样的\b/汉字问题）
    (re.compile(r"(?<![0-9A-Za-z_])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z_])"), "[REDACTED_IP]"),
    # MAC 地址 / WiFi BSSID——独立审查实测原规则对这类标识**完全没有规则**
    # （IPv4 打码了 8,684 处而 MAC 一处未动），MAC 地址能唯一标识物理网卡，
    # 属于设备指纹类隐私。同样改用显式边界断言，避免\b在汉字邻接时失效。
    (re.compile(r"(?<![0-9A-Fa-f:])([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])"), "[REDACTED_MAC]"),
    # SSH 私钥/公钥内容标记块（存在即整体打码提示，具体内容不在这里处理，
    # 因为私钥通常是多行，交给调用方在拼接前判断是否为此类字段直接丢弃）
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY_HEADER]"),
]

# ---------------------------------------------------------------------------
# 规则三：命令行里"结构上一看就是密码"的写法 —— 不管密码本身长什么样（哪怕只有
# 11位、不是乱码），只要出现在这些惯用的密码传递写法里，就整体打码。
#
# 这条规则是实测暴露出的真实教训：本项目部署麒麟环境过程中反复用
# `-pw '密码'`（plink）、`echo '密码' | sudo -S`（sudo密码管道）这类写法，
# 密码本身是人为设定的11位字符串，完全不符合"32位以上乱码"的判断标准，
# 首版脱敏规则对这类真实存在于 Claude Code 会话记录里的命令完全没检测到，
# 导致密码在提取出的数据集里明文出现了近6000次。加上这条结构匹配规则后修复。
# ---------------------------------------------------------------------------
# 🔴 第四轮独立复核 R4-19（三条真实漏检之三）：`sshpass -pQuickPass99` 这种
# **选项与口令紧贴、不留空格**的写法漏检。此前 sshpass 只有"带引号"和
# "空格分隔裸词"两条规则，而紧贴形式是第三种、也是最省事的写法。
#
# 这条本身是一行就能补的实例，但它暴露的是**类没被收口**：R3-02 把
# "`-p` 后面是口令"从"对任何命令都成立"降级成了"命令锚定"，却只给
# mysql 家族写了紧贴规则，sshpass 留在了"只有空格分隔"的旧状态——同一个
# 类下的两个成员各写各的，第四次重演"修实例不修类"。
#
# 因此这里把"哪些命令的紧贴式短选项后面确实是口令"做成一张表，两条规则
# 由同一个函数生成。以后再冒出 `pscp -pw`/`mosquitto_pub -P` 之类，加一行
# 表项即可，不必再各自手写一条正则、也就不会再各自漏一半。
#
# `allow_middle`（命令与选项之间是否允许夹别的参数）为什么必须逐条声明：
#   · mysql 家族：`mysql -h host -u root -pPw` 是常态，中间必须允许夹参数；
#   · sshpass：**必须禁止**。sshpass 的典型用法是 `sshpass -p PW ssh -p 2222
#     host`——后半段那个 `-p` 是 **ssh 的端口**。允许夹参数的话，
#     `sshpass -f file ssh -p2222 host` 会把端口号当口令打码。sshpass 自己的
#     `-p` 永远紧跟在命令名后面，所以收紧到"紧跟"既不丢召回、又杜绝这类误伤。
#     （不加 re.IGNORECASE 也是同一个理由：sshpass 的 `-P` 是"提示语"不是口令。）
_GLUED_PASSWORD_FLAG_COMMANDS = [
    # (命令别名正则, 选项字母, 命令与选项之间是否允许夹别的参数)
    ("mysql|mysqladmin|mysqldump|mysqlshow", "p", True),
    ("sshpass", "p", False),
]


def _glued_password_rule(cmds: str, flag: str, allow_middle: bool):
    """生成一条"命令锚定 + 选项与口令紧贴"的脱敏规则。

    值一侧统一加 `(?=\\S*[A-Za-z0-9])`：与 `-pw` / `sshpass -p` 两条既有规则
    同一个理由——按标记切分后残留的 `\\"` 之类纯转义符不该被当成口令再打一次
    码（真实语料上那个坑实测踩过 84 处）。这条只会让规则更窄，不会更宽。
    """
    tail = r"-" + flag + r"(?=\S)(?=\S*[A-Za-z0-9])\S+"
    if allow_middle:
        return (re.compile(r"\b(" + cmds + r")\b([^\n]*?)\s" + tail, re.IGNORECASE),
                r"\1\2 -" + flag + "[REDACTED_SECRET]")
    return (re.compile(r"\b(" + cmds + r")(\s+)" + tail),
            r"\1\2-" + flag + "[REDACTED_SECRET]")


COMMAND_PASSWORD_RULES = [
    (re.compile(r"(-pw\s+)'[^']*'"), r"\1'[REDACTED_SECRET]'"),
    (re.compile(r'(-pw\s+)"[^"]*"'), r'\1"[REDACTED_SECRET]"'),
    # -pw 后面不加引号的裸词形式（plink/pscp 都支持这种写法）
    # ⚠️ 值必须至少含一个字母或数字：按标记拆分保护上线后，
    # `-pw \"[REDACTED_SECRET]\"` 这类写法会被切成 `… -pw \"` 一段，裸词
    # 规则原本会把仅剩的转义引号 `\"` 当成口令再打一次码、破坏原文结构
    # （真实语料实测 84 处）。要求值里有字母数字，纯标点/转义符不再误伤。
    (re.compile(r"(-pw\s+)(?!['\"])(?=\S*[A-Za-z0-9])\S+"), r"\1[REDACTED_SECRET]"),
    (re.compile(r"(echo\s+)'[^']*'(\s*\|\s*sudo\s+-S)"), r"\1'[REDACTED_SECRET]'\2"),
    (re.compile(r"(echo\s+)\"[^\"]*\"(\s*\|\s*sudo\s+-S)"), r'\1"[REDACTED_SECRET]"\2'),
    # 裸词形式（没加引号），比如嵌套在别的引号里时常见： echo <口令> | sudo -S
    (re.compile(r"(echo\s+)\S+(\s*\|\s*sudo\s+-S)"), r"\1[REDACTED_SECRET]\2"),
    (re.compile(r"(sshpass\s+-p\s*)'[^']*'"), r"\1'[REDACTED_SECRET]'"),
    # sshpass -p 后面不加引号的裸词形式——独立审查指出：此前只写了带引号
    # 形式，裸词（无空格密码时的常见写法，如 `sshpass -p abc123 ssh ...`）
    # 完全没覆盖，实测就是这样一处真实泄漏未被拦下。
    (re.compile(r"(sshpass\s+-p\s+)(?!['\"])(?=\S*[A-Za-z0-9])\S+"),
     r"\1[REDACTED_SECRET]"),
    # chpasswd：整行都是敏感的（典型形如 `echo 'user:pass' | chpasswd`），
    # 独立审查发现一条真实的账户口令通过这个写法泄漏、此前完全没有规则覆盖。
    (re.compile(r"echo\s+\S+.*?\|\s*chpasswd", re.IGNORECASE), "[REDACTED_SECRET] | chpasswd"),
    # 第二轮独立复核 R2-08 对抗集实测漏检：`-p`紧贴密码不留空格的写法
    # （mysql -pSecretPw99 这种，mysql客户端专门支持这种写法，且鼓励这么写
    # 因为命令行历史里明文密码用ps aux能看到，但对本项目而言"看不看得到"
    # 不重要，"数据集里有没有"才重要）——原有规则只写了"-p 空格 密码"形式。
    #
    # 🔴 第三轮独立复核 R3-02：上一版写成 `-p(?=\S)(?!w\b)\S+`，**只排除了
    # `-pw` 一种**，对所有其他 `-p*` 长短选项一律吞掉。真实语料实测命中
    # 9,035 处，其中绝大多数是 `--prefix` / `-print` / `--production` /
    # `-pthread` / `-pxzf` / `-p8080:80` 这类完全正常的命令行选项。
    # ⚠️ 这条规则与 R2-12 要求的"rescrub 全量重跑"处于**直接冲突**状态：
    # 先跑 rescrub 就会把这 9,035 处知识内容永久改写成 [REDACTED_SECRET]。
    # 必须先修规则、再跑 rescrub，顺序反了就毁语料。
    #
    # 修法**不采用**复核建议的"长选项白名单"（--prefix/--production/…）：
    # 那是枚举实例，下次冒出个 --parallel 又会中招，正是本项目反复被点名的
    # "修实例不修类"。修复过程中先后试了两版结构性判据，都被真实语料否掉，
    # 过程本身是这条修复最有价值的部分，完整留档：
    #
    #   试法一：排除 `--` 长选项 + 要求值同时含数字与字母。
    #     真实语料实测 9,035 → 1,018 处，抽样看剩余全是误伤：
    #     `gemini-3.1-pro-preview`（`-p` 是 pro 的开头）、`-py3-none-any.whl`、
    #     `-pve-network`。根因：前瞻 `[^\s=]*` 会扫到整个非空白串末尾，
    #     长 token 里只要**任何位置**有数字就满足条件。
    #
    #   试法二：再加"`-p` 必须在 token 边界（前面是空白/引号/行首）"。
    #     1,018 → 33 处，**但这 33 处依然全是误伤**：`find -print0`、
    #     `ffmpeg -profile:v:0`、`-probesize`、甚至本项目源码里的正则字符串
    #     `-p\s+[A-Za-z]...`。
    #
    #   ★ 关键实测结论：把 `-p<紧贴值>` 这条规则在**全部 7 个数据集文件**上
    #     跑一遍，命中的真实口令是 **0 处**。这条规则当初是照着复核报告里
    #     `mysql -pSecretPw99` 这**一个例句**写的，而本项目真实的口令传递
    #     形态是 `-pw`（plink）、`sshpass -p`、`echo|sudo -S`，三者都各有
    #     专门规则覆盖。一条抓不到任何真实目标、却确定会误伤数千处的规则，
    #     留着是纯负收益。
    #
    #   ★ 根因升级为类：**`-p` 根本没有普适语义**——mysql 里是 password，
    #     docker 里是 port，find 里是 print，tar 里是 preserve，ffmpeg 里是
    #     profile。脱离命令谈"`-p` 后面是口令"本身就不成立。因此改为
    #     **命令锚定**：只在确实用 `-p` 传口令的那几个命令上生效。
    #     这同时顺带修掉了 R3-30 点名的 `redis-cli -a <口令>` 漏检。
    #
    # 🔴 R4-19：这里原本只手写了 mysql 家族一条，sshpass 的紧贴形式漏在外面。
    #     现由 `_GLUED_PASSWORD_FLAG_COMMANDS` 一张表统一生成（说明见上）。
    *[_glued_password_rule(*entry) for entry in _GLUED_PASSWORD_FLAG_COMMANDS],
    (re.compile(r"\b(redis-cli)\b([^\n]*?)\s-a\s+(?!\s)\S+", re.IGNORECASE),
     r"\1\2 -a [REDACTED_SECRET]"),
    # curl -u user:pass 这种"用户名:密码"合一形式，原来完全没有规则覆盖。
    # R3-02 同批修复：`docker run -u 1000:1000` 是 uid:gid，不是账号口令，
    # 用"两侧全是数字"这个结构特征排除；并同样改为命令锚定（-u 在 docker
    # 里是 user、在 curl/wget 里才是凭据），不再对任何命令的 -u 一律下手。
    (re.compile(r"\b(curl|wget)\b([^\n]*?)\s-u\s+(?!\d+:\d+(?:\s|$))\S+:\S+",
                re.IGNORECASE),
     r"\1\2 -u [REDACTED_SECRET]"),
    # R3-30 漏检：postgres://user:pass@host 这类 URI 内嵌口令
    (re.compile(r"\b([a-z][a-z0-9+.\-]*)://([^\s:/@]+):([^\s:/@]+)@", re.IGNORECASE),
     r"\1://\2:[REDACTED_SECRET]@"),
    # 中文"密码"后面紧跟的值（自然语言描述，比如"密码 <口令>"/"密码`<口令>`"）
    (re.compile(r"(密码[:：`]?\s*`?)([A-Za-z0-9_\-\.]{4,})"), r"\1[REDACTED_SECRET]"),
]
REGEX_RULES = REGEX_RULES + COMMAND_PASSWORD_RULES

# ---------------------------------------------------------------------------
# 规则四（兜底）：已知的具体敏感字符串硬匹配替换。
#
# 说明：自然语言里提及密码的方式千变万化（"密码是XXX"、"用XXX登录"、
# 直接在对话里贴一遍……），靠正则模式覆盖不完。对于本项目实际使用过程中
# 已知确实存在、且经检测发现漏检的具体密钥/密码，在这里直接补一条精确匹配，
# 作为规则一~三之外的最后一道保险。
#
# ⚠️ 维护说明：这个列表需要随项目推进持续更新——每次发现新的真实密钥/密码
# 泄漏到提取结果里，就把它加进来重新跑一遍脱敏，不是一次性就能穷举完的。
# 用"词根 + 正则"而不是精确字符串匹配，因为实测发现同一个密码会以不同大小写/
# 不同后缀的变体出现（同一个密码的不同大小写/后缀变体是同一个词根），
# 精确字符串匹配只能抓到写死的那一种，容易漏。
#
# ⚠️ 词根本身就是敏感信息，不能硬编码在会进 git 历史的源码里——这正是
# 《独立评审报告与整改清单.md》§1 指出的事故：早期版本把真实密码词根直接
# 写在这里，随源码一起提交，脱敏模块自己的源码反而成了唯一的泄漏点。
# 改为从同目录下 gitignore 掉的 secrets_blocklist.txt 读取（每行一个词根），
# 该文件不随项目分发，仓库里只留一个 .example 模板。
# ---------------------------------------------------------------------------
def _load_secret_roots() -> list:
    blocklist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "secrets_blocklist.txt")
    if not os.path.exists(blocklist_path):
        return []
    with open(blocklist_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


KNOWN_SECRET_ROOTS = _load_secret_roots()
KNOWN_SECRET_PATTERNS = [
    re.compile(re.escape(root) + r"\w*", re.IGNORECASE) for root in KNOWN_SECRET_ROOTS
]


def _scrub_known_literals(text: str) -> str:
    for pattern in KNOWN_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_KNOWN_SECRET]", text)
    return text


# ---------------------------------------------------------------------------
# 规则五：用户名/主机名一致性假名化——不是打码，是替换成固定的假名。
#
# 独立审查指出：原有规则只打码"看起来像密钥"的内容，对 Linux 主机名（含
# 真实姓名）、Linux/Windows 用户名、完整本机目录树完全没有规则，实测这些
# 标识符在数据集里出现了成千上万次，足以把记录关联回一个具体自然人。
#
# 用**打码**（替换成 [REDACTED_XXX]）会把"同一个人反复出现"这个信号也抹掉，
# 而这个信号本身对知识挖掘有用（工作流程知识依赖"同一会话/同一用户"这类
# 关联）。所以这里用**一致性假名化**：同一个真实标识符，每次都替换成同一个
# 固定假名（如 <真实用户名> → user_a），既去标识化，又不破坏数据内部的
# 关联结构——这正是勘误记录里"过度脱敏切断数据关联"那次事故的反向修正。
#
# 真实的用户名/主机名列表本身就是敏感信息，不能硬编码在会进 git 历史的源码
# 里——处理方式与 KNOWN_SECRET_ROOTS 相同：从同目录下 gitignore 掉的
# identity_map.txt 读取（每行 `真实值<TAB>假名`），仓库里只留 .example 模板。
# ---------------------------------------------------------------------------
# 🔴 第三轮独立复核 R3-28：这里原本自己读同目录的 identity_map.txt，
# 而 可复现性体系/measure.py 读它那边的另一份——两份表各自维护，实测
# 3 条真实值映射到了不同假名，且那一份还把两个不同真实值坍缩成同一个假名。
# 一致性假名化的全部价值就是保持可关联性，映射不一致等于把这个价值抵消掉。
# 改为共用项目根目录的**唯一**一份表与**唯一**一份加载逻辑，理由与取舍
# （为什么假名词表沿用数据侧、R3-29 的全词匹配怎么处理）都写在
# identity_map_loader.py 的文件头里，不在这里重复第二遍。
#
# 另：R3-01 曾指出这段注释里直接抄了真实用户名与真实 GitHub 用户名举例
# ——同一个文件、同一个机制的**第三次**同型泄漏（前两次是 sudo 口令、
# 两条真实密码）。根因是防复发检查只查 secrets_blocklist、不查 identity_map，
# 身份标识这一整类零自动覆盖。现已在 test_detectors.py 把两张表一起纳入。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from identity_map_loader import load_patterns as _load_identity_patterns
except ImportError:      # 只拿到本目录时降级为不做假名化，与表缺失同一口径
    def _load_identity_patterns():
        return []

IDENTITY_PATTERNS = _load_identity_patterns()


def _pseudonymize_identities(text: str) -> str:
    for pattern, pseudonym in IDENTITY_PATTERNS:
        text = pattern.sub(pseudonym, text)
    return text


def _is_sensitive_key(key_name: str) -> bool:
    """判断字段名本身是否属于敏感字段（大小写不敏感）。"""
    lower = str(key_name).lower()
    return any(pattern in lower for pattern in SENSITIVE_KEY_PATTERNS)


# 已经打过的码。整条规则链必须对它们**完全免疫**，否则重跑一次就吃掉一截
# 真实内容——见 _scrub_string 的说明。捕获组是为了让 re.split 把标记本身
# 也留在结果列表里（分段脱敏后原样拼回）。
_REDACTION_MARKER_RE = re.compile(r"(\[REDACTED_[A-Z_]+\])")


def _apply_rules_marker_safe(seg: str) -> str:
    """顺序跑完 REGEX_RULES，且**每条规则都看不见任何已存在的打码标记**。

    与"跑规则链之前切一次"的区别只有一个字：切分从**一次**变成**每条规则
    之前各一次**。代价是同一段文本要被 `re.split` 多跑几十遍（规则条数），
    收益是第 N 条规则产出的标记对第 N+1…M 条规则同样不可见——这正是
    `[REDACTED_LONG_TOKEN[REDACTED_SECRET]` 这类残缺嵌套的成因，完整实测与
    影响面见 `_scrub_string` 的文档字符串。

    为什么不改成"给标记换个不含关键词的名字"（比如 `[X1]`）：那是**改症状**
    ——今天是标记名里带 `TOKEN`/`SECRET` 撞上关键词表，明天新加一条规则的
    输出形态照样可能被另一条规则吃掉。让"已产生的标记"整体对后续规则不可见
    才是类级判据，且不需要改动任何一条既有规则、也不影响已落盘的标记文本。
    """
    for pattern, replacement in REGEX_RULES:
        parts = _REDACTION_MARKER_RE.split(seg)
        if len(parts) == 1:
            seg = pattern.sub(replacement, seg)
            continue
        # 偶数下标是普通文本，奇数下标是标记本身（原样保留）
        for i in range(0, len(parts), 2):
            parts[i] = pattern.sub(replacement, parts[i])
        seg = "".join(parts)
    return seg


def _scrub_string(text: str) -> str:
    """对一段字符串应用所有正则规则，替换掉看起来敏感的内容。

    ═══ 🔴 幂等性：本函数必须能对自己的输出反复运行而不改变结果 ═══════

    第三轮整改时实测发现（这一条**不在复核清单里**，是执行 R3-03 的
    "全量重跑 rescrub"之前做安全检查时自己发现的）：规则链**对自己产出
    的打码标记不免疫**，重跑一次就会吃掉标记后面的一截真实内容——

        `密码[REDACTED_SECRET]",`      → `密码[REDACTED_SECRET]`   （吞掉 JSON 的 `",`）
        `SECRET] 帮我测一下",`          → `SECRET[REDACTED_SECRET]` （吞掉中文正文）
        `SECRET]\\" root@[REDACTED_IP]` → `SECRET[REDACTED_SECRET]` （吞掉后面整段）

    根因：关键词上下文规则的关键词表里有 `secret`/`token`/`密码`，而打码
    标记本身就叫 `[REDACTED_SECRET]` / `[REDACTED_LONG_TOKEN]`——标记里的
    "SECRET" 命中关键词，随后的 `[\\s:="'\\\\\\]\\}]*(\\S{6,})` 就把紧跟在
    `]` 后面的真实内容当成"密钥值"吞掉。**每重跑一次就多吃一截。**

    危险程度：`rescrub_strings.py` 的文档原本明写"已经打码过的值（如
    `[REDACTED_IP]`）不会匹配任何规则，重跑不会有副作用"——**这个声明是
    假的**，而 R2-12/R3-03 恰恰要求"规则一变就全量重跑全部产物"。两条
    要求叠加，等于每次改规则都在数据集上啃掉一圈真实内容，且因为改的是
    JSON 字符串内部、行数不变、肉眼几乎看不出来。

    修法（类级，不是给关键词规则打补丁）：**按标记把字符串切成若干段，
    逐段脱敏后原样拼回**。规则因此在原理上不可能跨越标记边界，"标记免疫"
    不依赖每条规则各自小心，将来新增规则也不会重新踩到这个坑。

    ⚠️ 先试过"把标记换成 \\x00 包裹的占位符"这个更省事的写法，实测**不成立**：
    `\\x00` 也是非空白字符，`\\S{6,}` 照样能吞掉它，`密码[REDACTED_SECRET]",`
    仍然丢掉尾部的 `",`。切分方案没有这个问题，因为规则根本看不到相邻段。

    代价：跨标记边界的敏感内容不会被识别。这是可接受的——边界另一侧本来
    就已经是打过码的内容了。

    ═══ 🔴 上面那个修法只挡住了一半，剩下的一半啃了 15.43% 的语料 ═══════

    第四轮整改期间实测（评审四轮都没点到，是并行写幂等性变异用例的同伴
    先撞上、本文件复验后确认）：上面的切分**只保护"进来时就已经存在"的
    标记，不保护"本趟刚刚产生"的标记**。规则链是 `for pattern in
    REGEX_RULES: seg = pattern.sub(...)` 顺序跑的，靠前的规则产出的
    `[REDACTED_LONG_TOKEN]`、`[REDACTED_SECRET]` 对靠后的规则来说就是**普通
    文本**，而这些标记的名字里恰好带着关键词表里的 `TOKEN` / `SECRET`，
    关键词规则的分隔符类又恰好含 `]`——于是：

        `Abcdefghij0123456789Abcdefghij0123":"nextvalue123"`
          ── 长串规则 ──▶ `[REDACTED_LONG_TOKEN]":"nextvalue123"`
          ── 关键词规则 ──▶ `[REDACTED_LONG_TOKEN[REDACTED_SECRET]`
                             ↑ 右括号被当成分隔符吃掉，标记本身被啃残

    所以 R3-00 那条"连续脱敏两次结果相同"的检查是**测不出这个的**：残缺
    形态在第一趟就已经定型，第二趟起稳定不变，幂等性成立而数据已经坏了。
    修复版与缺陷版在这个形态上表现一样坏，两者都要靠"标记里不许出现关键词"
    或"新标记也要受保护"才能挡住。

    实测影响面（本次改动前，7 个语料文件 36,999 条全量扫描）：
        含 `[REDACTED_XXX[REDACTED_` 残缺嵌套的记录 **5,709 条 = 15.43%**，
        共 **12,332 处**；分布 `[REDACTED_LONG_TOKEN[…` 12,151、
        `[REDACTED_SECRET[…` 163、`[REDACTED_KNOWN_SECRET[…` 18。
    这不是"规则不优雅"，是**已交付语料被吃坏了 12,332 处**。

    修法：把"按标记切分"从"整条规则链跑之前做一次"改成**每条规则之前都做
    一次**（`_apply_rules_marker_safe`）。这样第 N 条规则产出的标记，对第
    N+1…M 条规则同样不可见，保护范围从"输入里的标记"扩到"任何时刻已存在的
    标记"。复用的还是同一套已被验证过的切分机制，不引入新的占位符字符——
    R3 试过的 `\\x00` 占位在这里同样不成立（`\\S` 照吃不误），不重蹈。

    ⚠️ 这条修不回已经被吃掉的内容——那 12,332 处的原文在历史 rescrub 里就
    丢了，只能靠重新抽取原始来源恢复，属于本文件之外的动作。这里能保证的是
    **今后不再产生新的残缺**。
    """
    parts = _REDACTION_MARKER_RE.split(text)
    out = []
    for i, seg in enumerate(parts):
        # split 带捕获组时，奇数下标恒为标记本身，原样保留不参与脱敏
        if i % 2 == 1:
            out.append(seg)
            continue
        seg = _apply_rules_marker_safe(seg)
        seg = _scrub_known_literals(seg)
        seg = _pseudonymize_identities(seg)
        out.append(seg)
    return "".join(out)


def anonymize(data):
    """递归脱敏入口函数。

    - 如果是 dict：遍历每个 key，如果 key 名敏感 -> 整个 value 打码（不再递归进去，
      因为这个字段本身就不该保留原始结构）；否则递归处理 value。
    - 如果是 list：对每个元素递归处理。
    - 如果是 str：跑一遍正则规则做字符串级别的脱敏。
    - 其他类型（int/float/bool/None）：原样返回，没有脱敏必要。
    """
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if _is_sensitive_key(k):
                result[k] = "[REDACTED_FIELD]"
            else:
                result[k] = anonymize(v)  # 递归 —— 这是修复上次漏检事故的关键
        return result

    elif isinstance(data, list):
        return [anonymize(item) for item in data]

    elif isinstance(data, str):
        return _scrub_string(data)

    else:
        return data


# ---------------------------------------------------------------------------
# 自测：验证本模块确实能捕获"上次那次真实漏检事故"的同类场景
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 构造一个和事故现场结构类似的样例：敏感字段嵌套在无害字段名下面两层
    #
    # 🔴 第三轮独立复核 R3-04：这里的 apiKey 原本是一个 `sk-` + 48 位纯小写
    # 字母的串——**正是 SiliconFlow/OpenAI key 的标准形态**，且所处结构
    # （memorySearch.remote.apiKey）就是第一轮真实泄漏的现场结构；而本项目
    # 其余所有 sk- 夹具都是明显合成的短串，只有这一条是完整真实形态。
    # 复核员指出"仓库内无法证伪"。本轮按最保守处置：无论它是否真的是旧
    # 密钥，都换成一眼可辨的短合成串——自测用例要验证的是"嵌套字段能否被
    # 递归打码"，与值本身长什么样无关，没有任何理由保留一个真实形态的值。
    test_case = {
        "agentId": "lucy",
        "memorySearch": {
            "enabled": True,
            "remote": {
                "baseUrl": "https://api.siliconflow.cn/v1",
                "apiKey": "sk-FAKEFAKEFAKE1234"
            }
        },
        "contact_note": "联系人手机号 13812345678，邮箱 test@example.com，服务器IP 192.168.100.2"
    }

    cleaned = anonymize(test_case)
    import json
    print("原始结构（仅测试用，非真实数据）：")
    print(json.dumps(test_case, ensure_ascii=False, indent=2))
    print()
    print("脱敏后：")
    print(json.dumps(cleaned, ensure_ascii=False, indent=2))

    # 断言验证：确保嵌套的 apiKey 被打码了（这正是上次漏检的场景）
    assert cleaned["memorySearch"]["remote"]["apiKey"] == "[REDACTED_FIELD]", \
        "嵌套 apiKey 未被正确脱敏！这正是本模块要修复的漏洞。"
    assert "13812345678" not in json.dumps(cleaned), "手机号未脱敏！"
    assert "test@example.com" not in json.dumps(cleaned), "邮箱未脱敏！"
    print()
    print("[OK] 自测通过：嵌套字段、手机号、邮箱均已正确脱敏。")
