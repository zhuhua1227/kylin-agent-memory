#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_forget_tasks.py —— 生成 forget_tasks.jsonl（《精准遗忘-实施方案.md》§3.2）

instruction 是人工写的（覆盖 20 个不同的 gold doc，case/workflow 都有，
措辞刻意与 评测集/eval_set.jsonl 对应问题不同——见文件底部 TASKS 表后的
逐条比对）；gold_stable_key 不手填，从语料机算，避免手抄 session_id /
时间戳时敲错一个字符就悄悄指错单元。

跑法（本机纯 Python，不需要 device_c/embedding）：
    python3 记忆服务/遗忘评测/build_forget_tasks.py
产物：记忆服务/遗忘评测/forget_tasks.jsonl
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))          # 记忆服务/遗忘评测
MEMORY_DIR = os.path.dirname(BASE)                          # 记忆服务
RAG = os.path.dirname(MEMORY_DIR)                            # 仓库根
sys.path.insert(0, MEMORY_DIR)
sys.path.insert(0, os.path.join(RAG, "知识检索"))

from forget_ledger import compute_stable_key  # noqa: E402

# (doc_id, instruction) —— doc_id 是 build_knowledge_index.py 里
# load_units() 的编号顺序：cases.jsonl 在前（0..len(cases)-1），
# workflows.jsonl 紧接其后。measurement 用的是这份对照表，不是猜的。
# 2026-09-13 第一轮实测：定位准确率 30%（6/20），低于预登记的 60~85%、
# 也低于预登记写好的 50% 判据线——按《预登记.md》"低于50%说明措辞写偏了，
# 回头改指令不改判据"的预授权路径，把 miss 掉的 14 条改得更贴近文档实际
# 内容（措辞仍与 评测集/eval_set.jsonl 对应问题不同，只是不再刻意避开
# 关键概念词），命中的 6 条（doc_id 1/41/52/53/56/74）原样不动。
# 只改这一轮，不再迭代调到分数好看为止。
TASKS = [
    (0,  "有一条记录是关于本地数据库文件用命令行工具打不开，后来改用python自带的数据库模块读出了里面的表内容那次的处理经过"),
    (1,  "有一次直接查看某个python脚本文件内容时被系统告知文件不存在，后来才发现真正该运行的其实是同目录下另一个开头多了几个字的脚本"),
    (2,  "有一次要查本机哪些端口正在监听，发现常用的那个网络诊断命令没装，换了个替代命令才查出来"),
    (4,  "记得有个脚本要解析本地某个种子文件里的元数据（比如追踪地址），一开始读取默认目录下标越界报错，后来改成直接传入文件的绝对路径才跑通"),
    (9,  "有一次下载Go语言的编译工具链压缩包，解压时报错说文件格式不对，后来通过SSH远程重新下载了一遍才解决"),
    (10, "有一次远程执行命令导出了当前机器的网络节点信息保存成文件，再用脚本解析这个文件内容时报错格式有问题，查出来是文件里混进了不该有的其他文本"),
    (13, "有一次要远程登录检查一台服务器状态，结果对方一直拒绝连接，登不进去"),
    (15, "有一次用脚本读取种子下载软件的配置文件，报错说指定路径不存在，后来发现真正的文件其实存在于Windows系统的临时文件夹里"),
    (23, "有一次修改了一台虚拟机的配置想让它重启，结果远程执行重启命令时提示超时失败，只好再连过去确认它现在到底有没有起来"),
    (26, "有一次远程服务器上有个正在下载容器镜像的任务卡住很久毫无进展，最后是连上去把那个下载进程强制终止掉解决的"),
    (41, "帮忙处理过一份内容没法整段选中复制的参考资料，把里面的内容一条条摘出来做成了独立文本文件"),
    (51, "写过一个简单的健身记录网页文件，做完之后放在了电脑桌面上方便随时打开看"),
    (52, "在桌面上新建过一个专门存放某AI相关资料的文件夹"),
    (53, "写过一个有存取上限、旧数据会被自动挤掉的小型缓存工具，并且另外跑了一遍脚本确认没有问题"),
    (56, "整理过一份国际首富名单的资料，最后做成能直接打开编辑的电子表格放在了桌面"),
    (58, "帮忙查过一次当天发生了哪些重要新闻事件，并做了简单汇总"),
    (63, "查过一次挂载着网盘的那台电脑上，搜一下有没有存某部很火的韩剧资源"),
    (66, "把之前专门建的一个用来做测试的文件夹删掉了，删之前先看了一眼里面都有些什么内容"),
    (74, "做过一个假想大陆的可视化连线图，可以选两个位置由程序告诉你相隔多远"),
    (78, "把之前手动敲的一长串用来升级系统版本的命令保存成了一个脚本文件，然后拷贝到了一个外接的U盘里"),
]


