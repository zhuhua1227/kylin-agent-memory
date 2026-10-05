#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_knowledge_index.py —— 把挖掘出的知识单元建成可检索的索引

═══ 与「B2 原计划」的区别 ═══════════════════════════════════════════

原计划是「一条工具调用记录 = 一个知识单元」，14047 条切成 2~3 万块建索引。
那样做出来的是**日志**不是知识（见《多源数据整合》说明报告第十五点五节）。

本脚本索引的是 mine_cases.py / mine_workflows.py **挖掘产出**的知识单元
（cases.jsonl / workflows.jsonl），而不是原始记录本身。

═══ 输入 ═══════════════════════════════════════════════════════════

  cases.jsonl      —— mine_cases.py 产出，「历史案例」
  workflows.jsonl  —— mine_workflows.py 产出，「工作流程知识」

（模板抽象器未实现——实测当前 97 条流程里 72% 无法归入任何重复主题，
 数据规模不足以支撑这个知识类型，详见说明报告。）

═══ 用法 ═══════════════════════════════════════════════════════════

    python3 build_knowledge_index.py --backend numpy   # 本地/无麒麟环境验证用
    python3 build_knowledge_index.py --backend kylin    # 麒麟机上，走官方向量引擎

依赖：同目录 vector_store.py（双后端）；
      ~/rag/serverd/embedding_client.py（常驻向量化服务，需在麒麟机上）
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.expanduser("~/rag/serverd"))

from vector_store import create_store, DocStore, DEFAULT_DIM  # noqa: E402


# render_case/render_workflow/render_template/is_low_value_workflow 已抽到
# render_units.py（第二轮独立复核 R2-17）——此前这几个函数在本文件与
# 评测集/generate_eval_candidates.py 里各自实现一遍、靠注释互相声明"保持
# 一致"，render_case 的 error_text 截断长度（150 vs 300）实际上还是走样
# 了，靠 import 同一个实现才能真正杜绝这类问题。
from render_units import (render_case, render_workflow, render_template,  # noqa: E402,F401
                          render_conflict, is_low_value_workflow)


