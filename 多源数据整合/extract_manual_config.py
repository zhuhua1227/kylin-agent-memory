#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_manual_config.py —— 提取「手动配置信息」（第三类数据源）

═══ 为什么补这一路 ═══════════════════════════════════════════════════

比赛方案题目内容 (1) 要求接入**工具执行结果、用户行为数据、手动配置信息**三类。
本脚本补上第三类：用户**亲手写下的配置**。

数据源：
  · `~/.claude/settings.json`        —— 全局配置
  · `~/.claude/settings.local.json`  —— 本地覆盖配置（含权限放行清单）

真实内容示例：
```json
// settings.json —— 显式声明的「输出风格」偏好
{"model": "opus", "theme": "dark", "skipAutoPermissionPrompt": true, "effortLevel": "high"}

// settings.local.json —— 显式声明的「安全策略」偏好
{"permissions": {"allow": ["PowerShell(Get-NetIPConfiguration *)", ...]}}
```

═══ 这一路数据最重要的用途：**当评测的答案键** ═══════════════════════

配置文件是用户**亲手写的、且早于本项目存在**的显式偏好声明。这给了偏好提取一个
**零循环论证**的交叉验证手段：

    让偏好提取器**只吃行为数据**（工具执行结果 + 用户输入历史），
    看它能否独立推导出与配置文件一致的偏好。

两边完全独立产生——配置是用户自己写的，偏好是从行为里挖的——所以一致性就是
真实的正确性证据，不需要任何人做主观判断。

这一点很关键：偏好提取的准确率如果靠"数据主人自己看着模型输出说对不对"，会被质疑
**看数据下菜碟**（先看结果再决定什么算对）。用配置文件当答案键就没有这个问题。

⚠️ 因此本脚本的产出有**双重身份**：既是数据源之一（可参与整合），
也是评测答案键之一。**做偏好提取实验时必须把它从训练输入里排除**，
否则就是拿答案当输入，实验作废。已在记录里打了 `is_answer_key: true` 标记。

═══ 为什么不把 memory/ 目录也当配置源 ═══════════════════════════════

`~/.claude/projects/*/memory/*.md` 里确实记录了用户偏好（如 `type: feedback` 的条目），
但那些文件是**模型从对话里自动总结生成的**，不是用户手写。拿它当答案键会变成
"模型给模型打分"，评委一问就穿。故明确排除，只用真正的手写配置文件。

