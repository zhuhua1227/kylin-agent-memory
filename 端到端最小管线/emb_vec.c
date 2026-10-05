/*
 * emb_vec.c —— 通用文本向量化命令行工具
 *
 * 用途：给定一段文本（命令行参数），调用官方 KYSDK embedding SDK，
 *       输出该文本的 768 维向量（纯数字，空格分隔，单行），
 *       供其他程序（如 Python 管线）通过管道/subprocess 直接解析。
 *
 * 与 emb_test.c 的区别：
 *   emb_test.c 是"证明 768 维"用的演示程序，输出带人类可读的说明文字；
 *   emb_vec.c  是"管线内部调用"用的工具，只输出纯向量数据，不打印任何多余内容，
 *              便于被脚本化调用（Python 的 subprocess 可以直接切分解析）。
 *
 * 编译：
 *   gcc emb_vec.c -o emb_vec \
 *       -I/usr/include/kylin-ai/coreai/embedding \
 *       -lkysdk-coreai-embedding
 * 用法：
 *   ./emb_vec "一段要向量化的文本"
 * 输出（成功时，stdout，仅一行）：
 *   0.012345 -0.067890 ... （共 768 个浮点数，空格分隔）
 * 失败时：非 0 退出码，错误信息打印到 stderr（不会污染 stdout 的向量数据）
 */

#include <stdio.h>
#include <stdbool.h>
#include "embedding.h"

int main(int argc, char *argv[]) {

    /* 检查命令行参数：必须恰好传入一个文本参数 */
    if (argc != 2) {
        fprintf(stderr, "用法: %s \"待向量化的文本\"\n", argv[0]);
        return 1;
    }
    const char *input_text = argv[1];

    /* ① 创建并初始化会话 */
    TextEmbeddingSession *session = text_embedding_create_session();
    if (session == NULL) {
        fprintf(stderr, "创建 session 失败\n");
        return 1;
    }
    int rc = text_embedding_init_session(session);
    if (rc != 0) {
        fprintf(stderr, "init_session 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ② 加载模型（固定用 GTE-base 文本模型） */
    const char *model_name = "ensemble-embd_gte-base_uint8-text";
    rc = text_embedding_init_model(session, model_name);
    if (rc != 0) {
        fprintf(stderr, "init_model 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ③ 向量化传入的文本 */
    EmbeddingResult *result = NULL;
    bool ok = text_embedding(session, input_text, &result);
    if (!ok || result == NULL) {
        fprintf(stderr, "text_embedding 调用失败\n");
        return 1;
    }

    /* ④ 检查错误码 */
    int err = embedding_result_get_error_code(result);
    if (err != 0) {
        fprintf(stderr, "向量化出错，错误码=%d，信息=%s\n",
                err, embedding_result_get_error_message(result));
        embedding_result_destroy(&result);
        return 1;
    }

    /* ⑤ 只把向量数据打印到 stdout，空格分隔，一行结束 */
    int dim = embedding_result_get_vector_length(result);
    float *vec = embedding_result_get_vector_data(result);
    for (int i = 0; i < dim; i++) {
        printf("%f ", vec[i]);
    }
    printf("\n");

    /* ⑥ 释放资源 */
    embedding_result_destroy(&result);
    text_embedding_destroy_session(&session);
    return 0;
}
