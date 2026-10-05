/*
 * ve_demo.cpp —— 银河麒麟官方向量引擎（libkysdk-vector-engine-client）联调验证程序
 *
 * ── 这个程序要证明什么 ──────────────────────────────────────────────
 * 证明「官方 embedding SDK → 官方向量引擎」这条**全官方组件**的链路能端到端打通：
 *     文本 → [官方 KYSDK embedding] → 768维向量 → [官方向量引擎] → 建库 → 检索 → 结果
 *
 * 这是本项目「麒麟端侧适配」这一护城河的关键一环：不仅 embedding 用官方 SDK，
 * 向量存储与检索也用麒麟自带的向量引擎，而不是自己拿 Python list 或第三方库凑。
 *
 * ── 为什么向量不在本程序里算 ────────────────────────────────────────
 * 向量由同项目的常驻向量化服务（emb_serverd + embedding_client.py）预先算好，
 * 通过文本文件传进来。这样做的原因：
 *   1. 职责分离——本程序只验证向量引擎，向量化正确性已在《向量化常驻服务》中单独证明过；
 *   2. 便于对照——用的是与阶段01 完全相同的五条文本，检索结果可以和历史记录逐位比对，
 *      一旦排序或分数对不上，就能立刻判断是向量引擎这一环引入的问题。
 *
 * ── 输入文件格式 ────────────────────────────────────────────────────
 *   docs.tsv  ：每行 `<原文>\t<768个float，空格分隔>`  —— 要入库的知识
 *   query.tsv ：一行   `<原文>\t<768个float，空格分隔>`  —— 查询
 *
 * ── 关于向量引擎的几个已查明事实（写在这里免得后人再查一遍）────────────
 *   · 它是 Milvus Lite 的麒麟分支（包名 kylin-ai-vector-engine），
 *     以**用户级 systemd 服务**随桌面会话自启（/usr/lib/systemd/user/），
 *     所以 `systemctl list-unit-files`（system 作用域）查不到它，要加 --user；
 *   · 通信走 Unix socket + gRPC，数据库文件默认在
 *     ~/.local/share/kylin-ai-vector-engine/default.db（位于 /home，重启不丢）；
 *   · 默认字段名定义在 types/Constants.h：
 *     id 字段 "id"、向量字段 "vector"、动态字段 "$meta"。
 *
 * ── 编译 ────────────────────────────────────────────────────────────
 * 头文件取自 dev 包解压目录（未安装 dev 包，避免动系统状态），
 * 链接则直接指向系统已装的 .so 实体文件：
 *
 *   g++ -std=c++17 ve_demo.cpp -o ve_demo \
 *       -I$HOME/rag/ve/extracted/usr/include/kysdk-vector-engine-client \
 *       -I$HOME/rag/ve/extracted/usr/include \
 *       /usr/lib/x86_64-linux-gnu/libkysdk-vector-engine-client.so.1
 */

#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "Database.h"

using namespace VectorDB;

/* 集合名。带 b1 前缀是为了和后续正式的知识库集合区分开，
   本程序每次运行都会先 Drop 再建，属于一次性验证用集合。 */
static const char* kCollection = "rag_b1_probe";
static const int kDim = 768;   /* 必须与官方 embedding 输出维度一致 */

/* 统一的错误检查：向量引擎的每个调用都返回 Status，不检查会让失败静默通过 */
static bool Check(const Status& st, const char* what) {
    if (!st.IsOk()) {
        std::cerr << "[失败] " << what
                  << "  code=" << static_cast<int>(st.Code())
                  << "  msg=" << st.Message() << std::endl;
        return false;
    }
    std::cout << "[成功] " << what << std::endl;
    return true;
}

struct Doc {
    std::string text;
    std::vector<float> vec;
};

