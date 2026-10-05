#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_and_freeze.py —— 人工复核评测集候选，剔除歧义题，冻结定稿

═══ 2026-08-05：数据层+挖掘层返工后第三次整批重新生成 ═══════════════════

独立评审后数据层补了去重/编码检测/凭据脱敏修复（《多源数据整合/说明报告.md》
16.5节），挖掘层修了假案例判定（K-1）、工作流程渲染（K-2），并新增了模板
抽象器（K-3，第三类知识）。知识单元从 152 条（55案例+97流程）变成 95 条
（27案例+59流程+9模板），doc_id 编号随之整体重新分配，评测集第三次整批
重新生成——不是挑几条改，Gemini 出题本身也不是确定性的，必须从近似重复
扫描到人工核查全部重新走一遍。

结果：178 条候选（89个知识单元产出，6个信息量不足被跳过），
`scan_duplicates.py` 阈值0.60扫出12对、阈值0.75扫出3对。

═══ 本轮新发现的一类碰撞：模板与其源案例/源流程撞车 ═══════════════════

K-3 新增的模板类知识是从案例/流程里"抽"出来的（按失败工具→修复工具、或
工具序列签名聚合），`example_intent`/`example_error` 直接复用了源知识单元
的原文——**模板的出题材料和它的源头材料高度重叠是必然的**，12对里有6对
是这一类：doc_id 61(workflow)/92(template)、62(workflow)/92、6(case)/87、
14(case)/89、28(workflow)/91、1(case)/86。

处理原则：**模板本身的信息量普遍低于它的源头**（如"遇到PowerShell报错换用
`&`"——`&`是PowerShell调用操作符不是有效工具名，是`command_text()`用命令
首词近似工具名的已知副作用，见《知识检索/说明报告.md》4.4节承认的局限），
源案例/源流程才是有实质内容的答案。因此**统一剔除模板一侧的问题，保留
源头一侧**，不是逐对单独判断——这是本轮碰撞里唯一一类可以批量套用同一
原则的，其余仍然逐对核查源头。

═══ 其余碰撞：延续上两轮的判断框架 ═══════════════════════════════════

- **误判（false positive）**：doc_id 20/22（不同集数S01E03/S01E04）、
  46/68（不同真实事件，一个直接检查一个经7步排查）、5/19（不同话题共享
  "No such file"句式模板）——保留原词区分信息的都判不歧义、保留。
- **源头本身是重复的真实事件**：doc_id 58/60，同一天两个会话相隔6分钟
  问了两次邯郸天气（58失败/60成功），整条剔除58保留60——与上两轮的
  邯郸天气/漏密码补发是同一类真实重复。
- **同会话/近邻会话的相似真实事件**：doc_id 61/62，两个不同会话相隔
  仅2.5分钟问了几乎一样的"claudecode为什么不用账号"，判为真实近重复，
  保留信息更早的61、剔除62。
- **丢失区分信息**：doc_id 19/21，同为qbit-pt的qbittorrent.log排查
  （19用tail、21用cat，源头不同但close档都概括成通用问法丢了区分），
  保留19剔除21。

因此复核结论是**逐对人工判断后手写的决定**，不是套一个统一阈值自动执行。
每条决定连同理由记在 REMOVALS 里，接受审计。

═══ 冻结 ═══════════════════════════════════════════════════════════

复核后的结果写入 `eval_set.jsonl`，并生成：
  - `eval_set_frozen.json`：定稿元信息（版本号、SHA256、题目数、冻结时间）
  - 打印一段可直接发到 GitHub issue 的锚定文本（供后续外部时间戳锚定用，
    该步骤本身是否执行、发到哪个仓库，需要用户另行确认，本脚本只生成内容）
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES_PATH = os.path.join(BASE, "eval_candidates.jsonl")
OUT_EVAL_SET = os.path.join(BASE, "eval_set.jsonl")
OUT_FROZEN_META = os.path.join(BASE, "eval_set_frozen.json")
OUT_REVIEW_LOG = os.path.join(BASE, "review_decisions.json")