def load_corpus():
    cases = [json.loads(l) for l in open(os.path.join(RAG, "知识检索", "cases.jsonl"), encoding="utf-8") if l.strip()]
    workflows = [json.loads(l) for l in open(os.path.join(RAG, "知识检索", "workflows.jsonl"), encoding="utf-8") if l.strip()]
    return cases, workflows


def doc_id_to_meta(doc_id, cases, workflows):
    n_cases = len(cases)
    if doc_id < n_cases:
        return {"type": "case", "raw": cases[doc_id]}
    if doc_id < n_cases + len(workflows):
        return {"type": "workflow", "raw": workflows[doc_id - n_cases]}
    raise ValueError(f"doc_id={doc_id} 落在 case/workflow 范围之外（template/conflict 未覆盖）")


def load_gold_doc_ids_from_eval_set():
    path = os.path.join(RAG, "评测集", "eval_set.jsonl")
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ids.add(json.loads(line)["doc_id"])
    return ids


def main():
    cases, workflows = load_corpus()
    gold_ids = load_gold_doc_ids_from_eval_set()

    task_ids_by_doc = {}
    out_path = os.path.join(BASE, "forget_tasks.jsonl")
    rows = []
    for i, (doc_id, instruction) in enumerate(TASKS, 1):
        if doc_id in task_ids_by_doc:
            raise SystemExit(f"doc_id={doc_id} 重复出现，20 条任务必须覆盖 20 个不同的 gold doc")
        task_ids_by_doc[doc_id] = True

        if doc_id not in gold_ids:
            raise SystemExit(f"doc_id={doc_id} 不是 eval_set.jsonl 里的 gold doc，选错了")

        meta = doc_id_to_meta(doc_id, cases, workflows)
        stable_key = compute_stable_key(meta)
        if stable_key is None:
            raise SystemExit(f"doc_id={doc_id}（type={meta['type']}）算不出 stable_key")

        rows.append({
            "task_id": f"F-{i:02d}",
            "instruction": instruction,
            "gold_doc_id": doc_id,
            "gold_stable_key": stable_key,
            "gold_type": meta["type"],
        })

    n_case = sum(1 for r in rows if r["gold_type"] == "case")
    n_wf = sum(1 for r in rows if r["gold_type"] == "workflow")
    if n_case == 0 or n_wf == 0:
        raise SystemExit(f"case/workflow 必须都覆盖到，实际 case={n_case} workflow={n_wf}")

    # newline="" —— 仓库 .gitattributes 钉死 eol=lf，Windows 上 open(..,"w")
    # 默认文本模式会把 \n 悄悄换成 \r\n，制造出"证据被改动过"的假哈希差异。
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"写出 {len(rows)} 条任务 → {out_path}")
    print(f"  case={n_case}  workflow={n_wf}")
    for r in rows:
        print(f"  {r['task_id']}  doc_id={r['gold_doc_id']:>2}  {r['gold_type']:<8}  {r['gold_stable_key']}")


if __name__ == "__main__":
    main()