/* 读入 `<原文>\t<768个float>` 格式的文件 */
static std::vector<Doc> LoadTsv(const std::string& path) {
    std::vector<Doc> docs;
    std::ifstream in(path);
    if (!in) {
        std::cerr << "打不开文件：" << path << std::endl;
        return docs;
    }
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        auto tab = line.find('\t');
        if (tab == std::string::npos) continue;

        Doc d;
        d.text = line.substr(0, tab);
        std::istringstream iss(line.substr(tab + 1));
        float f;
        while (iss >> f) d.vec.push_back(f);

        if (static_cast<int>(d.vec.size()) != kDim) {
            std::cerr << "维度不对（期望 " << kDim << "，实得 " << d.vec.size()
                      << "）：" << d.text << std::endl;
            continue;
        }
        docs.push_back(std::move(d));
    }
    return docs;
}

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "用法: " << argv[0] << " <db文件路径> <docs.tsv> <query.tsv>" << std::endl;
        return 1;
    }
    const std::string db_file = argv[1];

    auto docs = LoadTsv(argv[2]);
    auto queries = LoadTsv(argv[3]);
    if (docs.empty() || queries.empty()) {
        std::cerr << "文档或查询为空，退出" << std::endl;
        return 1;
    }
    std::cout << "读入 " << docs.size() << " 条文档、"
              << queries.size() << " 条查询，维度 " << kDim << std::endl;

    /* ① 建客户端并连接。ConnectParam 只传一个 appId，
          具体连哪个 socket 由 SDK 内部按当前用户解析，调用方不需要关心地址。 */
    auto db = Database::Create();
    if (!db) {
        std::cerr << "Database::Create() 返回空" << std::endl;
        return 1;
    }
    ConnectParam param("rag_b1_probe");
    if (!Check(db->Connect(param), "Connect 连接向量引擎")) return 1;

    /* ② 指定数据库文件。
          传 "-" 表示跳过，直接使用引擎守护进程启动时已加载的默认库
          （~/.local/share/kylin-ai-vector-engine/default.db）。

          实测记录：向量引擎是**独立的服务端进程**，db 路径由服务端解析，
          不是客户端本地打开文件。指定服务端数据目录之外的任意路径会被拒绝，
          返回 SERVER_FAILED(1002)。因此这里不把失败当致命错误——
          若打不开就退回默认库继续验证，避免因为一个可选步骤中断整个联调。 */
    if (db_file != "-") {
        Status st = db->LoadDBFile(db_file);
        if (!st.IsOk()) {
            std::cerr << "[警告] LoadDBFile(" << db_file << ") 失败"
                      << "  code=" << static_cast<int>(st.Code())
                      << "（1002=SERVER_FAILED，通常是服务端不接受该路径）"
                      << "，改用引擎默认数据库继续" << std::endl;
        } else {
            std::cout << "[成功] LoadDBFile 打开数据库文件 " << db_file << std::endl;
        }
    } else {
        std::cout << "[跳过] LoadDBFile，直接使用引擎默认数据库" << std::endl;
    }

    /* ③ 若集合已存在先删除，保证每次运行都是干净状态、结果可复现 */
    bool has = false;
    db->HasCollection(kCollection, has);
    if (has) {
        std::cout << "集合已存在，先删除以保证可复现" << std::endl;
        if (!Check(db->DropCollection(kCollection), "DropCollection")) return 1;
    }

    /* ④ 快捷建集合：自动创建 id（自增）与 vector 两个字段，并开启动态字段。
          动态字段用来存原文——这样检索命中后可以直接拿回文本，不必再回查别的存储。 */
    if (!Check(db->CreateCollection(kCollection, kDim), "CreateCollection 建集合(768维)")) return 1;

    /* ⑤ 批量插入。需要两个字段的数据：
          · vector 字段（FloatVecFieldData）
          · 动态字段 $meta（JsonFieldData），这里放原文 */
    std::vector<std::vector<float>> vecs;
    std::vector<nlohmann::json> metas;
    for (const auto& d : docs) {
        vecs.push_back(d.vec);
        nlohmann::json j;
        j["text"] = d.text;
        metas.push_back(j);
    }

    std::vector<FieldDataPtr> fields;
    fields.push_back(std::make_shared<FloatVecFieldData>(DEFAULT_VECTOR_FIELD_NAME, vecs));
    fields.push_back(std::make_shared<JsonFieldData>(DYNAMIC_FIELD_NAME, metas));

    DmlResults dml;
    if (!Check(db->Insert(kCollection, fields, dml), "Insert 插入向量")) return 1;
    std::cout << "已插入 " << dml.IdArray().IntIDArray().size() << " 条" << std::endl;

    /* ⑥ 检索。度量方式用 COSINE，与阶段01 Python 版的余弦相似度保持一致，
          这样分数才有可比性。 */
    for (const auto& q : queries) {
        std::cout << "\n========== 查询：" << q.text << " ==========" << std::endl;

        SearchArguments args(kCollection, 3, MetricType::COSINE);
        args.AddTargetVector(DEFAULT_VECTOR_FIELD_NAME, q.vec);
        args.AddOutputField(DYNAMIC_FIELD_NAME);   /* 让结果带回原文 */

        SearchResults results;
        if (!Check(db->Search(args, results), "Search 检索")) return 1;

        for (auto& single : results.Results()) {
            const auto& scores = single.Scores();
            auto meta_field = single.OutputField(DYNAMIC_FIELD_NAME);
            auto meta = std::dynamic_pointer_cast<JsonFieldData>(meta_field);

            for (size_t i = 0; i < scores.size(); i++) {
                std::string text = "(无)";
                if (meta && i < meta->Data().size()) {
                    text = meta->Data()[i].value("text", std::string("(无)"));
                }
                std::cout << "  第" << (i + 1) << "名  相似度=" << scores[i]
                          << "   原文：" << text << std::endl;
            }
        }
    }

    /* ⑦ 收尾。保留集合不删，方便人工用别的工具复查数据确实落盘了。 */
    std::cout << "\n验证完成。集合 " << kCollection << " 保留在 " << db_file
              << "，可重复查询。" << std::endl;
    db->Disconnect();
    return 0;
}