# 麒麟机上目录名是英文 knowledge，本地开发机是中文 知识检索——同一个历史坑，
# 用环境变量覆盖，和 measure_recall.py 的 RAG_KNOWLEDGE_DIR 处理方式一致。
KNOWLEDGE_DIR = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(BASE, "..", "知识检索")
CASES_PATH = os.path.join(KNOWLEDGE_DIR, "cases.jsonl")
WORKFLOWS_PATH = os.path.join(KNOWLEDGE_DIR, "workflows.jsonl")
TEMPLATES_PATH = os.path.join(KNOWLEDGE_DIR, "templates.jsonl")


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ⚠️ 每次内容级重新冻结**必须**手动升版本号。这个常量写死在源码里，
# 而冻结动作是脚本自动跑的——2026-08-14 实测踩到：整条链路重跑（语料
# 95→86、题目 169→227、全新的 doc_id 空间）之后，脚本照样把新文件标成
# "v1.3.0"。而 v1.3.0 的 sha256 已经做过 GitHub issue 外部锚定，**两份
# 内容完全不同的文件同叫一个版本号，会让锚定链条彻底失去意义**：
# 任何人拿锚定记录去核验，都不知道该对哪一份。
#
# 版本号与内容的绑定不能靠人记得，所以下面加了一道断言：冻结时若发现
# 当前语料指纹与上一版冻结记录不同、而版本号没变，直接拒绝执行。
VERSION = "1.4.0"  # 2026-08-14：数据集重抽(6927→9807)+语料重挖(95→86)后整批重生成
                    # （知识单元152条→95条，新增第三类"模板"），题目内容与
                    # doc_id 编号相对v1.2.0全部不同，是真正的内容变更，不是
                    # 元信息补丁。

# ═════════════════════════════════════════════════════ 人工复核决定

# 每条决定：(doc_id, difficulty) 唯一定位一条候选问题，reason 是人工判断依据。
# 2026-08-05 数据层+挖掘层返工后第三次重新生成，是从 `scan_duplicates.py
# --threshold 0.60` 扫出的 12 对（0.75 档 3 对）逐一人工核查源头知识单元后
# 得出的，doc_id 编号与上两版均不对应——见本脚本文档字符串顶部的说明。
REMOVALS = [
    {
        "doc_id": 92, "difficulty": "close",
        "reason": ("模板与源流程撞车：doc_id=92 是模板（tool_sequence类型，"
                  "Bash→Read，support=3），其 example_intent 直接复用了源流程"
                  "doc_id=61 的原文（'检查一下为什么当前目录下的claudecode不"
                  "需要官方账号也能用'），出题时两者问法高度重合（相似度0.86，"
                  "且与doc_id=62也相似，见下方62的剔除理由——92是这一小簇里"
                  "唯一同时撞两边的'枢纽'，删掉它即可同时化解两组碰撞）。"
                  "模板本身信息量低于源流程（只说'依次用了Bash和Read'，不像"
                  "流程61那样有具体的排查步骤），剔除模板一侧，保留源流程61。"),
    },
    {
        "doc_id": 87, "difficulty": "close",
        "reason": ("模板与源案例撞车：doc_id=87 是模板（tool_switch，"
                  "PowerShell→&，support=3），源自案例6（add2.sh脚本报错）。"
                  "相似度0.76。且该模板质量本身有已知缺陷——`&`是PowerShell"
                  "调用操作符不是有效工具名（`command_text()`用命令首词近似"
                  "工具名的副作用，见《知识检索/说明报告.md》4.4节），剔除"
                  "模板保留有实质内容的案例6。"),
    },
    {
        "doc_id": 62, "difficulty": "close",
        "reason": ("与 doc_id=61 是两个不同会话但近乎相同的真实提问——"
                  "'检查一下为什么当前目录下的claudecode不需要官方账号也能用'"
                  "（61，10:37:01）与'检查一下当前目录下的claudecode为什么不"
                  "登陆就能使用'（62，10:39:35），相隔仅2.5分钟，相似度0.70，"
                  "与本项目此前发现的邯郸天气/漏密码补发是同一类真实近重复"
                  "（不同session_id但明显是同一次真实使用场景里的连续动作）。"
                  "保留时间更早、更完整的61，剔除62。"),
    },
    {
        "doc_id": 89, "difficulty": "close",
        "reason": ("模板与源案例撞车：doc_id=89 是模板（tool_switch，"
                  "Bash→python，technical类，support=2），源自案例14（解析"
                  "torrent bencode文件IndexError）。相似度0.70，且模板问法"
                  "很通用（'在bash里执行报错IndexError怎么修'，不含torrent/"
                  "bencode这类具体场景），剔除模板保留案例14。"),
    },
    {
        "doc_id": 21, "difficulty": "close",
        "reason": ("与 doc_id=19 同为qbit-pt的qbittorrent.log排查（19用tail"
                  "读取失败、21用cat读取失败，源头是两次不同的真实尝试），"
                  "但close档问法都概括成'读取/查qbittorrent.log报错No such"
                  "file'类的通用表述，丢失了tail/cat这个区分信息，相似度0.61，"
                  "与上一轮doc_id=8/10（mysql不同SQL但close档概括成一样的"
                  "'command not found'）是同一类问题。保留19，剔除21。"),
    },
    {
        "doc_id": 91, "difficulty": "close",
        "reason": ("模板与源流程撞车：doc_id=91 是模板（tool_sequence，"
                  "Bash→PowerShell，support=4，涉及4个会话），example_intent"
                  "复用了源流程doc_id=28的原文（'我用的就是misaka的hy2节点啊，"
                  "但还是比较慢'），相似度0.61。模板信息量（只说用了哪两类"
                  "工具）远低于流程28的具体排查步骤，剔除模板保留流程28。"),
    },
    {
        "doc_id": 58, "difficulty": "close",
        "reason": ("与 doc_id=60 是同一真实事件——2026-07-31两个不同会话"
                  "（2ec786f1.../9cfdd06b...）相隔仅6分钟，问了两次几乎一样的"
                  "'今天邯郸天气怎么样'（相似度0.61），与前两版反复出现的"
                  "邯郸天气重复提问是同一类真实事件。58那次WebSearch执行失败"
                  "（is_error=true），60成功，保留信息更完整可靠的60，整条"
                  "剔除58（close+hard都删）。"),
    },
    {
        "doc_id": 58, "difficulty": "hard",
        "reason": "同上，与60同为一次真实重复提问，58整条剔除。",
    },
    {
        "doc_id": 86, "difficulty": "hard",
        "reason": ("模板与源案例在hard档撞车：doc_id=86 是模板（tool_switch，"
                  "exec→python3，technical类，support=2，session_count=1——"
                  "两次出现都在同一会话内），源自案例1（sqlite3命令找不到→"
                  "改用python3）。hard档相似度0.60（'系统没装数据库客户端'"
                  "vs'服务器没装数据库命令行客户端'），且模板的两次支持样本"
                  "同属一个会话，样本独立性本就弱。剔除模板hard档，保留"
                  "案例1的hard档。"),
    },
]

