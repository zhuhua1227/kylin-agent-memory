#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freeze_guard.py —— 挖掘器写产物前的冻结守卫（第四轮独立复核 R4-05）

═══ 这是在防什么 ═══════════════════════════════════════════════════

三个挖掘器（`mine_cases.py` / `mine_workflows.py` / `mine_templates.py`）
的 `main()` 里原本**没有 argparse、没有 --force、没有 exists 检查**，
`open(out_path, "w")` 无条件覆写。

第四轮复核实测（本地已复现）：在项目副本里直接敲 `python mine_cases.py`，
`cases.jsonl` 静默从 **27 条变成 15 条**，sha256 从 `0f41a65c…` 变成
`46b792c9…`。而 `评测集/eval_set_frozen.json` 的 `knowledge_base_sha256`
字段冻结的正是这三个文件的哈希，v1.3.0 评测集的 169 道题的 `doc_id`
全部按这三个文件的**行号**编号。

于是：

> **任何人照项目自己写的复现步骤跑一遍，就打破冻结链。**

——而冻结链是本项目"先冻结考题、后跑模型"这条唯一无法靠重跑验证的主张
的全部依据，它的外部锚定（GitHub issue 评论的服务端时间戳）一旦作废
**补不回来**：重新锚定只能证明"今天冻结了"，证明不了当初。

对比之下 `merge_and_validate.py` 早就有 `--allow-stale-inputs` 守卫，
而被冻结、被哈希锚定的偏偏是**一个守卫都没有**的挖掘器。

═══ 为什么单独一个文件，而不是在三个 main() 里各写一遍 ═════════════

本项目被四轮复核反复点名的元问题就是"修实例不修类"。三个挖掘器面对的是
同一个风险、同一份冻结登记表，判定逻辑写三遍必然分叉——写第四个挖掘器的
人也一定会忘。所以守卫是一份，三个挖掘器 import 它。

═══ 判定规则 ═══════════════════════════════════════════════════════

按输出文件当前的 sha256 与冻结登记表比对，分四种情形：

| 情形 | 处理 |
|---|---|
| 文件不存在 | 放行——第一次生成，没有东西可毁 |
| 文件存在但**没被登记** | 放行——没有下游依赖它的冻结 |
| 已登记且**哈希吻合** | **拒跑**（退出码 3），除非 `--force` |
| 已登记但**哈希对不上** | **拒跑**（退出码 3）且措辞不同——冻结链**已经**断了，这时候再覆写一次只会把断裂现场也抹掉 |

