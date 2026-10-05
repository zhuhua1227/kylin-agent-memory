/*
 * emb_serverd.c —— 常驻文本向量化服务（KYSDK embedding 长驻进程版）
 *
 * ── 为什么要有这个程序 ──────────────────────────────────────────────
 * 阶段01 的 emb_vec.c 是「一次调用 = 一个进程」：每向量化一段文本，都要完整走一遍
 *   建会话 → 初始化会话 → 加载模型（GTE-base，339MB）
 * 这个固定开销在「跑通骨架」阶段无所谓，但往后走会直接卡死项目：
 *   1) 硬指标要求知识检索响应 ≤500ms，而光是加载模型就远超这个数；
 *   2) 要把 14047 条真实记录建成索引，按旧架构要跑几个小时。
 *
 * 本程序把生命周期反过来：模型只加载一次，然后常驻，反复接收请求、返回向量。
 * 单次向量化的开销从「建会话+加载模型+推理」降到只剩「推理」。
 *
 * ── 通信协议（长度前缀，不是按行分隔）────────────────────────────────
 * 请求（调用方 → 本程序 stdin）：
 *     <字节数>\n<该字节数的 UTF-8 原文>
 * 响应（本程序 stdout → 调用方）：
 *     成功：<768 个 float，空格分隔>\n
 *     失败：ERR <错误码>\n      （单条失败不致命，继续服务下一条请求）
 *
 * 为什么不用「一行一条文本」的简单行协议？
 *   因为本项目要向量化的是工具执行记录，tool_output 里必然含换行符，
 *   行分隔会被内容本身撑破。长度前缀不需要任何转义，内容里有什么字节都无所谓。
 *
 * ── 启动握手 ────────────────────────────────────────────────────────
 * 模型加载完成前调用向量化会报 errCode 24 (Timeout)，这是部署手册里记录过的已知坑
 * （模型 339MB 仍在后台加载，不是故障）。本程序在启动时自己做预热重试，
 * 直到真正能出向量了，才向 stdout 打印 READY。调用方只要等到 READY，就可以放心用，
 * 不必再自己处理这个坑。
 *
 * ── 编译 ────────────────────────────────────────────────────────────
 *   gcc emb_serverd.c -o emb_serverd \
 *       -I/usr/include/kylin-ai/coreai/embedding -lkysdk-coreai-embedding
 *
 * ── 手工验证（不经 Python，直接喂一条请求）──────────────────────────
 *   printf '6\n你好\n' | ./emb_serverd
 *   （"你好" 的 UTF-8 编码是 6 字节；输出应为 READY 一行 + 768 个数一行）
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <unistd.h>
#include "embedding.h"

/* 固定使用的文本向量化模型（与 emb_test.c / emb_vec.c 保持一致，勿改：
   换模型会改变向量维度与数值，导致已建好的索引失效） */
#define MODEL_NAME "ensemble-embd_gte-base_uint8-text"

/* 单条请求文本的字节数上限。超过则拒绝该条并继续服务，避免异常长度撑爆内存。
   1MB 对工具执行记录来说绰绰有余（最长的 tool_output 也远小于此）。 */
#define MAX_TEXT_BYTES (1024 * 1024)

/* 预热重试参数：模型 339MB，冷启动加载需要数十秒。
   每次等 2 秒、最多试 60 次 = 最长等 120 秒，超时则判定为真故障并退出。 */
#define WARMUP_MAX_RETRY 60
#define WARMUP_INTERVAL_SEC 2

/*
 * 向量化一段文本并把结果写到 stdout。
 * 返回 true 表示成功输出了向量，false 表示这一条失败（已输出 ERR 行）。
 * 注意：无论成功失败都要释放 result，否则常驻进程会持续泄漏内存。
 */
static bool embed_and_print(TextEmbeddingSession *session, const char *text) {
    EmbeddingResult *result = NULL;

    bool ok = text_embedding(session, text, &result);
    if (!ok || result == NULL) {
        printf("ERR -1\n");
        fflush(stdout);
        if (result != NULL) {
            embedding_result_destroy(&result);
        }
        return false;
    }

    int err = embedding_result_get_error_code(result);
    if (err != 0) {
        /* 错误详情写 stderr（给人看），stdout 只放协议内容（给程序解析） */
        fprintf(stderr, "[emb_serverd] 向量化出错 errCode=%d msg=%s\n",
                err, embedding_result_get_error_message(result));
        printf("ERR %d\n", err);
        fflush(stdout);
        embedding_result_destroy(&result);
        return false;
    }

    /* 独立评审R-10：%f 默认只输出6位小数（约5~6位有效数字），而下游
       vector_store.py 用 %.9g、ve_serverd.cpp 用 setprecision(9) 读写，
       注释里写的是"float往返需要9位十进制才不丢精度"——源头这里精度
       已经先丢了，下游的精心处理等于白做。改成 %.9g 对齐。 */
    int dim = embedding_result_get_vector_length(result);
    float *vec = embedding_result_get_vector_data(result);
    for (int i = 0; i < dim; i++) {
        printf("%.9g", vec[i]);
        putchar(i == dim - 1 ? '\n' : ' ');
    }
    /* 必须 flush：调用方（Python）在管道另一端同步等这一行，
       不 flush 会因为 stdio 缓冲而双方互相干等，形成死锁。 */
    fflush(stdout);

    embedding_result_destroy(&result);
    return true;
}

