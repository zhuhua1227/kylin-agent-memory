#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rescrub_strings.py —— 用当前的脱敏规则对已交付数据集做一次增量再脱敏（独立评审D-8/D-9，R2-12）

背景：M-113 标注测试发现 `_scrub_string()` 的多个真实 bug（`\\b`词边界在
汉字邻接时失效、content级关键词表漏了credential、UUID被误伤、R2-08 新增的
sk-左边界/云厂商token/长串豁免/关键词散文误伤），修复后回查已交付的
`dataset/memory_source_records.jsonl`，实测发现 27 处私网IP因`\\b` bug
漏检（如"至192.168.137.1"这类紧贴汉字的写法）。

只重跑 `_scrub_string()`（字符串级正则替换），不重跑 `anonymize()`（会重新
按字段名判定，冒着重新触发D-4"整条脱敏"类问题的风险）——这样做是安全、
幂等的：`_scrub_string` 不看字段名，只对字符串内容做正则替换，已经打码过
的值（如"[REDACTED_IP]"）不会匹配任何规则，重跑不会有副作用。

═══ 第二轮独立复核 R2-12 指出的两个问题，均已修复 ═══════════════════════

1. **只覆盖1/7个数据集文件**：`IN_PATH` 原来硬编码指向
   `dataset/memory_source_records.jsonl`，本项目实际有7个数据集文件（含
   `rejected.jsonl`与4个提取脚本各自产出的`dataset_*.jsonl`），其余6个
   从未被这个"修复后重新脱敏"流程覆盖过。改为遍历全部产物。

2. **规则/映射变更后不会自动重跑**：脱敏规则（`anonymize.py`）或身份
   假名化映射（`identity_map.txt`）每次增强，都必须全量重跑全部产物，
   否则新规则形同虚设——但此前没有任何机制记住"上次是用哪个版本的规则
   脱敏的"，全凭人记得手动重跑。改为给每个产物写一个同名`.scrub.json`
   旁路文件，记录处理时用的规则指纹（`anonymize.py`+`secrets_blocklist.
   txt`+`identity_map.txt`三者内容的sha256），下次运行时先比对指纹，
   不一致才重新处理，一致则跳过（避免规则没变时无意义地重复扫描几十MB
   文件）。

用法：
    python rescrub_strings.py                # 增量：只处理指纹不匹配的文件
    python rescrub_strings.py --force-all     # 强制全部重新处理，忽略指纹
