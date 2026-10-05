#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mutate.py —— 交付前的变异测试驱动：随机把已修复的缺陷改回缺陷版，
确认对应的检测器一定报红。

═══ 为什么必须有这个脚本，而不是"记得做" ═══════════════════════════════

第二轮独立复核就建议过"把变异测试纳入流程"，第三轮复核在全项目里找不到
任何落地痕迹：**无 CI 配置、无 pre-commit、无 Makefile、无驱动脚本**；
"变异测试"一词在 6 份 md 里出现过，全部只是给 `test_detectors.py` 起的
**称呼**，没有一处描述"每次整改前随机回退 N 个缺陷"这个动作。

    一个不在流程里的方法论 = 不存在的方法论。

本脚本把它变成一条能跑的命令。**下面的 MUTATIONS 列表本身就是"已修复
缺陷清单"**——每修一个缺陷就在这里加一条，列表越长，回归防线越厚。

═══ 与 test_detectors.py 的分工 ═══════════════════════════════════════

- `test_detectors.py` 回答：**当前代码**下，检测器能不能抓到已知缺陷？
  （它靠自己构造的缺陷样本，不改动任何源码）
- `mutate.py` 回答：把**真实的修复代码**改回缺陷版，检测器会不会报红？
  （它真的去改源码，在副本里跑，跑完还原）

后者更强：它验证的是"这条修复确实被某个检测器保护着"，而不是"我们另外
写了一个能抓这类问题的检测器"。**改不回红的，说明那条修复没有检测器
保护，它迟早会在某次重构中悄悄退化。**

═══ 🔴 第四轮独立复核 R4-01：本脚本自己曾是一个恒真检查器 ═══════════════

第三轮交付时本脚本 `--all` 打印「被检出 10，未检出 0」，评审据此写下"三轮
以来第一次有可运行的质量门禁"。**那个 10/10 是结构性假绿**，两个缺陷叠加：

  ① 沙箱用 `ignore_patterns(".git", …)` 把 `.git` 排除了，于是被测的
     `test_detectors.py` 里"防复发-git枚举到受跟踪文件"那一组**必然报红**；
  ② 本脚本的唯一判据是 `rc != 0`（`_run_detectors` 只返回退出码，旧注释还
     写着"不关心具体是哪条用例红"）。

两条相乘 = 判据恒真：

> **即使把全部检测器删光，`mutate.py --all` 的输出一字不变。**

第四轮亲自跑的空变异对照（复刻沙箱配方、不注入任何变异）：
  · 排除 `.git`  → rc=1，红 4 条，全是 git 枚举不到导致的
  · 保留 `.git`  → rc=0，105/105 全绿

**为解决"检查器是摆设"而造的工具，自己成了摆设（R2-04 原病）。**

修法两条，缺一不可：
  ① 沙箱保留 `.git`（它只有 1.8MB，排它省不下什么，却毁掉整个判据）；
  ② 判据改成**差分**：`变异后的红用例集合 - 基线红用例集合` 必须非空。
     退出码不再参与判定——退出码是个布尔值，装不下"是哪几条红"这个信息，
     而"是哪几条红"恰恰是唯一能证明"这条变异真的被抓到了"的证据。

并且：**跑变异之前先跑一次空变异基线**，基线里出现任何**没有登记在案**的
红，就直接 `SystemExit` 停下——基线不干净时，任何"检出"都可能只是基线噪声，
此时继续跑等于输出一堆没有证据力的绿灯。这条方法论适用于本项目每一个新增
检查器，不止本脚本。

═══ 用法 ═══════════════════════════════════════════════════════════

    python mutate.py                # 随机抽 3 个缺陷做变异
    python mutate.py --n 5          # 抽 5 个
    python mutate.py --all          # 全跑（交付前建议用这个）
    python mutate.py --list         # 只列出当前登记的缺陷，不执行
    python mutate.py --baseline-only  # 只跑空变异基线（几分钟，用来自检）
    python mutate.py --all --jobs 1   # 串行（默认 4 路并行，各自独立沙箱）

任一变异未被检出 → 退出码 1；基线不干净 → 退出码 2。

