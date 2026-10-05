/*
 * ve_bench.cpp —— 官方向量引擎在真实数据规模下的插入与检索性能测量
 *
 * 要回答的问题：
 *   本项目 14047 条记录切块后约 2~3 万条。这个规模下，
 *   用官方向量引擎做检索，能不能满足「知识检索响应 ≤500ms」的硬指标？
 *   插入这 2~3 万条又要多久（影响建索引的开发节奏）？
 *
 * ⚠️ 用随机向量测量的说明：
 *   检索耗时只取决于数据规模与向量维度，与向量数值无关，因此用随机向量测**延迟**有效。
 *   但随机向量**不能**用来评估召回质量——那需要真实向量与标注评测集（任务 C）。
 *   本程序只回答"快不快"，不回答"准不准"。
 *
 * 编译：
 *   g++ -std=c++17 ve_bench.cpp -o ve_bench \
 *       -I$HOME/rag/ve/extracted/usr/include/kysdk-vector-engine-client \
 *       -I$HOME/rag/ve/extracted/usr/include \
 *       /usr/lib/x86_64-linux-gnu/libkysdk-vector-engine-client.so.1
 *
 * 用法：
 *   ./ve_bench 20000 20        # 库中 20000 条，检索重复 20 次
 */

#include <algorithm>
#include <chrono>
#include <iostream>
#include <memory>
#include <random>
#include <string>
#include <vector>

#include "Database.h"

using namespace VectorDB;
using Clock = std::chrono::steady_clock;

static const char* kCollection = "rag_b2_bench";
static const int kDim = 768;
static const int kBatch = 1000;   /* 分批插入，避免单次 gRPC 消息过大 */

static double MsSince(Clock::time_point t0) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

static bool Check(const Status& st, const char* what) {
    if (!st.IsOk()) {
        std::cerr << "[失败] " << what << "  code=" << static_cast<int>(st.Code())
                  << "  msg=" << st.Message() << std::endl;
        return false;
    }
    return true;
}

static double Percentile(std::vector<double> v, double pct) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    size_t k = static_cast<size_t>(pct / 100.0 * v.size() + 0.5);
    if (k == 0) k = 1;
    if (k > v.size()) k = v.size();
    return v[k - 1];
}

int main(int argc, char** argv) {
    int count = (argc > 1) ? std::stoi(argv[1]) : 20000;
    int repeat = (argc > 2) ? std::stoi(argv[2]) : 20;

    std::cout << "规模：" << count << " 条 × " << kDim << " 维，检索重复 "
              << repeat << " 次" << std::endl;

    /* 生成随机单位向量。归一化是为了让 COSINE 度量的数值落在正常范围，
       与真实 embedding 的分布形态一致。 */
    std::mt19937 rng(42);
    std::uniform_real_distribution<float> dist(-1.0f, 1.0f);
    auto make_vec = [&]() {
        std::vector<float> v(kDim);
        float norm = 0.0f;
        for (auto& x : v) { x = dist(rng); norm += x * x; }
        norm = std::sqrt(norm);
        for (auto& x : v) x /= norm;
        return v;
    };

    // 独立评审R-9：Database::Create() 返回值此前未判空即调用 db->Connect()
    auto db = Database::Create();
    if (!db) {
        std::cerr << "Database::Create() 返回空" << std::endl;
        return 1;
    }
    ConnectParam param("rag_b2_bench");
    if (!Check(db->Connect(param), "Connect")) return 1;

    // 独立评审R-13：HasCollection/DropCollection 的 Status 返回值此前被忽略
    bool has = false;
    if (!Check(db->HasCollection(kCollection, has), "HasCollection")) return 1;
    if (has && !Check(db->DropCollection(kCollection), "DropCollection")) return 1;
    if (!Check(db->CreateCollection(kCollection, kDim), "CreateCollection")) return 1;

    /* ---------- 插入 ---------- */
    std::cout << "\n开始插入..." << std::endl;
    auto t_insert = Clock::now();
    int inserted = 0;
    while (inserted < count) {
        int n = std::min(kBatch, count - inserted);
        std::vector<std::vector<float>> vecs;
        std::vector<nlohmann::json> metas;
        vecs.reserve(n);
        metas.reserve(n);
        for (int i = 0; i < n; i++) {
            vecs.push_back(make_vec());
            nlohmann::json j;
            j["text"] = "样本记录 #" + std::to_string(inserted + i);
            metas.push_back(j);
        }
        std::vector<FieldDataPtr> fields;
        fields.push_back(std::make_shared<FloatVecFieldData>(DEFAULT_VECTOR_FIELD_NAME, vecs));
        fields.push_back(std::make_shared<JsonFieldData>(DYNAMIC_FIELD_NAME, metas));

        DmlResults dml;
        if (!Check(db->Insert(kCollection, fields, dml), "Insert")) return 1;
        inserted += n;
        std::cout << "  已插入 " << inserted << "/" << count << std::endl;
    }
    double insert_ms = MsSince(t_insert);
    std::cout << "插入完成，总耗时 " << insert_ms / 1000.0 << " 秒，"
              << "平均 " << insert_ms / count << " ms/条" << std::endl;

    /* ---------- 检索 ---------- */
    std::cout << "\n开始检索测量..." << std::endl;
    std::vector<double> timings;
    for (int i = 0; i < repeat; i++) {
        auto q = make_vec();
        SearchArguments args(kCollection, 10, MetricType::COSINE);
        args.AddTargetVector(DEFAULT_VECTOR_FIELD_NAME, q);
        args.AddOutputField(DYNAMIC_FIELD_NAME);

        SearchResults results;
        auto t0 = Clock::now();
        Status st = db->Search(args, results);
        double ms = MsSince(t0);
        if (!st.IsOk()) {
            std::cerr << "检索失败 code=" << static_cast<int>(st.Code()) << std::endl;
            return 1;
        }
        timings.push_back(ms);
    }

    double p50 = Percentile(timings, 50), p95 = Percentile(timings, 95);
    double mn = *std::min_element(timings.begin(), timings.end());
    double mx = *std::max_element(timings.begin(), timings.end());

    std::cout << "\n================ 检索延迟（top-10）================" << std::endl;
    std::cout << "  最小 " << mn << "ms   P50 " << p50 << "ms   P95 " << p95
              << "ms   最大 " << mx << "ms" << std::endl;

    // 独立评审R-5：查询向量化P95先后测出68.4/84.9/118~124/28.4ms四个值，
    // 此前这里硬编码了最早、已被重跑推翻的68.4ms（《向量化常驻服务/说明
    // 报告.md》结论区已更新为84.9ms，2026-08-04重跑）。
    const double kEmbP95 = 84.9;
    std::cout << "\n对照硬指标 ≤500ms：" << std::endl;
    std::cout << "  检索 P95 = " << p95 << "ms，占预算 " << (p95 / 500.0 * 100) << "%" << std::endl;
    std::cout << "  加上查询向量化（实测 30 字 P95 = " << kEmbP95 << "ms，"
              << "《向量化常驻服务/说明报告.md》5.3节），"
              << "端到端上界估计约 " << (p95 + kEmbP95) << "ms，占预算 "
              << ((p95 + kEmbP95) / 500.0 * 100) << "%"
              << "（两个独立基准的P95相加，非逐题求和分位数）" << std::endl;

    std::cout << "\n（基准集合 " << kCollection << " 保留，如需清理请自行 DropCollection）" << std::endl;
    db->Disconnect();
    return 0;
}