def load_units(exclude_conflicts=False):
    """加载三类知识单元（案例/流程/模板），统一编号（doc_id），并记录
    原始类型供回溯。模板是独立评审 K-3 之后新增的第三类，此前赛题要求的
    三类知识里只做了前两类。

    exclude_conflicts: 只建 cases/workflows/templates 三类（=
    eval_set_frozen 登记在册的语料）。用于复现冻结基准：conflict 是
    第四类、不在 REQUIRED_CORPUS 里，守卫看不见它，含它的索引与冻结
    评测集不可比。
    """
    units = []

    cases_path = os.path.join(BASE, "cases.jsonl")
    if os.path.exists(cases_path):
        with open(cases_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    c = json.loads(line)
                    units.append({"type": "case", "text": render_case(c), "raw": c})

    workflows_path = os.path.join(BASE, "workflows.jsonl")
    if os.path.exists(workflows_path):
        with open(workflows_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    w = json.loads(line)
                    units.append({"type": "workflow", "text": render_workflow(w), "raw": w})

    templates_path = os.path.join(BASE, "templates.jsonl")
    if os.path.exists(templates_path):
        with open(templates_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    t = json.loads(line)
                    units.append({"type": "template", "text": render_template(t), "raw": t})

    # 第四类：知识冲突记录（知识冲突处理-设计方案.md §五第③点，"这件事的
    # 认识变化过"本身是可检索的知识）。只取 conflicts_eval.jsonl 里
    # label=="conflict" 的行——那份文件同时含负例（append_only/same_
    # sitting/人工构造的无关对），负例是评测集素材，不是要检索到的知识，
    # 不能连它们一起入库。
    conflicts_eval_path = os.path.join(BASE, "conflicts_eval.jsonl")
    if not exclude_conflicts and os.path.exists(conflicts_eval_path):
        with open(conflicts_eval_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    k = json.loads(line)
                    if k.get("label") == "conflict":
                        units.append({"type": "conflict", "text": render_conflict(k), "raw": k})

    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["kylin", "numpy"], default="numpy")
    ap.add_argument("--collection", default="rag_knowledge")
    ap.add_argument("--exclude-conflicts", action="store_true",
                     help="只建 cases/workflows/templates 三类（= eval_set_frozen 登记在册的语料）。"
                          "用于复现冻结基准：conflict 是第四类、不在 REQUIRED_CORPUS 里，"
                          "守卫看不见它，含它的索引与冻结评测集不可比。")
    args = ap.parse_args()

    units = load_units(exclude_conflicts=args.exclude_conflicts)
    print(f"知识单元合计：{len(units)} 条 "
          f"（案例 {sum(1 for u in units if u['type']=='case')} + "
          f"流程 {sum(1 for u in units if u['type']=='workflow')} + "
          f"模板 {sum(1 for u in units if u['type']=='template')} + "
          f"冲突 {sum(1 for u in units if u['type']=='conflict')}）")

    if args.backend == "kylin":
        from embedding_client import EmbeddingClient
        emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
        client = EmbeddingClient(serverd_path=emb_path)
    else:
        # numpy 后端场景下（本地无麒麟环境验证用）需要一个可用的向量化方式。
        # 若本机就是麒麟且已起 emb_serverd，也可以用它；否则报错提示。
        try:
            from embedding_client import EmbeddingClient
            emb_path = os.path.expanduser("~/rag/serverd/emb_serverd")
            client = EmbeddingClient(serverd_path=emb_path)
        except Exception as e:
            raise SystemExit(
                "本脚本的向量化步骤依赖官方 KYSDK embedding（题目硬性要求），"
                f"需在已部署好的麒麟环境上运行。错误：{e}")

    docs = DocStore(os.path.join(BASE, "knowledge_docs.jsonl"))
    # 每次都是全量重建，不是增量续写——构造函数会把已有文件的旧内容
    # 预先加载进内存（这是 DocStore 服务"读"场景的正确行为，search_
    # knowledge.py 依赖它），但用在"全量重建"这个场景下就会留坑：
    # 挖掘出的知识单元数量一旦比上次少（比如这次因为数据层去重新增，
    # 案例+流程从152条降到95条），新的 add() 只覆盖 doc_id 0~94，
    # 旧文件里 95~151 那些条目从未被触碰，save() 会把它们跟新数据
    # 混在一起原样写出去——产出一份新旧掺杂、doc_id 与当前语料对不上的
    # 文档库（这是本次实际重建索引时撞见的真实 bug，不是假设）。
    # 全量重建前必须显式清空，不能依赖构造函数的加载结果。
    docs._docs = {}
    kwargs = {"collection": args.collection} if args.backend == "kylin" else \
             {"data_dir": os.path.join(BASE, "knowledge_store_numpy")}

    try:
        with create_store(args.backend, **kwargs) as store:
            store.create(dim=DEFAULT_DIM)

            print("向量化并写入索引...")
            for i, u in enumerate(units):
                vec = client.encode(u["text"])
                store.add([(i, vec)])
                # 第二轮独立复核 R2-21：is_low_value_workflow() 此前定义了但全项目
                # 零调用，是死代码——判据本身没问题（30/59=50.8%的流程信息量不足），
                # 只是没接进实际流程。这里在建索引时把判定结果写进 meta，
                # search_knowledge.py 检索时据此降权（不是删除，见该函数docstring：
                # 低价值流程仍是评测集需要的干扰项，删了会影响覆盖率）。
                low_value = (u["type"] == "workflow" and is_low_value_workflow(u["raw"]))
                docs.add(i, u["text"], meta={"type": u["type"], "raw": u["raw"],
                                              "low_value": low_value})
                if (i + 1) % 20 == 0 or i + 1 == len(units):
                    print(f"  {i + 1}/{len(units)}")
                    # 独立评审 R-3 指出：docs.save() 原来只在整个循环结束后调用
                    # 一次，中途异常时它不执行，但 numpy 后端的 store 在 with
                    # 块退出（哪怕因异常退出）时 close()→save() 会把已写入的
                    # 部分向量落盘——向量库和原文库一个有部分数据、一个完全
                    # 没有，doc_id 直接错位。改成每 20 条落一次盘，把"部分失败"
                    # 的最大损失从"全部原文丢失"降到"最近 20 条内"。
                    docs.save()

            docs.save()
            print(f"\n索引条数：{store.count()}")
            print(f"文档存储：{docs.path}（{len(docs)} 条）")
    finally:
        client.close()


if __name__ == "__main__":
    main()
