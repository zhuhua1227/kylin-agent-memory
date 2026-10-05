#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 测量记录框架

═══ 这个模块解决什么问题 ═══════════════════════════════════════════

在此之前，本项目所有报告里的数字（768 维、0.9441、68.4ms、14588 条……）
**都是人手打进 markdown 的文本**。读者无从验证，我们也无法自证。

本模块让每次测量自动留下一份结构化记录：跑在什么机器上、用的哪份输入、
跑了几次、每次多少、原始输出是什么。

═══ ⚠️ 先说清楚它不是什么 ═══════════════════════════════════════

**它不能证明我们没造假。** 这个 JSON 我们能写，别人也能写，
拿 AI 编一份格式完全正确的记录毫无难度。

它的真实作用是**把主张写得足够精确，使别人能廉价地检验**：

    「检索很快」           —— 无法证伪，说了等于没说
    「在 2vCPU/3.9GB 的 Kylin V11 上，
      用 sha256=abc… 的输入跑 bench.py --seed 42，P95=68.4ms」
                          —— 五分钟就能验，假的当场露馅

**可信度不来自"无法伪造"，而来自"容易被验证"。** 真正承重的是配套的
一键复现脚本（reproduce_L1.py），本模块只是让复现有个可对照的基准。

因此这里**刻意不做**哈希链、防篡改校验之类的设计——对一个能改整个文件的人
来说那些毫无意义，只会营造虚假的严谨感。

═══ 用法 ═══════════════════════════════════════════════════════

    from measure import Measurement

    with Measurement("M-007", "查询向量化延迟（30字）", layer="L2",
                     inputs=["sample.jsonl"], seed=42) as m:
        for _ in range(5):
            t = time_one_run()
            m.add_run("query_30char_p95_ms", t, unit="ms")

    # 退出 with 时自动：算均值/标准差、采集环境指纹、写 records/M-007.json

单值指标用 set_value()；多次运行用 add_run()（会自动统计 mean/stdev）。
"""

import hashlib
import io
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

# Windows 控制台默认 GBK，输出中文标点/符号会抛 UnicodeEncodeError。
# 测量脚本要在别人的机器上跑，这一步必须先做稳。
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# 记录默认落在本文件同级的 records/ 目录
_RECORDS_DIR_FALLBACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "records")


def _default_records_dir():
    """R2-27：默认落盘目录做成函数、每次调用时读一次环境变量，而不是
    模块级常量——常量在 `import measure` 那一刻就固定死了，`reproduce_
    L1.py` 的 `--records-dir` 参数要到 argparse 解析完才知道，那时早就
    错过给常量赋值的窗口。改成函数，`--records-dir` 只需要在解析完参数
    后设置 RAG_RECORDS_DIR 环境变量，这里每次都会读到最新值。
    """
    return os.environ.get("RAG_RECORDS_DIR") or _RECORDS_DIR_FALLBACK


# 保留原名字作为向后兼容的模块级快照（部分代码可能直接引用这个名字），
# 但内部一律走 _default_records_dir()，不依赖这个可能过期的快照值。
DEFAULT_RECORDS_DIR = _RECORDS_DIR_FALLBACK

# 采集版本号的关键包。只在麒麟机上能查到，其他机器自动跳过。
# 这些包决定了 embedding 与向量引擎的行为，是 L2 类数字的关键上下文——
# 部署报告里那次 undefined symbol 事故就是版本错配导致的，所以必须记下来。
KEY_PACKAGES = [
    "libkylin-coreai-embedding",
    "libkylin-coreai-embedding-dev",
    "kylin-ai-runtime",
    "kytensor-client",
    "kytensor-server",
    "kylin-ai-vector-engine",
    "libkysdk-vector-engine-client",
    "kylin-gte-base-model",
]


# ═════════════════════════════════════════════════════ 环境指纹

def _read_first(path, prefix):
    """从 /proc 风格的文件里读出以 prefix 开头的第一行的值。读不到返回 None。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(prefix):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _cpu_model():
    # Linux：/proc/cpuinfo 的 model name
    v = _read_first("/proc/cpuinfo", "model name")
    if v:
        return v
    # Windows/其他：platform.processor() 通常够用，虽然没那么细
    return platform.processor() or "unknown"


