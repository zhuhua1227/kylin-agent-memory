#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vector_store.py —— 可切换的向量存储层（官方麒麟向量引擎 / 轻量 numpy 两种后端）

═══ 为什么做成可切换的两个后端 ═══════════════════════════════════════

比赛方案原文对存储这一环的要求是：

    「（4）适配端侧部署需求，**调用**银河麒麟国产桌面操作系统 embedding SDK，
      **设计轻量化存储方案**，确保检索响应延迟≤500ms」

注意措辞：embedding SDK 是**必须调用**的，但存储方案是让参赛者**"设计"**，
关键词是**"轻量化"**——原文并没有规定必须用官方向量库。

而评分标准里「方案可行性（30分）」明确要求：

    「……适配端侧部署的**轻量化程度，需提供量化论证数据**」

所以这里不做二选一，而是把两个后端都实现、接口完全一致，然后用实测数据论证选型。
这样做的三重收益：
  1. 直接产出评分要求的「轻量化程度量化论证数据」；
  2. 「**设计**轻量化存储方案」这个动作被做实了——是对比论证后选的，不是随手抓一个；
  3. 两个数字都能报，既有官方栈适配、又有速度，不必在两者间取舍。

═══ 一个关键的设计决定：向量索引与原文存储分离 ═════════════════════

本模块只负责 **doc_id → 向量** 的索引，**不存原文**。原文由 `DocStore` 单独管理。

为什么这么切：如果让向量库同时存原文，官方引擎可以塞进动态字段 `$meta`，
但 numpy 后端就得为了对齐而硬造一个平行结构，两个后端的职责就不对称了，
"可切换"会变成表面功夫。把原文统一交给 DocStore 之后，两个后端做的事情完全相同，
换后端才是真的只换一行配置。

═══ 用法 ═══════════════════════════════════════════════════════════

    from vector_store import create_store, DocStore

    store = create_store("kylin")   # 或 "numpy"
    store.create(dim=768)
    store.add([(doc_id, vector), ...])
    hits = store.search(query_vector, top_k=5)   # -> [(doc_id, score), ...]
    store.close()
"""

import json
import os
import select
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod


DEFAULT_DIM = 768   # 与官方 embedding SDK 输出维度一致


# ═════════════════════════════════════════════════════ 抽象接口

class VectorStore(ABC):
    """向量存储后端的统一接口。两个实现必须完全遵守，否则"可切换"就不成立。"""

    #: 后端名称，用于报告与日志
    name = "abstract"

    @abstractmethod
    def create(self, dim: int = DEFAULT_DIM) -> None:
        """建立（或重建）索引。已存在则清空重来，保证可复现。"""

    @abstractmethod
    def add(self, items) -> int:
        """批量写入。items 为 [(doc_id:int, vector:list[float]), ...]，返回写入条数。"""

    @abstractmethod
    def search(self, query_vector, top_k: int = 5):
        """检索，返回 [(doc_id:int, score:float), ...]，按相似度从高到低。"""

    @abstractmethod
    def count(self) -> int:
        """当前索引里的条数。"""

    @abstractmethod
    def close(self) -> None:
        """释放资源。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ═════════════════════════════════════════════════════ 后端一：官方麒麟向量引擎

