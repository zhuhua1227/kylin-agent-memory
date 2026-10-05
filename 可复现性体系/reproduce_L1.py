#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reproduce_L1.py —— 一键复现并逐条核对报告中的数字

═══ 这是整套可复现性方案里唯一真正承重的东西 ═══════════════════════

其余材料（测量记录 JSON、日志、哈希）我们都能伪造，**读者没理由仅凭它们相信我们**。
真正让数字站得住的，是"你可以自己跑一遍，我一旦撒谎就当场露馅"。

    可信度不来自「无法伪造」，而来自「容易被验证」。

因此本脚本的设计目标只有一个：**让核对成本降到最低**——
一条命令、纯 Python、不需要银河麒麟、不需要 KYSDK，跑完直接给结论。

═══ 怎么用 ═══════════════════════════════════════════════════════

    python reproduce_L1.py                # 重跑全部 L1 测量并核对
    python reproduce_L1.py --check-only   # 不重跑，只用已有记录核对

依赖：Python 3.8+ 与 numpy。没有 numpy 时会提示安装。

═══ ⚠️ 关于「哪些数字能跨机器复现」的诚实说明 ═══════════════════════

不是所有数字都该期待完全一致，这一点必须讲在前面，否则读者会被误导：

  · **确定性数字**（记录条数、向量维度、相似度分数、磁盘占用）
    —— 与硬件无关，**任何机器上都应完全一致**，不一致就是真有问题。

  · **内存占用** —— 受内存分配器影响，允许约 20% 容差。
    实测麒麟虚机 62.6MB、开发机 61.7MB，可移植性良好。

  · **延迟类数字** —— **随硬件显著变化，不应期待一致**。
    实测同一段代码在麒麟虚机(2vCPU)上比开发机(24核)慢约 2 倍。
    因此本脚本对延迟**只核对结论**（是否达标、比值是否在合理区间），
    不核对绝对毫秒数。报告中的绝对延迟只在其记录的那台机器上成立，
    对应环境指纹写在 records/*.json 的 environment 字段里。

  · **本层无法验证的事**：这些向量确实出自官方麒麟 KYSDK。
    该主张属于 L2 层，需要麒麟环境，由 measure_L2.py 覆盖
    （`python3 measure_L2.py`，跑全部 L2 测量；单独跑某一项见该文件用法说明）。
    我们不假装 L1 能证明它。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# Windows 控制台默认是 GBK，遇到本文件里的中文标点与符号会直接抛
# UnicodeEncodeError 让脚本崩掉。复现脚本是给别人跑的，必须先把这一步做稳——
# 否则读者第一眼看到的是报错，而不是核对结果。
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from measure import load_all_records, _RECORDS_DIR_FALLBACK  # noqa: E402

CLAIMS_PATH = os.path.join(BASE, "claims.json")

# 终端颜色。不支持时自动降级为纯文本，避免在 Windows 老终端里输出乱码转义符
if sys.platform == "win32":
    os.system("")          # 让 Windows 终端启用 ANSI 转义
GREEN, RED, YELLOW, GREY, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"


def fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


# ═════════════════════════════════════════════════════ 各类校验

def pick_stat(metric, claim):
    """按主张声明的统计口径取值。

    ⚠️ 这个函数存在的原因是一次真实的方法问题：

    默认代表值是 P95。但对**很快的操作**（numpy 矩阵乘法只要 1.3ms），
    首次调用要付出预热代价（BLAS 线程池初始化、缓存冷启动），
    在 n=10~20 的样本里，P95 实际上就是「第二慢的那次」，
    **几乎必然落在预热那一次上**——numpy 检索 p50=1.3ms 而 P95=9.8ms，差了 7 倍。

    拿被预热污染的 P95 去算「numpy 比纯 Python 快多少倍」，分母虚高，
    比值从 328× 掉到 49×，结论被严重低估。

    解法不是放宽容差（那是掩盖问题），而是**让每条主张显式声明用哪个统计量**：
      · 延迟上界、达标判定 → 用 p95（保守，代表最坏情况）
      · 倍数关系、趋势比较 → 用 p50（稳健，不受预热离群点影响）

    口径写在 claims.json 里，读者一眼能看到我们用的是哪一个，不含糊。
    """
    stat = claim.get("stat", "value")
    if stat in metric and metric[stat] is not None:
        return metric[stat]
    return metric["value"]


def check_exact(claim, records):
    rec = records.get(claim["record_id"])
    if rec is None:
        return None, "缺少记录", None
    metric = rec["metrics"].get(claim["metric"])
    if metric is None:
        return None, f"记录中无此指标 {claim['metric']}", None

    actual = pick_stat(metric, claim)
    expected = claim["expected"]
    # 浮点数按报告中标注的精度比较：报告写 0.9441，就比到小数点后 4 位，
    # 不去纠结第 8 位——那不是报告声称的精度。
    #
    # ⚠️ 第二轮独立复核 R2-30（P-7）指出这个精度推断本身有一个真实漏洞：
    # 精度是从 `str(expected)` 的小数位数反推的，对 0.675/0.9441 这类
    # "报告里主动圆整过、圆整位数就代表想核对到的精度"的值没问题；但对
    # "恰好是整数比例"的值（比如 expected=1.0，代表100%）会失效——
    # `str(1.0)` 只有1位小数，推出 ndigits=1，`round(0.955, 1) == 1.0`
    # 恒成立，意味着真实数值从100%回归到95.5%这种有意义的退化，仍会被
    # 判定为"通过"。两类值字符串位数规律相反，不能用同一条自动规则处理，
    # 只能让"看起来精度可疑的"那一小撮 claim 显式声明 `ndigits`（本项目
    # 已对 claims.json 里唯一的这类情况——C-241 recall=1.0——加了显式
    # ndigits=6），多数 claim 不受影响，继续用字符串反推的默认行为。
    # `abs_tol`（绝对容差）作为另一种显式声明方式一并支持。
    if isinstance(expected, float):
        actual_f = float(actual)
        if "abs_tol" in claim:
            ok = abs(actual_f - expected) <= claim["abs_tol"]
        else:
            ndigits = claim.get("ndigits")
            if ndigits is None:
                ndigits = len(str(expected).split(".")[-1])
            ok = round(actual_f, ndigits) == round(expected, ndigits)
    else:
        ok = actual == expected
    return ok, f"期望 {fmt(expected)}，实测 {fmt(actual)}", actual


def check_close(claim, records):
    rec = records.get(claim["record_id"])
    if rec is None:
        return None, "缺少记录", None
    metric = rec["metrics"].get(claim["metric"])
    if metric is None:
        return None, f"记录中无此指标 {claim['metric']}", None

    actual = float(pick_stat(metric, claim))
    expected = float(claim["expected"])
    tol = float(claim.get("rel_tol", 0.1))
    diff = abs(actual - expected) / expected if expected else 0
    ok = diff <= tol
    return ok, (f"期望 {fmt(expected)}±{tol*100:.0f}%，实测 {fmt(actual)}"
                f"（偏差 {diff*100:.1f}%）"), actual


def check_bound(claim, records):
    rec = records.get(claim["record_id"])
    if rec is None:
        return None, "缺少记录", None
    metric = rec["metrics"].get(claim["metric"])
    if metric is None:
        return None, f"记录中无此指标 {claim['metric']}", None

    actual = float(pick_stat(metric, claim))
    thr = float(claim["threshold"])
    op = claim["op"]
    # 2026-08-04 补：此前所有 bound 主张只用过严格的 >/<，直到「recall@10 ≥ 85%」
    # 这条——硬指标原文就是"≥"，实测恰好卡在临界值附近时，把 >= 错记成 > 会让
    # 一条本该通过的主张被判不符（反之亦然）。按赛题原文的符号补全，不含糊处理。
    OPS = {
        ">": lambda a, t: a > t, "<": lambda a, t: a < t,
        ">=": lambda a, t: a >= t, "<=": lambda a, t: a <= t,
    }
    ok = OPS[op](actual, thr)
    return ok, f"要求 {op} {fmt(thr)}，实测 {fmt(actual)}", actual


def check_ratio(claim, records):
    rec = records.get(claim["record_id"])
    if rec is None:
        return None, "缺少记录", None
    num = rec["metrics"].get(claim["numerator"])
    den = rec["metrics"].get(claim["denominator"])
    if num is None or den is None:
        return None, "记录中缺少构成比值的指标", None

    nv, dv = float(pick_stat(num, claim)), float(pick_stat(den, claim))
    if dv == 0:
        return None, "分母为 0", None
    actual = nv / dv
    lo, hi = float(claim["min"]), float(claim["max"])
    ok = lo <= actual <= hi
    return ok, f"要求比值落在 [{fmt(lo)}, {fmt(hi)}]，实测 {actual:.2f}×", actual


CHECKERS = {"exact": check_exact, "close": check_close,
            "bound": check_bound, "ratio": check_ratio}

# 各类校验的可移植性说明，直接打在结果里，避免读者误解"为什么延迟不比数值"
PORTABILITY = {
    "exact": "确定性，任何机器应一致",
    "close": "允许容差",
    "bound": "硬件相关，只验结论",
    "ratio": "硬件相关，只验比值",
}


# ═════════════════════════════════════════════════════ 主流程

def main():
    """R3-37：完整重跑创建的临时 records 目录此前**从不清理**（复核时已在
    %TEMP% 下积累了 4 个）。改为跑完即删；只在核对失败时保留，方便追查。"""
    try:
        return _main_impl()
    finally:
        _cleanup_tmp_records()


_TMP_RECORDS_DIR_HOLDER = []


def _cleanup_tmp_records():
    for d in _TMP_RECORDS_DIR_HOLDER:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_RECORDS_DIR_HOLDER.clear()


def _main_impl():
    ap = argparse.ArgumentParser(description="一键复现并核对报告数字（L1 层）")
    ap.add_argument("--check-only", action="store_true",
                    help="不重跑测量，只用已有记录核对")
    ap.add_argument("--records-dir", default=None,
                    help="测量记录读写目录。不指定时：--check-only 模式读交付的"
                         "records/（只读参照，不会被本次运行改动）；完整重跑模式"
                         "默认写一个临时目录，不覆盖交付证据（见 R2-27）")
    args = ap.parse_args()

    print("=" * 78)
    print("麒麟 RAG 项目 —— L1 层数字复现与核对")
    print("=" * 78)
    print("L1 = 纯 Python，不需要银河麒麟环境。")
    print("⚠️ 延迟类数字随硬件变化，本脚本对其只核对结论，不核对绝对数值。\n")

    # 🔴 第三轮独立复核 R3-36：交付物 sha256 清单此前**没接进这里**——而这是
    # 复核员唯一一定会跑的入口，清单不在里面就只能指望别人主动想起来敲
    # `generate_manifest.py --check`，实际等于不存在。先核清单再核数字：
    # 记录若已被改动过，后面那 72 条"核对通过"就都是在核一份改过的东西。
    manifest_failures = []
    try:
        import generate_manifest
        manifest_failures, manifest_warnings, _absent = \
            generate_manifest.check(verbose=False)
        if manifest_failures:
            print(f"{RED}✗ 交付物清单核对不通过（{len(manifest_failures)} 处）——"
                  f"下面的数字核对是在一份被改动过的证据上做的，请勿采信：{RESET}")
            for rel, msg in manifest_failures:
                print(f"    {RED}✗{RESET} {rel}：{msg}")
            print()
        elif manifest_warnings:
            print(f"{YELLOW}⚠ 交付物清单：证据文件全部一致，但 "
                  f"{len(manifest_warnings)} 个脚本的哈希对不上{RESET}")
            for rel, msg in manifest_warnings:
                print(f"{GREY}    {rel}：{msg}{RESET}")
            print(f"{GREY}  脚本改过而记录未重跑，意味着报告里对应的数字可能是"
                  f"上一版代码的产物。不判失败，但请核实。{RESET}\n")
        else:
            print(f"{GREY}交付物 sha256 清单核对通过（records/ + claims.json + "
                  f"关键脚本）。{RESET}\n")
    except Exception as e:
        print(f"{YELLOW}⚠ 清单核对未能执行（{e}），跳过这一步，继续核对数字。"
              f"{RESET}\n")

    # 本次实际被重跑（而非仅读盘）的 record_id 集合——第二轮独立复核 R2-29
    # 指出：结论区把"我们刚刚重新跑出来的数字"和"M-201~M-209 这类只能在
    # 真麒麟机上产生、这里只是读现成 records/*.json 核对"的数字混在同一个
    # "通过"计数里，读者分不清两者的可信度差了一个数量级。
    recomputed_ids = set()
    _skipped_modules = []    # R3-31：输入文件不齐而整模块跳过的
    _crashed_records = []    # R3-31：真抛异常的单项（与"跳过"分开报，别混为一谈）
    _missing_modules = []    # R4-17：目录在、脚本不在——交付物残缺，必须进退出码

    # ★R2-27★：此前完整重跑一次 reproduce_L1.py 就会就地覆盖交付目录
    # records/*.json——复核员自己的机器上跑一次，M-104/M-105 的 7 个交付
    # 数字就被换成了复核机上跑出来的值，交付证据被自己的验证工具悄悄改掉。
    # 默认行为改为：--check-only 时读交付的 records/（本就是只读参照，
    # 不该被这个模式动到）；完整重跑默认写一个临时目录，交付目录全程不碰，
    # 想真的覆盖交付证据（比如确实要更新交付物）必须显式传 --records-dir
    # 指向 records/。
    _tmp_records_dir = None
    if args.records_dir:
        records_dir = args.records_dir
    elif args.check_only:
        records_dir = None  # None → measure.py 内部落回默认的交付 records/
    else:
        _tmp_records_dir = tempfile.mkdtemp(prefix="reproduce_L1_records_")
        records_dir = _tmp_records_dir
        _TMP_RECORDS_DIR_HOLDER.append(_tmp_records_dir)
        # M-201~M-209 这类 L2 记录本次不会重新生成（需要真麒麟环境），
        # 先把交付的 records/ 整份拷进临时目录打底，L1 测量函数随后只会
        # 覆盖它们自己产出的那些 M-1xx 文件——否则 L2 主张会在临时目录里
        # 找不到记录，被误判成"跳过"，而不是它本来该有的"读交付证据核对"。
        if os.path.isdir(_RECORDS_DIR_FALLBACK):
            shutil.copytree(_RECORDS_DIR_FALLBACK, records_dir, dirs_exist_ok=True)
        print(f"{GREY}本次完整重跑的记录写入临时目录（不覆盖交付证据，已用交付"
              f"records/ 打底，L2 类记录原样保留）：\n"
              f"  {records_dir}\n"
              f"  如果确实要更新交付的 records/，显式传 --records-dir "
              f"指向该目录。{RESET}\n")

    if records_dir:
        os.environ["RAG_RECORDS_DIR"] = records_dir

    # 🔴 本轮自查（不在评审清单内，是修 R4-04 时撞见的）：测量抛异常时，
    # `Measurement.__exit__` 会把失败记录写进 `records/failed/`，**交付目录里
    # 那份成功的旧记录原样留着**——这是 P-1c 有意的设计（防止一次崩溃就把
    # 交付证据覆盖成空记录）。但代价是：谁也没读过 `failed/`，于是
    #   「测量崩了」→「旧记录还在」→「核对全绿、退出 0」
    # 这条链路一路畅通。实测：把语料读取搞坏后 M-111 抛异常退出 1，
    # 而 records/M-111.json 仍是上一次的 succeeded=True。
    # 证据保护没错，错在保护完之后没人告诉核对器"这份记录已经过期了"。
    failed_dir = os.path.join(records_dir or _RECORDS_DIR_FALLBACK, "failed")
    stale_records = []
    if os.path.isdir(failed_dir):
        stale_records = sorted(f[:-5] for f in os.listdir(failed_dir)
                               if f.endswith(".json"))
    if stale_records:
        print(f"{RED}✗ 检测到失败记录残留：{', '.join(stale_records)}{RESET}")
        print(f"{GREY}  这些测量最近一次运行是**失败**的（记录在 "
              f"{failed_dir}）。\n"
              f"  交付目录里对应的 records/*.json 是更早一次成功运行留下的，"
              f"下面的核对会拿那份旧记录判绿——\n"
              f"  所以这里先把话说明白：**它们当前不可采信**。"
              f"请先修好测量再重跑，或确认失败原因后删除 failed/ 下的标记。"
              f"{RESET}\n")


    if not args.check_only:
        try:
            import numpy  # noqa: F401
        except ImportError:
            print(f"{RED}缺少 numpy，请先 pip install numpy{RESET}")
            return 2

        print("正在重跑全部 L1 测量（约需两到三分钟）...\n")
        # M-107~M-109：知识挖掘统计，同样纯 Python、不需要麒麟，之前漏接在这里
        # ——独立评审 P-2 指出：52 条主张里有 14 条（C-200~C-212、C-220、C-221）
        # 本可自动复现却只能靠读现成 JSON 核对，接上之后重算主张 26→40 条。
        #
        # 🔴 R3-15：M-114/M-115 同样是纯 Python 的 L1 测量（一个吃固化向量做
        # 子采样对照，一个吃已有逐题结果算置信区间），第二轮新增时**又一次
        # 漏接在这里**——P-2 那次为 measure_B2 修的是实例，没做成"凡 layer=L1
        # 必须在重跑集合里"的检查，于是同一个坑再踩一次，重算占比 78%→72%。
        # 本轮除了接线，更重要的是下面那条覆盖断言，把它从实例升级成类。
        #
        # 🔴 第三轮独立复核 R3-31：孤立文件夹（只拿到 可复现性体系/ 一个目录）
        # 下完整重跑，此前是裸 FileNotFoundError + 整屏调用栈。R2-28 修过一次，
        # 但只修在 measure_L1.py 的 `__main__` 里，而这里是 import 之后直接调
        # ALL[mid]()，绕开了那道检查——修实例没修类，三轮未动。
        # 现在每个测量模块统一提供 check_preconditions()，两条入口都过同一道。
        # 但**光有那道检查还不够**：measure_corpus_size_effect / measure_recall_ci
        # 住在旁边的 评测集/ 目录里，孤立文件夹下连 import 都会 ModuleNotFoundError
        # ——检查函数还没机会跑，栈就已经打出来了。所以 import 本身也得包起来，
        # 模块不在＝那部分交付物没拿到，与"文件不齐"同一类，不是代码坏了。
        sys.path.insert(0, os.path.join(os.path.dirname(BASE), "评测集"))
        _modules = []
        for _name, _ids, _where in (
            ("measure_L1", ["M-101", "M-102", "M-103", "M-104", "M-105",
                            "M-106", "M-111", "M-112", "M-113"], "可复现性体系/"),
            ("measure_B2", ["M-107", "M-108", "M-109", "M-110"], "可复现性体系/"),
            ("measure_corpus_size_effect", ["M-114"], "评测集/"),
            ("measure_recall_ci", ["M-115"], "评测集/"),
            ("measure_conflicts", ["M-301", "M-302", "M-303", "M-304", "M-305", "M-306"], "可复现性体系/"),
            ("measure_preferences", ["M-401", "M-402", "M-403", "M-404"], "可复现性体系/"),
        ):
            try:
                _mod = __import__(_name)
                # 上面那份 _ids 是为了"模块 import 不进来时也说得出跳过了哪几项"
                # 才不得不写死的第二份清单。写死＝会腐烂，所以只要 import 成功
                # 就当场和真身对一次；对不上说明有人加了测量却没更新这里，直接
                # 报错，而不是等到孤立文件夹场景才发现跳过清单是错的。
                assert sorted(_mod.ALL) == sorted(_ids), (
                    f"{_name} 的 ALL={sorted(_mod.ALL)} 与本处登记的 "
                    f"{sorted(_ids)} 不一致，请同步更新 reproduce_L1.py 的模块表")
                _modules.append(_mod)
            except ImportError as e:
                # 🔴 第四轮独立复核 R4-17：此前这里无差别地把 ImportError 当成
                # 「交付物不全」黄字放过，退出码照样 0——复核员**删掉
                # measure_L1.py 这个脚本文件**，重算项从 58 静默降到 55，
                # 一键复现依然全绿。这正是本项目反复栽的那一类：
                # **证据缺失被当成了「跳过检查」而不是「检查失败」**。
                #
                # 但也不能一刀切判失败：R3-31 认定的「只拿到 可复现性体系/
                # 一个文件夹」是合法场景，那时 评测集/ 整个目录都不在。
                #
                # 区分判据是**目录在不在**，不是模块在不在：
                #   · 模块该在的目录**不存在** → 这部分交付物没拿到，跳过合法
                #   · 目录在、模块不在        → 文件被删/被改名，**必须失败**
                _dir = (BASE if _where.startswith("可复现性体系")
                        else os.path.join(os.path.dirname(BASE), "评测集"))
                if os.path.isdir(_dir):
                    # 文件在不在，决定的是**诊断措辞**，不是判定结果——两种都
                    # 是"这台机器上本该能重算却算不了"，都必须计入退出码。
                    # 分开写是因为：删 measure_L1.py 会让 评测集/ 里那两个
                    # 依赖它的脚本一起 ImportError，若一律报成"脚本找不到"，
                    # 读者会去找两个根本没丢的文件。
                    _self_gone = not os.path.isfile(os.path.join(_dir, _name + ".py"))
                    if _self_gone:
                        _why = (f"目录在、脚本不在，说明文件被删掉或改了名——这不是"
                                f"「交付物只拿到一部分」，是**交付物本身不完整**。")
                        _head = f"{_name}.py 不在 {_where} 目录里（该目录是存在的）"
                    else:
                        _why = (f"脚本文件本身在位，是它 import 的东西不在——"
                                f"多半是被它依赖的另一个测量脚本丢了（见本段其他条目）。")
                        _head = f"{_name}.py 在位但 import 失败"
                    print(f"{RED}✗ {_head}，{len(_ids)} 项 L1 测量无法重算{RESET}")
                    print(f"{GREY}  {e}\n  {_why}\n"
                          f"  R4-17：此前这种情况只报黄字、退出码 0，重算项从 58 "
                          f"静默降到 55 而一键复现依然全绿。现在计入退出码。{RESET}\n")
                    _missing_modules.append((_name, _ids, _dir, _self_gone))
                else:
                    print(f"{YELLOW}⚠ 跳过 {_name}（{len(_ids)} 项测量）："
                          f"模块不在这台机器上{RESET}")
                    print(f"{GREY}  {e}\n"
                          f"  该脚本随交付物放在 {_where} 目录下，而该目录本次"
                          f"整个不在——属于「只拿到部分交付物」，是合法场景。\n"
                          f"  只想核对已有记录、不重新生成的话，用："
                          f"python reproduce_L1.py --check-only{RESET}\n")
                    _skipped_modules.append((_name, _ids))

        for mod in _modules:
            mod_name = mod.__name__
            precheck = getattr(mod, "check_preconditions", None)
            msg = precheck() if precheck else None
            if msg:
                print(f"{YELLOW}⚠ 跳过 {mod_name}（{len(mod.ALL)} 项测量）："
                      f"输入文件不齐{RESET}")
                print(f"{GREY}  {msg}{RESET}\n")
                _skipped_modules.append((mod_name, sorted(mod.ALL)))
                continue
            for mid, fn in mod.ALL.items():
                # 前置检查只兜"文件不在位"这一类可预期的情况；真出了代码 bug
                # 仍然要看得见调用栈，但不该让一个模块的崩溃吃掉其余全部测量
                # （同 R3-34 给 test_detectors 加的按组隔离）。
                try:
                    fn()
                    recomputed_ids.add(mid)
                except Exception:
                    print(f"{RED}✗ {mid}（{mod_name}）重跑时抛异常，"
                          f"该项按「未重算」处理，其余测量继续：{RESET}")
                    traceback.print_exc()
                    _crashed_records.append(mid)
                    print()
        print()

        if _skipped_modules:
            total = sum(len(ids) for _, ids in _skipped_modules)
            print(f"{YELLOW}本次有 {total} 项测量因输入文件不齐被跳过"
                  f"（不是失败，是这台机器上没有那些文件）：{RESET}")
            for name, ids in _skipped_modules:
                print(f"{GREY}  {name}：{', '.join(ids)}{RESET}")
            print(f"{GREY}  它们对应的主张下面仍会按「读交付记录核对」处理。"
                  f"{RESET}\n")

    records = load_all_records(records_dir)
    with open(CLAIMS_PATH, "r", encoding="utf-8") as f:
        claims = json.load(f)["claims"]

    # 打印环境，让读者知道这轮数字产自哪台机器
    any_rec = next(iter(records.values()), None)
    if any_rec:
        env = any_rec["environment"]
        print(f"{GREY}本次运行环境：{env.get('os')} / {env.get('cpu_model')} "
              f"/ {env.get('cpu_count')} 核 / {env.get('mem_total_mb')}MB "
              f"/ Python {env.get('python')}{RESET}\n")

    print(f"{'主张':<8}{'校验类型':<10}{'结论':<6}  说明")
    print("-" * 78)

    passed = failed = skipped = 0
    failures = []
    skips = []
    for c in claims:
        checker = CHECKERS.get(c["check"])
        if checker is None:
            print(f"{c['claim_id']:<8}{c['check']:<10}{YELLOW}未知{RESET}")
            skipped += 1
            skips.append((c, f"未知校验类型 {c['check']}"))
            continue

        ok, detail, _ = checker(c, records)
        if ok is None:
            mark, color = "跳过", YELLOW
            skipped += 1
            skips.append((c, detail))
        elif ok:
            mark, color = "通过", GREEN
            passed += 1
        else:
            mark, color = "不符", RED
            failed += 1
            failures.append((c, detail))

        stat_note = f"（口径 {c['stat']}）" if c.get("stat") else ""
        print(f"{c['claim_id']:<8}{c['check']:<10}{color}{mark}{RESET}    {detail}{stat_note}")
        print(f"{GREY}        └ {c['text']}{RESET}")
        print(f"{GREY}          （{PORTABILITY.get(c['check'], '')}｜出处：{c['report']}）{RESET}")

    print("-" * 78)
    n_recomputed = sum(1 for c in claims if c.get("record_id") in recomputed_ids)
    n_readonly = len(claims) - n_recomputed
    print(f"合计 {len(claims)} 条主张：{GREEN}通过 {passed}{RESET}、"
          f"{RED}不符 {failed}{RESET}、{YELLOW}跳过 {skipped}{RESET}")
    if args.check_only:
        print(f"{GREY}（--check-only 模式：全部 {len(claims)} 条均为读取已有记录核对，"
              f"本次未重新执行任何测量代码）{RESET}")
    else:
        # R3-31：模块被跳过时，剩下那些主张并不都是"只能在麒麟上产生"的 M-2xx，
        # 里面还混着"本可重算、只是这台机器缺输入文件"的 L1。原来的措辞会把后者
        # 说成前者，等于替读者把可信度评高了一档，必须分开数。
        skipped_ids = {mid for _, ids in _skipped_modules for mid in ids}
        n_skipped_claims = sum(1 for c in claims
                               if c.get("record_id") in skipped_ids)
        print(f"{GREY}其中 {n_recomputed} 条对应的测量代码本次已重新执行"
              f"（纯 Python 可重算部分）；{n_readonly - n_skipped_claims} 条对应 "
              f"M-2xx 类记录——这些数字只能在真实麒麟环境上产生，L1 在这里只能"
              f"读取已有 records/*.json 核对数字，不是本次重算，可信度弱于前者"
              f"{RESET}")
        if n_skipped_claims:
            print(f"{YELLOW}另有 {n_skipped_claims} 条本来可以在这台机器上重算，"
                  f"但对应模块因缺输入文件/缺脚本被跳过，本次同样只是读记录核对"
                  f"——拿到完整项目目录再跑一次就能真正重算。{RESET}")

    # ★★ R3-15 的类级修复：凡 claims 里声明 layer="L1" 的记录，都必须
    # 出现在本次实际重跑的集合里。P-2（第一轮）为 measure_B2 补过一次接线、
    # R3-15（第三轮）又为 M-114/M-115 补一次——两次都是"修实例"，所以同一个
    # 坑踩了两次。这条断言把它变成"类"：以后任何人新增一个 L1 测量脚本却
    # 忘了接进这里，一键复现当场报红，而不是安静地把它算进"需要麒麟环境"
    # 那一档、悄悄拉低重算占比。
    if not args.check_only:
        declared_l1 = {c["record_id"] for c in claims
                       if c.get("layer") == "L1" and c.get("record_id")}
        # R3-31：因输入文件不齐而整模块跳过的，不算"没接线"——它接线了，
        # 只是这台机器上没有它要吃的文件。两件事必须分开，否则孤立文件夹
        # 场景会被这条断言误报成"漏接线"，把一条真断言变成噪音。
        wired_but_skipped = {mid for _, ids in _skipped_modules for mid in ids}
        # R4-17：脚本文件缺失的那些**是接了线的**，只是文件没了。它们由下面
        # 专门的缺失判定报出并计入退出码，这里减掉，免得再被误报成"漏接线"
        # ——同一个问题报两次不同的原因，会让读者以为是两个问题。
        _missing_ids = {mid for _, ids, _, _ in _missing_modules for mid in ids}
        unwired = sorted(declared_l1 - recomputed_ids - wired_but_skipped
                         - set(_crashed_records) - _missing_ids)
        if wired_but_skipped & declared_l1:
            print(f"\n{YELLOW}注：{len(wired_but_skipped & declared_l1)} 条 L1 记录"
                  f"已接进重跑但因缺输入文件未执行，不计入下面的漏接线判定。{RESET}")
        # 🔴 R4-17：目录在、脚本不在 = 交付物残缺，必须计入退出码。
        # 放在 unwired 判定之前，且**不**并进 wired_but_skipped——那个集合是
        # "合法跳过"的白名单，把残缺塞进去等于又一次用豁免掩盖失败。
        if _missing_modules:
            n_missing = sum(len(ids) for _, ids, _, _ in _missing_modules)
            n_gone = sum(1 for _, _, _, gone in _missing_modules if gone)
            print(f"\n{RED}✗ {len(_missing_modules)} 个 L1 测量模块无法导入"
                  f"（其中 {n_gone} 个是脚本文件本身不见了，其余是被它们连累的），"
                  f"{n_missing} 项测量无法重算：{RESET}")
            for name, ids, d, gone in _missing_modules:
                _tag = "脚本文件不见了" if gone else "文件在位，import 失败"
                print(f"    {name}.py（{_tag}；目录 {d} 存在）→ {', '.join(ids)}")
            print(f"{RED}  这不是「只拿到部分交付物」，是交付物本身不完整。"
                  f"重算项会静默减少而数字照样全绿——正是 R4-17 复核时"
                  f"删掉 measure_L1.py 后看到的现象。{RESET}")
            failed += n_missing
            failures.append(({"claim_id": "L1-MISSING-SCRIPT",
                              "text": f"L1 测量模块无法导入：{[n for n, _, _, _ in _missing_modules]}",
                              "report": "reproduce_L1.py 的交付物完整性检查（R4-17）"},
                             "目录存在但脚本不在，见上方输出"))
        if _crashed_records:
            print(f"\n{RED}✗ {len(_crashed_records)} 项测量重跑时抛异常："
                  f"{', '.join(_crashed_records)}{RESET}")
            failed += len(_crashed_records)
            failures.append(({"claim_id": "L1-CRASH",
                              "text": f"重跑时抛异常：{_crashed_records}",
                              "report": "reproduce_L1.py 的按项异常隔离"},
                             "调用栈见上方输出"))
        if unwired:
            print(f"\n{RED}✗ 以下记录在 claims.json 里声明为 L1（纯 Python 可重算），"
                  f"却没有接进一键重跑：{RESET}")
            for mid in unwired:
                print(f"    {mid}")
            print(f"{RED}  L1 的承诺是「任何机器一条命令就能重算」，声明了却不接线，"
                  f"等于把可重算的数字混进「只能信我们」那一档。{RESET}")
            failed += len(unwired)
            failures.append(({"claim_id": "L1-COVERAGE",
                              "text": f"声明为L1却未接进重跑：{unwired}",
                              "report": "reproduce_L1.py 的 L1 覆盖断言"},
                             "见上方清单"))

    # 跳过（记录/指标缺失）不能算"核对通过"——独立评审 P-1 指出：删掉任何一条
    # 不利记录，旧版实现依然打印"全部主张核对通过"且退出码为 0。一套以
    # "可证伪"为纲的核对工具，必须让"证据消失"和"核对失败"是同一件事，
    # 否则"任何不一致都会红字报出来"这句承诺就是不成立的。
    if failures:
        print(f"\n{RED}以下主张未通过核对，请勿采信报告中的对应数字：{RESET}")
        for c, detail in failures:
            print(f"  {RED}✗{RESET} [{c['claim_id']}] {c['text']}")
            print(f"      {detail}")
            print(f"      出处：{c['report']}")
        print("\n若你是在自己的机器上复现：确定性数字不符说明确有问题；"
              "\n延迟类主张若不符，请先确认机器负载是否正常。")

    if skips:
        print(f"\n{YELLOW}⚠️ {len(skips)} 条主张因记录或指标缺失而无法核对"
              f"（证据缺失，不等于核对通过）：{RESET}")
        for c, reason in skips:
            print(f"  {YELLOW}?{RESET} [{c['claim_id']}] {c['text']}")
            print(f"      {reason}")
        print("   请检查 records/ 是否完整——证据缺失与核对失败等价。")

    if not failures and not skips and not manifest_failures:
        print(f"\n{GREEN}全部主张核对通过。{RESET}")
    elif manifest_failures and not failures and not skips:
        # R3-36：数字全对但证据被改动过——这种情况**不能**打印"全部通过"。
        # 一份改过的记录当然自洽，正是它需要被单独喊出来的原因。
        print(f"\n{RED}主张数字全部对得上，但交付物清单核对不通过"
              f"（见开头 {len(manifest_failures)} 处）——证据文件自冻结以来"
              f"被改动过，"
              f"\n数字自洽只说明改得一致，不说明数字还是原来那份测量的产物。"
              f"{RESET}")

    # 🔴 R4-16：篡改一个**未被任何主张覆盖**的指标（M-111.recall_at_1），
    # 主张层 72/72 全绿毫无察觉，只有 MANIFEST 哈希兜住。读者此前无从知道
    # "72 条主张全绿"到底覆盖了 records/ 里多大比例的数字。现在直接印出来。
    # 放在"本层无法验证的事"旁边——它们是同一类：**主动划出绿灯的边界**。
    coverage_failed = False
    try:
        import metric_coverage
        _cov, _tot, _per = metric_coverage.compute()
        print("\n" + metric_coverage.format_disclosure(
            _cov, _tot, _per, color=(GREEN, YELLOW, RED, RESET)))
        _ok, _msg = metric_coverage.check_no_regression(_cov, _tot)
        if _msg:
            print(f"{'' if _ok else RED + '✗ '}{GREY if _ok else ''}{_msg}{RESET}")
        coverage_failed = not _ok
    except Exception as e:  # noqa: BLE001
        # 披露算不出来 = 边界说不清楚，按失败处理，不静默跳过
        # （同 R2-11/R4-17：证据缺失必须等价于检查失败）
        print(f"\n{RED}✗ 记录指标覆盖率披露无法计算：{e}{RESET}")
        coverage_failed = True

    print(f"\n{GREY}本层无法验证的事：这些向量确实出自官方麒麟 KYSDK。"
          f"\n该主张需要麒麟 V11 + KYSDK 环境，由 measure_L2.py 覆盖——"
          f"\n我们不假装 L1 能证明它。{RESET}")

    if stale_records:
        print(f"\n{RED}⚠️ 再说一遍：{len(stale_records)} 条测量最近一次运行是失败的"
              f"（{', '.join(stale_records)}），上面用的是它们更早一次的旧记录。"
              f"{RESET}")

    return 1 if (failed or skipped or manifest_failures or stale_records
                 or coverage_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
