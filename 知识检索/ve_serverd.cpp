/*
 * ve_serverd.cpp —— 官方向量引擎的常驻桥接进程
 *
 * ── 为什么需要它 ────────────────────────────────────────────────────
 * 官方向量引擎 SDK（libkysdk-vector-engine-client）只提供 C++ 接口，没有 Python 绑定。
 * 而本项目的管线编排在 Python 侧（切块、建库、评测都是 Python 写起来快得多）。
 * 于是沿用《向量化常驻服务》里已经验证有效的同一套模式：
 *   把 C++ 能力封装成一个常驻的命令行进程，Python 通过管道驱动它。
 *
 * 这样做的好处：
 *   · 连接与集合句柄常驻，不必每次操作都重连；
 *   · Python 侧的两种存储后端（官方引擎 / numpy）可以有完全一致的接口，实现真正可切换；
 *   · 出问题时可以直接在命令行手敲协议调试，不用在 Python 和 C++ 之间混合排查。
 *
 * ── 协议（纯文本，只传数字，因此不需要任何转义）─────────────────────
 * 设计要点：**本进程只负责「doc_id → 向量」的索引，不存原文**。
 * 原文由 Python 侧的文档存储统一管理——这样 numpy 后端不必为了对齐而硬塞一个
 * 元数据字段，两个后端的职责边界完全相同，「可切换」才是真的可切换。
 *
 *   READY                                    启动完成后主动输出
 *
 *   CREATE <集合名> <维度>                     建集合（若已存在则先删，保证可复现）
 *     → OK
 *
 *   ADD <集合名> <条数> <维度>                  随后每行： <doc_id> <f1> <f2> ... <fN>
 *     → OK <实际插入条数>
 *
 *   SEARCH <集合名> <topK> <维度>              随后一行： <f1> <f2> ... <fN>
 *     → <返回条数>
 *       随后每行： <doc_id> <相似度>
 *
 *   COUNT <集合名>                             → <条数>
 *   DROP <集合名>                              → OK
 *   QUIT                                       → 退出
 *
 *   任何命令出错   → ERR <状态码> <说明>
 *
 * ── 编译 ────────────────────────────────────────────────────────────
 *   g++ -std=c++17 -O2 ve_serverd.cpp -o ve_serverd \
 *       -I$HOME/rag/ve/extracted/usr/include/kysdk-vector-engine-client \
 *       -I$HOME/rag/ve/extracted/usr/include \
 *       /usr/lib/x86_64-linux-gnu/libkysdk-vector-engine-client.so.1
 */

#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <vector>

#include "Database.h"

using namespace VectorDB;

/* 每批插入的上限。分批是为了避免单次 gRPC 消息过大；
   1000 条 × 768 维 × 4 字节 ≈ 3MB，实测稳定。 */
static const int kInsertBatch = 1000;

static std::shared_ptr<Database> g_db;

static void Fail(const Status& st, const char* what) {
    std::cout << "ERR " << static_cast<int>(st.Code()) << " " << what
              << " " << st.Message() << std::endl;
}

/* 独立评审R-11：cin 一旦因格式错误进入 failbit 状态，后续所有 >> 操作
   会静默no-op、不读取也不报错——外层 `while (std::cin >> cmd)` 主循环
   会因此立即判假退出，整个常驻进程悄悄死掉，而不是"后续命令解析错位"
   这么轻的后果（协议本身没有显式帧边界，没法做到完全精确的重同步，
   这里退而求其次：清掉错误状态 + 丢弃到下一个换行，把"整个进程死掉"
   降级成"这一条命令失败，后续命令有机会正常处理"）。 */
static void ResyncStream() {
    std::cin.clear();
    std::cin.ignore(std::numeric_limits<std::streamsize>::max(), '\n');
}

/* 读入 dim 个浮点数。返回是否读满；失败时调用方负责 ResyncStream()。 */
static bool ReadVector(int dim, std::vector<float>& out) {
    out.resize(dim);
    for (int i = 0; i < dim; i++) {
        if (!(std::cin >> out[i])) return false;
    }
    return true;
}