输出：`dataset_manual_config.jsonl`
"""

import json
import os
from datetime import datetime, timezone

from anonymize import anonymize, _is_sensitive_key
from normalize_time import apply_to_record


# ⚠️ 展平操作会破坏递归脱敏的保护，必须手工补回来 —— 详见 redact_if_sensitive()


# 只收真正由用户手写的配置文件。刻意不含 memory/（理由见文件头注释）
CONFIG_SOURCES = [
    (os.path.join(os.path.expanduser("~"), ".claude", "settings.json"), "global"),
    (os.path.join(os.path.expanduser("~"), ".claude", "settings.local.json"), "local"),
]

OUTPUT = "dataset_manual_config.jsonl"


# 配置项 → 偏好类别的映射。
# 题目要求偏好覆盖「操作习惯、输出风格、安全策略」三类，这里先做一个显式归类，
# 让后续偏好提取模块有明确的对照目标，而不是笼统地"提取偏好"。
PREFERENCE_CATEGORY = {
    "model": "输出风格",
    "effortLevel": "输出风格",
    "theme": "输出风格",
    "skipAutoPermissionPrompt": "安全策略",
    "permissions": "安全策略",
}


def flatten(obj, prefix=""):
    """把嵌套的配置展平成 `点分路径 → 值` 的列表。

    展平而不是整块存的原因：偏好是**一条一条**的（"偏好用 opus 模型"是一条，
    "放行 Get-NetIPConfiguration"是另一条），展平后每条配置项独立成一条记录，
    才能和提取出的偏好逐条对照。
    """
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            items.extend(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.extend(flatten(v, f"{prefix}[{i}]"))
    else:
        items.append((prefix, obj))
    return items


def category_of(key_path: str) -> str:
    """按配置路径的首段判定偏好类别，判不出来的归「其他」。"""
    head = key_path.split(".")[0].split("[")[0]
    return PREFERENCE_CATEGORY.get(head, "其他")


def redact_if_sensitive(key_path: str, value):
    """按**原始配置路径**判断是否敏感，敏感则打码。

    ⚠️ 这个函数存在的原因是一个真实踩到的坑：

    `anonymize.py` 的第一层规则是「**字段名**含 key/token/secret/密码 等关键词
    → 整个值打码」，它靠的是 JSON 里的字段名。但本脚本把嵌套配置**展平**成了
    `{"config_path": "apiKey", "config_value": "sk-xxx"}` 这种形状——
    原来的字段名 `apiKey` 降级成了普通字符串值，而承载真实密钥的字段现在叫
    `config_value`，不含任何敏感关键词。**递归脱敏的保护就这样被展平操作拆掉了。**

    所以必须在展平的同时，拿原始路径手工过一遍同样的判定，把保护补回来。

    教训：**改变数据形状的操作（展平/转置/重命名）可能会让原有的安全规则失效**，
    因为那些规则往往依赖于特定的数据结构。做这类变换时要专门复查一遍安全假设
    还成不成立，不能默认"数据没变所以保护还在"。
    """
    # 路径可能是 "permissions.allow[0]" 这样，逐段查，任一段敏感就打码
    for seg in key_path.replace("[", ".").replace("]", "").split("."):
        if seg and _is_sensitive_key(seg):
            return "[REDACTED_CONFIG_VALUE]"
    return value


def extract():
    records = []
    for path, scope in CONFIG_SOURCES:
        if not os.path.exists(path):
            print(f"  跳过（不存在）：{path}")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  跳过（解析失败）：{path} —— {e}")
            continue

        # 配置文件没有"发生时间"，用文件修改时间代替。
        # 这是个近似：它只反映最后一次修改，不反映每一项各自何时被写下。
        # 已在报告的已知限制里注明。
        mtime = datetime.fromtimestamp(
            os.path.getmtime(path), tz=timezone.utc).isoformat()

        for key_path, value in flatten(data):
            rec = {
                "source": "claude_code_config",
                "record_type": "config_item",
                "session_id": None,          # 配置不属于任何会话
                "timestamp": mtime,
                # config_item 类型的负载
                "config_scope": scope,       # global / local
                "config_file": os.path.basename(path),
                # 字段名刻意叫 config_path 而不是 config_key：
                # 名字里带 "key" 会被 anonymize 的第一层规则误判为密钥字段，
                # 把配置项的**名字本身**给打码掉（实测踩过，产出全是 [REDACTED_FIELD]）。
                # 这与首版报告里记的 session_id 被"长随机串"规则误伤是同一类假阳性问题。
                "config_path": key_path,
                "config_value": redact_if_sensitive(key_path, value),
                "preference_category": category_of(key_path),
                # ⚠️ 答案键标记：做偏好提取实验时必须把这些记录排除出训练输入
                "is_answer_key": True,
            }
            apply_to_record(rec)
            # ⚠️ 不对整条记录调 anonymize()——这正是说明报告 §4.3 讲过的教训
            # （"整条过一遍"是危险的偷懒），这里踩的还是同一个坑的另一个变体：
            # is_answer_key 字段名含"key"，会被 anonymize() 的第一层规则整体
            # 打成 [REDACTED_FIELD]，这个标记本身就是本模块设计的核心机制
            # （下游偏好提取实验靠它排除答案键，避免拿答案当输入），一旦被
            # 打码就彻底失效，且不报错、看起来一切正常（详见《独立评审报告
            # 与整改清单.md》D-4）。真正需要脱敏的只有 config_value 这一个
            # 字段，且已经用 redact_if_sensitive() 按原始路径逐段处理过了
            # （见上面第165行），元数据字段（config_path/config_file/
            # config_scope/preference_category/is_answer_key）不含用户
            # 数据，不需要也不应该再过一遍脱敏器。
            records.append(rec)

        print(f"  已读取：{path}（{scope}）")

    return records


def main():
    print("扫描手动配置文件：")
    records = extract()

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n提取配置项：{len(records)} 条")
    print(f"输出：{OUTPUT}")

    if records:
        from collections import Counter
        cats = Counter(r["preference_category"] for r in records)
        print("\n按偏好类别分布：")
        for c, n in cats.most_common():
            print(f"  {c}: {n}")
        print("\n全部配置项（这些将作为偏好提取的交叉验证答案键）：")
        for r in records:
            print(f"  [{r['preference_category']}] {r['config_path']} = {r['config_value']}")


if __name__ == "__main__":
    main()