⚠️ 安全性：全部变异在**临时副本**里进行，原项目目录只读。即使脚本被
中途杀掉，也不会留下被改坏的源码——这一点与 test_detectors 用例 7 早期
"就地改交付文件"的做法形成对照（那个已按 R3-34 / R4-18 改掉）。
"""

import argparse
import concurrent.futures
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ═══ 基线已知红名单（R4-01） ═══════════════════════════════════════════
#
# 基线**应该**全绿。这里每多一条，本工具的判别力就少一分——因为登记在案的
# 红用例在差分里被扣掉了，落在它们身上的变异将无法被检出。所以这个名单的
# 正确长度是 0，每一条都必须写清：为什么现在允许它红、**什么时候必须摘掉**。
#
# ⚠️ 名单是精确全名匹配，不是前缀/子串匹配：写模糊了就等于给一整类用例开
#    免检通道，那是 R3-02 `-p` 误伤、R2-04 恒真测试反复栽过的同一个跟头。
KNOWN_BASELINE_RED = {
    # ── R4-16：9 个 .scrub.json 旁路文件的规则指纹全部过期 ──────────────
    # 这是**真实状态**，不是环境噪声：数据集当前是用一份旧脱敏规则处理的。
    # 之所以暂时登记而不是当场停下，是因为让它变绿的唯一办法是重跑
    # `rescrub_strings.py` 改写真实数据集，而那件事会打破已被外部锚定的
    # 冻结链，必须作为独立步骤单独决策、单独记录。
    # ★ 摘掉条件：跑过一次 rescrub_strings.py、9 个指纹刷新之后，把整段删掉。
    #   删掉之后基线必须全绿——这是验收这条整改的判据。
    f"rescrub-旁路指纹是当前规则-{name}": "R4-16：sidecar 指纹过期，待重跑 rescrub_strings.py"
    for name in (
        "memory_source_records.jsonl", "rejected.jsonl", "tool_execution_records.jsonl",
        "dataset_claude_code.jsonl", "dataset_manual_config.jsonl",
        "dataset_openclaw_lucy.jsonl", "dataset_user_history.jsonl",
        "generation_raw_log.jsonl", "generation_skipped.jsonl",
    )
}


# 每条 = (缺陷编号与描述, 相对路径, 原文片段, 替换成的缺陷版)
# 判据：改完之后 test_detectors.py **必须新增**至少一条红用例（相对基线）。
MUTATIONS = [
    ("K-1 判等过滤（修复命令与失败命令完全相同时不得产出案例）",
     "知识检索/mine_cases.py",
     "if failed_cmd.strip() == fixed_cmd.strip():",
     "if False:  # MUTATED"),

    ("R2-01 pscp 虚假失败检测",
     "知识检索/mine_cases.py",
     'return "pscp" in cmd.lower() and bool(_PSCP_TRANSFER_DONE.search(out_text))',
     'return False  # MUTATED'),

    ("R2-08 AWS Access Key 规则",
     "多源数据整合/anonymize.py",
     r'(re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_SECRET]"),',
     r'(re.compile(r"\bMUTATEDNEVERMATCH[0-9A-Z]{16}\b"), "[REDACTED_SECRET]"),'),

    ("R2-08 SHA256 豁免（不得误伤锚定证据链）",
     "多源数据整合/anonymize.py",
     'or re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", m.group(0))',
     'or False  # MUTATED'),

    ("R3-00 脱敏幂等性（不得吃掉已有打码标记后面的内容）",
     "多源数据整合/anonymize.py",
     "    parts = _REDACTION_MARKER_RE.split(text)",
     "    parts = [text]  # MUTATED"),

    ("D-5 schema 类型校验（字符串冒充 bool 必须被拦）",
     "多源数据整合/merge_and_validate.py",
     "def _type_ok(value, type_spec):",
     "def _type_ok(value, type_spec):\n    return True  # MUTATED"),

    ("R2-06 输入新鲜度检查",
     "多源数据整合/merge_and_validate.py",
     "    stale = [p for p in input_paths if os.path.exists(p) and os.path.getmtime(p) > out_mtime]",
     "    stale = []  # MUTATED"),

    ("R2-11 绕过③：冻结元信息缺失必须拒跑",
     "评测集/measure_recall.py",
     '        raise SystemExit(\n            f"冻结元信息 {FROZEN_META_PATH} 不存在',
     '        return  # MUTATED\n        raise SystemExit(\n            f"冻结元信息 {FROZEN_META_PATH} 不存在'),

    ("R3-12 绕过⑥：语料哈希登记不完整必须拒跑",
     "评测集/measure_recall.py",
     "    missing_corpus = REQUIRED_CORPUS - set(expected)",
     "    missing_corpus = set()  # MUTATED"),

    ("R2-22 bug③：模板支持度门槛必须按会话数而非出现次数",
     "知识检索/mine_templates.py",
     "        if len(sessions) < MIN_SUPPORT:\n            continue\n        templates.append({\n            \"kind\": \"template\",\n            \"template_type\": \"tool_switch\",",
     "        if len(items) < MIN_SUPPORT:  # MUTATED\n            continue\n        templates.append({\n            \"kind\": \"template\",\n            \"template_type\": \"tool_switch\","),

    ("知识冲突-纯追加过滤（旧文本整体被新文本包含时不得产出冲突候选）",
     "知识检索/mine_conflicts.py",
     "            if old_text in new_text:",
     "            if False:  # MUTATED"),

    ("知识冲突-同一次坐下来写不算冲突（同会话且同一天的连续修订）",
     "知识检索/mine_conflicts.py",
     "            if old_session == e[\"session_id\"] and old_day == new_day:",
     "            if False:  # MUTATED"),

    ("偏好-跨会话门槛（单会话做法不得当成习惯，R2-22 bug③同款判据）",
     "知识检索/mine_preferences.py",
     "        if len(top_sessions) < min_sessions:",
     "        if False:  # MUTATED"),

    ("偏好-切分保持会话完整（防止会话被按时间戳腰斩）",
     "知识检索/mine_preferences.py",
     "    ordered = sorted(starts, key=lambda s: starts[s])",
     "    ordered = sorted(starts)  # MUTATED 按 id 排序，打乱时间序"),

    ("S1-非Bash过滤（Write/Read 同路径不得当成同一命令）",
     "知识检索/mine_conflicts.py",
     'if r.get("tool_name") != "Bash":',
     "if False:  # MUTATED"),

    ("S1-跨会话门槛（同会话内的重复执行不得产出冲突）",
     "知识检索/mine_conflicts.py",
     "if len(sessions) < 2:",
     "if False:  # MUTATED"),

    ("消融修复§C-gold_type豁免名单不得被放宽（防 meta 式标签泄漏复发）",
     "评测集/measure_recall.py",
     "    use_meta_oracle = config == \"meta_oracle\"",
     "    use_meta_oracle = config in (\"meta_oracle\", \"all\")  # MUTATED"),
]


# `check()` 打印的格式：`  [FAIL] 用例名` 或 `  [FAIL] 用例名  (细节)`。
# 用例名里出现过 `(`（如"…枚举为空则本检查无意义，必须红)"），但**不会**出现
# 连续两个空格，所以在第一个 `  (` 处切断是安全的；细节里出现 `)` 也不影响。
_FAIL_LINE_RE = re.compile(r"^\s*\[FAIL\]\s+(?P<name>.+?)(?:\s\s+\(.*\))?\s*$")


def _parse_red_cases(stdout: str) -> set:
    """从 test_detectors.py 的输出里解析出**红用例名的集合**。

    为什么要解析文本而不是让被测套件返回结构化结果：被测的是**沙箱副本里
    那一份** test_detectors.py，它是子进程、而且随变异而变，本脚本能依赖的
    只有它的 stdout 契约。反过来说这也是好事——如果哪天有人改了输出格式，
    基线会立刻变成空集合、差分恒为空、全部变异报"未检出"，是**朝安全方向
    失败**（吵闹地失败），而不是像旧版那样朝"全绿"方向失败。
    """
    red = set()
    for line in stdout.splitlines():
        m = _FAIL_LINE_RE.match(line)
        if m:
            red.add(m.group("name").strip())
    return red


def _make_sandbox(tmp: str) -> str:
    """复制一份项目副本。

    ★ R4-01：`.git` **必须**复制进来。旧版把它排除掉，导致被测套件里 4 条
      git 相关用例必然报红、rc 恒为非零、判据恒真。它只有 1.8MB，排除它省
      下的时间可以忽略不计，毁掉的却是整个工具的判别力。
      —— 教训一般化：**沙箱与真实环境的每一处差异，都是判据里的一个自由
      变量。** 差异必须逐条论证"与被测对象无关"，不能凭"这个大概不影响"。
      下面剩的三条：`__pycache__`/`*.pyc` 是可再生的编译产物，
      `knowledge_store_numpy` 是几百 MB 的向量库且 test_detectors 不读它。
    """
    root = os.path.join(tmp, "RAG")
    shutil.copytree(RAG, root, symlinks=False, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "knowledge_store_numpy"))
    return root


def _run_detectors(root: str):
    """在副本里跑一遍被测套件，返回 (退出码, 红用例名集合)。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, os.path.join(root, "可复现性体系", "test_detectors.py")],
        cwd=os.path.join(root, "可复现性体系"), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800)
    return r.returncode, _parse_red_cases(r.stdout)