"""
import argparse
import glob
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from anonymize import _scrub_string  # noqa: E402

DATASET_DIR = os.path.join(BASE, "dataset")


# 🔴 第三轮 R3-01/R3-05：受跟踪文件里还有 74 处真实身份标识残留，全部在
# 评测集的**出题日志**里（generation_raw_log.jsonl 73 处 + generation_
# skipped.jsonl 1 处）——它们记录的是发给 Gemini 的知识单元原文，与
# dataset/ 一样是"从真实使用记录里抄来的内容"，却因为不在 dataset/ 目录、
# 文件名也不叫 dataset_*，被本脚本的两条 glob 整个漏在覆盖范围之外。
# 根因是"按目录/文件名前缀圈定范围"这个做法本身：判据应该是**这个文件里
# 有没有承载真实使用记录**，而不是它恰好放在哪个目录。
#
# ⚠️ 同目录下的 eval_set.jsonl / eval_candidates.jsonl **不能**纳入：它们
# 被 eval_set_frozen.json 用 sha256 冻结锚定，任何改写都会打破冻结哈希、
# 连带作废 v1.3.0 全套召回率数字。经实测这两个文件当前**零身份残留**
# （见 test_no_real_secret_in_tracked_files），无需改写，因此把它们显式
# 排除、并在下面用 _FROZEN_NEVER_SCRUB 记录原因，而不是靠"忘了写进 glob"
# 这种偶然。
_EXTRA_SCRUB_GLOBS = [
    os.path.join(os.path.dirname(BASE), "评测集", "generation_*.jsonl"),
]
_FROZEN_NEVER_SCRUB = {
    "eval_set.jsonl": "被 eval_set_frozen.json 的 sha256 冻结锚定，改写即作废 v1.3.0 全套数字",
    "eval_candidates.jsonl": "冻结集的候选来源，同上",
}


def _dataset_files(dataset_dir=None, base_dir=None, extra_globs=None):
    """两处都做成可传参（默认用模块级常量）——不然测试只能对着真实项目
    目录跑，没法在隔离的临时目录里验证"两类文件都被扫到"这件事。"""
    dataset_dir = dataset_dir if dataset_dir is not None else DATASET_DIR
    base_dir = base_dir if base_dir is not None else BASE
    files = []
    if os.path.isdir(dataset_dir):
        files += sorted(glob.glob(os.path.join(dataset_dir, "*.jsonl")))
    files += sorted(glob.glob(os.path.join(base_dir, "dataset_*.jsonl")))
    for g in (_EXTRA_SCRUB_GLOBS if extra_globs is None else extra_globs):
        files += sorted(glob.glob(g))
    return [f for f in files if os.path.basename(f) not in _FROZEN_NEVER_SCRUB]


def _normalize_newlines(raw: bytes) -> bytes:
    """把 CRLF / 单独的 CR 一律归一成 LF，再参与指纹计算。

    🔴 第四轮独立复核 R4-10：指纹算的是三个文件的**原始字节**，而这三个文件
    在 `.gitattributes` 里走的是 `* text=auto eol=lf`、**没有被标成 binary**。
    仓库根目录只把 6 个"已被外部锚定的冻结产物"标了 binary，为的是保住它们
    锚定时的原始字节；普通源码走行尾归一化是对的，不该改。

    后果：`core.autocrlf` 设置不同的两台机器，克隆出来的 `anonymize.py`
    字节不同（一边 LF、一边 CRLF），指纹就不同。于是同一份规则会被判成
    "规则变了"，rescrub 被无谓触发全量重写几十 MB 语料；反过来，也可能让
    "指纹一致"这个跳过判据在跨机比对时失去意义。这是一条**跨文件的隐式
    耦合**：改 `.gitattributes` 的人不会想到它影响脱敏指纹。

    修法选"在代码里自己归一化"，**不选**"把这三个文件也标 binary"：
      · `.gitattributes` 在仓库根目录，是全项目共享的文件，本模块管不着它，
        也不该为了自己的指纹去改一个影响全仓库行尾策略的配置；
      · 更重要的是，靠 `.gitattributes` 等于把正确性寄托在"每个克隆者的 git
        都正确应用了这份属性"上（旧版 git、导出 zip、直接下载 tarball、
        用非 git 工具同步的场景全都不生效）。而 `.replace(b"\\r\\n", b"\\n")`
        是本函数自己就能保证的，不依赖任何外部工具的行为；
      · 这三个文件与那 6 个冻结产物性质相反——冻结产物的价值恰恰在"字节
        逐位不变"，绝不能归一化；而规则指纹关心的是"规则内容变没变"，
        行尾是纯粹的表示形式噪声，本就该被归一化掉。
    """
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _rules_fingerprint() -> str:
    """规则指纹 = anonymize.py 源码 + secrets_blocklist.txt + identity_map.txt
    三者内容拼接后的 sha256。三者任意一个变了，指纹就变，触发全量重跑。
    后两个文件是 gitignore 掉的本地专属文件，不存在时用空字节代替
    （指纹依然稳定、可比较，只是反映"当前没有本地兜底词表"这个状态）。

    ⚠️ 三者的字节先过 `_normalize_newlines()` 做行尾归一（R4-10，理由见该函数）。
    """
    h = hashlib.sha256()
    # ⚠️ R3-28 把 identity_map.txt 从本目录挪到了项目根目录（两份合一）。
    # 这行路径要是没跟着改，指纹就会**永远按"文件不存在"计入空字节**——
    # 映射表怎么改指纹都不动，全量重跑再也不会被触发，而且不报错、没人知道。
    # 下面那条 assert 就是为了不让这种"静默失效"再次发生：本项目确实一直
    # 有这张表，路径写错时当场炸，而不是安静地退化成空字节。
    identity_map = os.path.join(os.path.dirname(BASE), "identity_map.txt")
    for path in (
        os.path.join(BASE, "anonymize.py"),
        os.path.join(BASE, "secrets_blocklist.txt"),
        identity_map,
    ):
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(_normalize_newlines(f.read()))
        else:
            h.update(b"")
    return h.hexdigest()


def _assert_fingerprint_inputs_present():
    """指纹的三个输入里，`anonymize.py` 与根目录的 `identity_map.txt` 在本项目
    里一定存在。允许"不存在时计空字节"是给第三方复现者留的降级路径，但在
    我们自己的仓库里，它只可能意味着路径写错——不让它静默通过。"""
    must = [os.path.join(BASE, "anonymize.py"),
            os.path.join(os.path.dirname(BASE), "identity_map.txt")]
    missing = [p for p in must if not os.path.exists(p)]
    if missing and os.path.exists(os.path.join(os.path.dirname(BASE), ".git")):
        raise SystemExit(
            "规则指纹的输入文件找不到，多半是路径写错而不是真的没有：\n  "
            + "\n  ".join(missing)
            + "\n（在本仓库之外运行时不会走到这条检查，那里允许降级）")


def rescrub(obj):
    if isinstance(obj, str):
        return _scrub_string(obj)
    if isinstance(obj, dict):
        return {k: rescrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rescrub(v) for v in obj]
    return obj


def _sha256_file(path: str) -> str:
    """被处理文件自身的内容指纹（行尾归一，同 _rules_fingerprint 的口径）。"""
    with open(path, "rb") as f:
        return hashlib.sha256(_normalize_newlines(f.read())).hexdigest()


def process_file(path: str, fingerprint: str, force: bool) -> dict:
    sidecar = path + ".scrub.json"
    if not force and os.path.exists(sidecar):
        prev = json.load(open(sidecar, encoding="utf-8"))
        # 🔴 2026-08-14 发现的真实盲区：跳过判据原本**只看规则指纹**，
        # 不看被处理的文件自己变没变。于是"规则没改、但数据整批换了"这种
        # 情况下，新数据**完全不过脱敏**——本轮整链重跑后
        # `generation_raw_log.jsonl` 从 152 条换成 86 条全新内容，
        # 旁路文件却还记着 total=152，跳过判据照样成立，新内容一次都没被
        # 扫过。而这个文件恰恰承载着发给模型的知识单元原文。
        #
        # 判据补上**内容指纹**：规则与内容任一变化都必须重跑。
        # 这与项目里 provenance/血缘那套是同一个道理——只锁一半的输入，
        # 等于没锁。
        content_fp = _sha256_file(path)
        if (prev.get("rules_fingerprint") == fingerprint
                and prev.get("content_sha256") == content_fp):
            return {"path": path, "skipped": True, "changed": 0,
                    "total": prev.get("total")}

    records = []
    changed = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            new_rec = rescrub(rec)
            if new_rec != rec:
                changed += 1
            records.append(new_rec)

    # newline="\n"：同 measure.py，仓库按 .gitattributes 存 LF，
    # 写成 CRLF 会让工作区字节与入库字节不一致（详见《勘误与修正记录》第九节）
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # content_sha256 记的是**脱敏之后**这个文件的指纹——下次比对时它与
    # 当时读到的内容一致才说明"没人动过"；若内容被换掉（如整链重跑），
    # 指纹立刻不同，跳过判据不再成立，新内容必然被重新扫一遍。
    json.dump({"rules_fingerprint": fingerprint,
               "content_sha256": _sha256_file(path),
               "total": len(records), "changed": changed},
              open(sidecar, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=2)

    return {"path": path, "skipped": False, "changed": changed, "total": len(records)}


def main():
    ap = argparse.ArgumentParser(description="用当前脱敏规则对全部数据集文件做增量再脱敏")
    ap.add_argument("--force-all", action="store_true",
                     help="忽略规则指纹，强制重新处理全部文件")
    args = ap.parse_args()

    _assert_fingerprint_inputs_present()
    fingerprint = _rules_fingerprint()
    print(f"当前规则指纹：{fingerprint[:16]}…")

    files = _dataset_files()
    if not files:
        print(f"未找到任何数据集文件（{DATASET_DIR} 或 dataset_*.jsonl）。")
        return 1

    for path in files:
        result = process_file(path, fingerprint, args.force_all)
        rel = os.path.relpath(result["path"], BASE)
        if result["skipped"]:
            print(f"  [跳过，指纹未变] {rel}（{result['total']}条）")
        else:
            print(f"  [已处理] {rel}：总记录 {result['total']} 条，"
                  f"因规则而变化 {result['changed']} 条")

    return 0


if __name__ == "__main__":
    sys.exit(main())