class KylinEngineStore(VectorStore):
    """麒麟官方向量引擎后端（libkysdk-vector-engine-client，Milvus Lite 分支）。

    官方 SDK 只有 C++ 接口，因此通过常驻桥接进程 ve_serverd 驱动
    （与《向量化常驻服务》里 emb_serverd 是同一套模式）。

    数据落在引擎守护进程的默认库 ~/.local/share/kylin-ai-vector-engine/default.db，
    该路径位于 /home 分区，**重启不丢**（磐石系统只重置 /usr）。
    不同用途用**集合名**隔离，不自建 db 文件——实测自定义 db 路径会被服务端拒绝
    （SERVER_FAILED 1002），因为路径是由服务端而非客户端解析的。
    """

    name = "kylin"

    # 独立评审R-7：单条请求正常应在毫秒到数十毫秒级完成（见召回率实测报告
    # 的检索P95数据），30秒留了几百倍余量，足够覆盖偶发的引擎侧卡顿，
    # 又不会让一次真正的挂起拖到调用方毫无察觉。
    DEFAULT_READ_TIMEOUT = 30.0

    def __init__(self, collection: str = "rag_knowledge",
                 serverd_path: str = None, startup_timeout: int = 60):
        self.collection = collection
        self.dim = DEFAULT_DIM
        self._startup_timeout = startup_timeout
        self._serverd_path = serverd_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ve_serverd")

        if not os.path.exists(self._serverd_path):
            raise RuntimeError(
                f"找不到 ve_serverd：{self._serverd_path}\n"
                f"请先编译（编译命令见 ve_serverd.cpp 文件头注释）")

        # stderr 落到临时文件而非 PIPE：PIPE 有容量上限，没人读满了会阻塞子进程，
        # 而主进程正阻塞等它的 stdout —— 双方互等就死锁了。
        # （这个坑在《向量化常驻服务》里已经踩过一次，此处沿用同样的解法。）
        self._stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".ve_serverd.log", delete=False,
            encoding="utf-8", errors="replace")

        # 独立评审R-7修复过程中发现的一个真实bug：文本模式（text=True）的
        # 缓冲读取器一次底层 read() 系统调用可能把服务端一次flush写出的
        # 多行数据（比如SEARCH的"条数行+N条命中行"）全部读进 Python 自己
        # 的内部缓冲区；这之后如果再对 select() 探测 OS 层 fd 是否就绪，
        # 会因为"OS层确实没有新数据了"而误判超时——即使 readline() 本可以
        # 立刻从 Python 自己的缓冲区拿到下一行。select() 只知道 OS 层的
        # 管道状态，看不见用户态缓冲区里已经有的数据，这是 select()+缓冲
        # 读取器组合的经典陷阱。实测验证过：同样的请求序列，纯阻塞
        # readline()（不做select探测）0秒完成；套一层"每次读之前先
        # select()"就必然在第二行开始超时。
        #
        # 解决办法：不用 Python 内置的缓冲 TextIOWrapper，改成二进制模式 +
        # 自己维护一个字节缓冲区：先看缓冲区里有没有现成的整行，有就直接
        # 切出来返回（不碰 select、不做系统调用）；没有才 select()+os.read()
        # 补充数据。这样"数据已经在缓冲区"和"数据还没到"两种情况分得清楚，
        # 不会互相干扰。
        self._read_buf = b""

        self._proc = subprocess.Popen(
            [self._serverd_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr_file, bufsize=0,
        )

        # 独立评审R-7：此前 startup_timeout 参数接收了却从未被使用——
        # 冷启动要建会话+加载模型，比常态请求慢得多，单独给一个更宽松的
        # 超时窗口，而不是复用请求级的 DEFAULT_READ_TIMEOUT。
        line = self._readline_with_timeout(self._startup_timeout)
        if line.strip() != "READY":
            raise RuntimeError(
                f"ve_serverd 启动失败，期望 READY 实得 {line!r}\n"
                f"stderr:\n{self._read_stderr()}")

    # ---------------------------------------------------------- 内部

    def _read_stderr(self) -> str:
        try:
            self._stderr_file.flush()
            with open(self._stderr_file.name, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return "(读取 stderr 失败)"

    def _send(self, text: str) -> None:
        if self._proc.poll() is not None:
            raise RuntimeError(f"ve_serverd 已退出。stderr:\n{self._read_stderr()}")
        self._proc.stdin.write(text.encode("ascii"))
        self._proc.stdin.flush()

    def _readline_with_timeout(self, timeout: float) -> str:
        """独立评审R-7：`readline()` 此前是纯阻塞调用，ve_serverd 一旦
        挂起（比如 R-11/R-8 修复前那类协议错位、或引擎侧真的卡死），
        调用方会永久阻塞、没有任何提示。这里先查自己的字节缓冲区里有没有
        现成的整行（不触发任何系统调用），没有才用 select() 探测 OS 层
        管道是否有新数据、超时则明确报错，而不是死等。

        ⚠️ 边界说明：这里只做"超时后明确报错"，不做"自动重启子进程"——
        自动重启需要重建连接状态（集合是否已建好、已插入的数据是否
        完整），贸然重启可能把"部分写入"伪装成"看起来正常"，风险高于
        收益。出错后交给调用方决定是重连、放弃还是人工介入。
        select() 只在类 POSIX 系统上对管道有效（ve_serverd 本身是
        Linux-only 二进制，这个前提天然成立，不需要跨平台兼容）。
        """
        deadline = time.monotonic() + timeout
        while b"\n" not in self._read_buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"ve_serverd 在 {timeout}秒 内无响应，可能已挂起或崩溃。"
                    f"stderr:\n{self._read_stderr()}")
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError(
                    f"ve_serverd 在 {timeout}秒 内无响应，可能已挂起或崩溃。"
                    f"stderr:\n{self._read_stderr()}")
            chunk = os.read(self._proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError(f"ve_serverd 无响应（管道已关闭）。stderr:\n{self._read_stderr()}")
            self._read_buf += chunk

        line, _, self._read_buf = self._read_buf.partition(b"\n")
        return line.decode("ascii") + "\n"

    def _readline(self) -> str:
        line = self._readline_with_timeout(self.DEFAULT_READ_TIMEOUT)
        return line.strip()

    def _expect_ok(self, what: str) -> str:
        resp = self._readline()
        if resp.startswith("ERR"):
            raise RuntimeError(f"{what} 失败：{resp}\nstderr:\n{self._read_stderr()}")
        return resp

    @staticmethod
    def _fmt(vec) -> str:
        # 9 位有效数字：float 往返所需的十进制精度，少了会丢位、影响分数可比性
        return " ".join(f"{v:.9g}" for v in vec)

    # ---------------------------------------------------------- 接口实现

    def create(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self._send(f"CREATE {self.collection} {dim}\n")
        self._expect_ok("CREATE")

    def add(self, items) -> int:
        items = list(items)
        if not items:
            return 0
        lines = [f"ADD {self.collection} {len(items)} {self.dim}\n"]
        for doc_id, vec in items:
            lines.append(f"{doc_id} {self._fmt(vec)}\n")
        self._send("".join(lines))
        resp = self._expect_ok("ADD")          # 形如 "OK 1000"
        return int(resp.split()[1])

    def search(self, query_vector, top_k: int = 5):
        self._send(f"SEARCH {self.collection} {top_k} {self.dim}\n"
                   f"{self._fmt(query_vector)}\n")
        head = self._readline()
        if head.startswith("ERR"):
            raise RuntimeError(f"SEARCH 失败：{head}")
        n = int(head)
        hits = []
        for _ in range(n):
            doc_id, score = self._readline().split()
            hits.append((int(doc_id), float(score)))
        return hits

    def count(self) -> int:
        self._send(f"COUNT {self.collection}\n")
        return int(self._expect_ok("COUNT"))

    def drop(self) -> None:
        self._send(f"DROP {self.collection}\n")
        self._expect_ok("DROP")

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._send("QUIT\n")
                self._proc.stdin.close()
            except (OSError, RuntimeError):
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


# ═════════════════════════════════════════════════════ 后端二：轻量 numpy

class NumpyStore(VectorStore):
    """轻量后端：把所有向量放进一个 numpy 矩阵，检索用一次矩阵乘法。

    算法上就是「暴力检索」，但与阶段01 的朴素实现有两处关键区别：

      1. **建库时就把向量归一化好**。归一化后的向量做点积，结果直接等于余弦相似度。
         阶段01 的实现每比较一条就把查询向量重新归一化一次，2 万条数据白算 2 万次——
         实测这一处就差了 3.8 倍（828ms → 3.4ms 里的主要部分）。
      2. **用矩阵乘法代替逐条循环**，交给 BLAS 用向量化指令算。

    持久化：向量矩阵存 .npy，doc_id 存 .npy，另有一个 .json 记录维度等元信息。
    """

    name = "numpy"

    def __init__(self, data_dir: str = None):
        import numpy as np           # 延迟导入：不用这个后端时不强制依赖 numpy
        self._np = np
        self.data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".local", "share", "rag-numpy-store")
        os.makedirs(self.data_dir, exist_ok=True)

        self.dim = DEFAULT_DIM
        self._matrix = None          # (N, dim) float32，已 L2 归一化
        self._doc_ids = None         # (N,) int64
        self._pending = []           # 尚未合并进矩阵的新数据

        self._load()

    # ---------------------------------------------------------- 内部

    @property
    def _matrix_path(self):
        return os.path.join(self.data_dir, "vectors.npy")

    @property
    def _ids_path(self):
        return os.path.join(self.data_dir, "doc_ids.npy")

    @property
    def _meta_path(self):
        return os.path.join(self.data_dir, "meta.json")

    def _load(self) -> None:
        if os.path.exists(self._matrix_path) and os.path.exists(self._ids_path):
            self._matrix = self._np.load(self._matrix_path)
            self._doc_ids = self._np.load(self._ids_path)
            if os.path.exists(self._meta_path):
                with open(self._meta_path, "r", encoding="utf-8") as f:
                    self.dim = json.load(f).get("dim", DEFAULT_DIM)

    def _flush_pending(self) -> None:
        """把待写入的数据合并进主矩阵。归一化在这里一次性做完。"""
        if not self._pending:
            return
        np = self._np
        ids = np.array([i for i, _ in self._pending], dtype=np.int64)
        mat = np.array([v for _, v in self._pending], dtype=np.float32)

        # L2 归一化。零向量会导致除零，用 1 兜底（正常 embedding 不会是全零）
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat /= norms

        if self._matrix is None:
            self._matrix, self._doc_ids = mat, ids
        else:
            self._matrix = np.concatenate([self._matrix, mat], axis=0)
            self._doc_ids = np.concatenate([self._doc_ids, ids], axis=0)
        self._pending = []

    def save(self) -> None:
        """落盘。调用方在批量写入结束后显式调用。"""
        self._flush_pending()
        if self._matrix is None:
            return
        self._np.save(self._matrix_path, self._matrix)
        self._np.save(self._ids_path, self._doc_ids)
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump({"dim": self.dim, "count": int(self._matrix.shape[0])},
                      f, ensure_ascii=False)

    # ---------------------------------------------------------- 接口实现

    def create(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self._matrix = None
        self._doc_ids = None
        self._pending = []
        for p in (self._matrix_path, self._ids_path, self._meta_path):
            if os.path.exists(p):
                os.remove(p)

    def add(self, items) -> int:
        items = list(items)
        self._pending.extend(items)
        return len(items)

    def search(self, query_vector, top_k: int = 5):
        np = self._np
        self._flush_pending()
        if self._matrix is None or self._matrix.shape[0] == 0:
            return []

        q = np.asarray(query_vector, dtype=np.float32)
        n = np.linalg.norm(q)
        if n != 0:
            q = q / n

        scores = self._matrix @ q                       # (N,)

        k = min(top_k, scores.shape[0])
        # argpartition 只把前 k 个挑到前面，不做全排序——N 很大时比 argsort 快得多
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [(int(self._doc_ids[i]), float(scores[i])) for i in idx]

    def count(self) -> int:
        self._flush_pending()
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    def close(self) -> None:
        self.save()


# ═════════════════════════════════════════════════════ 文档存储（两个后端共用）

class DocStore:
    """doc_id → 原文/元数据 的映射。与向量后端解耦，两个后端共用同一份。

    格式是 JSONL（一行一条，写入简单、可人工查看），但 `save()` 每次都是
    **全量覆写**（从内存里的 `_docs` 整份重新写出），不是增量追加——独立
    评审R-14指出此前文档字符串写的是"追加写"，与实现不符。全量覆写是
    刻意的，不是笔误：`build_knowledge_index.py` 依赖这个语义做"每存20条
    落一次盘"的部分失败保护（R-3），全量覆写保证每次落盘都是内存当前
    状态的完整快照，不会有"追加导致新旧数据混杂"的问题。
    真到了需要按条件过滤查询的时候再换 sqlite，届时接口不变。
    """

    def __init__(self, path: str):
        self.path = path
        self._docs = {}
        if os.path.exists(path):
            self.load()

    def add(self, doc_id: int, text: str, meta: dict = None) -> None:
        self._docs[doc_id] = {"doc_id": doc_id, "text": text, "meta": meta or {}}

    def get(self, doc_id: int):
        return self._docs.get(doc_id)

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for d in self._docs.values():
                f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def load(self) -> None:
        self._docs = {}
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                self._docs[d["doc_id"]] = d

    def __len__(self):
        return len(self._docs)


# ═════════════════════════════════════════════════════ 工厂

def create_store(backend: str = "kylin", **kwargs) -> VectorStore:
    """按名字创建后端。切换后端只需要改这一个字符串。

    backend: "kylin"（官方麒麟向量引擎）| "numpy"（轻量 numpy）
    """
    backend = backend.lower()
    if backend in ("kylin", "ve", "official"):
        return KylinEngineStore(**kwargs)
    if backend in ("numpy", "np", "lite"):
        return NumpyStore(**kwargs)
    raise ValueError(f"未知后端：{backend}（可选：kylin / numpy）")


# ═════════════════════════════════════════════════════ 自检
#
# 用与阶段01「端到端最小管线」完全相同的五条文本与查询，分别跑两个后端。
# 两个后端的排序与分数都应与历史记录一致（0.9441 / 0.7118 / 0.5326），
# 这同时验证了「可切换」是真的等价，而不是接口长得像而已。

if __name__ == "__main__":
    import sys

    SERVERD_DIR = os.path.expanduser("~/rag/serverd")
    sys.path.insert(0, SERVERD_DIR)
    from embedding_client import EmbeddingClient   # noqa: E402

    DEMO_TEXTS = [
        "怎么重启网络服务",
        "如何查看磁盘剩余空间",
        "今天天气怎么样",
        "网络连接不上怎么办",
        "如何清理系统缓存文件",
    ]
    QUERY = "网络服务重启方法"

    print("用官方 embedding SDK 生成向量...")
    with EmbeddingClient(serverd_path=os.path.join(SERVERD_DIR, "emb_serverd")) as emb:
        vectors = [emb.encode(t) for t in DEMO_TEXTS]
        qvec = emb.encode(QUERY)
    print(f"完成，维度 {len(vectors[0])}\n")

    docs = DocStore(os.path.expanduser("~/rag/ve/work/selftest_docs.jsonl"))
    for i, t in enumerate(DEMO_TEXTS):
        docs.add(i, t)
    docs.save()

    for backend in ("kylin", "numpy"):
        print("=" * 60)
        print(f"后端：{backend}")
        print("=" * 60)
        kwargs = {"collection": "rag_selftest"} if backend == "kylin" else {}
        with create_store(backend, **kwargs) as store:
            store.create(dim=len(vectors[0]))
            store.add(list(enumerate(vectors)))
            print(f"  已写入 {store.count()} 条")
            for rank, (doc_id, score) in enumerate(store.search(qvec, top_k=3), 1):
                print(f"  第{rank}名  相似度={score:.4f}   原文：{docs.get(doc_id)['text']}")
        print()

    print("对照阶段01 记录：0.9441 / 0.7118 / 0.5326，两个后端都应一致")