/* ---------------------------------------------------------------- CREATE */
static void CmdCreate(const std::string& name, int dim) {
    bool has = false;
    // 独立评审R-13：HasCollection 的 Status 返回值此前被忽略
    Status hst = g_db->HasCollection(name, has);
    if (!hst.IsOk()) { Fail(hst, "HasCollection"); return; }
    if (has) {
        Status st = g_db->DropCollection(name);
        if (!st.IsOk()) { Fail(st, "DropCollection"); return; }
    }
    Status st = g_db->CreateCollection(name, dim);
    if (!st.IsOk()) { Fail(st, "CreateCollection"); return; }
    std::cout << "OK" << std::endl;
}

/* ------------------------------------------------------------------- ADD */
static void CmdAdd(const std::string& name, long count, int dim) {
    long done = 0;
    while (done < count) {
        long n = std::min<long>(kInsertBatch, count - done);

        std::vector<std::vector<float>> vecs;
        std::vector<nlohmann::json> metas;
        vecs.reserve(n);
        metas.reserve(n);

        for (long i = 0; i < n; i++) {
            long doc_id = 0;
            if (!(std::cin >> doc_id)) {
                ResyncStream();
                std::cout << "ERR -1 输入提前结束" << std::endl;
                return;
            }
            std::vector<float> v;
            if (!ReadVector(dim, v)) {
                ResyncStream();
                std::cout << "ERR -1 向量读取不完整" << std::endl;
                return;
            }
            vecs.push_back(std::move(v));

            /* 引擎的 id 字段是自增的，我们自己的 doc_id 放进动态字段，
               检索时再取回来——这样 Python 侧才能拿 doc_id 去查原文。 */
            nlohmann::json j;
            j["doc_id"] = doc_id;
            metas.push_back(std::move(j));
        }

        std::vector<FieldDataPtr> fields;
        fields.push_back(std::make_shared<FloatVecFieldData>(DEFAULT_VECTOR_FIELD_NAME, vecs));
        fields.push_back(std::make_shared<JsonFieldData>(DYNAMIC_FIELD_NAME, metas));

        DmlResults dml;
        Status st = g_db->Insert(name, fields, dml);
        if (!st.IsOk()) { Fail(st, "Insert"); return; }

        done += n;
    }
    std::cout << "OK " << done << std::endl;
}

/* ---------------------------------------------------------------- SEARCH */
static void CmdSearch(const std::string& name, int topk, int dim) {
    std::vector<float> q;
    if (!ReadVector(dim, q)) {
        ResyncStream();
        std::cout << "ERR -1 查询向量读取不完整" << std::endl;
        return;
    }

    /* 用 COSINE，与 numpy 后端的余弦相似度保持一致，两个后端的分数才可比 */
    SearchArguments args(name, topk, MetricType::COSINE);
    args.AddTargetVector(DEFAULT_VECTOR_FIELD_NAME, q);
    args.AddOutputField(DYNAMIC_FIELD_NAME);

    SearchResults results;
    Status st = g_db->Search(args, results);
    if (!st.IsOk()) { Fail(st, "Search"); return; }

    /* 单次查询只有一组结果。
       独立评审R-12：meta缺失时此前静默塞一个doc_id=-1的哨兵值，Python侧
       docs.get(-1)拿到None、打印"(未找到原文)"——问题被悄悄伪装成了正常
       输出的一部分。改为：跳过这条结果（不计入返回条数），同时往stderr写
       一行明确的异常记录，让"引擎返回的元数据缺失"这个真实问题在日志里
       可见，而不是被业务层的"未找到"文案吞掉。 */
    std::vector<std::pair<long, float>> hits;
    int meta_missing = 0;
    for (auto& single : results.Results()) {
        const auto& scores = single.Scores();
        auto meta = std::dynamic_pointer_cast<JsonFieldData>(
            single.OutputField(DYNAMIC_FIELD_NAME));
        for (size_t i = 0; i < scores.size(); i++) {
            if (!meta || i >= meta->Data().size() || !meta->Data()[i].contains("doc_id")) {
                meta_missing++;
                continue;
            }
            long doc_id = meta->Data()[i].value("doc_id", -1L);
            hits.emplace_back(doc_id, scores[i]);
        }
    }
    if (meta_missing > 0) {
        std::cerr << "[ve_serverd] SEARCH " << name << "：" << meta_missing
                  << " 条命中缺少 doc_id 元数据，已从结果中剔除（不是正常现象，"
                  << "值得排查索引是否写入不完整）" << std::endl;
    }

    std::cout << hits.size() << "\n";
    /* 分数用 9 位有效数字输出：float 往返需要 9 位十进制才不丢精度 */
    std::cout << std::setprecision(9);
    for (auto& h : hits) {
        std::cout << h.first << " " << h.second << "\n";
    }
    std::cout.flush();
}

