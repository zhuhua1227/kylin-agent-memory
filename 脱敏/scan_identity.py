#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_identity.py —— 扫 git 已跟踪文件里的可识别信息

═══ 为什么只扫已跟踪文件 ═══════════════════════════════════
会被公开的就是 `git ls-files` 这一份。工作区里的 dataset、identity_map.txt、
secrets_blocklist.txt、提示词、评审报告都在 .gitignore 里，不进提交，
扫它们只会淹没真正要处理的项。

═══ 不回显凭据明文 ═══════════════════════════════════════
高敏类别（HEX / 密钥赋值）只报位置、长度与掩码形式。
排查要看上下文时用 --context，它仍然掩码中段。
═══════════════════════════════════════════════════════════

用法：
    python3 scan_identity.py                 # 汇总
    python3 scan_identity.py --context       # 带上下文（仍掩码）
    python3 scan_identity.py --check         # 验收模式：只看"应清零"的类别，
                                             #   有残留则 exit 1
"""
import argparse
import collections
import fnmatch
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL_PATTERNS = "脱敏/patterns.local.txt"


def load_identity_patterns():
    """身份类规则从 gitignore 的本地词表读。每行：<类别名><TAB><正则>。

    🔴 为什么不内联：本文件是受跟踪源码，把待查的名字写进来
       等于在防泄漏工具里泄漏一次。本仓已在 test_detectors.py 上犯过三次。
    缺词表则返回 None，由调用方明确报出——不静默当成"没问题"。
    """
    if not os.path.exists(LOCAL_PATTERNS):
        return None
    out = []
    for l in io.open(LOCAL_PATTERNS, encoding="utf-8"):
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        name, _, rx = l.strip().partition(chr(9))     # chr(9)=TAB，避开转义
        if rx.strip():
            out.append((name.strip(), re.compile(rx.strip(), re.I), False, True))
    return out


# 以下通用规则不含任何可识别字面量，可安全留在受跟踪源码里。
# ⚠️ 自有域名（.cyou）那条含具体 TLD 字面量，已移进 patterns.local.txt（gitignore），
#    否则本扫描器一旦入库就会扫到自己的规则定义、误报为泄漏。
PATTERNS = [
    ("邮箱",             re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,10}\b"), False, False),
    ("内网 192.168.x",   re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), False, False),
    ("内网 10.x",        re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), False, False),
    ("Tailscale 100.64+", re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"), False, True),
    ("公网 IP（疑）",     re.compile(r"\b(?:38|121|154|61|60)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), False, True),
    ("QQ AppID",         re.compile(r"\b190[0-9]{7}\b"), False, True),
    ("HEX 20/40 位",     re.compile(r"\b(?:[0-9a-f]{20}|[0-9a-f]{40})\b"), True, False),
    ("疑似密钥赋值",      re.compile(r"(?i)(?:password|passwd|pw|token|api[_-]?key|secret)\s*[=:]\s*['\"][^'\"$][^'\"]{5,}"), True, True),
]

# 这些是【脱敏器自己的测试夹具】，动了会把 anonymize 的断言测试搞坏。永不处理。
FIXTURES = {
    "多源数据整合/anonymize.py",
    "多源数据整合/labeled_redaction_testset.json",
}

# 🔴 设计内公开 —— 与 可复现性体系/test_detectors.py 的 BY_DESIGN 保持一致。
#    这两个文件里的 GitHub 用户名是【锚定机制的承重部分】：第三方要靠它去
#    GitHub 核验 issue 真实存在。删掉等于把可核验性拆了。
#    ⚠️ test_detectors.py 是权威，改这里之前先看那边。
BY_DESIGN = {
    "可复现性体系/anchor_issue_snapshot.json":
        "GitHub issue 锚定快照：URL 与作者字段必须可被第三方公开核验",
    "评测集/说明报告.md":
        "正文引用上述锚定 issue 的地址，同上",
    "README.md":
        "作者署名与仓库地址",
    "LICENSE":
        "MIT 许可证的版权人署名",
}


def tracked():
    out = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8")
    return [f for f in out.split("\0") if f]


def frozen_set(files):
    """.gitattributes 标 binary 的文件 —— 字节不得变。"""
    pats = []
    if os.path.exists(".gitattributes"):
        for l in io.open(".gitattributes", encoding="utf-8"):
            l = l.strip()
            if l and "binary" in l and not l.startswith("#"):
                pats.append(l.split()[0])
    return {f for f in files
            if any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(os.path.basename(f), p)
                   for p in pats)}


def mask(v):
    return "%s…%s(%d位)" % (v[:4], v[-4:], len(v)) if len(v) > 10 else "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="验收模式：应清零的类别若有残留则 exit 1")
    a = ap.parse_args()

    idpat = load_identity_patterns()
    if idpat is None:
        print("⚠️  缺 %s —— 身份类规则本次【未扫描】。" % LOCAL_PATTERNS)
        print("    该词表含可识别字面量，故不入 git；建立方法见方案 §2.3。")
        print("")
        idpat = []
    pats = idpat + PATTERNS

    files = tracked()
    froz = frozen_set(files)
    hits = collections.defaultdict(lambda: collections.defaultdict(list))

    for f in files:
        if not os.path.exists(f) or f in FIXTURES:
            continue
        try:
            txt = io.open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for name, rx, sens, _ in pats:
            for m in rx.finditer(txt):
                line = txt.count("\n", 0, m.start()) + 1
                hits[name][f].append((line, m.group(0), m.start(), txt))

    print("已跟踪文件 %d，冻结（binary）%d，测试夹具跳过 %d\n"
          % (len(files), len(froz), len(FIXTURES)))

    residual = 0
    for name, rx, sens, must_zero in pats:
        d = hits.get(name)
        if not d:
            continue
        nf, nh = len(d), sum(len(v) for v in d.values())
        infroz = sum(len(v) for f, v in d.items() if f in froz)
        indesign = sum(len(v) for f, v in d.items() if f in BY_DESIGN)
        tag = "   ⚠️敏感,掩码" if sens else ""
        gate = "  🔴应清零" if must_zero else "  （可保留,需登记）"
        print("%-20s %2d 文件 / %2d 处（冻结内 %d，设计内公开 %d）%s%s"
              % (name, nf, nh, infroz, indesign, tag, gate))
        for f, lst in sorted(d.items(), key=lambda kv: -len(kv[1])):
            mark = " 🔒冻结" if f in froz else ""
            if f in BY_DESIGN:
                mark += " ✅设计内公开（%s）" % BY_DESIGN[f]
            print("      %3d  %s%s" % (len(lst), f, mark))
            if a.context:
                for line, val, pos, txt in lst[:3]:
                    shown = mask(val) if sens else val
                    ctx = txt[max(0, pos - 70):pos].replace("\n", " ")[-60:]
                    print("             行%-5d %s   …%s" % (line, shown, ctx))
        if must_zero:
            # 冻结文件内的按方案 §三 单独登记；设计内公开的是承重项，都不计残留
            residual += nh - infroz - indesign
        print()

    if a.check:
        if residual:
            print("🔴 非冻结文件中仍有 %d 处应清零项 —— 未通过" % residual)
            return 1
        print("✅ 非冻结文件中应清零类别为 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