def _mem_total_mb():
    v = _read_first("/proc/meminfo", "MemTotal")
    if v:
        try:
            return int(v.split()[0]) // 1024      # kB -> MB
        except (ValueError, IndexError):
            pass
    # Windows 回退：走 ctypes 读全局内存状态，避免引入第三方依赖
    if sys.platform == "win32":
        try:
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys // (1024 * 1024))
        except Exception:
            pass
    return None


def _os_pretty_name():
    # Linux 发行版名称在 /etc/os-release 的 PRETTY_NAME
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def _package_versions():
    """查关键包版本。只在有 dpkg 的机器上有结果，其他机器返回空字典。"""
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${Version}\n"] + KEY_PACKAGES,
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}

    versions = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            versions[parts[0]] = parts[1]
    return versions


# 第二轮独立复核 §二·五：records/*.json 的 environment.hostname 与
# Measurement.script 路径此前原样记录 platform.node()/__file__，本地开发机
# 真实主机名、远程麒麟机真实主机名、Windows用户名都残留在了受跟踪的记录
# 文件里——这是身份标识，不是密钥，但同属隐私。
# 固定映射（不是随机化）：环境指纹要求跨记录一致性（同一台机器在不同
# 记录里必须显示同一个假名，否则"哪些记录来自同一台机器"这个可比较性
# 就丢了），所以用显式映射表而不是每次运行随机生成。
#
# 映射表本身不能硬编码在这个受跟踪的源文件里——那只是把"真实值残留在
# 数据字段里"换成"真实值残留在源代码里"，同一类问题换了个地方复发
# （防复发检测器 test_no_real_secret_in_tracked_files 第一次跑这份改动
# 就当场抓到了这个问题：命中的正是本文件）。做法与 anonymize.py 的
# identity_map.txt 一致：真实映射写到本地专属、gitignore 排除的
# identity_map.txt 里，格式见同目录 identity_map.txt.example。
# 🔴 第三轮独立复核 R3-28：这里原本读本目录下的一份 identity_map.txt，
# 而 多源数据整合/anonymize.py 读它那边的另一份，两份实测有 3 条冲突、
# 且本侧还把两个不同真实值坍缩成同一个假名。改为共用项目根目录的唯一
# 一份表与唯一一份加载逻辑（理由与取舍见 identity_map_loader.py 文件头）。
#
# 映射表不存在（或只拿到 可复现性体系/ 一个文件夹）时返回空表——不报错，
# 代表这台机器没配置过映射，hostname/路径原样通过而不是假名化，
# 不影响测量结果本身的正确性。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from identity_map_loader import load_mapping as _load_identity_map
except ImportError:
    def _load_identity_map():
        return {}


_IDENTITY_MAP = _load_identity_map()


def _pseudonymize_hostname(hostname: str) -> str:
    return _IDENTITY_MAP.get(hostname, hostname)


def _pseudonymize_path(path):
    """把路径里的 Windows/Linux 真实用户名替换成占位符。"""
    if not path:
        return path
    for real, fake in _IDENTITY_MAP.items():
        path = path.replace(f"\\Users\\{real}\\", f"\\Users\\{fake}\\")
        path = path.replace(f"/home/{real}/", f"/home/{fake}/")
    return path


