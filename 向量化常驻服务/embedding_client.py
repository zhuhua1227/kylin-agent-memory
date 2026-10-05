#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embedding_client.py —— 常驻向量化服务的 Python 客户端

配合同目录的 emb_serverd（C 程序）使用：本模块负责把 emb_serverd 作为一个长驻子进程
拉起来，之后所有向量化请求都走同一个进程，避免每次都重新加载 339MB 模型。

对比阶段01 的 pipeline.py：
    阶段01：每调用一次 get_vector() → 起一个新进程 → 建会话 → 加载模型 → 出向量 → 进程退出
    现在　：进程只起一次 → 加载一次模型 → 之后每次请求只剩推理开销

用法：

    from embedding_client import EmbeddingClient

    with EmbeddingClient() as client:
        vec = client.encode("怎么重启网络服务")          # 单条
        vecs = client.encode_batch(["文本1", "文本2"])   # 批量，带进度

用 with 语句是推荐写法：退出时会自动关闭子进程，不会留下孤儿进程占着模型内存。
"""

import os
import select
import subprocess
import sys
import tempfile
import time
import math

# 独立评审R-7：单条 encode() 正常应在毫秒到数十毫秒级完成（见《向量化常驻
# 服务/说明报告.md》的P95数据），30秒留了几百倍余量。
DEFAULT_READ_TIMEOUT = 30.0


# emb_serverd 可执行文件路径，默认认为它和本文件在同一目录
DEFAULT_SERVERD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "emb_serverd"
)

# 独立评审R-6：《向量化常驻服务/说明报告.md》实测256字文本向量化P95=538.3ms，
# 已经超过500ms硬指标预算——代码此前没有任何长度检查/告警，一条长查询能悄悄
# 把端到端延迟顶穿预算而不会有任何提示。200字按936字/秒的实测吞吐外推约
# 214ms，仍在预算内但留有余量，作为阈值。
#
# 设计取舍：只告警、不静默截断。encode() 同时服务"建索引"（离线，不占500ms
# 预算，截断会丢信息、损伤知识单元质量）与"在线查询"（占预算，长了才是真
# 问题）两种调用场景，本函数分不清调用方是哪一种；静默截断对建索引场景是
# 净损害，对在线查询场景才是保护。因此把决定权交还给调用方：打印告警到
# stderr，若调用方明确知道自己是在线查询路径，应自行截断或分段编码。
MAX_SAFE_QUERY_CHARS = 200


class EmbeddingServerError(RuntimeError):
    """常驻服务异常。抛出时会尽量把 emb_serverd 的 stderr 内容带上，方便定位。"""
    pass


class EmbeddingClient:
    """常驻向量化服务的客户端。

    构造时会启动 emb_serverd 并阻塞等待它完成模型加载与预热（等到 READY 为止）。
    因为模型有 339MB，冷启动这一步可能要等数十秒，属于正常现象，只需等一次。
    """

    def __init__(self, serverd_path: str = DEFAULT_SERVERD_PATH, startup_timeout: int = 180):
        if not os.path.exists(serverd_path):
            raise EmbeddingServerError(
                f"找不到 emb_serverd：{serverd_path}\n"
                f"请先编译：gcc emb_serverd.c -o emb_serverd "
                f"-I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding"
            )

        self._serverd_path = serverd_path

        # stderr 单独重定向到临时文件，而不是 subprocess.PIPE。
        # 原因：PIPE 有容量上限，如果没人读，子进程写满后会阻塞，
        #       而我们正阻塞等它的 stdout —— 双方互等，直接死锁。
        #       落到文件就没有这个问题，出错时再把内容读出来。
        self._stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".emb_serverd.log", delete=False, encoding="utf-8", errors="replace"
        )

        # stdin/stdout 都走二进制：请求体是原始 UTF-8 字节，长度前缀算的也是字节数，
        # 用文本模式会因为换行符转换等处理导致字节数对不上。
        #
        # bufsize=0（无缓冲）+ 自己维护字节缓冲区 `self._read_buf`，配合下面
        # `_readline_with_timeout` 的手写读取循环——独立评审R-7修复过程中
        # 发现：如果用 Python 内置的缓冲读取器，一次底层 read() 系统调用
        # 可能把比一行更多的数据读进用户态缓冲区，这之后 select() 只能看见
        # OS 层管道状态、看不见用户态缓冲区里已经有的数据，会把"缓冲区里
        # 其实已经有现成数据"误判成"无响应"进而超时。自己管理缓冲区能
        # 精确区分"缓冲区已有整行"（直接返回，不碰 select）与"确实还没
        # 到"（select()+超时）两种情况。
        self._read_buf = b""

        self._proc = subprocess.Popen(
            [serverd_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            bufsize=0,
        )

        self._wait_ready(startup_timeout)

    # ------------------------------------------------------------------ 内部方法

    def _read_stderr(self) -> str:
        """把 emb_serverd 到目前为止的 stderr 内容读出来（用于报错时附带上下文）。"""
        try:
            self._stderr_file.flush()
            with open(self._stderr_file.name, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return "(读取 stderr 失败)"

    def _readline_with_timeout(self, timeout: float) -> bytes:
        """独立评审R-7：`readline()` 此前是纯阻塞调用，没有任何超时——
        emb_serverd 一旦挂起，调用方会永久阻塞、没有任何提示。`_wait_ready`
        此前甚至接收了 `timeout` 参数却从未真正使用（一个死参数）。

        先查自己的字节缓冲区里有没有现成的整行（不触发系统调用），没有
        才用 select() 探测 OS 层管道是否有新数据、超时则明确报错。

        ⚠️ 边界说明：这里只做"超时后明确报错"，不做"自动重启子进程"——
        自动重启需要重建会话状态，贸然重启的风险高于收益，出错后交给
        调用方决定。select() 只在类POSIX系统上对管道有效，emb_serverd
        本身是Linux-only二进制，这个前提天然成立。
        """
        deadline = time.monotonic() + timeout
        while b"\n" not in self._read_buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EmbeddingServerError(
                    f"emb_serverd 在 {timeout}秒 内无响应，可能已挂起。\n"
                    f"stderr:\n{self._read_stderr()}"
                )
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                raise EmbeddingServerError(
                    f"emb_serverd 在 {timeout}秒 内无响应，可能已挂起。\n"
                    f"stderr:\n{self._read_stderr()}"
                )
            chunk = os.read(self._proc.stdout.fileno(), 65536)
            if not chunk:
                raise EmbeddingServerError(
                    f"emb_serverd 无响应（管道已关闭）。\nstderr:\n{self._read_stderr()}"
                )
            self._read_buf += chunk

        line, _, self._read_buf = self._read_buf.partition(b"\n")
        return line + b"\n"

    def _wait_ready(self, timeout: int) -> None:
        """阻塞等待 emb_serverd 打印 READY（冷启动要建会话+加载模型，
        比常态请求慢得多，用调用方传入的 startup_timeout 而不是请求级的
        DEFAULT_READ_TIMEOUT）。"""
        line = self._readline_with_timeout(timeout)
        if not line:
            # 子进程还没打印 READY 就没了 —— 通常是 SDK 初始化失败或环境没恢复
            self._proc.wait()
            raise EmbeddingServerError(
                f"emb_serverd 启动失败（退出码 {self._proc.returncode}）。\n"
                f"stderr:\n{self._read_stderr()}"
            )

        if line.strip() != b"READY":
            raise EmbeddingServerError(
                f"emb_serverd 启动握手异常，期望 READY，实际收到：{line!r}\n"
                f"stderr:\n{self._read_stderr()}"
            )

    def _ensure_alive(self) -> None:
        if self._proc.poll() is not None:
            raise EmbeddingServerError(
                f"emb_serverd 已退出（退出码 {self._proc.returncode}）。\n"
                f"stderr:\n{self._read_stderr()}"
            )

    # ------------------------------------------------------------------ 对外接口

    def encode(self, text: str) -> list:
        """把一段文本向量化，返回 768 维浮点数列表。

        协议：先发 "<字节数>\\n"，再发原文的 UTF-8 字节；然后读回一行结果。
        用字节数做前缀而不是按行分隔，是因为文本里可能含换行符（工具执行结果几乎必然有）。

        长度告警（独立评审R-6）：超过 MAX_SAFE_QUERY_CHARS 只打印告警，不静默
        截断——本函数同时服务建索引（离线）与在线查询（占500ms预算）两种场景，
        截断对前者是净损害。若调用方明确是在线查询路径，应自行截断或分段编码。
        """
        self._ensure_alive()

        if len(text) > MAX_SAFE_QUERY_CHARS:
            print(f"⚠️ EmbeddingClient.encode(): 输入长度 {len(text)} 字符超过安全阈值 "
                  f"{MAX_SAFE_QUERY_CHARS}，向量化耗时可能显著增加、逼近甚至超过500ms端到端"
                  f"预算（实测256字P95=538.3ms已超标，见《向量化常驻服务/说明报告.md》）。"
                  f"若这是在线查询路径，请自行截断或分段编码；若是离线建索引，可忽略此告警。",
                  file=sys.stderr)

        data = text.encode("utf-8")
        try:
            self._proc.stdin.write(f"{len(data)}\n".encode("ascii"))
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except BrokenPipeError:
            raise EmbeddingServerError(
                f"向 emb_serverd 写入失败，进程可能已崩溃。\nstderr:\n{self._read_stderr()}"
            )

        line = self._readline_with_timeout(DEFAULT_READ_TIMEOUT)
        if not line:
            raise EmbeddingServerError(
                f"emb_serverd 无响应，进程可能已崩溃。\nstderr:\n{self._read_stderr()}"
            )

        if line.startswith(b"ERR"):
            raise EmbeddingServerError(
                f"向量化失败：{line.decode('ascii', errors='replace').strip()}\n"
                f"（出错文本前 80 字：{text[:80]!r}）\n"
                f"stderr:\n{self._read_stderr()}"
            )

        return [float(x) for x in line.split()]

    def encode_batch(self, texts, progress_every: int = 0) -> list:
        """批量向量化。

        progress_every > 0 时，每处理这么多条就往终端打一行进度
        （建索引要跑 14047 条，没有进度提示会让人以为卡死了）。
        """
        total = len(texts)
        vectors = []
        for i, text in enumerate(texts, start=1):
            vectors.append(self.encode(text))
            if progress_every and (i % progress_every == 0 or i == total):
                print(f"  向量化进度：{i}/{total}  ({i / total * 100:.1f}%)", flush=True)
        return vectors

    def close(self) -> None:
        """关闭子进程。关掉 stdin 让 emb_serverd 读到 EOF，它会自己正常退出。"""
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

        try:
            self._stderr_file.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


# ---------------------------------------------------------------------------
# 数学工具函数
# 原样沿用阶段01 pipeline.py 中已验证过的实现，未做改动 ——
# 保持一致是为了让本模块的检索结果能和阶段01 的记录逐位对照，验证改造没有引入偏差。
# ---------------------------------------------------------------------------

def dot_product(a: list, b: list) -> float:
    """两个等长向量的点积：对应位置相乘，再把所有结果加起来。"""
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total


def normalize(vec: list) -> list:
    """L2 归一化：把向量的模长变成 1，方向不变。"""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def cosine_similarity(a: list, b: list) -> float:
    """余弦相似度 = 两个向量各自归一化后，再做点积。"""
    return dot_product(normalize(a), normalize(b))


# ---------------------------------------------------------------------------
# 自检：用阶段01 完全相同的一组文本和查询跑一遍，
# 结果应与《端到端最小管线/说明报告.md》第五节记录的一致。
# 这是验证「改成常驻服务后，向量化结果没有发生任何变化」的手段。
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo_texts = [
        "怎么重启网络服务",
        "如何查看磁盘剩余空间",
        "今天天气怎么样",
        "网络连接不上怎么办",
        "如何清理系统缓存文件",
    ]
    query = "网络服务重启方法"

    print("=" * 56)
    print("自检：与阶段01 端到端最小管线使用完全相同的文本与查询")
    print("=" * 56)

    with EmbeddingClient() as client:
        print("\n步骤1：构建知识库")
        store = []
        for t in demo_texts:
            vec = client.encode(t)
            store.append({"text": t, "vector": vec})
            print(f"  已存入：{t}  (向量维度={len(vec)})")

        print(f"\n步骤2：检索 —— 查询文本：「{query}」")
        qvec = client.encode(query)
        scored = [
            {"text": item["text"], "score": cosine_similarity(qvec, item["vector"])}
            for item in store
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        for rank, r in enumerate(scored[:3], start=1):
            print(f"  第{rank}名  相似度={r['score']:.4f}   原文：{r['text']}")

    print("\n对照阶段01 记录：第1名 应为「怎么重启网络服务」，相似度约 0.9441")
