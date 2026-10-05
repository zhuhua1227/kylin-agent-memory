/*
 * emb_test.c —— 银河麒麟 V11 KYSDK 文本 Embedding 维度验证程序
 *
 * 目的：调用官方 embedding SDK（libkylin-coreai-embedding），
 *       将一句中文文本向量化，并由官方接口打印其向量维度，
 *       实测证明输出为 768 维。
 *
 * 编译：
 *   gcc emb_test.c -o emb_test \
 *       -I/usr/include/kylin-ai/coreai/embedding \
 *       -lkysdk-coreai-embedding
 * 运行：
 *   ./emb_test
 *
 * 依赖头文件：/usr/include/kylin-ai/coreai/embedding/embedding.h
 */

#include <stdio.h>
#include <stdbool.h>
#include "embedding.h"   /* 官方 SDK 头文件，声明下列 text_embedding_* 接口 */

int main(void) {

    /* ① 创建文本向量化会话，得到会话句柄（不透明指针，只需持有并传递） */
    TextEmbeddingSession *session = text_embedding_create_session();
    if (session == NULL) {
        printf("创建 session 失败\n");
        return 1;
    }

    /* ② 初始化会话；返回值为错误码，0 表示成功 */
    int rc = text_embedding_init_session(session);
    if (rc != 0) {
        printf("init_session 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ③ 加载指定的向量化模型；模型名为官方 GTE-base 模型 */
    const char *model_name = "ensemble-embd_gte-base_uint8-text";
    rc = text_embedding_init_model(session, model_name);
    if (rc != 0) {
        printf("init_model 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ④ 同步接口：把一句中文变成向量。
     *    result 为出参 —— 先置空，再传 &result，函数内部为其分配结果对象。 */
    EmbeddingResult *result = NULL;
    bool ok = text_embedding(session, argv[1], &result);
    if (!ok || result == NULL) {
        printf("text_embedding 调用失败\n");
        return 1;
    }

    /* ⑤ 检查本次向量化是否出错（例如后端协议不匹配会在此返回非 0 错误码） */
    int err = embedding_result_get_error_code(result);
    if (err != 0) {
        printf("向量化出错，错误码=%d，信息=%s\n",
               err, embedding_result_get_error_message(result));
        embedding_result_destroy(&result);
        return 1;
    }

    /* ⑥ ★核心★ 由官方接口读取向量长度（即维度）与向量数据。
     *    embedding_result_get_vector_length() 的返回值即为权威的向量维度。 */
    int dim = embedding_result_get_vector_length(result);
    float *vec = embedding_result_get_vector_data(result);

    printf("=============== 真实结果 ===============\n");
    printf("模型名   : %s\n", model_name);
    printf("向量维度 : %d\n", dim);                  /* 实测输出 768 */
    printf("前 5 个值: %f  %f  %f  %f  %f\n",
           vec[0], vec[1], vec[2], vec[3], vec[4]);
    printf("=======================================\n");

    /* ⑦ 释放资源：结果对象与会话对象均以"传地址"方式销毁 */
    embedding_result_destroy(&result);
    text_embedding_destroy_session(&session);
    return 0;
}