`--out 另一个路径` 始终放行：想试新算法就写到别处去比，这是正当用法，
不该被守卫挡住。守卫要挡的是"不小心原地覆写"，不是"不许做实验"。
"""

import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
FROZEN_META = os.path.join(os.path.dirname(BASE), "评测集", "eval_set_frozen.json")

EXIT_REFUSED = 3          # 与 measure_recall.py 的 E-3 语料哈希卫同一约定


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def frozen_registry():
    """读冻结登记表，返回 {文件名: sha256}。读不到就返回空字典。

    ⚠️ 读不到时返回空 = 守卫全放行。这是**有意的降级**：第三方在没有
    评测集目录的情况下跑挖掘器，本来就没有冻结链要保护。但在本仓库里
    这个文件一定存在，所以 `assert_registry_available()` 会在仓库内
    把"读不到"升级成硬失败——不然这道守卫会以"文件找不到"的方式被静默
    绕过，那正是本项目反复出现的"缺失即放行"病。
    """
    if not os.path.exists(FROZEN_META):
        return {}
    try:
        with open(FROZEN_META, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(meta.get("knowledge_base_sha256") or {})


def assert_registry_available():
    """在本仓库内运行时，登记表必须读得到；读不到说明路径写错或文件被删，
    此时守卫是瞎的，宁可当场停下也不要假装在保护。"""
    in_repo = os.path.exists(os.path.join(os.path.dirname(BASE), ".git"))
    if in_repo and not frozen_registry():
        raise SystemExit(
            f"[冻结守卫] 读不到冻结登记表：{FROZEN_META}\n"
            f"  在本仓库内它一定存在。读不到说明路径写错或文件被删——\n"
            f"  此时守卫无法判断任何产物是否被冻结，等于没有守卫。\n"
            f"  这不是可以忽略的警告，请先修好再跑。")


# 本次运行是否被 `--out` 重定向。由 guard() 记账，write_provenance() 读它——
# 别处不要自己去猜（见 write_provenance 的注释）。用单元素列表是为了让
# 模块级状态可被函数内赋值，不必写 global。
_REDIRECTED = [False]


def guard(out_path, force=False, out_override=None):
    """写产物前调用。返回**实际应该写入的路径**；判定为拒跑时直接退出。"""
    _REDIRECTED[0] = bool(out_override)
    if out_override:
        print(f"[冻结守卫] 输出已重定向到 {out_override}，不触碰冻结产物。")
        return out_override

    assert_registry_available()

    name = os.path.basename(out_path)
    registry = frozen_registry()

    if not os.path.exists(out_path):
        return out_path                      # 第一次生成
    if name not in registry:
        return out_path                      # 没有下游冻结依赖它

    current, registered = _sha256(out_path), registry[name]
    if force:
        print(f"[冻结守卫] --force：明知 {name} 被冻结登记仍覆写。\n"
              f"  覆写后必须重跑整条链路（建索引 → 重出题 → 重冻结 → 重锚定 →\n"
              f"  重测召回），否则 evaluation 全套数字与 doc_id 编号都会失配。")
        return out_path

    # ⚠️ 这里用「print 到 stderr + SystemExit(整数)」而不是 SystemExit(字符串)：
    # SystemExit 传字符串时 Python 会把它打到 stderr 然后**以退出码 1 结束**，
    # 拿不到本模块约定的 3。本地实测踩到过——三个挖掘器都拒跑了、但退出码是 1，
    # 与 measure_recall.py 的 E-3 守卫（退出码 3）对不上，脚本化判定会分不清
    # 「被守卫拦下」和「随便什么别的错误」。
    if current == registered:
        print(f"[冻结守卫] 拒绝覆写 {name} —— 它正被冻结登记"
              f"（sha256 {registered[:12]}…）。\n"
              f"\n"
              f"  为什么拦你：v1.3.0 评测集的 169 道题按这个文件的**行号**编 doc_id，\n"
              f"  且它的哈希写在 评测集/eval_set_frozen.json 里、已发成 GitHub issue\n"
              f"  评论做外部时间戳锚定。覆写 = doc_id 全体错位 + 锚定作废，\n"
              f"  而锚定作废**补不回来**（重锚只能证明今天冻结了，证明不了当初）。\n"
              f"\n"
              f"  想做实验：  python {os.path.basename(sys.argv[0])} --out 别的路径.jsonl\n"
              f"  确实要重跑整条链路： 加 --force，然后**必须**依次重跑\n"
              f"    建索引 → 重出题 → 重冻结 → 重锚定 → 重测召回\n"
              f"  （详见 可复现性体系/代码产物落差登记表.md）",
              file=sys.stderr)
        raise SystemExit(EXIT_REFUSED)

    print(f"[冻结守卫] 拒绝覆写 {name} —— 冻结链**已经**断了，先查清再动。\n"
          f"  登记的 sha256： {registered[:12]}…\n"
          f"  文件当前 sha256：{current[:12]}…\n"
          f"\n"
          f"  两者不一致，说明这个文件在冻结之后已经被改过。此刻再覆写一次，\n"
          f"  只会把「什么时候被谁改的」这个现场也一并抹掉。\n"
          f"  请先确认当前文件是不是你要的那一版；确实要覆写就加 --force。",
          file=sys.stderr)
    raise SystemExit(EXIT_REFUSED)


def write_provenance(script_file, out_path, input_paths):
    """产出之后写内容级血缘（第四轮，替代被 touch 绕过的 mtime 判据）。

    放在 freeze_guard 里而不是三个挖掘器各写一遍——理由同本模块存在的理由：
    三处同样的逻辑一定会分叉。

    只在**真的写了交付路径**时写：`--out` 到别处做实验不该在旁边留下血缘
    文件，那会让实验产物看起来像正式产物。

    ⚠️ "是不是交付路径"这件事必须由 `guard()` 记账，不能在这里现推。
    第一版写的是 `abspath(out_path) != abspath(join(BASE, basename(out_path)))`
    ——实测 `--out ./试.jsonl` 时，工作目录恰好就是 BASE，两边算出来一样，
    于是实验产物旁边照样落了一份血缘文件。**用"路径长得像不像"去反推
    "用户是不是重定向了"，本身就是在猜**；`guard()` 明明知道答案。
    """
    if _REDIRECTED[0]:
        return                      # 写到别处了，不留血缘
    sys.path.insert(0, os.path.join(os.path.dirname(BASE), "可复现性体系"))
    try:
        from provenance import write as _write
    except ImportError:
        print("（未找到 可复现性体系/provenance.py，跳过血缘写入）")
        return
    _write(out_path, os.path.abspath(script_file), input_paths)
    print(f"已写血缘：{os.path.basename(out_path)}.provenance.json")


def add_arguments(ap):
    """给挖掘器的 argparse 挂上统一的两个开关——同样只写一份。"""
    ap.add_argument("--force", action="store_true",
                    help="明知产物被冻结登记仍覆写（会作废评测集冻结与外部锚定）")
    ap.add_argument("--out", default=None,
                    help="改写到别的路径，不触碰冻结产物（做实验用这个）")
    return ap