def run_baseline():
    """空变异基线：完全按变异时的配方造沙箱，但**不注入任何变异**。

    这是本脚本（以及本项目任何新增检查器）的第一道自检。基线不干净说明
    判据里混进了与被测对象无关的变量，此时它的任何"检出"都不可信——第四轮
    正是靠这一次对照实验，当场戳破了自己的 10/10。
    """
    with tempfile.TemporaryDirectory(prefix="mutate_base_") as tmp:
        root = _make_sandbox(tmp)
        rc, red = _run_detectors(root)
    return rc, red


def _run_one(item):
    """跑一条变异，返回 (状态, 描述, 附加信息)。状态 ∈ ok/undetected/skip。

    每条变异独占一个沙箱：并行跑时互不干扰，串行跑时也避免"上一条变异没还
    干净"这种最经典的测试污染。
    """
    desc, rel, old, new, baseline_red = item
    with tempfile.TemporaryDirectory(prefix="mutate_") as tmp:
        root = _make_sandbox(tmp)
        target = os.path.join(root, *rel.split("/"))
        if not os.path.exists(target):
            return "skip", desc, f"找不到 {rel}"
        with open(target, encoding="utf-8") as f:
            src = f.read()
        if old not in src:
            # 源码变了、锚点失效——这本身要报出来，不能当成"通过"
            return "skip", desc, "锚点片段已不在源码中，变异条目需要更新"
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new, 1))

        _rc, red = _run_detectors(root)
        newly_red = red - baseline_red
        if newly_red:
            return "ok", desc, sorted(newly_red)
        # 差分为空 = 这条修复没有任何检测器守着。附上"是否有用例消失"这个
        # 信息：基线红了、变异后反而不红，多半说明变异改坏了检查器自身。
        vanished = baseline_red - red
        return "undetected", desc, sorted(vanished)