# 复核中主动核查、确认不构成歧义、决定保留的对（供审计——证明这些不是被
# 漏掉，是判断过的）。2026-08-05 第三次核查，doc_id 与前两版均不对应。
CONFIRMED_KEPT_PAIRS = [
    ("doc_id 20/22", "不同真实事件：同一plink命令模板查不同集数（Yani Neko S01E03.mkv vs S01E04.mkv），close档均保留了具体集数，可区分"),
    ("doc_id 46/68", "不同真实事件：直接检查jellyfin(46,单步,07-26) vs 经7步排查确认远程主机jellyfin状态(68,07-31)，不同会话不同复杂度"),
    ("doc_id 5/19", "不同话题：butler_setup.py(移动机器人配置) vs qbittorrent.log(PT下载器日志)，仅因'No such file or directory'句式模板相似度虚高"),
]

# 说明：doc_id 58/60（workflows）、61/62（workflows）在人工核查源头知识单元
# 时确认是同一类真实重复交互（前者是同一天两个会话问了两次一样的问题，
# 后者是2.5分钟内两个会话问了近乎一样的问题），不是挖掘算法的系统性缺陷，
# 因此不在 mine_workflows.py 里加通用去重规则，只在评测集层面剔除——与前
# 两版对 66/102、77/78、126/128 的处理方式一致。
#
# 本轮新增的"模板与源头撞车"这一类（92/87/89/91/86，共5条）不是挖掘算法的
# 缺陷，是模板抽象器（K-3）的设计本身决定的——模板的出题材料必然复用源头
# 原文，这类碰撞会在每一轮模板生成后持续出现，不是一次性问题。如果后续
# 模板数量增长，评测集生成阶段应考虑让模板的出题 prompt 主动避免与其
# example_intent/example_error 原文过近，而不是每次都靠人工复核兜底——
# 这是一个方法论层面的已知限制，记录在《说明报告.md》里，本轮暂不改
# `generate_eval_candidates.py` 的出题 prompt（样本量小，且需要与出题模型
# 反复调试才能确认改进有效，超出当前时间投入）。