def collect_environment():
    """采集环境指纹。

    为什么要记这些：同一段代码在不同硬件上跑出的数字本来就不同，
    脱离环境谈性能数字没有意义。记下来既是给复现者的参照，
    也解释了为什么别的队伍会得到不同的数字。

    hostname 经过一致性假名化（见 _HOSTNAME_MAP）——不影响"同机器可比对"
    这个用途，只是不再直接暴露真实主机名。
    """
    env = {
        "hostname": _pseudonymize_hostname(platform.node()),
        "os": _os_pretty_name(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "mem_total_mb": _mem_total_mb(),
        "python": platform.python_version(),
    }
    pkgs = _package_versions()
    if pkgs:
        env["packages"] = pkgs

    # numpy 版本会影响 BLAS 后端与数值细节，用到时记一下
    try:
        import numpy
        env["numpy"] = numpy.__version__
    except ImportError:
        pass

    return env


def rss_mb(pid=None):
    """当前（或指定）进程的常驻内存，单位 MB。跨平台。

    ⚠️ 这个函数刻意做成跨平台的：内存占用是「轻量化程度」的关键指标之一，
    而评分标准明确要求提供量化论证数据。如果只能在麒麟上测，
    没有麒麟环境的读者就验不了这个数字，L1 层的意义就打了折扣。
    """
    pid = pid or os.getpid()

    # Linux：/proc/<pid>/status 的 VmRSS
    v = _read_first(f"/proc/{pid}/status", "VmRSS")
    if v:
        try:
            return int(v.split()[0]) / 1024.0      # kB -> MB
        except (ValueError, IndexError):
            pass

    # Windows：psapi 的 GetProcessMemoryInfo，取 WorkingSetSize
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
            if handle:
                counters = PMC()
                counters.cb = ctypes.sizeof(PMC)
                ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb)
                ctypes.windll.kernel32.CloseHandle(handle)
                if ok:
                    return counters.WorkingSetSize / (1024.0 * 1024.0)
        except Exception:
            pass

    return None


# ═════════════════════════════════════════════════════ 输入指纹

def _relpath(path):
    """os.path.relpath 的跨盘符兜底：Windows 上 path 与 cwd 不在同一盘符
    （如 TEMP 在 D:、仓库在 C:）时 relpath 会抛 ValueError，此时退回绝对路径。"""
    try:
        return os.path.relpath(path)
    except ValueError:
        return os.path.abspath(path)


def file_fingerprint(path):
    """算输入文件的 SHA256 与体积。

    作用是让复现者能确认"我用的是不是同一份输入"——
    数字对不上时，第一件事就是看输入是不是同一份。
    """
    info = {"path": _relpath(path) if os.path.exists(path) else path}
    if not os.path.exists(path):
        info["missing"] = True
        return info

    h = hashlib.sha256()
    size = 0
    lines = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
            lines += chunk.count(b"\n")
    info.update({"sha256": h.hexdigest(), "bytes": size, "lines": lines})
    return info


def _script_sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def percentile(values, pct):
    """百分位数，用最近秩法。

    刻意不用插值法：样本量不大时，插值会造出原始数据里根本不存在的数值，
    而我们希望报告里的每个数字都能在 runs 数组里找到出处。
    """
    s = sorted(values)
    if not s:
        return None
    k = max(1, int(round(pct / 100.0 * len(s))))
    return s[min(k, len(s)) - 1]