def main():
    ap = argparse.ArgumentParser(description="变异测试驱动：确认每条修复都有检测器保护")
    ap.add_argument("--n", type=int, default=3, help="随机抽取的变异条数（默认3）")
    ap.add_argument("--all", action="store_true", help="全部跑一遍")
    ap.add_argument("--list", action="store_true", help="只列出登记的缺陷")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（便于复现某次抽样）")
    ap.add_argument("--jobs", type=int, default=4, help="并行沙箱数（默认4，各自独立目录）")
    ap.add_argument("--baseline-only", action="store_true",
                    help="只跑空变异基线并打印红用例，不做任何变异")
    args = ap.parse_args()

    if args.list:
        for i, (desc, path, _, _) in enumerate(MUTATIONS):
            print(f"{i:>3}  {desc}\n     ← {path}")
        print(f"\n共 {len(MUTATIONS)} 条已登记缺陷")
        return 0

    # ═══ 第一步：空变异基线（R4-01 的核心，任何变异结果都建立在它之上）═══
    print("=" * 78)
    print("第 0 步｜空变异基线：干净副本上跑一遍 test_detectors.py")
    print("=" * 78)
    print("基线不干净 → 差分判据失去意义 → 本工具输出的任何绿灯都不可信。\n")
    base_rc, baseline_red = run_baseline()
    unexpected = baseline_red - set(KNOWN_BASELINE_RED)
    print(f"基线退出码 {base_rc}，红用例 {len(baseline_red)} 条"
          f"（登记在案 {len(baseline_red & set(KNOWN_BASELINE_RED))}，"
          f"意外 {len(unexpected)}）")
    for name in sorted(baseline_red & set(KNOWN_BASELINE_RED)):
        print(f"  · [已登记] {name}  —— {KNOWN_BASELINE_RED[name]}")
    if unexpected:
        for name in sorted(unexpected):
            print(f"  ✗ [意外]   {name}")
        print(f"\n基线不干净：上面这 {len(unexpected)} 条红用例没有登记在 "
              "KNOWN_BASELINE_RED 里。\n"
              "在它们红着的时候，落在它们身上的变异一定检不出来，而本工具会\n"
              "把这种情况报成绿灯——这正是 R4-01 要消灭的那个病。\n"
              "请先修好这些用例；确属暂时无法修复的，登记进 KNOWN_BASELINE_RED\n"
              "并写清摘掉条件，不要笼统放行。", file=sys.stderr)
        raise SystemExit(2)
    if not baseline_red:
        print("  （基线全绿——这是最理想的状态）")
    if args.baseline_only:
        return 0

    chosen = MUTATIONS if args.all else random.Random(args.seed).sample(
        MUTATIONS, min(args.n, len(MUTATIONS)))

    print("\n" + "=" * 78)
    print(f"第 1 步｜变异测试：抽取 {len(chosen)} / {len(MUTATIONS)} 条已修复缺陷改回缺陷版")
    print("=" * 78)
    print("判据：改回缺陷版后，test_detectors.py **必须新增**至少一条红用例。")
    print("（不看退出码——退出码在基线里就已经是 1，看它等于什么都不看。）")
    print("未被检出 = 这条修复没有检测器保护，迟早会悄悄退化。\n")

    tasks = [(desc, rel, old, new, baseline_red) for desc, rel, old, new in chosen]
    results = []
    if args.jobs > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for r in pool.map(_run_one, tasks):
                results.append(r)
    else:
        results = [_run_one(t) for t in tasks]

    undetected, skipped = [], []
    for status, desc, info in results:
        print(f"→ {desc}")
        if status == "ok":
            print(f"   [OK]   新增红用例 {len(info)} 条——这条修复有保护")
            for n in info[:6]:
                print(f"            ↳ {n}")
            if len(info) > 6:
                print(f"            ↳ …另有 {len(info)-6} 条")
        elif status == "skip":
            print(f"   [SKIP] {info}")
            skipped.append((desc, info))
        else:
            print("   [FAIL] 相对基线**没有任何用例变红**——这条修复没有检测器保护")
            if info:
                print(f"            ↳ 反而有 {len(info)} 条基线红用例消失了："
                      f"{info[:3]}（多半是变异改坏了检查器自身）")
            undetected.append(desc)

    print("\n" + "-" * 78)
    print(f"变异 {len(chosen)} 条：被检出 {len(chosen)-len(undetected)-len(skipped)}，"
          f"未检出 {len(undetected)}，跳过 {len(skipped)}")
    if skipped:
        print("\n以下变异未能执行（锚点失效说明源码结构变了，条目要跟着更新）：")
        for desc, why in skipped:
            print(f"  ? {desc}  —— {why}")
    if undetected:
        print("\n以下修复没有检测器保护，必须补检测用例：")
        for desc in undetected:
            print(f"  ✗ {desc}")
        return 1
    if skipped:
        return 1
    print("\n全部被抽中的修复都有检测器保护。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