def main():
    candidates = [json.loads(l) for l in open(CANDIDATES_PATH, "r", encoding="utf-8") if l.strip()]

    removal_keys = {(r["doc_id"], r["difficulty"]) for r in REMOVALS}
    kept = [c for c in candidates if (c["doc_id"], c["difficulty"]) not in removal_keys]
    removed = [c for c in candidates if (c["doc_id"], c["difficulty"]) in removal_keys]

    print(f"候选问题：{len(candidates)} 条")
    print(f"人工复核剔除：{len(removed)} 条")
    for r in removed:
        print(f"  - [{r['difficulty']}] doc_id={r['doc_id']}: {r['query']}")
    print(f"最终评测集：{len(kept)} 条")

    # 给最终题目编号，便于报告与答辩引用
    for i, c in enumerate(kept):
        c["question_id"] = f"Q-{i+1:03d}"

    # 显式指定 LF 换行：**冻结文件的 sha256 要被外部锚定**，而它在 Windows 上
    # 默认写成 CRLF——那样锚的是一个**平台相关**的哈希：任何人在 Linux 上
    # 用同样内容重新生成，得到的会是完全不同的值，锚定就失去了可核验性。
    # 2026-08-14 在锚定 v1.4.0 之前发现并修正（此前 v1.3.0 锚的就是 CRLF 版）。
    with open(OUT_EVAL_SET, "w", encoding="utf-8", newline="\n") as f:
        for c in kept:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    with open(OUT_REVIEW_LOG, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "candidates_total": len(candidates),
            "removed_count": len(removed),
            "removals": REMOVALS,
            "confirmed_kept_pairs": CONFIRMED_KEPT_PAIRS,
        }, f, ensure_ascii=False, indent=2)

    # 冻结：算最终文件的 SHA256，写元信息
    with open(OUT_EVAL_SET, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    # doc_id 是语料文件的行号，是相对编号——只对 eval_set.jsonl 算哈希不够，
    # 语料一旦重新生成、行序变化，全部 doc_id 会静默失效，而 eval_set.jsonl
    # 自身的哈希校验照样通过（因为它没变）。v1.0.0（241题）就是这么整批
    # 作废的：mine_workflows.py 的 set 遍历非确定性让 workflows.jsonl 行序
    # 变了，但当时的冻结元信息只有一句"cases.jsonl(55条)+workflows.jsonl(97条)"
    # 的自然语言描述，没有哈希，没法在测量前自动发现语料已经不是当初冻结的
    # 那一份。把语料哈希也纳入冻结，配合 measure_recall.py 里的
    # assert_corpus_matches_frozen()，同一枚地雷不会再踩第二次。
    knowledge_base_sha256 = {
        "cases.jsonl": sha256_of(CASES_PATH),
        "workflows.jsonl": sha256_of(WORKFLOWS_PATH),
    }
    knowledge_base_lines = {
        "cases.jsonl": sum(1 for _ in open(CASES_PATH, encoding="utf-8") if _.strip()),
        "workflows.jsonl": sum(1 for _ in open(WORKFLOWS_PATH, encoding="utf-8") if _.strip()),
    }
    # templates.jsonl 是 K-3 之后新增的第三类知识，可能不存在（旧版流水线）——
    # 校验与统计都做成可选，不强制要求这个文件存在。
    if os.path.exists(TEMPLATES_PATH):
        knowledge_base_sha256["templates.jsonl"] = sha256_of(TEMPLATES_PATH)
        knowledge_base_lines["templates.jsonl"] = sum(
            1 for _ in open(TEMPLATES_PATH, encoding="utf-8") if _.strip())

    # ★ 版本号必须随内容变 ★（2026-08-14 新增，起因见 VERSION 常量处的注释）
    #
    # 判据：上一版冻结记录若存在，且它登记的语料指纹与本次不同，说明这是一次
    # **内容级重新冻结**，版本号就必须不同。相同则拒绝执行——不给"忘了改"
    # 留任何余地。这条检查不能靠"人记得"，因为忘记的后果（两份不同内容
    # 共用一个已被外部锚定的版本号）恰恰是最不可逆的那种。
    if os.path.exists(OUT_FROZEN_META):
        try:
            with open(OUT_FROZEN_META, encoding="utf-8") as _f:
                _prev = json.load(_f)
        except (OSError, json.JSONDecodeError):
            _prev = None
        if _prev:
            _prev_kb = _prev.get("knowledge_base_sha256") or {}
            if _prev_kb and _prev_kb != knowledge_base_sha256 \
                    and _prev.get("version") == VERSION:
                raise SystemExit(
                    f"拒绝执行：语料指纹已变（这是一次内容级重新冻结），"
                    f"但版本号仍是 {VERSION}。\n"
                    f"  上一版登记的语料：{ {k: v[:12] for k, v in _prev_kb.items()} }\n"
                    f"  本次的语料指纹：  { {k: v[:12] for k, v in knowledge_base_sha256.items()} }\n"
                    f"\n"
                    f"  两份内容不同的评测集共用一个版本号，会让已做过外部时间戳\n"
                    f"  锚定的那个版本失去意义——拿锚定记录去核验的人，不知道该对\n"
                    f"  哪一份。请先把 review_and_freeze.py 里的 VERSION 升上去。")

    frozen_meta = {
        "version": VERSION,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "file": os.path.basename(OUT_EVAL_SET),
        "sha256": sha256,
        "question_count": len(kept),
        # 🔴 2026-08-14：这里原本硬编码 close/hard 两档，而 v1.4.0 新增了
        # paraphrase 档 77 题——冻结记录里 74+76=150，与 question_count=227
        # 差了整整 77 题，**统计悄悄漏掉一整档且不报错**。
        #
        # 讽刺的是下面 `by_knowledge_type` 的注释正好写着"硬编码的两类字典
        # 会把新增的第三类统计漏掉……改成动态生成规避掉"——同一个文件、
        # 同一个教训，隔了三行没被应用到隔壁那个字段上。
        # 一并改成动态生成，并加一条和值校验，让"漏掉一档"不可能再静默发生。
        "by_difficulty": dict(Counter(c["difficulty"] for c in kept)),
        # 动态按实际出现的 knowledge_type 分组，不写死 case/workflow 两类——
        # K-3 新增了 template 第三类知识后，硬编码的两类字典会把它统计漏掉
        # （不报错，只是这项统计里悄悄看不见，属于本项目反复强调过的
        # "静默失效"那类问题，这里改成动态生成规避掉）。
        "by_knowledge_type": dict(Counter(c["knowledge_type"] for c in kept)),
        "generation_model": "openai/gemini-3.1-pro-preview（经 192.168.100.3:8082 litellm代理）",
        "knowledge_base_source": (
            "; ".join(f"{name}({n}条)" for name, n in knowledge_base_lines.items())
            + "，见 知识检索/说明报告.md"),
        "knowledge_base_sha256": knowledge_base_sha256,
        "knowledge_base_lines": knowledge_base_lines,
    }

    # 分档统计的和必须等于总题数。漏掉一整档（v1.4.0 的 paraphrase 就被
    # 硬编码的两档字典漏掉过）不会抛异常、不会少个文件，只是冻结记录里
    # 两个数字对不上——而没人会去做这道加法。现在让机器做。
    for field in ("by_difficulty", "by_knowledge_type"):
        _sum = sum(frozen_meta[field].values())
        if _sum != len(kept):
            raise SystemExit(
                f"拒绝执行：{field} 各档之和 {_sum} ≠ 总题数 {len(kept)}。\n"
                f"  分档明细：{frozen_meta[field]}\n"
                f"  多半是新增了一档而统计代码没跟上（硬编码分档的典型症状）。\n"
                f"  冻结记录里的分档统计对不上总数，评委一做加法就会发现。")
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
    print(f"麒麟RAG评测集 v{VERSION} 冻结\n"
          f"文件：{os.path.basename(OUT_EVAL_SET)}\n"
          f"SHA256：{sha256}\n"
          f"题目数：{len(kept)}\n"
          f"冻结时间：{frozen_meta['frozen_at']}")


if __name__ == "__main__":
    main()
