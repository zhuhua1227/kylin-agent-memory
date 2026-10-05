/*
 * dim_crosscheck.c —— 向量维度的双重独立验证（交叉验证 X-001）
 *
 * ── 为什么要单独写这个程序 ──────────────────────────────────────────
 * 「官方 embedding 输出 768 维」是本项目最基础的一条事实，后面所有工作都建立在它之上。
 * 单一来源的测量说服力有限——万一是我们读错了某个字段呢？
 *
 * 所以这里用**两条互相独立的路径**取同一个数字：
 *
 *   路径一（问模型自己）：
 *       text_embedding_get_model_list() → embedding_model_list_get_model(0)
 *       → embedding_model_info_get_model_dim()
 *     数据来自**模型描述元数据**，是模型对自己的声明，此时还没做过任何推理。
 *
 *   路径二（数实际输出）：
 *       text_embedding(session, 文本, &result)
 *       → embedding_result_get_vector_length()
 *     数据来自**一次真实推理的结果**，是实际产出了多少个浮点数。
 *
 * 两条路径在 SDK 内部不共用同一个数据源：一个读配置，一个数结果。
 * 若二者一致，「768」这个结论就不再依赖于我们是否读对了某一个字段。
 *
 * 程序同时打印前 5 个向量值，作为**跨程序比对的锚点**——
 * emb_test / emb_vec / emb_serverd 三个独立程序对同一文本应输出完全相同的数值（交叉验证 X-003）。
 *
 * ── 编译 ────────────────────────────────────────────────────────────
 *   gcc dim_crosscheck.c -o dim_crosscheck \
 *       -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
 */

#include <stdio.h>
#include <stdbool.h>
#include "embedding.h"

/* 固定用这段文本，与 emb_test 保持一致，便于三个程序横向比对数值 */
#define PROBE_TEXT "怎么重启网络服务"
#define MODEL_NAME "ensemble-embd_gte-base_uint8-text"

int main(void) {
    TextEmbeddingSession *session = text_embedding_create_session();
    if (session == NULL) {
        fprintf(stderr, "创建 session 失败\n");
        return 1;
    }
    if (text_embedding_init_session(session) != 0) {
        fprintf(stderr, "init_session 失败\n");
        return 1;
    }

    /* ───── 路径一：读模型元数据里自己声明的维度 ─────
     * 注意这套接口的风格：每个函数都用出参 error_code 返回错误码，
     * 返回值本身是数据。model_list 的生命周期绑定在 session 上，不需要单独销毁。 */
    int dim_from_metadata = -1;
    int err = 0;

    const EmbeddingModelList *list = text_embedding_get_model_list(session, &err);
    if (list != NULL && err == 0) {
        int count = embedding_model_list_get_count(list, &err);
        printf("模型数量: %d\n", count);
        if (count > 0) {
            const EmbeddingModelInfo *info =
                embedding_model_list_get_model(list, 0, &err);
            if (info != NULL && err == 0) {
                const char *name = embedding_model_info_get_model_name(info, &err);
                printf("模型名: %s\n", name ? name : "(未知)");
                dim_from_metadata = embedding_model_info_get_model_dim(info, &err);
            }
        }
    } else {
        fprintf(stderr, "get_model_list 失败 errCode=%d（不影响路径二）\n", err);
    }

    /* ───── 路径二：实际跑一次推理，数输出了多少个数 ───── */
    if (text_embedding_init_model(session, MODEL_NAME) != 0) {
        fprintf(stderr, "init_model 失败\n");
        return 1;
    }

    EmbeddingResult *result = NULL;
    int dim_from_result = -1;
    if (text_embedding(session, PROBE_TEXT, &result) && result != NULL &&
        embedding_result_get_error_code(result) == 0) {
        dim_from_result = embedding_result_get_vector_length(result);
        float *vec = embedding_result_get_vector_data(result);

        printf("前5个值:");
        for (int i = 0; i < 5 && i < dim_from_result; i++) {
            printf(" %f", vec[i]);
        }
        printf("\n");
        embedding_result_destroy(&result);
    } else {
        fprintf(stderr, "向量化失败\n");
        if (result) embedding_result_destroy(&result);
    }

    /* ───── 比对 ───── */
    printf("model_dim=%d\n", dim_from_metadata);
    printf("vector_length=%d\n", dim_from_result);
    printf("交叉验证: %s\n",
           (dim_from_metadata == dim_from_result && dim_from_result > 0)
               ? "一致" : "不一致");

    text_embedding_destroy_session(&session);
    /* 两条路径不一致时用非 0 退出码，便于脚本判定 */
    return (dim_from_metadata == dim_from_result && dim_from_result > 0) ? 0 : 1;
}