def summarize_runs(vals, unit=""):
    """把多次运行的原始值汇总成一个指标条目。

    ⚠️ 同时给出 p50 与 p95，是因为踩过一次坑：
    早期报告用 P95 记录延迟，而框架初版只存中位数，
    结果重跑时拿中位数去比 P95，成了苹果比橘子。
    两个都存下来，引用时说清楚用的是哪一个，就不会再混。

    原始 runs 数组一并保留——报告里的任何统计量都应该能从它重新算出来。
    """
    entry = {
        "value": round(percentile(vals, 95), 6),   # 代表值用 P95，与报告口径一致
        "value_kind": "p95",
        "unit": unit,
        "repeats": len(vals),
        "runs": [round(v, 6) for v in vals],
        "mean": round(statistics.mean(vals), 6),
        "p50": round(percentile(vals, 50), 6),
        "p95": round(percentile(vals, 95), 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "stdev": round(statistics.stdev(vals), 6) if len(vals) > 1 else None,
    }
    return entry


def wilson_ci(hits, n, z=1.96):
    """Wilson score interval，用于二项分布比例（如召回率）的置信区间。

    第二轮独立复核 R2-09：单一召回率数字掩盖了抽样误差——n 不大时换一批
    题目，数字本身会在什么范围内波动。不用正态近似（p̂±z√(p̂(1-p̂)/n)）是
    因为它在 p̂ 接近 0/1 或 n 较小时会给出越界或过窄的区间；Wilson 区间
    在这些边界场景下更稳健，是二项比例置信区间的标准选择之一。

    z=1.96 对应 95% 置信水平。返回 (下界, 上界)，n=0 时返回 (0.0, 1.0)
    （没有样本，区间应覆盖整个可能范围，不是"无法计算"就报 0）。
    """
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, center - margin), min(1.0, center + margin)


# ═════════════════════════════════════════════════════ stdout 旁路捕获

class _Tee(io.TextIOBase):
    """把写入 stdout 的内容同时留一份副本。

    为什么要留原始输出：报告里的表格是我从输出里摘出来的，
    留一份完整原文，读者可以逐项对照，看有没有摘错或摘漏。
    """

    def __init__(self, original):
        self._original = original
        self._buffer = io.StringIO()

    def write(self, s):
        self._original.write(s)
        self._buffer.write(s)
        return len(s)

    def flush(self):
        self._original.flush()

    def getvalue(self):
        return self._buffer.getvalue()


# ═════════════════════════════════════════════════════ 主体

