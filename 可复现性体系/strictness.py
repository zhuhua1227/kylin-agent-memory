#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strictness.py —— 「缺失即失败」的统一入口（第四轮独立复核 R4-14）

═══ 这在治什么病 ═══════════════════════════════════════════════════

本项目四轮复核里出现频率最高的一类缺陷，形状永远是这一句：

    if os.path.exists(某个输入):
        ...做一次核对...
    # 没有 else

输入不在 → 核对不做 → **一声不吭继续往下走、退出码 0**。于是"这条检查
通过了"和"这条检查压根没跑"在输出上长得一模一样。第一轮的诊断
（"验证器只做阳性验证"）、第二轮的 P-1（删掉记录仍报全绿）、第三轮的
R3-32、第四轮的 R4-14，说的都是它。

第四轮实测的存量：`可复现性体系/` 内 `if os.path.exists` **32 处**、
`or {}` / `.get() or` **53 处**、`return None` **22 处**。复核给的判语是
"未做类级收口"——之前每一轮都只改被点名的那几处。

═══ 为什么是一个模块，而不是"以后大家都记得写 else" ═════════════════

因为"记得"这件事本身就是四轮没做到的那件事。把判断收进一个函数，
至少让"我这里为什么可以缺失"变成一个**必须显式填写的参数**，
而不是一个可以忘记写的 else 分支。

═══ 三种合法姿势，别的都不合法 ═════════════════════════════════════

| 函数 | 语义 | 缺失时 |
|---|---|---|
| `require()` | 这个输入必须在，缺了这条检查就是瞎的 | **抛异常**，测量记录判失败 |
| `optional()` | 缺失是正常场景（第三方没拿到某个子目录），但必须**说出来** | 打印 + 记账，调用方拿到 None 自行降级 |
| `assert_equal()` | 两个本应相等的值 | 不等则**抛异常**，不是 print、不是 note |

`optional()` 强制要求填 `why`（什么情况下缺失是正常的）与 `impact`
（缺了之后哪条结论不再成立）。填不出来，说明它其实是 `require()`。

═══ 已知未收口的范围（如实声明，不假装做完了） ═══════════════════════

本模块目前只被下列位置使用（都是"缺了会让某条核对静默失效"的要害）：
`measure_L1.py` 的 M-111 四处、`measure.py` 的 inputs 缺失、
`measure_L2.py` 的记录读取。剩下的几十处 `or {}` / `return None` 多数是
普通的数据访问兜底，不承担校验职责，**没有逐个改**——把它们全改成抛异常
只会制造噪声。这个边界写在这里，是为了下一轮复核不必再猜"到底收口了没有"。
"""

import os
import sys

# 本次运行里所有"允许缺失"的记账，供调用方在结束时统一披露
_ABSENCES = []


class MissingInput(RuntimeError):
    """必需输入缺失。故意用异常而不是返回值——返回值会被忽略，异常不会。"""


def require(path, why, hint=None):
    """这个输入必须存在。缺了就抛异常，让上层的测量记录判失败。

    `why` 写清"没有它，哪条核对会变成瞎的"——不是写"需要这个文件"。
    """
    if os.path.exists(path):
        return path
    msg = [f"必需输入缺失：{path}", f"  没有它，{why}——这条检查会变成瞎的，"
           f"所以本次运行判失败，而不是跳过后照常报绿。"]
    if hint:
        msg.append(f"  {hint}")
    raise MissingInput("\n".join(msg))


def optional(path, why, impact, record=None):
    """缺失是正常场景，但必须说出来并记账。返回 path 或 None。

    `why`    —— 什么情况下缺失是正常的（例：第三方只拿到 可复现性体系/）
    `impact` —— 缺了之后**哪条结论不再成立**
    `record` —— 传入 Measurement 实例时，同时写进记录的 note，
                让读记录的人也看得到"这次少验了什么"
    """
    if os.path.exists(path):
        return path
    line = (f"⚠️ 可选输入缺失：{os.path.basename(path)}\n"
            f"   缺失属正常场景：{why}\n"
            f"   本次因此未验证：{impact}")
    print(line, file=sys.stderr)
    _ABSENCES.append({"path": path, "why": why, "impact": impact})
    if record is not None and hasattr(record, "note"):
        record.note(f"[未覆盖] {os.path.basename(path)} 不存在，本次未验证：{impact}")
    return None


def assert_equal(actual, expected, what, tolerance=0.0, record=None):
    """两个本应相等的值。不等就抛异常。

    ⚠️ 这个函数存在的唯一理由，是 R4-04 那处教训：`measure_L1.py` 的
    docstring 白纸黑字写着"对不上直接判失败"，实际只调了 `m.note()` 加
    `print()`——不抛异常、不改退出码、记录照常 `succeeded=True` 落盘。
    第四轮实测把语料读取搞坏之后，L1=0.4911 / L2=0.4852 明明对不上，
    整个流程**依然一路绿灯退出 0**。
    **比原来的问题更坏的是**：原来是注释在吹牛，现在是"已修复"在吹牛。
    """
    ok = (abs(actual - expected) <= tolerance
          if isinstance(actual, (int, float)) and isinstance(expected, (int, float))
          else actual == expected)
    if ok:
        return True
    detail = f"{what}：实测 {actual!r}，应为 {expected!r}"
    if record is not None and hasattr(record, "note"):
        record.note(f"[断言失败] {detail}")
    raise AssertionError(
        f"{detail}\n"
        f"  这不是可以只记一条 note 就放过去的情况——两个值本应相等，"
        f"不等就说明前提被破坏了。")


def absences():
    """本次运行里所有被允许的缺失。供收尾时统一披露。"""
    return list(_ABSENCES)


def reset():
    """测试用：清空记账。"""
    _ABSENCES.clear()