/* ----------------------------------------------------------------- 其他 */
static void CmdCount(const std::string& name) {
    CollectionStat stat;
    Status st = g_db->GetCollectionStatistics(name, stat);
    if (!st.IsOk()) { Fail(st, "GetCollectionStatistics"); return; }
    std::cout << stat.RowCount() << std::endl;
}

static void CmdDrop(const std::string& name) {
    bool has = false;
    Status hst = g_db->HasCollection(name, has);
    if (!hst.IsOk()) { Fail(hst, "HasCollection"); return; }
    if (has) {
        Status st = g_db->DropCollection(name);
        if (!st.IsOk()) { Fail(st, "DropCollection"); return; }
    }
    std::cout << "OK" << std::endl;
}

int main() {
    /* 关掉 C++ 流与 C stdio 的同步，大批量读浮点数时快很多
       （建索引要读 2~3 万 × 768 个数字） */
    std::ios::sync_with_stdio(false);

    g_db = Database::Create();
    if (!g_db) {
        std::cerr << "[ve_serverd] Database::Create() 返回空" << std::endl;
        return 1;
    }

    ConnectParam param("rag_store");
    Status st = g_db->Connect(param);
    if (!st.IsOk()) {
        std::cerr << "[ve_serverd] Connect 失败 code="
                  << static_cast<int>(st.Code()) << std::endl;
        return 1;
    }

    /* 连接就绪后宣告，Python 侧等到 READY 再发命令 */
    std::cout << "READY" << std::endl;

    std::string cmd;
    while (std::cin >> cmd) {
        // 独立评审R-8：SDK调用与nlohmann::json操作全文没有任何try/catch，
        // 一次异常（比如json取值类型不符）会直接std::terminate，常驻进程
        // 静默死亡，且没有任何日志说明死因。包一层，捕获后打印ERR、继续
        // 服务下一条命令，而不是让一次异常拖垮整个常驻服务。
        try {
            if (cmd == "CREATE") {
                std::string name; int dim;
                std::cin >> name >> dim;
                CmdCreate(name, dim);
            } else if (cmd == "ADD") {
                std::string name; long count; int dim;
                std::cin >> name >> count >> dim;
                CmdAdd(name, count, dim);
            } else if (cmd == "SEARCH") {
                std::string name; int topk, dim;
                std::cin >> name >> topk >> dim;
                CmdSearch(name, topk, dim);
            } else if (cmd == "COUNT") {
                std::string name;
                std::cin >> name;
                CmdCount(name);
            } else if (cmd == "DROP") {
                std::string name;
                std::cin >> name;
                CmdDrop(name);
            } else if (cmd == "QUIT") {
                break;
            } else {
                std::cout << "ERR -1 未知命令 " << cmd << std::endl;
            }
        } catch (const std::exception& e) {
            std::cerr << "[ve_serverd] 处理命令 " << cmd << " 时抛出异常：" << e.what() << std::endl;
            std::cout << "ERR -1 内部异常 " << e.what() << std::endl;
            ResyncStream();
        } catch (...) {
            std::cerr << "[ve_serverd] 处理命令 " << cmd << " 时抛出未知类型异常" << std::endl;
            std::cout << "ERR -1 内部未知异常" << std::endl;
            ResyncStream();
        }
    }

    g_db->Disconnect();
    std::cerr << "[ve_serverd] 正常退出" << std::endl;
    return 0;
}