/*
 * 预热：反复尝试向量化一小段文本，直到成功。
 * 目的是把「模型仍在后台加载」这段时间挡在 READY 之前，让调用方拿到 READY 就能直接用。
 */
static bool warmup(TextEmbeddingSession *session) {
    for (int attempt = 1; attempt <= WARMUP_MAX_RETRY; attempt++) {
        EmbeddingResult *result = NULL;
        bool ok = text_embedding(session, "预热", &result);

        if (ok && result != NULL && embedding_result_get_error_code(result) == 0) {
            embedding_result_destroy(&result);
            fprintf(stderr, "[emb_serverd] 预热成功（第 %d 次尝试）\n", attempt);
            return true;
        }

        if (result != NULL) {
            int err = embedding_result_get_error_code(result);
            fprintf(stderr, "[emb_serverd] 预热第 %d 次：errCode=%d（模型可能仍在加载，%d 秒后重试）\n",
                    attempt, err, WARMUP_INTERVAL_SEC);
            embedding_result_destroy(&result);
        } else {
            fprintf(stderr, "[emb_serverd] 预热第 %d 次：调用失败，%d 秒后重试\n",
                    attempt, WARMUP_INTERVAL_SEC);
        }

        sleep(WARMUP_INTERVAL_SEC);
    }
    return false;
}

int main(void) {
    /* ① 建会话 + 初始化（整个进程生命周期内只做一次，这正是本程序存在的意义） */
    TextEmbeddingSession *session = text_embedding_create_session();
    if (session == NULL) {
        fprintf(stderr, "[emb_serverd] 创建 session 失败\n");
        return 1;
    }

    int rc = text_embedding_init_session(session);
    if (rc != 0) {
        fprintf(stderr, "[emb_serverd] init_session 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ② 加载模型（同样只做一次） */
    rc = text_embedding_init_model(session, MODEL_NAME);
    if (rc != 0) {
        fprintf(stderr, "[emb_serverd] init_model 失败，错误码=%d\n", rc);
        return 1;
    }

    /* ③ 预热，把「模型后台加载中」这段时间挡掉 */
    if (!warmup(session)) {
        fprintf(stderr, "[emb_serverd] 预热在 %d 秒内始终未成功，判定为故障退出\n",
                WARMUP_MAX_RETRY * WARMUP_INTERVAL_SEC);
        text_embedding_destroy_session(&session);
        return 1;
    }

    /* ④ 向调用方宣告「可以开始发请求了」 */
    printf("READY\n");
    fflush(stdout);

    /* ⑤ 请求循环：读长度 → 读原文 → 出向量，直到 stdin 关闭（EOF） */
    char *buf = malloc(MAX_TEXT_BYTES + 1);
    if (buf == NULL) {
        fprintf(stderr, "[emb_serverd] 内存分配失败\n");
        text_embedding_destroy_session(&session);
        return 1;
    }

    long nbytes;
    while (scanf("%ld", &nbytes) == 1) {
        int c = getchar();          /* 吃掉长度后面的那个换行符 */
        if (c == EOF) break;

        if (nbytes < 0 || nbytes > MAX_TEXT_BYTES) {
            fprintf(stderr, "[emb_serverd] 请求长度非法或超限：%ld\n", nbytes);
            printf("ERR -2\n");
            fflush(stdout);
            /* 长度都不可信，后续字节流已无法对齐，只能断开而不是硬读下去 */
            break;
        }

        size_t got = fread(buf, 1, (size_t)nbytes, stdin);
        if (got != (size_t)nbytes) {
            fprintf(stderr, "[emb_serverd] 请求体不完整：期望 %ld 字节，实到 %zu 字节\n",
                    nbytes, got);
            break;
        }
        buf[nbytes] = '\0';         /* SDK 接口收的是 C 字符串，必须补结尾 */

        embed_and_print(session, buf);
    }

    /* ⑥ 收尾 */
    fprintf(stderr, "[emb_serverd] stdin 已关闭，正常退出\n");
    free(buf);
    text_embedding_destroy_session(&session);
    return 0;
}