class Measurement:
    """一次测量的记录器。用作上下文管理器。

    参数：
      mid     记录编号，如 "M-007"。报告里用 [M-007] 引用它。
      title   人类可读的标题
      layer   "L1" = 纯 Python、任何机器可复现
              "L2" = 需要麒麟 V11 + KYSDK 环境
      inputs  输入文件路径列表，会算 SHA256
      seed    随机种子（若有）。没有种子的测量无法严格复现，要如实留空。
      command 复现该测量的命令行，写给读者照着敲
    """

    def __init__(self, mid, title, layer="L1", inputs=None, seed=None,
                 command=None, script=None, records_dir=None, notes=None):
        assert layer in ("L1", "L2"), "layer 只能是 L1 或 L2"
        self.mid = mid
        self.title = title
        self.layer = layer
        self.inputs = list(inputs or [])
        self.seed = seed
        self.command = command
        self.script = _pseudonymize_path(script or (sys.argv[0] if sys.argv and sys.argv[0] else None))
        self.records_dir = records_dir or _default_records_dir()
        self.notes = list(notes or [])

        self._runs = {}       # name -> [values]
        self._units = {}      # name -> unit
        self._values = {}     # name -> 单值指标
        self._tee = None
        self._t0 = None

    # ------------------------------------------------------------ 采集

    def add_run(self, name, value, unit=""):
        """记录某指标的一次运行结果。多次调用会自动统计均值与标准差。

        为什么要多次运行：单次数字看不出稳定性。**报均值±标准差比报单次可信**——
        而且真实测量必然有波动，一组毫无波动的数字反而可疑。
        """
        self._runs.setdefault(name, []).append(float(value))
        if unit:
            self._units[name] = unit

    def set_value(self, name, value, unit=""):
        """记录一个单值指标（如条数、维度这种没有波动的量）。"""
        self._values[name] = value
        if unit:
            self._units[name] = unit

    def note(self, text):
        """附加一条说明，会写进记录。用来记测量时的特殊情况。"""
        self.notes.append(text)
        print(f"  [note] {text}")

    # ------------------------------------------------------------ 上下文

    def __enter__(self):
        self._t0 = time.perf_counter()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._tee = _Tee(sys.stdout)
        sys.stdout = self._tee
        print(f"===== [{self.mid}] {self.title}  (层级 {self.layer}) =====")
        return self

    def __exit__(self, exc_type, exc_value, tb):
        duration = time.perf_counter() - self._t0
        if exc_type is not None:
            print(f"[{self.mid}] 测量中断：{exc_type.__name__}: {exc_value}")

        raw_stdout = self._tee.getvalue()
        sys.stdout = self._tee._original      # 还原，避免影响后续代码

        metrics = {name: summarize_runs(vals, self._units.get(name, ""))
                   for name, vals in self._runs.items()}

        for name, v in self._values.items():
            metrics[name] = {"value": v, "unit": self._units.get(name, ""), "repeats": 1}

        record = {
            "record_id": self.mid,
            "title": self.title,
            "layer": self.layer,
            "script": self.script,
            "script_sha256": _script_sha256(self.script) if self.script else None,
            "command": self.command,
            "started_at": self._started_at,
            "duration_sec": round(duration, 3),
            "random_seed": self.seed,
            "environment": collect_environment(),
            "inputs": [file_fingerprint(p) for p in self.inputs],
            "metrics": metrics,
            "notes": self.notes,
            "succeeded": exc_type is None,
            "raw_stdout": raw_stdout,
        }

        # 异常时写到 records/failed/ 子目录，不覆盖已交付的正常记录。
        #
        # 之前的写法是无论成败都直接覆盖 records/{mid}.json——独立评审 P-1 指出：
        # 在缺依赖文件的环境里（比如评委只拿到 可复现性体系 这一个文件夹跑）
        # 测量会中途抛异常，__exit__ 仍然执行、把交付的正常记录覆盖成
        # succeeded:false、metrics:{} 的空记录，证据就这么被销毁了，且没有备份。
        if exc_type is not None:
            out_dir = os.path.join(self.records_dir, "failed")
        else:
            out_dir = self.records_dir
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{self.mid}.json")
        # newline="\n"：不写就会在 Windows 上落成 CRLF，而仓库按
        # .gitattributes 存 LF——于是「工作区字节 ≠ 入库字节」，
        # 而 MANIFEST 算的正是工作区字节。后果是**任何人克隆后跑一键复现，
        # 都会看到一屏「证据被改动过」**。第三轮已经为此栽过一次
        # （见《勘误与修正记录》第九节），当时只修了 generate_manifest.py，
        # 没回头看还有谁在写文件——这里就是漏网的一处。
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        print(f"[{self.mid}] 记录已写入 {_relpath(out_path)}"
              f"（{len(metrics)} 个指标，耗时 {duration:.1f}s）")
        return False      # 不吞异常


# ═════════════════════════════════════════════════════ 读取侧工具
# 供 reproduce_L1.py 与 verify_reports.py 使用

def load_record(mid, records_dir=None):
    """按编号读取一条记录。"""
    path = os.path.join(records_dir or _default_records_dir(), f"{mid}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all_records(records_dir=None):
    """读取全部记录，返回 {编号: 记录}。"""
    d = records_dir or _default_records_dir()
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                r = json.load(f)
                out[r.get("record_id", fn[:-5])] = r
    return out


if __name__ == "__main__":
    # 自检：跑一个假测量，确认环境指纹采集与统计都正常
    import random

    print("environment 采集结果：")
    for k, v in collect_environment().items():
        print(f"  {k}: {v}")
    print()

    random.seed(1)
    with Measurement("M-000", "自检：框架是否工作", layer="L1",
                     seed=1, command="python3 measure.py") as m:
        for _ in range(5):
            m.add_run("dummy_latency_ms", 10 + random.random(), unit="ms")
        m.set_value("dummy_count", 42, unit="条")
        m.note("这是框架自检产生的假数据，不代表任何真实指标")
