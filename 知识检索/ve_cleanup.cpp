/*
 * ve_cleanup.cpp —— 清理验证/基准测试遗留的集合
 *
 * 为什么需要它：向量引擎的默认数据库 default.db 是**全系统共用**的，
 * 基准测试插入 2 万条 768 维向量会让它从 15MB 涨到约 97MB。
 * 验证做完就该清掉，别把一次性测试数据长期留在公共库里。
 *
 * 编译：
 *   g++ -std=c++17 ve_cleanup.cpp -o ve_cleanup \
 *       -I$HOME/rag/ve/extracted/usr/include/kysdk-vector-engine-client \
 *       -I$HOME/rag/ve/extracted/usr/include \
 *       /usr/lib/x86_64-linux-gnu/libkysdk-vector-engine-client.so.1
 */

#include <iostream>
#include <string>
#include <vector>

#include "Database.h"

using namespace VectorDB;

// 独立评审R-9/R-13：Database::Create() 返回值此前未判空即调用 db->Connect()；
// HasCollection/ShowCollections 的 Status 返回值此前被忽略。同项目 ve_demo.cpp
// 已经这么处理了（Check() 辅助函数 + 判空），这里补齐，做法保持一致。
int main(int argc, char** argv) {
    auto db = Database::Create();
    if (!db) {
        std::cerr << "Database::Create() 返回空" << std::endl;
        return 1;
    }
    ConnectParam param("rag_cleanup");
    Status st = db->Connect(param);
    if (!st.IsOk()) {
        std::cerr << "连接失败 code=" << static_cast<int>(st.Code()) << std::endl;
        return 1;
    }

    /* 命令行给了集合名就删指定的，没给就删本项目默认的两个测试集合 */
    std::vector<std::string> targets;
    if (argc > 1) {
        for (int i = 1; i < argc; i++) targets.push_back(argv[i]);
    } else {
        targets = {"rag_b2_bench", "rag_b1_probe"};
    }

    for (const auto& name : targets) {
        bool has = false;
        Status hs = db->HasCollection(name, has);
        if (!hs.IsOk()) {
            std::cerr << "查询失败：" << name << "  code=" << static_cast<int>(hs.Code()) << std::endl;
            continue;
        }
        if (!has) {
            std::cout << "跳过（不存在）：" << name << std::endl;
            continue;
        }
        Status s = db->DropCollection(name);
        std::cout << (s.IsOk() ? "已删除：" : "删除失败：") << name << std::endl;
    }

    std::vector<std::string> remaining;
    Status ss = db->ShowCollections(remaining);
    if (!ss.IsOk()) {
        std::cerr << "ShowCollections 失败 code=" << static_cast<int>(ss.Code()) << std::endl;
    }
    std::cout << "\n剩余集合数：" << remaining.size() << std::endl;
    for (const auto& c : remaining) std::cout << "  " << c << std::endl;

    db->Disconnect();
    return 0;
}
