#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_detectors.py —— 检验"检验器"本身：故意制造已知缺陷，确认对应的验证器
一定会报错。

═══ 为什么需要这个文件 ═══════════════════════════════════════════════

独立评审指出本项目最重要的一条系统性问题：**所有验证器都只做过阳性验证，
从未做过阴性验证**。

| 验证器 | 它的表现 | 实际情况 |
|---|---|---|
| M-103 脱敏测试 | 7/7 全绿 | 交付数据集里曾有真 API key（已修复） |
| C-212 确定性回归测试（旧版） | 通过 | 修复前也会通过——测试恒真 |
| merge_and_validate.py 质检器（旧版） | 拒绝数恒为 0 | 放行了 33.1% 重复 + 3.4% 编码损坏 |
| reproduce_L1.py 核对器（旧版） | 全绿，退出码0 | 删掉任何一条记录，结论依然是绿的 |

**每一个验证器都能证明"通过时确实通过了"，没有一个证明过"该失败时真的
会失败"。** 一个永不报错的检验器，和没有检验器，信息量是相同的。

═══ 设计原则 ═══════════════════════════════════════════════════════

每个 case 都是「注入一个本项目真实犯过的错误 → 断言对应的检测器必须失败」。
不是测功能是否正确，是测检测能力本身——这是"我们的验证器被验证过"，
比"我们验证过"更进一步。

用法：
    python test_detectors.py
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(RAG, "多源数据整合"))
sys.path.insert(0, os.path.join(RAG, "知识检索"))

RESULTS = []

# ═══ 🔴 第四轮独立复核 R4-15：申报总数必须恒定，不能随环境浮动 ═════════
#
# 实测：无 `.git` 时打印「合计 93 项」、缺一个记录时「合计 94 项」、正常
# 环境「合计 96 项」——同一份套件在三台机器上自称三个规模，而 `.md` 文档里
# 写死的「test_detectors 96/96 通过」和 verify_reports.py 的自我描述核对
# 都拿这个数当锚。**更糟的是浮动方向：环境越残缺，分母越小、越容易全绿。**
#
# 修法两条，缺一不可：
#   ① 所有早退路径改成 `unavailable()` 显式记一行 FAIL，而不是 `return`
#      把后面几条检查整个抹掉——「这条检查此刻没在检查任何东西」本身就是
#      必须报红的信息，不该表现为「总数少了几项」。
#   ② 把总数写成下面这个常量，main() 末尾拿实际记录数与它比对。条目增减
#      时这条会当场红，逼人同步改常量与文档，而不是让文档里的数字慢慢漂。
#
# ⚠️ 改了用例数量就要改这个常量——它红了不是 bug，是提醒你去同步 `.md`
#    里那句自我描述（verify_reports.py 会核对两边）。
#
# ⚠️ 这个常量此前**只是个常量**：定义在这里，`main()` 从没读过它，于是①有
#    定义没执行，浮动照旧。第四轮把比对补进 `main()` 末尾（自己也算一条检查
#    项），常量才真正开始起作用。117 = 本轮实测的记录条数（不含它自己那条）。
#
# +7 = test_cmd_drift_negatives()（S1 命令输出漂移检测器，145→152）。
# +8 = test_retrieval_gold_type_leak_detector()（消融修复-执行方案.md §C，
#      第19项：1条覆盖面自检 + 6个非oracle配置各1条阴性检查 + 1条阳性对照，
#      156→164）。
DECLARED_CHECK_COUNT = 164


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))


def unavailable(name, why):
    """这条检查本次**没能真正执行**——记一行 FAIL，不是悄悄少一行。

    R4-15 的核心：缺席必须显式、且计入总数。缺席即跳过会让分母缩水，
    环境越残缺越容易「全绿」，正是本项目反复被点名的那类假绿。
    """
    RESULTS.append((name, False, f"未执行：{why}"))
    print(f"  [FAIL] {name}  (未执行：{why})")


# ═════════════════════════════════════════════════════ 1. 脱敏检测能力

def test_anonymize():
    print("\n1. 脱敏检测能力（anonymize.py 必须打码这些已知真实漏检模式）")
    from anonymize import anonymize

    cases = [
        ("新式带连字符API密钥",
         "密钥sk-xxxx-yyyyZZZZ1234",
         lambda out: "REDACTED" in out and "sk-xxxx-yyyyZZZZ1234" not in out),
        ("裸词sshpass",
         "sshpass -p PLAINPW1234 ssh a@b",
         lambda out: "PLAINPW1234" not in out),
        ("MAC地址",
         "link/ether bc:24:11:3d:d1:3e brd ff:ff:ff:ff:ff:ff",
         lambda out: "bc:24:11:3d:d1:3e" not in out),
        ("chpasswd整行",
         "echo 'user:realpassword123' | chpasswd",
         lambda out: "realpassword123" not in out),
        ("下划线拼接的password字段(文档式写法)",
         'cfg["downloader"]["password"] = "SynthPw2026!"',
         lambda out: "SynthPw2026!" not in out),
        ("转义JSON里的web_ui_password",
         'web_ui_password\\": \\"FakeSecret9!2026\\"',
         lambda out: "FakeSecret9!2026" not in out),
        # R2-08：漏检类
        ("AWS Access Key",
         "AKIAABCDEFGHIJKLMNOP",
         lambda out: "AKIAABCDEFGHIJKLMNOP" not in out),
        ("Slack token",
         "xoxb-1234567890-abcdefghijk",
         lambda out: "abcdefghijk" not in out),
        ("GitHub PAT",
         "ghp_" + "a" * 36,
         lambda out: ("ghp_" + "a" * 36) not in out),
        ("-p紧贴密码无空格(mysql)",
         "mysql -pSecretPw99 -e select1",
         lambda out: "SecretPw99" not in out),
        ("-u user:pass形式(curl)",
         "curl -u admin:SuperSecret1 http://x",
         lambda out: "SuperSecret1" not in out),
        ("task-前缀不应被sk-规则误伤",
         "task-execution-record-2026-08-05-final",
         lambda out: out == "task-execution-record-2026-08-05-final"),
    ]
    for name, payload, assertion in cases:
        out = anonymize(payload)
        check(f"脱敏-{name}", assertion(out), f"input={payload[:40]!r}")

    # R2-08：误伤类（不该打码的必须原样保留）
    exempt_cases = [
        ("64位SHA256（锚定证据链依赖，绝不能误伤）",
         "文件sha256为2e198716fa14b587e5832c0eb94a4dca259ec78a8f6d9f2c44e352d5087a21f0"),
        ("40位git commit hash",
         "commit hash a638753480da623c3c46631d8494e8c224ccb080"),
        ("技术包名(多段小写连字符)",
         "kylin-ai-vector-engine-client-devel"),
        ("关键词后接普通英文散文(纯空白分隔、值不含数字)",
         "password policy document"),
    ]
    for name, payload in exempt_cases:
        out = anonymize(payload)
        check(f"脱敏-不应误伤-{name}", out == payload, f"input={payload!r} output={out!r}")

    # ── 🔴 R3-00 脱敏幂等性（第四轮 R4-01 暴露出来的「零检测器保护」）──────
    #
    # 这条修复在 mutate.py 的 MUTATIONS 清单里登记了，但**改回缺陷版之后
    # 整套 test_detectors 一条都不红**——第四轮复核把 .git 放回沙箱、改成
    # 差分判据后当场显形。即：R3-00 是一条真实存在、却没有任何检测器守着的
    # 修复，随时可能在下一次重构里悄悄退化，而且退化的表现是**数据集里的
    # 真实内容被一截一截吃掉**，行数不变、肉眼几乎看不出来。
    #
    # 缺陷长什么样：规则链对自己产出的打码标记不免疫。关键词上下文规则的
    # 关键词表里有 secret/token/密码，而标记本身就叫 [REDACTED_SECRET] /
    # [REDACTED_LONG_TOKEN]——标记里的 SECRET/TOKEN 命中关键词，紧跟其后的
    # 真实内容被当成「密钥值」吞掉。修法是按标记切段、逐段脱敏后原样拼回。
    #
    # ★ 为什么用例要喂**已经打过码的文本**，而不是「同一段原文跑两遍」：
    #   实测（本轮亲自跑过）单趟内部规则是顺序执行的，原文那一趟里长串
    #   规则先把值换成标记、关键词规则随后就在同一趟里把后面吃掉了——
    #   两遍跑出来的结果因此**在缺陷版下也相同**，那种写法是恒真的。
    #   真实场景恰恰是「数据集里存的已经是打过码的文本，rescrub 再跑一遍」，
    #   所以判据必须是：**已打码文本再脱敏一次，必须逐字不变**。
    #   下面三条负载在缺陷版下实测分别被吃成
    #   `[REDACTED_SECRET[REDACTED_SECRET]` 之类，用例会红。
    already_redacted = [
        ("标记后紧跟主机名",
         "[REDACTED_SECRET]@node2026gpu01.lan:5432/db"),
        ("长串标记后紧跟路径",
         "[REDACTED_LONG_TOKEN]/backup2026data/latest.tar"),
        ("JSON 正文里标记后紧跟路径与后续文字",
         '{"tool_output": "scp [REDACTED_SECRET]/srv2026share/deploy.sh done"}'),
        ("标记后紧跟中文正文",
         "密码[REDACTED_SECRET] 后面这段中文正文必须原样保留"),
    ]
    for name, payload in already_redacted:
        out = anonymize(payload)
        check(f"脱敏-幂等性-已打码文本再脱敏必须逐字不变-{name}",
              out == payload, f"output={out!r}")

    # 再加一条端到端的幂等断言：anonymize(anonymize(x)) == anonymize(x)。
    # 这条对上面那个缺陷不敏感（原因见上），但它守的是另一件事——将来
    # 新增规则时把「跑两遍会变」这类不稳定行为挡在门外，成本几乎为零。
    unstable = []
    for _n, payload, _a in cases:
        once = anonymize(payload)
        if anonymize(once) != once:
            unstable.append(payload[:40])
    check("脱敏-幂等性-上述全部负载连续两次脱敏结果逐字相同",
          not unstable, f"两遍不一致：{unstable}" if unstable else "")


# ═════════════════════════════════════════════════════ 2. 数据质检能力

def test_merge_validate():
    print("\n2. 数据质检能力（merge_and_validate.py 的检测函数必须识别这些噪声）")
    from merge_and_validate import classify_execution_status, dedup_key, has_encoding_damage

    check("质检-用户拒绝识别为user_rejected不是execution_failed",
          classify_execution_status({"tool_output": "The user doesn't want to "
                                     "proceed with this tool use. was rejected",
                                     "is_error": True}) == "user_rejected")

    check("质检-真实执行失败识别为execution_failed",
          classify_execution_status({"tool_output": "command not found",
                                     "is_error": True}) == "execution_failed")

    check("质检-U+FFFD编码损坏检测",
          has_encoding_damage({"tool_output": "cannot access ���"}))

    check("质检-正常文本不误判编码损坏",
          not has_encoding_damage({"tool_output": "cannot access /root/normal/path"}))

    k1 = dedup_key({"session_id": "s1", "tool_name": "Bash",
                    "tool_input": {"command": "ls"}, "tool_output": "a.txt"})
    k2 = dedup_key({"session_id": "s1", "tool_name": "Bash",
                    "tool_input": {"command": "ls"}, "tool_output": "a.txt"})
    k3 = dedup_key({"session_id": "s1", "tool_name": "Bash",
                    "tool_input": {"command": "pwd"}, "tool_output": "a.txt"})
    check("质检-去重签名对相同四元组给出相同key", k1 == k2)
    check("质检-去重签名对不同tool_input给出不同key", k1 != k3)

    # D-5：schema.json的类型/枚举校验必须真的能拦住类型错误的记录，
    # 不是摆设（此前只查字段存在/非空，"is_error": "true"这种字符串
    # 冒充布尔值的记录不会被拦下）
    from merge_and_validate import validate_record
    base = {"source": "x", "record_type": "tool_execution", "timestamp": "t",
            "session_id": "s", "tool_name": "n", "tool_input": {}, "tool_output": "o"}
    ok, _ = validate_record({**base, "is_error": "true"})
    check("质检-schema类型校验拦住字符串冒充bool的is_error", not ok)
    ok, _ = validate_record({**base, "execution_status": "maybe"})
    check("质检-schema枚举校验拦住execution_status越界值", not ok)
    ok, _ = validate_record({**base, "is_error": False})
    check("质检-schema校验放行类型正确的正常记录", ok)

    # R2-06：输入新鲜度检查——改完提取脚本没重跑合并，输出比输入还旧，
    # 交付的数据集与报告数字对不上。这类错误不报错不崩溃，只有把两边
    # mtime放一起看才发现，必须有检测器守着，不能靠人记得。
    from merge_and_validate import assert_inputs_not_newer_than_output
    with tempfile.TemporaryDirectory() as tmpdir:
        old_input = os.path.join(tmpdir, "input.jsonl")
        output = os.path.join(tmpdir, "output.jsonl")
        with open(old_input, "w", encoding="utf-8") as f:
            f.write("{}")
        with open(output, "w", encoding="utf-8") as f:
            f.write("{}")
        os.utime(old_input, (1000000000, 1000000000))  # 输入设为很旧的时间
        try:
            assert_inputs_not_newer_than_output([old_input], output)
            check("R2-06-输入不比输出新时不应报错", True)
        except SystemExit:
            check("R2-06-输入不比输出新时不应报错", False)

        # 输入改成明确比 output 晚（output 的 mtime + 5 秒，不依赖"现在"
        # 一定比刚写完的 output 晚——文件系统时间戳精度粗、或两步操作落在
        # 同一个时钟粒度内时，"现在"未必真的更新，会让这条用例偶发性失败）
        out_mtime = os.path.getmtime(output)
        os.utime(old_input, (out_mtime + 5, out_mtime + 5))
        raised = False
        try:
            assert_inputs_not_newer_than_output([old_input], output)
        except SystemExit:
            raised = True
        check("R2-06-输入比输出新时必须拒绝并报错", raised)

    # R2-12：rescrub_strings.py 规则指纹机制——覆盖多文件、规则不变时跳过、
    # 规则变了强制重跑，三点都要验证到，不能只测"能跑"
    sys.path.insert(0, os.path.join(RAG, "多源数据整合"))
    import rescrub_strings
    with tempfile.TemporaryDirectory() as tmpdir:
        ds_dir = os.path.join(tmpdir, "dataset")
        os.makedirs(ds_dir)
        f1 = os.path.join(ds_dir, "memory_source_records.jsonl")
        f2 = os.path.join(tmpdir, "dataset_extra.jsonl")
        with open(f1, "w", encoding="utf-8") as f:
            f.write('{"tool_output": "\\u5bc6\\u7801: SecretPw123456"}\n')
        with open(f2, "w", encoding="utf-8") as f:
            f.write('{"tool_output": "AKIAABCDEFGHIJKLMNOP"}\n')

        files = rescrub_strings._dataset_files(dataset_dir=ds_dir, base_dir=tmpdir)
        covers_both = (
            any(os.path.basename(p) == "memory_source_records.jsonl" for p in files)
            and any(os.path.basename(p) == "dataset_extra.jsonl" for p in files))
        check("R2-12-遍历覆盖dataset/子目录与根目录dataset_*.jsonl两类文件",
              covers_both, f"files={[os.path.basename(p) for p in files]}")

        fp = rescrub_strings._rules_fingerprint()
        r1 = rescrub_strings.process_file(f2, fp, force=False)
        check("R2-12-首次处理应实际执行(未跳过)", r1["skipped"] is False)
        r2 = rescrub_strings.process_file(f2, fp, force=False)
        check("R2-12-指纹未变时第二次应跳过", r2["skipped"] is True)
        r3 = rescrub_strings.process_file(f2, "different-fingerprint-value", force=False)
        check("R2-12-指纹变化后应重新处理(不跳过)", r3["skipped"] is False)


# ═════════════════════════════════════════════════════ 3. mine_cases.py 的漏检修复（K-1）

def test_mine_cases_k1():
    print("\n3. 案例挖掘 K-1 修复（mine_cases.py 必须拦住这两类已知假案例）")
    import mine_cases

    # 修复命令与失败命令完全相同——独立评审 K-1 根因一
    recs_identical = [
        {"record_type": "tool_execution", "session_id": "t1",
         "timestamp": "2026-01-01T00:00:00Z", "tool_name": "Bash",
         "tool_input": {"command": "cat missing.txt"},
         "tool_output": "cat: missing.txt: No such file or directory",
         "is_error": True},
        {"record_type": "tool_execution", "session_id": "t1",
         "timestamp": "2026-01-01T00:00:01Z", "tool_name": "Bash",
         "tool_input": {"command": "cat missing.txt"},
         "tool_output": "hello world", "is_error": False},
    ]
    cases, stats = mine_cases.mine(recs_identical)
    check("K-1-命令完全相同不产出案例",
          len(cases) == 0 and stats["rejected_by_identical_cmd"] >= 1,
          f"cases={len(cases)} rejected={stats['rejected_by_identical_cmd']}")

    # 探查动词被当成修复——独立评审 K-1 根因二的典型样例
    recs_probe = [
        {"record_type": "tool_execution", "session_id": "t2",
         "timestamp": "2026-01-01T00:00:00Z", "tool_name": "exec",
         "tool_input": {"command": "mysql -u root -e 'SELECT 1'"},
         "tool_output": "bash: mysql: command not found", "is_error": True},
        {"record_type": "tool_execution", "session_id": "t2",
         "timestamp": "2026-01-01T00:00:01Z", "tool_name": "exec",
         "tool_input": {"command": "ping -n 4 127.0.0.1"},
         "tool_output": "4 packets transmitted", "is_error": False},
    ]
    cases, stats = mine_cases.mine(recs_probe)
    check("K-1-探查动词不产出案例",
          len(cases) == 0 and stats["rejected_by_probe_only"] >= 1,
          f"cases={len(cases)} rejected={stats['rejected_by_probe_only']}")

    # 第二轮独立复核 R2-01：探查动词被 plink/sudo 包装后应仍能识别——旧实现
    # 只查 fixed_cmd.split()[0]，包装后首词恒为 plink，这条用例改回旧逻辑
    # 应该会失败（本次先确认新逻辑能拦住）
    recs_wrapped_probe = [
        {"record_type": "tool_execution", "session_id": "t3",
         "timestamp": "2026-01-01T00:00:00Z", "tool_name": "Bash",
         "tool_input": {"command": 'plink -ssh host "docker start web1"'},
         "tool_output": "bash: docker: command not found", "is_error": True},
        {"record_type": "tool_execution", "session_id": "t3",
         "timestamp": "2026-01-01T00:00:01Z", "tool_name": "Bash",
         "tool_input": {"command": 'plink -ssh host "echo pw | sudo -S ls -la /mnt/data"'},
         "tool_output": "total 0", "is_error": False},
    ]
    cases, stats = mine_cases.mine(recs_wrapped_probe)
    check("R2-01-包装命令内层探查动词也能被拦住",
          len(cases) == 0 and stats["rejected_by_probe_only"] >= 1,
          f"cases={len(cases)} rejected={stats['rejected_by_probe_only']}")

    # pscp 报非零退出码但输出显示 100% 传输已完成——独立评审 R2-01 指出的
    # 假案例：真正的"失败"前提不成立，不该进入候选池
    recs_pscp_false_fail = [
        {"record_type": "tool_execution", "session_id": "t4",
         "timestamp": "2026-01-01T00:00:00Z", "tool_name": "Bash",
         "tool_input": {"command": '"D:\\putty\\pscp.exe" -pw x -batch deploy.sh a@host:/tmp/'},
         "tool_output": "Exit code 1\ndeploy.sh | 1 kB | 1.2 kB/s | ETA: 00:00:00 | 100%\n"
                        "bash: some-later-step: command not found",
         "is_error": True},
        {"record_type": "tool_execution", "session_id": "t4",
         "timestamp": "2026-01-01T00:00:01Z", "tool_name": "Bash",
         "tool_input": {"command": '"D:\\putty\\plink.exe" -pw x a@host "bash /tmp/deploy.sh"'},
         "tool_output": "deployed ok", "is_error": False},
    ]
    cases, stats = mine_cases.mine(recs_pscp_false_fail)
    check("R2-01-pscp虚假失败(100%已传完)不产出案例",
          len(cases) == 0 and stats.get("rejected_by_spurious_pscp", 0) >= 1,
          f"cases={len(cases)} rejected={stats.get('rejected_by_spurious_pscp', 0)}")


def test_mine_templates_r2_22():
    print("\n3.5 模板挖掘 R2-22 修复（mine_templates.py 三个 bug）")
    import mine_templates

    # bug②：str==list 恒假比较——失败命令与修复命令剥壳后其实是同一个
    # 命令（都是 ls），旧实现的守卫从未生效，会把"没换工具"误判成"换了
    # 工具"产出伪模板。两个不同会话都是这个模式，凑够 MIN_SUPPORT=2。
    same_tool_cases = [
        {"tool_name": "Bash", "session_id": "s1", "error_text": "command not found",
         "failed_input": {"command": 'plink -ssh h "ls /a"'},
         "fixed_input": {"command": 'plink -ssh h "ls -la /a"'}},
        {"tool_name": "Bash", "session_id": "s2", "error_text": "command not found",
         "failed_input": {"command": 'plink -ssh h "ls /b"'},
         "fixed_input": {"command": 'plink -ssh h "ls -la /b"'}},
    ]
    templates = mine_templates.mine_case_templates(same_tool_cases)
    check("R2-22-bug②修复命令与失败命令内层动词相同不产出切换模板",
          len(templates) == 0, f"templates={len(templates)}")

    # bug③：会话数与出现次数混淆——同一个会话里出现 2 次不该算"跨会话"，
    # 门槛应挡住这种情况
    single_session_cases = [
        {"tool_name": "Bash", "session_id": "only-one-session",
         "error_text": "command not found",
         "failed_input": {"command": "cat x.txt"},
         "fixed_input": {"command": "python3 read.py"}},
        {"tool_name": "Bash", "session_id": "only-one-session",
         "error_text": "command not found",
         "failed_input": {"command": "cat y.txt"},
         "fixed_input": {"command": "python3 read2.py"}},
    ]
    templates = mine_templates.mine_case_templates(single_session_cases)
    check("R2-22-bug③单会话内重复不产出跨会话模板",
          len(templates) == 0, f"templates={len(templates)}")

    # 正例：两个不同会话、内层动词确实不同，应该产出模板——确认修复
    # 没有矫枉过正到"永远不产出"
    genuine_switch_cases = [
        {"tool_name": "Bash", "session_id": "gs1", "error_text": "command not found",
         "failed_input": {"command": "mysql -e 'select 1'"},
         "fixed_input": {"command": "sqlite3 db.sqlite 'select 1'"}},
        {"tool_name": "Bash", "session_id": "gs2", "error_text": "command not found",
         "failed_input": {"command": "mysql -e 'select 2'"},
         "fixed_input": {"command": "sqlite3 db2.sqlite 'select 2'"}},
    ]
    templates = mine_templates.mine_case_templates(genuine_switch_cases)
    check("R2-22-真实跨会话工具切换仍能正常产出模板(未矫枉过正)",
          len(templates) == 1, f"templates={len(templates)}")


# ═════════════════════════════════════════════════════ 4. mine_workflows.py 确定性（跨进程）

def cross_process_determinism_md5s(script_path, output_filename, seeds=("1", "2", "3"),
                                    extra_env=None):
    """起独立子进程、换不同 PYTHONHASHSEED，重跑同一个脚本，对比产出文件的
    md5——这是本文件里唯一真正验证"跨进程确定性"的实现。

    ★第二轮独立复核 R2-04 指出：`measure_B2.py` 的 M-109 此前自己另外
    写了一份"确定性回归"，但只在同一个进程里连续调用 mine() 3 次——那
    测的是"同一进程内反复调用是否稳定"，测不出 PYTHONHASHSEED 不同导致
    的 set 遍历顺序问题（独立评审 P-4 点名的那类 bug）。与其让 M-109
    另外维护一份弱化版实现，不如让它直接调用这里——本函数因此被设计成
    可复用的，不只是 test_detectors.py 内部用。

    ★R2-10 指出并修复的两个问题：
    1. **必须检查子进程 returncode**——不检查的话，子进程全部崩溃时会
       读到同一份陈旧的输出文件，三次 md5 照样相同，被误判为"确定性
       通过"，掩盖真实的运行失败。复核员的元测试实测复现过这个假通过
       通道：故意让脚本立即 `sys.exit("BOOM")`，旧实现依然判 PASS。
       现在 returncode != 0 直接抛异常，不会被悄悄放行。
    2. **不能在脚本原目录里跑，会反复覆写交付文件**——`mine_workflows.py`
       之类脚本按 `__file__` 所在目录（不是 cwd）确定输出路径，此前
       直接跑会覆写"知识检索/workflows.jsonl"这个真实交付文件（因为
       内容确定性不变而无害，但那是运气不是设计）。现在每次都把脚本
       复制进独立临时目录再跑，输出天然落在临时目录里，不碰交付目录。
    """
    env = {**os.environ, **(extra_env or {})}
    md5s = []
    for seed in seeds:
        env["PYTHONHASHSEED"] = seed
        with tempfile.TemporaryDirectory() as tmpdir:
            script_copy = os.path.join(tmpdir, os.path.basename(script_path))
            # ⚠️ 必须把**同目录的其它 .py 一起拷进去**，不能只拷主脚本。
            # 第四轮加 R4-05 冻结守卫之后，三个挖掘器都 `from freeze_guard
            # import …`，只拷主脚本会让子进程当场 ModuleNotFoundError，
            # 而本函数的 returncode 检查会把它报成"确定性无法判定"——
            # 症状离病因很远，实测时确实先怀疑到了别处。
            #
            # 修法是把依赖补齐，**不是**把守卫改成"import 不到就跳过"。
            # 后者会让守卫在任何拷贝式沙箱里静默消失，正是本项目四轮都在
            # 治的那个"缺失即放行"。
            src_dir = os.path.dirname(os.path.abspath(script_path))
            for fn in os.listdir(src_dir):
                if fn.endswith(".py"):
                    shutil.copy2(os.path.join(src_dir, fn),
                                 os.path.join(tmpdir, fn))
            shutil.copy2(script_path, script_copy)
            result = subprocess.run(
                [sys.executable, script_copy], cwd=tmpdir, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120)
            if result.returncode != 0:
                raise RuntimeError(
                    f"子进程失败(seed={seed}, returncode={result.returncode})，"
                    f"本用例无法判定确定性，不能悄悄放行：{result.stderr[-300:]}")
            output_path = os.path.join(tmpdir, output_filename)
            with open(output_path, "rb") as f:
                md5s.append(hashlib.md5(f.read()).hexdigest())
    return md5s


def test_workflows_determinism():
    print("\n4. mine_workflows.py 确定性（跨 PYTHONHASHSEED 独立进程，不是同进程重复调用）")
    print("   （独立评审 P-4 指出：同进程内重复调用 mine() 恒定一致，测不出这类 bug；")
    print("    必须起独立进程、换不同哈希种子才能真正暴露 set 遍历顺序问题）")

    knowledge_dir = os.path.join(RAG, "知识检索")
    dataset = os.environ.get("RAG_DATASET") or os.path.join(
        RAG, "多源数据整合", "dataset", "memory_source_records.jsonl")

    md5s = cross_process_determinism_md5s(
        os.path.join(knowledge_dir, "mine_workflows.py"), "workflows.jsonl",
        extra_env={"RAG_DATASET": dataset})
    check("确定性-3个独立进程(不同哈希种子)产出md5一致",
          len(set(md5s)) == 1, f"md5s={md5s}")

    # R2-10 元测试：子进程全部崩溃时，本函数必须报错，不能因为"读到同一份
    # 陈旧文件、三次md5照样相同"而被误判成"确定性通过"——这正是复核员
    # 实测复现过的假通过通道（旧实现不检returncode）。用一个立即崩溃的
    # 假脚本验证新实现会正确抛异常。
    with tempfile.TemporaryDirectory() as crash_dir:
        crash_script = os.path.join(crash_dir, "crashes_immediately.py")
        with open(crash_script, "w", encoding="utf-8") as f:
            f.write("import sys\nsys.exit('BOOM')\n")
        raised = False
        try:
            cross_process_determinism_md5s(crash_script, "workflows.jsonl", seeds=("1",))
        except (RuntimeError, FileNotFoundError):
            raised = True
        check("R2-10-子进程全崩溃时必须报错而不是假通过", raised)


# ═════════════════════════════════════════════════════ 5. reproduce_L1.py 的核对器

def test_reproduce_core_checkers():
    print("\n5. reproduce_L1.py 核对逻辑（删记录/改数字必须被判定不符，不能悄悄放行）")
    from reproduce_L1 import check_exact

    fake_records = {"M-FAKE": {"metrics": {"total": {"value": 100, "repeats": 1}}}}
    claim_ok = {"metric": "total", "expected": 100, "record_id": "M-FAKE"}
    claim_bad = {"metric": "total", "expected": 999, "record_id": "M-FAKE"}
    claim_missing_record = {"metric": "total", "expected": 100, "record_id": "M-GONE"}
    claim_missing_metric = {"metric": "nope", "expected": 100, "record_id": "M-FAKE"}

    ok, _, _ = check_exact(claim_ok, fake_records)
    check("核对器-数字相符判定通过", ok is True)

    ok, _, _ = check_exact(claim_bad, fake_records)
    check("核对器-数字被篡改判定不符（不能是True）", ok is False)

    ok, _, _ = check_exact(claim_missing_record, fake_records)
    check("核对器-记录缺失返回None(跳过)而不是静默通过",
          ok is None, "None会被reproduce_L1.py的main()计入退出码非零")

    ok, _, _ = check_exact(claim_missing_metric, fake_records)
    check("核对器-指标缺失返回None(跳过)而不是静默通过", ok is None)

    # R2-30（P-7）：expected 恰好是"整数比例"的浮点数（如1.0代表100%）时，
    # 精度从字符串反推会失效——str(1.0)只有1位小数，round(0.955,1)==1.0
    # 恒成立，真实数值从100%回归到95.5%这种有意义的退化会被误判成通过。
    # 这条用例故意还原这个场景，验证"没有显式ndigits时用旧的字符串反推"
    # 仍会被这个陷阱坑到（记录问题存在过），"有显式ndigits时"能正确识别。
    fake_recall_records = {"M-FAKE": {"metrics": {"recall": {"value": 0.955, "repeats": 1}}}}
    claim_round_number_no_ndigits = {
        "metric": "recall", "expected": 1.0, "record_id": "M-FAKE"}
    claim_round_number_with_ndigits = {
        "metric": "recall", "expected": 1.0, "record_id": "M-FAKE", "ndigits": 6}

    ok, _, _ = check_exact(claim_round_number_no_ndigits, fake_recall_records)
    check("核对器-无显式ndigits时整数比例值(100%)对95.5%漏检是已知陷阱",
          ok is True, "这是旧行为的陷阱本身，claims.json里唯一有此风险的"
          "C-241已加ndigits=6规避，不是本用例要修的地方")

    ok, _, _ = check_exact(claim_round_number_with_ndigits, fake_recall_records)
    check("核对器-显式ndigits=6时能正确识别100%回归到95.5%的退化",
          ok is False, f"ok={ok}，应为False")

    # 不只测合成场景，还要确认真实交付物里唯一有此风险的那条 claim
    # （C-241）确实配置了防护——否则以后有人动了claims.json、不小心删掉
    # ndigits字段，本用例上面两条合成测试也拦不住这个真实的回归。
    real_claims = json.load(open(os.path.join(BASE, "claims.json"), encoding="utf-8"))
    c241 = next((c for c in real_claims["claims"] if c["claim_id"] == "C-241"), None)
    check("核对器-claims.json里C-241确实配置了ndigits防护(不是只在测试里演示)",
          c241 is not None and c241.get("ndigits", 0) >= 4,
          f"C-241={c241}")


# ═════════════════════════════════════════════════════ 6. 防复发：受跟踪文件不得含真实密钥词根

def _load_real_value_tables():
    """加载两张"真实值"表：密钥词根 + 身份标识。返回 (值列表, 缺失说明列表)。

    🔴 第三轮独立复核 R3-01/R3-05 的核心修复：**身份标识这一整类此前零
    自动覆盖**。防复发检查只读 secrets_blocklist.txt，而 identity_map.txt
    就在同一个目录、由同一个 _load_identity_map 加载——两张表本来就是
    并列的"不能进 git 的真实值"，检查却只查了一张。

    直接后果：`anonymize.py` 的注释里第一轮抄进 sudo 口令、第二轮抄进两条
    真实密码（这两次都被 blocklist 抓到了），第三轮抄进真实用户名与真实
    GitHub 用户名——**同一个文件、同一个机制、第三次**，而这次没有任何
    检查会报红。补上 identity_map 的覆盖成本为零，是典型的"把实例升级
    成类"。
    """
    values, missing = [], []

    blocklist_path = os.path.join(RAG, "多源数据整合", "secrets_blocklist.txt")
    if os.path.exists(blocklist_path):
        values += [ln.strip() for ln in open(blocklist_path, encoding="utf-8")
                   if ln.strip() and not ln.startswith("#")]
    else:
        missing.append("secrets_blocklist.txt")

    # R3-28 已把原先两个子系统各一份的 identity_map 合并成项目根目录下
    # 唯一的一份，加载逻辑也只剩 identity_map_loader.py 一处。这里跟着改成
    # 读那一份——**并且不再自己解析一遍**：本文件要是自己再写一遍解析，
    # 就又制造了一处会和真实实现分叉的副本，正是 R3-28 要消灭的形态。
    map_path = os.path.join(RAG, "identity_map.txt")
    if os.path.exists(map_path):
        sys.path.insert(0, RAG)
        import identity_map_loader
        values += [real for real, _pseudo, _mode
                   in identity_map_loader.load_pairs(map_path)]
    else:
        missing.append("identity_map.txt")

    return values, missing


def test_identity_map_single_source():
    """🔴 第三轮独立复核 R3-28/R3-29：把"只有一份映射表"这件事钉成机器检查。

    合并本身是一次性动作，但**分叉是会重新长出来的**——下次谁在某个子目录
    里再放一个 identity_map.txt，一切又回到原样，而且照样静悄悄。所以这组
    用例查的不是"当初合并对不对"，是"现在还是不是只有一份"。
    """
    print("\n11. 身份映射表：单一数据源 + 无冲突（R3-28/R3-29）")

    sys.path.insert(0, RAG)
    import identity_map_loader

    strays = []
    for root, dirs, fs in os.walk(RAG):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for fn in fs:
            if fn == "identity_map.txt" and root != RAG:
                strays.append(os.path.relpath(os.path.join(root, fn), RAG))
    check("身份映射-全项目只有根目录一份 identity_map.txt",
          not strays, f"多出来的：{strays}")

    pairs = identity_map_loader.load_pairs()
    check("身份映射-根目录那份能加载出条目（0 条会让下面几条空集恒真）",
          len(pairs) > 0, f"{len(pairs)} 条")

    multi_pseudo, shared_pseudo = identity_map_loader.conflicts()
    check("身份映射-没有「一个真实值映射到多个假名」（这类一定是错的）",
          not multi_pseudo, f"冲突项：{sorted(multi_pseudo)}")
    # 多个真实值共用一个假名不判失败：同一个人的多个别名本就该同名，
    # 那正是一致性假名化要的效果。只打印供人看。
    print(f"       （多真实值共用同一假名 {len(shared_pseudo)} 组，"
          f"属一致性假名化的正常形态，不判失败）")

    # R3-29：纯字母条目必须走全词匹配，否则会撞上普通英文词
    bad_mode = [p for r, p, m in pairs if r.isalpha() and m != "word"]
    check("身份映射-纯字母的真实值一律走全词匹配（R3-29 防撞英文词）",
          not bad_mode, f"仍是子串匹配的：{bad_mode}")

    # 两个消费方必须拿到同一份数据——这是合并的**目的**，直接断言它
    sys.path.insert(0, os.path.join(RAG, "多源数据整合"))
    import measure
    import anonymize
    check("身份映射-measure.py 与 anonymize.py 条目数一致（同一份表）",
          len(measure._IDENTITY_MAP) == len(anonymize.IDENTITY_PATTERNS)
          == len(pairs),
          f"measure={len(measure._IDENTITY_MAP)} "
          f"anonymize={len(anonymize.IDENTITY_PATTERNS)} 表={len(pairs)}")

    # 全词匹配确有其效：纯字母条目不该命中"该词嵌在更长单词里"的形态
    alpha = [(r, p) for r, p, m in pairs if m == "word"]
    if not alpha:
        check("身份映射-存在纯字母条目可供验证全词匹配", False)
    else:
        real, pseudo = alpha[0]
        embedded = f"xx{real}yy"          # 嵌在字母中间：不该被替换
        standalone = f"路径 /home/{real}/ 下"   # 独立成词：应该被替换
        check("身份映射-全词匹配不会命中嵌在更长单词里的同形串",
              anonymize._pseudonymize_identities(embedded) == embedded)
        check("身份映射-全词匹配仍能命中独立成词的真实值",
              pseudo in anonymize._pseudonymize_identities(standalone))


def test_no_real_secret_in_tracked_files():
    """第二轮独立复核 §二·一：修脱敏规则的过程中，把两条真实密码原样写进了
    注释与测试夹具里，且已推送到远端——和最初P0发现（脱敏模块源码是某口令
    唯一泄漏点）完全同型的错误，在同一个文件里复发了一次。

    这条用例把"人会忘记注释也算交付物"这件事变成机器能查的检查：受git
    跟踪的每一个文件，都不应该出现任何一个真实密钥词根**或身份标识**。

    ═══ 🔴 第三轮独立复核 R3-05/R3-06：本检查此前有四个盲区，全部已堵 ═══

    复核员用四组元测试（M-A~M-D）实测，其中三个致命：

    | 元测试 | 破坏方式 | 旧行为 |
    |---|---|---|
    | M-A | 删掉 blocklist 再注入真实词根 | 退出码 0、[SKIP]、全绿——**在评委机器/CI 上永远不会失败** |
    | M-B | 真实用户名写进受跟踪文件 | 全绿——身份类零覆盖，正是 R3-01 的直接原因 |
    | M-C | 数据集从未被 rescrub 处理 | 全绿——只验机制不验结果（见下方 §6.4，R4-16 之前这里写的是一个**并不存在**的函数名）|
    | M-D | 凭据只在历史 commit、HEAD 已删 | 只读工作区，不查历史 |

    修法的共同原则是本项目反复强调却反复没贯彻的那一条——**"缺失即失败"，
    不是"缺失即跳过"**：词根表读不到、git 枚举不到文件，都说明这条检查
    此刻根本没有在检查任何东西，那就必须红，而不是绿着让人以为查过了。

    另外：命中时旧代码打印 `root[:3] + "***"`，**会泄漏词根前三个字符**。
    改为只报文件名与词根在表中的**序号**，报告本身不再携带任何真实值片段。
    """
    print("\n6. 防复发检测（受跟踪文件不得含真实密钥词根/身份标识）")

    values, missing = _load_real_value_tables()

    # ★ M-A 修复：缺失即失败。这些表是 gitignore 的本地专属文件，在评委
    #   机器上确实可能不存在——但那正意味着"这台机器上这条检查是瞎的"，
    #   必须显式红，让人知道要么补上表、要么明白此处未经检查，而不是
    #   给一个让人安心的绿灯。
    check("防复发-真实值表可加载(缺失说明本检查此刻是瞎的，必须红)",
          not missing, f"缺失：{missing}" if missing else "")
    check("防复发-真实值表非空", len(values) > 0, f"共 {len(values)} 条")

    # ★ R4-15：这四条是本组余下的检查项。表读不到时它们**没法执行**，但
    #   必须显式记成 FAIL 并计入总数——旧代码直接 `return`，总数从 96 缩到
    #   92，环境越残缺分母越小、越容易「全绿」。
    REMAINING = [
        "防复发-git枚举到受跟踪文件(枚举为空则本检查无意义，必须红)",
        "防复发-受跟踪文件不含任何真实密钥词根/身份标识",
        "防复发-能枚举到git历史commit(枚举为空则历史检查无意义，必须红)",
        "防复发-git历史(含已删除的旧commit)不含任何真实值",
    ]
    if not values:
        for n in REMAINING:
            unavailable(n, "真实值表为空，无从比对")
        return

    # -z：NUL分隔、不转义路径。git ls-files 默认（无-z）对非ASCII文件名会
    # 输出成带引号的八进制转义形式（如 "\346\226\207..."），本项目目录几乎
    # 全是中文名，不加-z会导致 os.path.isfile() 全部判假、整个检查静默漏检
    # 所有中文路径下的文件——本函数写完后自己注入一条真实词根做过验证，
    # 第一版正是这样悄悄放行的，属于本项目反复出现的那类"过度自信的检测器"。
    tracked = subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                             cwd=RAG).stdout.split(b"\0")
    tracked = [p for p in tracked if p]

    # ★ R3-06 修复：git 枚举为空（没有 .git、git 不可用、或**作为解压后的
    #   交付物运行**）时，下面的循环一次都不执行，旧代码直接 not hits → PASS。
    #   复核员实测：把真实词根注入 可复现性说明.md，在无 .git 的副本里跑，
    #   54/54 全绿；拷进 .git 再跑立刻 FAIL。**评委解压交付物跑，拿到的
    #   就是这个假绿灯。**
    check("防复发-git枚举到受跟踪文件(枚举为空则本检查无意义，必须红)",
          len(tracked) > 1, f"枚举到 {len(tracked)} 个文件")
    if len(tracked) <= 1:
        # R4-15：同上，缺席显式记账，总数恒定
        for n in REMAINING[1:]:
            unavailable(n, "git 枚举不到受跟踪文件（无 .git 或 git 不可用）")
        return

    # ⚠️ 匹配语义必须和真正的替换语义一致：**大小写敏感 + 词边界**。
    # 第一版写成 `v.lower() in content.lower()` 的裸子串匹配，立刻把
    # `congestion control`、`exec control` 这类正常英文判成"身份残留"
    # ——因为 identity_map 里有一条恰好是常用英文词（R3-29 已单独点名）。
    # 检查器比它守护的对象更宽松，只会制造噪声、逼人习惯性忽略红灯。
    # ⚠️ 序号从 1 开始。原来是 `enumerate(values)`（0-based），而输出里一直
    # 写「表内第 N 项」——任何人（包括写 BY_DESIGN 豁免的人）都会按 1-based
    # 去理解它。第四轮加 R4-06 按值豁免时我自己就踩了：豁免写 `{4}`，而
    # `idx` 实际是 3，豁免整个失效、当场多报两条。
    # 这类"对外称 1-based、对内用 0-based"的错位不会有任何报错，只会让
    # 引用这个序号的人默默算错一位，所以直接把基准统一掉，而不是在
    # 豁免那边写 `-1` 去迁就它。
    patterns = [(i, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])"))
                for i, v in enumerate(values, 1) if v]

    # 有意保留的例外：GitHub 用户名出现在**外部时间戳锚定**相关文件里是
    # 设计使然——锚定的全部价值就在于评委能公开访问那条 issue 自行核验，
    # 把用户名假名化等于把这条证据链本身作废。该 issue 本来就是公开的，
    # 不构成新增泄漏。写成显式白名单而不是"忘了检查"，并要求给出理由。
    # 🔴 第四轮独立复核 R4-06：这张表原本是 {文件: 理由} 的**整文件豁免**，
    # 意思是"这两个文件里出现表内任何值都放行"。复核用元测试 M-B3 实证过：
    # 往这两个文件里注入一个真实身份值**外加一条密钥词根**，检查照样
    # exit=0 全绿。也就是说，这两个文件今后混进任何凭据都不会有人发现，
    # 而其中一个恰恰是**报告正文**（会持续被编辑的文件）。
    #
    # 改为**按值豁免**：写清每个文件允许出现表内第几项、以及为什么。
    # 序号之外的任何值一律照常报红。实测这两个文件当前都只命中第 4 项
    # （GitHub 用户名），所以豁免面收窄到 {4} 不损失任何现有绿灯。
    #
    # ⚠️ 用序号而不是值本身：这份源码是受跟踪文件，写值进来就等于
    # 在防泄漏检查器里泄漏一次——第一/二/三轮已经在同一个文件上犯过三次。
    BY_DESIGN = {
        "可复现性体系/anchor_issue_snapshot.json": {
            "allowed": {4},
            "why": "GitHub issue 锚定快照，URL 与作者字段必须可被第三方公开"
                   "核验；表内第 4 项即该 GitHub 用户名，随仓库 URL 天然公开",
        },
        "评测集/说明报告.md": {
            "allowed": {4},
            "why": "正文引用上述锚定 issue 的地址，同上",
        },
        "README.md": {
            "allowed": {4},
            "why": "作者署名与仓库地址，即该 GitHub 用户名",
        },
        "LICENSE": {
            "allowed": {4},
            "why": "MIT 许可证的版权人署名，同上",
        },
    }

    hits = []
    for path_bytes in tracked:
        path = path_bytes.decode("utf-8")
        full = os.path.join(RAG, path)
        if not os.path.isfile(full):
            continue
        try:
            content = open(full, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        rel_key = path.replace("\\", "/")
        allowed = (BY_DESIGN.get(rel_key) or {}).get("allowed", set())
        for idx, pat in patterns:
            if pat.search(content):
                if idx in allowed:      # R4-06：按值豁免，不是整文件豁免
                    continue
                # 只报序号不报内容——旧版打印词根前3字符，报告本身就在泄漏
                hits.append((path, f"表内第{idx}项"))

    check("防复发-受跟踪文件不含任何真实密钥词根/身份标识",
          not hits, f"命中：{hits}" if hits else "")

    # ★ M-D 修复：只扫工作区抓不到"已推送、后来从 HEAD 删掉"的泄漏——
    #   而 R2-01 那次泄漏恰恰是这种（已 push 到远端）。用 git log -S 在
    #   全历史里搜。本项目历史已两次重建，正常应为 0 命中。
    # 与工作区检查用同一份 BY_DESIGN 豁免：GitHub 用户名本来就必须留在
    # 锚定快照里，它进历史是预期内的，不是泄漏。
    # R4-06：历史扫描同样改按值——只豁免各文件**显式登记允许**的那几项，
    # 而不是"凡在豁免文件里出现过的值，全历史都放行"。旧写法把豁免面从
    # 「两个文件里的第 4 项」放大成了「第 4 项在任何 commit 的任何文件里」。
    by_design_values = set()
    for rel, spec in BY_DESIGN.items():
        p = os.path.join(RAG, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        try:
            c = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for idx, pat in patterns:
            if idx in spec.get("allowed", set()) and pat.search(c):
                by_design_values.add(idx)

    # ⚠️ 实现方式踩过两个坑，都写在这里免得后人重蹈：
    #   ① `git log -S<值>` 是**裸子串**匹配，与工作区那边的整词匹配语义不
    #      一致——4~6 字符的常用英文词会大量误报（本轮实测 4 项命中里 2 项
    #      是这样来的假阳性）。
    #   ② 改用 `git log -G'(?<!\w)值(?!\w)' --pickaxe-regex` 看似解决了，
    #      实则更糟：**git 默认用 POSIX ERE，不支持 (?<!) 后顾断言**，
    #      正则整个失效、一条都匹配不到，于是"全绿"。这正是本项目被反复
    #      点名的那类"看起来在查、其实什么都没查"的检查器——而且是我在
    #      修这个问题的过程中当场又造了一个，写在这里作为教训。
    #   最终改用 `git grep -w -F <值> <commit>`：-w 整词、-F 纯字面量，
    #   语义明确且不依赖 git 的正则方言。commit 数很少，逐个扫成本可忽略。
    try:
        commits = subprocess.run(["git", "rev-list", "--all"], capture_output=True,
                                 cwd=RAG, timeout=60).stdout.decode().split()
    except (subprocess.TimeoutExpired, OSError):
        commits = []
    check("防复发-能枚举到git历史commit(枚举为空则历史检查无意义，必须红)",
          len(commits) > 0, f"{len(commits)} 个commit")

    history_hits = []
    for idx, _pat in patterns:
        if idx in by_design_values:
            continue
        for commit in commits:
            try:
                out = subprocess.run(
                    # idx 是 1-based（见 patterns 构造处的说明），取值要减 1。
                    # 这一处就是改基准时**唯一**忘了跟的地方，当场 IndexError
                    # ——好在它炸得响；要是数组再长一点，它会安静地拿错一个值
                    # 去扫历史，然后报"全历史干净"。
                    ["git", "grep", "-c", "-w", "-F", values[idx - 1], commit],
                    capture_output=True, cwd=RAG, timeout=60)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if out.stdout.strip():
                history_hits.append((f"表内第{idx}项", commit[:8]))
                break
    check("防复发-git历史(含已删除的旧commit)不含任何真实值",
          not history_hits, f"命中：{history_hits}" if history_hits else "")


# ═══════════════════ 6.4 rescrub 真的跑过、且跑的是当前规则（R4-16 幽灵测试引用）

# ★ 这份清单是**固定申报名单**，不是 `_dataset_files()` 当场扫出来的结果。
#   两个理由：
#   ① R4-15：检查项总数不能随环境浮动。按扫描结果生成用例，等于「文件被
#      删掉 → 这条检查连同它的红一起消失」，删得越干净越绿。
#   ② 覆盖面本身要被锁住：R2-12 当年的病就是 glob 只圈到 1/7 个文件。名单
#      写死在这里，将来谁改 `_dataset_files()` 的圈定范围，下面那条「名单与
#      实际扫描结果一致」会当场红，逼人来同步，而不是悄悄少扫几个文件。
#   增删数据集文件时要同步改这里 + DECLARED_CHECK_COUNT。
EXPECTED_SCRUB_TARGETS = [
    "多源数据整合/dataset/memory_source_records.jsonl",
    "多源数据整合/dataset/rejected.jsonl",
    "多源数据整合/dataset/tool_execution_records.jsonl",
    "多源数据整合/dataset_claude_code.jsonl",
    "多源数据整合/dataset_manual_config.jsonl",
    "多源数据整合/dataset_openclaw_lucy.jsonl",
    "多源数据整合/dataset_user_history.jsonl",
    "评测集/generation_raw_log.jsonl",
    "评测集/generation_skipped.jsonl",
    # 2026-08-14 新增：出题时若有问题重试后重合度仍超标，会产出这个文件
    # （v1.3.0 那轮也有，但当时它不在 rescrub 的 glob 范围内）。它同样承载
    # 从真实使用记录里抄来的原文，必须过脱敏——这条是被"名单与生产代码
    # 圈定范围一致"那道检查自己揪出来的，正是它存在的意义。
    "评测集/generation_coverage_violations.jsonl",
]


def test_rescrub_actually_applied():
    """🔴 第四轮独立复核 R4-16：**幽灵测试引用**——本文件 §6 的注释里白纸黑字
    写着「那条单独由 test_rescrub_actually_applied 覆盖」，而全项目 grep，
    **这个函数从来不存在**。本函数就是把那句注释补成真的。

    ═══ 为什么幽灵引用比漏一个检查更危险 ═══════════════════════════════

    漏检是「没人查」，读代码的人一眼看得出来；幽灵引用是「有人查过了」这个
    **假消息**，它让下一轮复核员放心跳过这块。第四轮复核方自述，第三轮时
    差点被这句话带过去。M-C 盲区（只验机制不验结果）第三轮标称已修，实为
    未修，就是被这一句话盖住的。

    ═══ 这条检查的判据 ═══════════════════════════════════════════════

    `rescrub_strings.py` 给每个数据集文件写一个 `.scrub.json` 旁路文件，记下
    处理时的规则指纹（`anonymize.py` + `secrets_blocklist.txt` +
    `identity_map.txt` 三者内容的 sha256）。这个机制的**全部价值**在于：
    规则一改，指纹就对不上，提醒人全量重跑。但此前 R2-12 那组用例只在
    `tempfile` 造的合成文件上验证过「机制能跑」，**从没核对过真实 sidecar
    的时效性**——于是 9 个 sidecar 的指纹全部过期而无人报警。

    ⚠️ 指纹过期 = **FAIL，不是 SKIP**。过期意味着「当前这份数据集是用某个
    已经不存在的旧规则脱敏的」，这正是脱敏漏检的直接来源，不是可以缓一缓的
    元数据不一致。想让它变绿只有一条路：跑一次 `rescrub_strings.py`。

    ★ 指纹算法**必须 import 过来调用，不许在这里复刻一遍**。R4-03 的教训：
      §12 曾在测试里把生产判定逻辑抄了一份，结果杀掉生产代码测试照样全绿——
      复刻一份判定逻辑，测的就是那份复制品，不是被测对象。
    """
    print("\n6.4 rescrub 真的跑过、且跑的是当前规则（R4-16 幽灵测试引用补实）")

    fingerprint = None
    try:
        sys.path.insert(0, os.path.join(RAG, "多源数据整合"))
        import rescrub_strings                        # ★ 真 import，不复刻
        fingerprint = rescrub_strings._rules_fingerprint()
    except Exception as e:  # noqa: BLE001
        check("rescrub-规则指纹可由生产代码算出", False, f"{type(e).__name__}: {e}")
    else:
        check("rescrub-规则指纹可由生产代码算出", bool(fingerprint),
              f"当前指纹 {fingerprint[:12]}…")

    # 覆盖面锁：登记名单必须与生产代码实际圈定的文件集合一致
    if fingerprint is None:
        unavailable("rescrub-登记名单与生产代码圈定范围一致", "指纹模块导入失败")
    else:
        scanned = {os.path.relpath(p, RAG).replace("\\", "/")
                   for p in rescrub_strings._dataset_files()}
        expected = set(EXPECTED_SCRUB_TARGETS)
        check("rescrub-登记名单与生产代码圈定范围一致",
              scanned == expected,
              f"仅在代码中：{sorted(scanned - expected)}；"
              f"仅在名单中：{sorted(expected - scanned)}"
              if scanned != expected else f"{len(expected)} 个文件")

    for rel in EXPECTED_SCRUB_TARGETS:
        name = f"rescrub-旁路指纹是当前规则-{os.path.basename(rel)}"
        if fingerprint is None:
            unavailable(name, "指纹算不出，无从比对")
            continue
        sidecar = os.path.join(RAG, *rel.split("/")) + ".scrub.json"
        if not os.path.exists(sidecar):
            # 缺失即失败：没有 sidecar 就等于「这个文件从没被 rescrub 覆盖过」
            check(name, False, "旁路文件不存在（该文件从未被 rescrub 覆盖）")
            continue
        try:
            recorded = json.load(open(sidecar, encoding="utf-8")).get("rules_fingerprint", "")
        except (OSError, ValueError) as e:
            check(name, False, f"旁路文件读不出：{type(e).__name__}")
            continue
        check(name, recorded == fingerprint,
              f"记录 {recorded[:12]}… ≠ 当前 {fingerprint[:12]}…（需重跑 rescrub_strings.py）"
              if recorded != fingerprint else "")


# ═════════════════════════════════════════════════════ 6.5 E-3 语料哈希卫的三处绕过口（R2-11）

def test_corpus_hash_guard_bypasses_closed():
    print("\n6.5 E-3 语料哈希卫：三处绕过口都必须拒跑，不能静默/降级放行（R2-11）")

    eval_dir = os.path.join(RAG, "评测集")
    sys.path.insert(0, eval_dir)
    import measure_recall as mr

    with tempfile.TemporaryDirectory() as tmpdir:
        frozen_src = os.path.join(eval_dir, "eval_set_frozen.json")
        eval_set_src = os.path.join(eval_dir, "eval_set.jsonl")
        frozen_copy = os.path.join(tmpdir, "eval_set_frozen.json")
        eval_set_copy = os.path.join(tmpdir, "eval_set.jsonl")
        shutil.copy2(frozen_src, frozen_copy)
        shutil.copy2(eval_set_src, eval_set_copy)

        orig_frozen, orig_eval, orig_kdir = (
            mr.FROZEN_META_PATH, mr.EVAL_SET_PATH, mr.KNOWLEDGE_DIR)
        mr.FROZEN_META_PATH = frozen_copy
        mr.EVAL_SET_PATH = eval_set_copy
        try:
            # 正常路径：未篡改，不应拒跑
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-正常路径(未篡改)不应误报拒跑", not raised)

            # 绕过①：篡改 eval_set.jsonl 本身
            with open(eval_set_copy, "a", encoding="utf-8") as f:
                f.write('{"tampered_by_test": true}\n')
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-绕过①篡改eval_set.jsonl本身必须拒跑", raised)
            shutil.copy2(eval_set_src, eval_set_copy)  # 还原

            # 绕过②：删掉 knowledge_base_sha256 字段
            frozen = json.load(open(frozen_copy, encoding="utf-8"))
            del frozen["knowledge_base_sha256"]
            json.dump(frozen, open(frozen_copy, "w", encoding="utf-8"), ensure_ascii=False)
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-绕过②删knowledge_base_sha256字段必须拒跑(不能只警告)", raised)

            # 绕过③：整个删掉 frozen.json
            os.remove(frozen_copy)
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-绕过③整个删掉frozen.json必须拒跑(不能静默放行)", raised)

            # 🔴 R3-12：上一轮修完点名的三个口之后，**新写的代码里立刻又长
            # 出两个同型的口**——复核员实测都绕过成功。这两条用例把它们
            # 也钉住，防止第四次复发。
            shutil.copy2(frozen_src, frozen_copy)
            shutil.copy2(eval_set_src, eval_set_copy)
            frozen = json.load(open(frozen_copy, encoding="utf-8"))
            del frozen["sha256"]          # 绕过④：删顶层 sha256 字段
            json.dump(frozen, open(frozen_copy, "w", encoding="utf-8"),
                      ensure_ascii=False)
            with open(eval_set_copy, "a", encoding="utf-8") as f:
                f.write('{"tampered_after_removing_sha256": true}\n')
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-绕过④删顶层sha256字段后改题目必须拒跑", raised)

            shutil.copy2(frozen_src, frozen_copy)
            shutil.copy2(eval_set_src, eval_set_copy)
            frozen = json.load(open(frozen_copy, encoding="utf-8"))
            # 绕过⑥：把语料哈希裁成只剩一个，其余语料从此改了也查不出来
            frozen["knowledge_base_sha256"] = {
                "cases.jsonl": frozen["knowledge_base_sha256"]["cases.jsonl"]}
            json.dump(frozen, open(frozen_copy, "w", encoding="utf-8"),
                      ensure_ascii=False)
            raised = False
            try:
                mr.assert_corpus_matches_frozen()
            except SystemExit:
                raised = True
            check("E-3守卫-绕过⑥语料哈希登记不完整必须拒跑(不只挡空字典)", raised)
        finally:
            mr.FROZEN_META_PATH, mr.EVAL_SET_PATH, mr.KNOWLEDGE_DIR = (
                orig_frozen, orig_eval, orig_kdir)


# ═════════════════════════════════════════════════════ 6.6 三条类级结构检查（第三轮 §三·二 / R3-17 / R3-20）

def test_structural_invariants():
    print("\n6.6 类级结构检查：产物落差登记 / claims 内部一致性 / 评测集语义校验")

    # ── ① 产物不得早于生成它的脚本（§三·二）────────────────────────────
    # 第三轮实测：cases.jsonl 比 mine_cases.py 早 20 小时 44 分、dataset 比
    # extract 早 3 分钟、eval_set 比 generate 早一天。三处都在报告某个角落
    # 做了声明，但**没有任何机制保证"声明"与"事实"同步**——下次忘了声明，
    # 就是又一次"报告数字建立在旧产物上"而无人知晓。
    # 这条检查把"有意冻结"变成必须显式登记的事：登记了才放行。
    PAIRS = [
        ("知识检索/cases.jsonl", "知识检索/mine_cases.py"),
        ("知识检索/workflows.jsonl", "知识检索/mine_workflows.py"),
        ("知识检索/templates.jsonl", "知识检索/mine_templates.py"),
        ("评测集/eval_set.jsonl", "评测集/generate_eval_candidates.py"),
        ("多源数据整合/dataset/memory_source_records.jsonl",
         "多源数据整合/merge_and_validate.py"),
    ]
    registry_path = os.path.join(BASE, "代码产物落差登记表.md")
    check("落差表-登记表文件存在(缺失则无法判断哪些落差是有意的)",
          os.path.exists(registry_path))
    registry = ""
    if os.path.exists(registry_path):
        registry = open(registry_path, encoding="utf-8").read()

    unregistered = []
    for art_rel, gen_rel in PAIRS:
        art = os.path.join(RAG, *art_rel.split("/"))
        gen = os.path.join(RAG, *gen_rel.split("/"))
        if not (os.path.exists(art) and os.path.exists(gen)):
            continue
        if os.path.getmtime(art) < os.path.getmtime(gen):
            # 产物比脚本旧 → 必须在登记表里出现（按产物路径名匹配）
            if os.path.basename(art_rel) not in registry:
                unregistered.append(art_rel)
    check("落差表-每个「产物早于生成脚本」的情形都已登记（mtime 判据，弱）",
          not unregistered, f"未登记：{unregistered}" if unregistered else "")

    # ── ①b 内容级血缘（第四轮：mtime 判据已被 touch 打穿）──────────────
    #
    # 上面那条判据是第三轮给的，第四轮实测**被一次 touch 全部绕过**：三个
    # 知识产物的 mtime 被摸到 08-10 18:03，五对比较全部 ok，而内容仍是旧的
    # （cases.jsonl 27 条 vs 当前代码产出的 14 条）。时间戳是元数据、不是内容
    # 证据——复制目录、解压、同步工具、编辑器保存都会改它。
    #
    # 所以 mtime 那条保留但降级标注为「弱」，真正承重的是这里的内容级血缘：
    # 产出时把 (代码 sha256, 输入 sha256) 写进伴生 .provenance.json，
    # 检查时重算比对。内容变了指纹必变，touch 无效。
    #
    # ⚠️ 血缘缺失 = 失败，不是跳过。旧 mtime 判据在文件不全时也静默通过，
    # 于是"没查"和"查过了"长得一模一样——这正是四轮反复出现的那个病。
    sys.path.insert(0, BASE)
    import provenance
    prov_results = provenance.check_all()
    check("血缘-依赖图非空（为空则下面几条空集恒真，等于没查）",
          len(prov_results) > 0, f"{len(prov_results)} 个产物")
    bad_prov = [(name, st, d) for name, st, d in prov_results
                if st not in ("ok", "frozen")]
    check("血缘-每个产物要么与当前代码/输入一致(ok)，要么有登记的冻结理由(frozen)",
          not bad_prov,
          f"异常：{[(n, s) for n, s, _ in bad_prov]}" if bad_prov else "")
    # frozen 是允许的，但**必须同时在落差登记表里有对应行**——否则"写个理由
    # 就能豁免"会变成万能出口
    frozen_unregistered = [name for name, st, _ in prov_results
                           if st == "frozen"
                           and os.path.basename(name) not in registry]
    check("血缘-标为 frozen 的产物必须同时登记在《代码产物落差登记表》里",
          not frozen_unregistered,
          f"血缘说冻结但登记表没有：{frozen_unregistered}"
          if frozen_unregistered else "")

    # ── ② claims.json 内部一致性（R3-17）─────────────────────────────
    claims = json.load(open(os.path.join(BASE, "claims.json"), encoding="utf-8"))["claims"]

    # N-5：散文里写的数字与 expected 数值矛盾——机器只校验 expected，
    # 评委读的却是散文。三条 claim 实测存在这个矛盾。
    # 判据：一条 claim 的散文里若出现了"N 条"这类计数表述，则**至少有一个
    # 要等于 expected**。不要求每个都相等——同一句话里合法地会提到别的
    # 数字（如 C-220「169 题（178 条候选，剔除 9 条）」，169 才是被主张的
    # 那个）；但如果 expected 一个都没出现，说明散文描述的根本不是这条
    # claim 实际校验的值，评委读到的与机器校验的是两回事。
    prose_mismatch = []
    for c in claims:
        exp = c.get("expected")
        # expected 是 0/1 时多半是"是否重叠""是否一致"这类布尔标志，不是
        # 计数，散文里当然不会出现"0 条"，跳过以免制造假红灯
        if not isinstance(exp, int) or exp < 2:
            continue
        # 量词要覆盖全：条/题/对/处 都在本项目的 claim 散文里实际用过
        nums = [int(n) for n in re.findall(r"(\d{1,6})\s*[条题对处]", c.get("text", ""))]
        if nums and exp not in nums:
            prose_mismatch.append((c["claim_id"], f"散文{nums}", f"expected={exp}"))
    check("claims-散文提到计数时至少一个要等于expected",
          not prose_mismatch,
          f"不一致：{prose_mismatch}" if prose_mismatch else "")

    # N-8：expected 缺失的主张永远不可能判失败，必须显式标为 record_only，
    # 否则它在"72 条主张全过"里充数，却对任何回归都免疫。
    silent = [c["claim_id"] for c in claims
              if c.get("expected") is None
              and c.get("check") not in ("bound", "ratio")
              and c.get("kind") != "record_only"]
    check("claims-无expected的主张必须是bound/ratio或显式标record_only",
          not silent, f"永不失败的主张：{silent}" if silent else "")

    # ── ③ 评测集语义级校验（R3-20）───────────────────────────────────
    # 实测：注入 doc_id=9999 的越界题 + 同步更新 frozen.sha256 → 54/54 全绿。
    # 整套体系对评测集**只有哈希级保护**，而 v1.0.0 整批作废的根因恰恰是
    # doc_id 错位——哈希只能证明"没被改过"，证明不了"内容本身合法"。
    eval_set = os.path.join(RAG, "评测集", "eval_set.jsonl")
    kdir = os.environ.get("RAG_KNOWLEDGE_DIR") or os.path.join(RAG, "知识检索")
    corpus_n = 0
    for fn in ("cases.jsonl", "workflows.jsonl", "templates.jsonl"):
        p = os.path.join(kdir, fn)
        if os.path.exists(p):
            corpus_n += sum(1 for x in open(p, encoding="utf-8") if x.strip())
    check("评测集-能确定语料条数(为0说明语料缺失，无法做语义校验)", corpus_n > 0,
          f"语料 {corpus_n} 条")
    if corpus_n and os.path.exists(eval_set):
        bad = []
        for line in open(eval_set, encoding="utf-8"):
            if not line.strip():
                continue
            q = json.loads(line)
            did = q.get("doc_id")
            if not isinstance(did, int) or not (0 <= did < corpus_n):
                bad.append((q.get("question_id"), did))
        check(f"评测集-全部doc_id落在语料区间[0,{corpus_n})内(哈希查不出越界)",
              not bad, f"越界：{bad[:5]}" if bad else "")


# ═════════════════════════════════════════════════════ 7. verify_reports.py 能真正失败（R2-05）

def test_verify_reports_can_fail():
    print("\n7. verify_reports.py 自我描述性统计能真正判定失败（不是永远return 0）")
    print("   （独立评审 R2-05 指出：旧版永远 return 0，改错7处数字它也只是从")
    print("    \"找到匹配\"移进\"待人工确认\"，退出码依旧是0——必须证明它现在")
    print("    真的能给出确定性的失败判定，不只是\"能跑\"）")

    # 🔴 第三轮独立复核 R3-34：本用例原先是**就地篡改交付文件**
    # （可复现性说明.md 备份→改坏→还原），进程若在中间被杀，交付物就永久
    # 停在"合计 999 条主张"这个测试用的损坏状态上。R3-34 改成了"新建一个
    # 临时 markdown、finally 删掉"。
    #
    # 🔴 第四轮独立复核 R4-18：那个"临时 markdown"仍然建在**交付目录里**
    # （`可复现性体系/_R3TEST_selfdescribe_probe.md`）。`finally` 不是保护——
    # 进程被 kill -9、断电、或用例里再抛一次异常，探针就留在交付树里，而它
    # 长得跟真报告一模一样（`合计 999 条主张`），下一次谁跑 verify_reports
    # 都会被它污染。**判据：测试不得往交付目录写任何字节。**
    #
    # 改法：把整棵树复制进临时目录，探针写进副本、verify_reports 也在副本里
    # 跑。verify_reports.py 的扫描根 `RAG` 是从它自己的 `__file__` 推出来的，
    # 所以副本里的那份天然只看副本——不需要给它加任何环境变量开关（给生产
    # 代码加一个"仅测试用"的路径开关，等于为了测试在生产代码上开一个口子）。
    # 代价是一次 55MB 的复制（实测几秒），换来交付目录**全程零写入**。
    with tempfile.TemporaryDirectory(prefix="vr_probe_") as tmp:
        root = os.path.join(tmp, "RAG")
        shutil.copytree(RAG, root, symlinks=False, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "knowledge_store_numpy"))
        probe = os.path.join(root, "可复现性体系", "_R4TEST_selfdescribe_probe.md")
        with open(probe, "w", encoding="utf-8", newline="\n") as f:
            f.write("# 临时探针文件（test_detectors.py 用例7生成，只存在于临时副本）\n\n"
                    "合计 999 条主张：通过 999、不符 0、跳过 0\n")
        # VERIFY_REPORTS_NO_RECURSE：verify_reports.py 的自我描述性
        # 统计检查里有一步会反过来跑 test_detectors.py——不掐断这条
        # 链路会导致 test_detectors.py → verify_reports.py →
        # test_detectors.py 无限递归（本地实测复现过一次）。
        env = {**os.environ, "VERIFY_REPORTS_NO_RECURSE": "1"}
        sandbox_base = os.path.join(root, "可复现性体系")
        result = subprocess.run(
            [sys.executable, os.path.join(sandbox_base, "verify_reports.py")],
            cwd=sandbox_base, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
        check("verify_reports-自我描述数字对不上时返回码必须非零",
              result.returncode != 0, f"returncode={result.returncode}")
        check("verify_reports-输出里能看到文档写的值与实测值",
              "999" in result.stdout)
    # R4-18 的自证：交付目录里不该留下任何探针（新名旧名都查）
    check("verify_reports-探针文件不得落在交付目录",
          not any(os.path.exists(os.path.join(RAG, "可复现性体系", n))
                  for n in ("_R3TEST_selfdescribe_probe.md",
                            "_R4TEST_selfdescribe_probe.md")))


# ═══════════════════════════════ 8. R3-35 三处覆盖空洞

def test_manifest_detector():
    """🔴 第三轮独立复核 R3-35：`generate_manifest.py` **零覆盖**——一个
    "核对交付物有没有被改动"的工具，自己从没被验证过"改动了它真的会报"。
    R3-36 刚给它扩了面（records/ → 加 claims.json 与关键脚本）并接进了
    一键复现，扩面之后更不能没有阴性验证。

    三条要钉死的行为，对应它三种判定强度：
      ① 证据文件（records/*.json）被改 → **必须失败**
      ② 脚本被改 → **警告而非失败**（开发期改脚本是正常的，判失败会让人
         麻木地重新生成清单，反而失去"记录可能过期"这个信号）
      ③ 旁边模块目录的脚本不存在 → **不算失败**（只拿到 可复现性体系/
         一个文件夹的场景，同 R3-31 口径）
    """
    print("\n8. generate_manifest.py 的检测能力（R3-35：此前零覆盖）")

    tmp = tempfile.mkdtemp(prefix="r3test_manifest_")
    try:
        work = os.path.join(tmp, "可复现性体系")
        shutil.copytree(BASE, work, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc"))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

        def run_check():
            return subprocess.run(
                [sys.executable, os.path.join(work, "generate_manifest.py"),
                 "--check"],
                cwd=work, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120)

        # 先在这份拷贝上重新生成一次清单，作为干净基线
        subprocess.run(
            [sys.executable, os.path.join(work, "generate_manifest.py")],
            cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)

        base_r = run_check()
        check("MANIFEST-干净拷贝上核对必须通过（阳性对照）",
              base_r.returncode == 0, f"returncode={base_r.returncode}")
        # ③ 旁边目录脚本在这份孤立拷贝里本就不存在，上面这条通过即证明
        #    可选条目缺失没有被判成失败
        check("MANIFEST-旁边模块脚本缺失不判失败（孤立文件夹场景）",
              base_r.returncode == 0 and "可选条目" in base_r.stdout,
              "干净拷贝里 评测集/ 等目录不存在")

        # ① 改一条记录 → 必须失败
        rec = os.path.join(work, "records", "M-101.json")
        with open(rec, encoding="utf-8") as f:
            data = json.load(f)
        first_metric = next(iter(data["metrics"]))
        data["metrics"][first_metric]["value"] = 424242
        with open(rec, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r1 = run_check()
        check("MANIFEST-证据文件被改动必须判失败",
              r1.returncode != 0 and "M-101.json" in r1.stdout,
              f"returncode={r1.returncode}")
        shutil.copy(os.path.join(BASE, "records", "M-101.json"), rec)

        # 改 claims.json（R3-36 新纳入的证据类条目）→ 必须失败
        cj = os.path.join(work, "claims.json")
        with open(cj, "a", encoding="utf-8") as f:
            f.write("\n")
        r2 = run_check()
        check("MANIFEST-claims.json 被改动必须判失败（R3-36 新纳入）",
              r2.returncode != 0 and "claims.json" in r2.stdout,
              f"returncode={r2.returncode}")
        shutil.copy(os.path.join(BASE, "claims.json"), cj)

        # ② 改脚本 → 警告但不失败
        sc = os.path.join(work, "measure.py")
        with open(sc, "a", encoding="utf-8") as f:
            f.write("\n# R3-35 测试用注释\n")
        r3 = run_check()
        check("MANIFEST-脚本被改动只告警不判失败",
              r3.returncode == 0 and "measure.py" in r3.stdout
              and "⚠" in r3.stdout, f"returncode={r3.returncode}")
        check("MANIFEST-脚本告警必须说清后果（记录可能过期）",
              "过期" in r3.stdout or "上一版代码" in r3.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_reports_body_branch():
    """🔴 第三轮独立复核 R3-35：`verify_reports.py` **只覆盖了自我描述那一支**，
    正文匹配那一支（复核实验 C/D1/D3 打穿的恰恰是它）一条用例都没有。

    这一组既验证它**能**做到的，也把它**做不到**的那条边界钉死——后者更重要：
    正文匹配是子串匹配，"数字写错了但错值恰好在文中别处出现过"它抓不到。
    这个局限如果不写成用例，下次就会有人拿"正文核对全绿"当"报告数字全对"用。
    """
    print("\n9. verify_reports.py 正文匹配支的检测能力与边界（R3-35：此前零覆盖）")

    import verify_reports as vr

    fake_report = os.path.join(RAG, "可复现性体系", "可复现性说明.md")
    rel = os.path.relpath(fake_report, RAG).replace(os.sep, "/")
    claims = [
        {"claim_id": "T-1", "text": "数字印在正文里", "check": "exact",
         "expected": 12345, "report": rel},
        {"claim_id": "T-2", "text": "数字完全没出现", "check": "exact",
         "expected": 98765, "report": rel},
    ]

    def read_stub(_path):
        return "本文档写着 12345 这个数字，另外还提到过 55555。"

    found, unmatched, no_report, _n = vr.match_claims_to_reports(
        claims, {}, read_report=read_stub)
    check("verify_reports正文支-数字印在正文里→算命中",
          found == 1, f"found={found}")
    check("verify_reports正文支-数字完全没出现→进待确认清单不被算成命中",
          len(unmatched) == 1 and unmatched[0][0]["claim_id"] == "T-2",
          f"unmatched={[c['claim_id'] for c, *_ in unmatched]}")

    # 边界：写错的数字若恰好在别处出现过，子串匹配抓不到——如实钉死
    claims_blind = [{"claim_id": "T-3", "text": "写错但值在别处出现",
                     "check": "exact", "expected": 55555, "report": rel}]
    found_b, unmatched_b, _nr, _n = vr.match_claims_to_reports(
        claims_blind, {}, read_report=read_stub)
    check("verify_reports正文支-已知盲区：错值恰在文中别处出现时抓不到"
          "（用例钉死局限，不是要求它能抓）",
          found_b == 1 and not unmatched_b,
          "所以正文全绿≠报告数字全对，确定性判定看自我描述支")

    # 报告文件定位不到时必须进 no_report，不能悄悄算成命中
    claims_missing = [{"claim_id": "T-4", "text": "报告文件不存在",
                       "check": "exact", "expected": 1,
                       "report": "根本不存在的报告_R3TEST.md"}]
    f4, u4, nr4, _n = vr.match_claims_to_reports(claims_missing, {})
    check("verify_reports正文支-定位不到报告文件→进no_report不算命中",
          f4 == 0 and not u4 and len(nr4) == 1)


def test_m114_m115_selfchecks():
    """🔴 第三轮独立复核 R3-35：M-114/M-115 **零覆盖**——第二轮新增的两个
    测量，记录进了 claims、数字进了报告，却没有任何用例验证它们内部的
    自洽关系。这两条关系都是不需要重跑就能从交付记录里直接核的：

      · M-114 的 N=95 一行**不作子采样**，理论上必须与 M-111 的真实召回率
        逐位吻合。这是脚本自己声明的"实现正确性内部校验"——声明了却没人
        断言，等于没有。
      · M-115 的 Wilson 区间必须**包含点估计**且落在 [0,1] 内。区间算错
        （比如 z 值或分母写错）最典型的症状就是这两条之一被破坏。
    """
    print("\n10. M-114/M-115 记录内部自洽关系（R3-35：此前零覆盖）")

    from measure import load_all_records
    records = load_all_records()

    # ★ R4-15：记录缺失时旧代码只记 1 行、正常时记 3 行——「合计 N 项」
    #   因此随记录是否齐全浮动（复核实测 96→94）。改成无论如何都记满
    #   1+3 行：记录缺失时那 3 行走 unavailable()，显式红且计入总数。
    m114 = records.get("M-114")
    m111 = records.get("M-111")
    check("M-114/M-111 记录都存在", bool(m114) and bool(m111),
          f"m114={bool(m114)} m111={bool(m111)}")
    # 全量档的语料条数不写死——它随语料变（v1.4.0 从 95 变成 86，写死的
    # `corpus_95_*` 当场取不到值，用例报 None）。从 M-111 的 doc_count 取，
    # 那是"这次实际用了多少条语料"的唯一权威来源。
    full_n = (m111 or {}).get("metrics", {}).get("doc_count", {}).get("value")
    for k in (1, 5, 10):
        name = (f"M-114 的全量档（N={full_n}）与 M-111 实测 R@{k} 逐位吻合"
                f"（脚本自称的内部校验）")
        if not m114 or not m111:
            unavailable(name, "M-114/M-111 记录缺失")
            continue
        if full_n is None:
            unavailable(name, "M-111 没有 doc_count，无法确定全量档是哪一行")
            continue
        sub = m114["metrics"].get(f"corpus_{int(full_n)}_recall_at_{k}",
                                 {}).get("value")
        real = m111["metrics"].get(f"recall_at_{k}", {}).get("value")
        check(name,
              sub is not None and real is not None and abs(sub - real) < 1e-9,
              f"M-114={sub} M-111={real}")

    m115 = records.get("M-115")
    check("M-115 记录存在", bool(m115))
    if not m115:
        for name in ("M-115 里能找到成套的 hits/n/ci_lo/ci_hi 指标"
                     "（0 组会让下面两条空集恒真，故先断言组数）",
                     "M-115 每组 Wilson 区间都包含 hits/n 现算的点估计",
                     "M-115 每组区间都落在 [0,1] 且下界≤上界"):
            unavailable(name, "M-115 记录缺失")
    else:
        # 指标名形如 <组名>_r<k>_{hits,n,ci_lo,ci_hi}；点估计不落盘，
        # 由 hits/n 现算——这正好让下面的"区间包含点估计"成为一条真断言，
        # 而不是把记录里两个字段拿来自己比自己。
        vals = {k: v["value"] for k, v in m115["metrics"].items()}
        groups = {}
        for name in vals:
            for suffix in ("_hits", "_n", "_ci_lo", "_ci_hi"):
                if name.endswith(suffix):
                    groups.setdefault(name[: -len(suffix)], set()).add(suffix)
        complete = sorted(g for g, s in groups.items() if len(s) == 4)
        # ⚠️ 组数为 0 时下面两条会**空集恒真**地"通过"——这正是本文件开篇
        # 列的那种"永不报错的检验器"。所以先把组数本身断言死：初版我按
        # `_point/_ci_low/_ci_high` 猜的命名，实际是 `_hits/_n/_ci_lo/_ci_hi`，
        # 当场就是 0 组，而两条区间检查照样打绿。没有这条断言就发现不了。
        check("M-115 里能找到成套的 hits/n/ci_lo/ci_hi 指标"
              "（0 组会让下面两条空集恒真，故先断言组数）",
              len(complete) >= 3, f"成套 {len(complete)} 组")
        bad_contain, bad_range = [], []
        for g in complete:
            lo, hi = vals[f"{g}_ci_lo"], vals[f"{g}_ci_hi"]
            n = vals[f"{g}_n"]
            pt = vals[f"{g}_hits"] / n if n else None
            if pt is None or not (lo - 1e-9 <= pt <= hi + 1e-9):
                bad_contain.append(f"{g}(pt={pt},[{lo},{hi}])")
            if not (0.0 <= lo <= hi <= 1.0):
                bad_range.append(f"{g}([{lo},{hi}])")
        check("M-115 每组 Wilson 区间都包含 hits/n 现算的点估计",
              complete and not bad_contain, f"不包含的组：{bad_contain}")
        check("M-115 每组区间都落在 [0,1] 且下界≤上界",
              complete and not bad_range, f"越界的组：{bad_range}")


def test_low_value_penalty_not_noop():
    """🔴 第三轮独立复核 R3-26：低价值流程降权在**生产索引上一次都没生效过**。

    R2-21 把判定结果写进了建索引时的 meta，可那之后索引再没重建过（重建需要
    麒麟环境的 embedding SDK），生产索引的 meta 里压根没有 `low_value` 这个键，
    `meta.get("low_value")` 恒为 None，整段降权是 no-op。**代码看起来接线了、
    实际是死的**——和 R2-21 修复前"函数定义了但零调用"是同一个病换了个位置。

    已改成检索时按 `meta["raw"]` 实时判定。这组用例钉的就是"实时"这一点：
    构造一条 meta **只有 raw、没有 low_value 字段**的旧格式索引条目
    （即生产索引的真实形态），断言它照样会被降权。

    ═══ 🔴 第四轮独立复核 R4-03：本组用例上一版是**假通过通道** ═══════════

    上一版在测试内部**复刻**了一份生产判定分支（`def is_low(meta)`），只用
    一句 `"is_low_value_workflow(raw)" in src` 的字符串 grep 当护栏。复核实测：
    把 `search_knowledge.py` 里真实的实时降权分支杀掉、但保留那句注释字面量，
    **96/96 全绿、rc=0**——因为用例测的自始至终是它自己复刻的那一份。

    作者当时在注释里写了"否则本组用例就成了自己测自己"，**意识到了风险，
    却选了一个能被一句字符串绕开的护栏**。这比没意识到更值得记一笔。

    本版改法：把生产分支从 `search()` 中间抽成模块级的
    `is_low_value_meta()` / `apply_low_value_penalty()`（这是 R4-03 允许的
    那一处必要改动），用例**直接 import 调用真函数**。复刻的那份删掉，
    字符串 grep 护栏一并删掉——真调用之后它已无存在价值，留着反而误导。
    """
    print("\n12. 低价值流程降权不是 no-op（R3-26 / R4-03 改为直接调真实函数）")

    sys.path.insert(0, os.path.join(RAG, "知识检索"))
    import render_units
    import search_knowledge          # ★ 就是生产检索用的那个模块

    # 造一条判据下确定属于"低价值"的流程单元
    low = {"intent": "好了吗", "tool_sequence": [{"tool_name": "Bash"}]}
    rich = {"intent": "在磐石不可变系统上安装 kysdk 的 embedding 开发包并验证维度",
            "tool_sequence": [{"tool_name": "Bash"}, {"tool_name": "Read"},
                              {"tool_name": "Write"}]}
    check("低价值降权-判据本身能区分信息量不足与充足的流程",
          render_units.is_low_value_workflow(low)
          and not render_units.is_low_value_workflow(rich),
          f"low={render_units.is_low_value_workflow(low)} "
          f"rich={render_units.is_low_value_workflow(rich)}")

    # 模拟生产索引的真实形态：meta 里有 type/raw，**没有** low_value 键
    legacy_meta = {"type": "workflow", "raw": low}
    check("低价值降权-生产索引的 meta 里确实没有 low_value 键（问题前提成立）",
          "low_value" not in legacy_meta)

    check("低价值降权-老格式 meta（无 low_value 键）仍能被判为低价值",
          search_knowledge.is_low_value_meta(legacy_meta),
          "这一条就是 R3-26：改之前这里恒为 False，降权从未生效")
    check("低价值降权-信息量充足的流程不被误降权",
          not search_knowledge.is_low_value_meta({"type": "workflow", "raw": rich}))
    check("低价值降权-非流程类型（案例/模板）不参与降权",
          not search_knowledge.is_low_value_meta({"type": "case", "raw": low}))

    # ★ 只断言"判定函数返回 True"还不够——R3-26 的病是**判出来了但没接线**。
    #   所以再走一遍真正会改变检索结果的那个函数：喂一个最小 DocStore 桩，
    #   低价值那条初始分更高，降权后必须掉到后面、且分数确实乘过系数。
    #   把降权那行删掉 → 顺序不变、分数不变 → 这两条立刻红。
    class _StubDocs:
        _META = {"w-low": {"type": "workflow", "raw": low},
                 "w-rich": {"type": "workflow", "raw": rich}}

        def get(self, did):
            return {"meta": self._META.get(did)} if did in self._META else None

    ranked = search_knowledge.apply_low_value_penalty(
        [("w-low", 1.0), ("w-rich", 0.9)], _StubDocs())
    order = [d for d, _ in ranked]
    scores = dict(ranked)
    check("低价值降权-真的改变了检索排序（低价值条目被压到信息量充足条目之后）",
          order == ["w-rich", "w-low"], f"排序={order}")
    check("低价值降权-低价值条目的分数确实乘过降权系数，充足条目分数不变",
          abs(scores["w-low"] - 1.0 * search_knowledge.LOW_VALUE_PENALTY) < 1e-9
          and abs(scores["w-rich"] - 0.9) < 1e-9,
          f"分数={scores}")

    # ★ 接线断言：search() 必须真的调用上面这个函数，而不是自己另写一份。
    #   这仍然是字符串检查，但性质与上一版**相反**：上一版是拿字符串
    #   代替真调用（可以只留注释骗过去），这里是在真调用之外，额外确认
    #   生产入口没有绕开它——两条一起才封死"函数对但没人用"这个口子。
    src = open(os.path.join(RAG, "知识检索", "search_knowledge.py"),
               encoding="utf-8").read()
    check("低价值降权-search() 里确实接线调用了 apply_low_value_penalty()",
          "raw_hits = apply_low_value_penalty(raw_hits, docs)" in src,
          "函数写对了但 search() 不调用，就是 R3-26 那个病换个位置")


# ═════════════════════════════════════════════════════ 主流程

def test_l1_missing_script_is_fatal():
    """🔴 第四轮独立复核 R4-17：删掉一个 L1 测量**脚本文件**，`reproduce_L1.py`
    只报一行黄字"跳过"，重算项静默减少，而**输出与干净基线一模一样**——
    本地实测修复前：删掉 measure_L1.py 后仍是「通过 71、不符 1」，
    重算项从 58 降到 22，读者无从分辨这份结果是不是完整跑出来的。

    这是本项目栽过很多次的同一类：**证据缺失被当成「跳过检查」而不是
    「检查失败」**（同 R2-11 的三处绕过口、R3-00 的记录消失）。

    但不能一刀切判失败：R3-31 认定「只拿到 可复现性体系/ 一个文件夹」是
    合法场景。区分判据是**目录在不在**，不是模块在不在：

      ① 本目录的脚本被删（目录在）        → 必须失败
      ② 旁目录的脚本被删（旁目录在）      → 必须失败
      ③ 整个旁目录不在（部分交付）        → 允许跳过，不判失败

    三条路径都要验，只验①会让②③的行为无人看管——这正是 R4 元诊断
    「新写的检查器无人检查」要防的事。
    """
    print("\n13. R4-17：L1 测量脚本缺失必须进退出码（不能只报黄字）")

    def _probe(mutate):
        """把整棵树复制进临时目录后施加变异，交付目录全程零写入（同 R4-18）。"""
        with tempfile.TemporaryDirectory(prefix="r4_17_") as tmp:
            root = os.path.join(tmp, "RAG")
            shutil.copytree(RAG, root, symlinks=False,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "*.pyc", "knowledge_store_numpy"))
            mutate(root)
            sb = os.path.join(root, "可复现性体系")
            r = subprocess.run(
                # ⚠️ 不能加 --check-only：那条路径根本不 import 测量模块，
                # 四种变异全都测了个空，而**空变异基线照样 PASS**——本用例
                # 初版就是这么写的，是一个活的"恒假检查器"标本。
                [sys.executable, os.path.join(sb, "reproduce_L1.py")],
                cwd=sb, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600)
            return r.stdout + r.stderr

    MARK = "L1-MISSING-SCRIPT"

    out = _probe(lambda root: os.remove(
        os.path.join(root, "可复现性体系", "measure_L1.py")))
    check("R4-17-删本目录的 measure_L1.py 必须报缺失（不是黄字跳过）",
          MARK in out, "输出中未出现 L1-MISSING-SCRIPT" if MARK not in out else "")
    check("R4-17-诊断要说清是文件不见了，而不是笼统说 import 失败",
          "脚本文件不见了" in out)
    # 被它连累的旁目录脚本必须**另一种措辞**——否则读者会去找两个没丢的文件
    check("R4-17-被连累的旁目录脚本不得误报成「文件不见了」",
          "文件在位，import 失败" in out)

    out = _probe(lambda root: os.remove(
        os.path.join(root, "评测集", "measure_recall_ci.py")))
    check("R4-17-删旁目录的脚本（该目录仍在）同样必须报缺失",
          MARK in out, "输出中未出现 L1-MISSING-SCRIPT" if MARK not in out else "")

    out = _probe(lambda root: shutil.rmtree(os.path.join(root, "评测集")))
    check("R4-17-整个旁目录不在时不得判失败（R3-31 的合法部分交付场景）",
          MARK not in out and "模块不在这台机器上" in out)

    # ★ 空变异基线：不injectanything，必须一条缺失都不报。基线不干净意味着
    #   这条判据混进了与"脚本在不在"无关的变量，上面三条就都不作数。
    out = _probe(lambda root: None)
    check("R4-17-空变异基线：未删任何脚本时不得报缺失",
          MARK not in out, "干净副本上误报了脚本缺失" if MARK in out else "")


def test_conflict_detector_negatives():
    """15. 知识冲突处理（`mine_conflicts.py` 必须拦住这四类假冲突 + 回溯必须报错）

    照 `test_mine_cases_k1()` 的模式：直接 import 真实模块、手造最小反例
    记录（字段与 memory_source_records.jsonl 真实 schema 一致）、调真实
    函数、`check()` 断言"不该产出的没产出"。

    ═══ 权限拦截这一条为什么测的是 case_total，不是 rejected_by_permission ═══

    `mine_conflicts.py` 里 `mine_case_conflicts()` 对每条 case 的
    `error_text` 有一道防御性 PERMISSION_PATTERNS 复查（设计方案要求：
    "必须先套 PERMISSION_PATTERNS 过滤，否则重蹈第一轮覆辙"）。但它 import
    的 `PERMISSION_PATTERNS` 与 `mine_cases.classify_error()` 用的是**同一个
    编译后的正则对象**——任何匹配它的 error_text，在 `mine_cases.mine()`
    内部就已经被分类成 "permission" 而根本不会产出 case，走不到
    `mine_conflicts.py` 这道复查。也就是说 `rejected_by_permission` 计数器
    在当前架构下**不可能被真实数据触发**（防御性代码，防的是"以后两处
    正则不小心分叉"这种假设情况，不是当前会发生的情况）。断言这个计数器
    > 0 会是一条只能靠"更精心构造反例、绕开上游过滤"才能点亮、平时形同虚设
    的检查——本项目自己反复批判的"检验器自己没被检验"正是这类东西。
    因此这里测的是**端到端能观察到的保证**：权限拦截文本不产出 case 类
    冲突候选（`case_total==0`，因为 mine_cases.mine() 那层就把它挡掉了），
    这是用户/评委真正关心的行为，不是内部某个计数器的数值。
    """
    print("\n15. 知识冲突处理（mine_conflicts.py 必须拦住这四类假冲突、回溯查不到必须报错、"
          "检索侧supersede过滤逻辑正确）")
    import mine_conflicts

    def _positive_count(conflicts):
        return sum(1 for c in conflicts if c.get("structural_label") == "candidate"
                   or c.get("kind") == "case")

    # ① 只是追加的修订：新文本完整包含旧文本，不算冲突
    recs_append = [{
        "record_type": "tool_execution", "session_id": "c1",
        "timestamp": "2026-01-01T00:00:00+00:00", "tool_name": "Edit",
        "tool_input": {"file_path": "/home/x/memory/a.md",
                       "old_string": "服务在 8080",
                       "new_string": "服务在 8080\n\n补充：日志在 /var/log"},
        "tool_output": "updated successfully", "is_error": False,
    }]
    conflicts, stats = mine_conflicts.mine(recs_append)
    check("冲突-纯追加不算冲突",
          _positive_count(conflicts) == 0 and stats["rejected_by_append_only"] >= 1,
          f"positive={_positive_count(conflicts)} rejected={stats['rejected_by_append_only']}")

    # ② 同一次"坐下来写"（同会话且同一天）内的连续修订：不算冲突
    recs_same_sitting = [
        {"record_type": "tool_execution", "session_id": "c2",
         "timestamp": "2026-01-01T00:00:00+00:00", "tool_name": "Write",
         "tool_input": {"file_path": "/home/x/memory/b.md", "content": "初始状态：X"},
         "tool_output": "created", "is_error": False},
        {"record_type": "tool_execution", "session_id": "c2",
         "timestamp": "2026-01-01T00:05:00+00:00", "tool_name": "Edit",
         "tool_input": {"file_path": "/home/x/memory/b.md",
                        "old_string": "初始状态：X", "new_string": "最终状态：Y（推翻了X）"},
         "tool_output": "updated", "is_error": False},
    ]
    conflicts2, stats2 = mine_conflicts.mine(recs_same_sitting)
    check("冲突-同会话同一天的连续修订不算冲突",
          _positive_count(conflicts2) == 0 and stats2["rejected_by_same_session"] >= 1,
          f"positive={_positive_count(conflicts2)} rejected={stats2['rejected_by_same_session']}")

    # ③ 权限拦截文本不产出 case 类冲突候选（见上方docstring：这是端到端
    #    保证，不是某个内部计数器）
    recs_permission = [
        {"record_type": "tool_execution", "session_id": "s1", "tool_name": "Bash",
         "timestamp": "2026-01-01T00:00:00+00:00",
         "tool_input": {"command": "rm -rf /important"},
         "tool_output": "The user doesn't want to proceed with this tool use.",
         "is_error": True},
        {"record_type": "tool_execution", "session_id": "s1", "tool_name": "Bash",
         "timestamp": "2026-01-01T00:00:05+00:00",
         "tool_input": {"command": "ls /important"},
         "tool_output": "file1 file2", "is_error": False},
        {"record_type": "tool_execution", "session_id": "s2", "tool_name": "Bash",
         "timestamp": "2026-01-02T00:00:00+00:00",
         "tool_input": {"command": "rm -rf /important"},
         "tool_output": "The user doesn't want to proceed with this tool use.",
         "is_error": True},
        {"record_type": "tool_execution", "session_id": "s2", "tool_name": "Bash",
         "timestamp": "2026-01-02T00:00:05+00:00",
         "tool_input": {"command": "chmod +w /important && rm -rf /important"},
         "tool_output": "done", "is_error": False},
    ]
    _, stats3 = mine_conflicts.mine(recs_permission)
    check("冲突-权限拦截文本不产出case类冲突候选",
          stats3["case_total"] == 0 and stats3["case_conflict_candidates"] == 0,
          f"case_total={stats3['case_total']} case_conflict_candidates={stats3['case_conflict_candidates']}")

    # ④ 时间戳对调，adjudicate() 的裁决结果必须跟着反过来——不能信 old/new
    #    这两个键的字面标签，只能信时间戳，否则时间序反转测不出任何东西
    pair = {"kind": "doc_revision",
            "old": {"stable_key": "a", "timestamp": "2026-01-01T00:00:00Z", "text": "旧结论"},
            "new": {"stable_key": "b", "timestamp": "2026-01-02T00:00:00Z", "text": "新结论"}}
    pair_swapped = {"kind": "doc_revision",
                    "old": {"stable_key": "a", "timestamp": "2026-01-02T00:00:00Z", "text": "旧结论"},
                    "new": {"stable_key": "b", "timestamp": "2026-01-01T00:00:00Z", "text": "新结论"}}
    v1 = mine_conflicts.adjudicate(pair)
    v2 = mine_conflicts.adjudicate(pair_swapped)
    check("冲突-时间序反转则裁决反转",
          v1["verdict"] != v2["verdict"],
          f"v1={v1['verdict']} v2={v2['verdict']}")

    # ⑤ 旧版本不在索引里（被删/从未挖掘到），回溯必须报错，不能静默返回 None
    raised = False
    try:
        mine_conflicts.retrieve_old("this-stable-key-does-not-exist-in-any-index",
                                    index={})
    except KeyError:
        raised = True
    check("冲突-旧版本缺失必须报错而不是静默返回None", raised)

    # ⑥/⑦ 检索侧集成：supersedes/superseded_by 过滤——默认挡掉已被取代的
    # 旧知识，include_superseded=True 时连同旧版本一起放行（"回溯"接口）。
    # 真实索引里目前没有 case 类冲突可用（S3 矿脉当前是空的，见
    # mine_conflicts.build_supersede_edges 的文档字符串），这里用合成边表
    # 直接测 apply_supersede_filter() 本身的过滤逻辑是否正确，不依赖真实
    # 索引数据是否凑巧覆盖到这条路径——同 apply_low_value_penalty 的测法
    # （喂最小桩对象，测的是真实函数）。
    import search_knowledge

    class _FakeDocs:
        def __init__(self, d):
            self.d = d

        def get(self, did):
            return self.d.get(did)

    fake_docs = _FakeDocs({
        1: {"meta": {"type": "case", "raw": {"session_id": "sA", "failed_at": "T1", "tool_name": "Bash"}}},
        2: {"meta": {"type": "case", "raw": {"session_id": "sB", "failed_at": "T2", "tool_name": "Bash"}}},
    })
    fake_edges = {"case:sA": {"supersedes": [], "superseded_by": ["case:sB"]},
                 "case:sB": {"supersedes": ["case:sA"], "superseded_by": []}}
    orig_stable_key = search_knowledge._stable_key
    search_knowledge._stable_key = lambda raw, kind: "case:" + raw["session_id"]
    try:
        hits = [(1, 0.9), (2, 0.8)]
        default = search_knowledge.apply_supersede_filter(hits, fake_docs, fake_edges)
        check("冲突-检索默认过滤掉已被取代的旧知识",
              default == [(2, 0.8)], f"default={default}")
        with_old = search_knowledge.apply_supersede_filter(
            hits, fake_docs, fake_edges, include_superseded=True)
        check("冲突-include_superseded=True时旧知识仍可回溯",
              with_old == hits, f"with_old={with_old}")
    finally:
        search_knowledge._stable_key = orig_stable_key


def test_preference_extractor_negatives():
    """16. 偏好提取（`mine_preferences.py` 必须拦住这三类假偏好 + 切分不得泄漏）

    写法照 test_mine_cases_k1()：import 真实模块 + 手造最小反例 + 调真实
    函数 + check() 双重断言（"不该产出的没产出"且"对应 stats 计数器动了"）。

    ⚠️ 第⑤条与偏好提取-设计方案.md §七原始草稿不同，是实测后**有意调整**
    的：原草稿设想"打乱时间顺序后准确率必须掉到基线水平"，实测发现这个
    假设对本模块不成立——operation_habit 是真实稳定的习惯（同一情境下的
    工具选择贯穿全部 41 天，不随时间漂移），随机打乱训练/测试切分后
    准确率是 94.6%，比按时间切分的 90.0% **更高**，不是更低。这恰恰符合
    "习惯"这个概念的本意：真正的习惯就应该跨时间稳定，如果打乱后准确率
    暴跌，反而说明提取到的是短期上下文效应而不是真习惯。
    照抄原设计的断言会测出一个不成立的假设，不是测出真 bug——改为验证
    设计方案 §6.2 真正要防的风险本身：**不做会话完整性切分，确实会让
    会话跨训练/测试两侧泄漏**（本数据集实测 3 个会话在朴素按时间戳切分
    下被腰斩），而 `split_by_time_keeping_sessions()` 必须把这个泄漏
    降到零。这是"防的风险是真实存在的"与"防线本身真的挡住了"两件事一起
    测，比一个不成立的准确率假设更贴近本模块真正的风险点。
    """
    print("\n16. 偏好提取（mine_preferences.py 必须拦住这三类假偏好，且切分不得泄漏会话）")
    import mine_preferences

    # ① 支持度不足：情境只出现 3 次，不得产出偏好
    recs_low_trigger = [
        {"record_type": "tool_execution", "session_id": f"s{i}",
         "timestamp": f"2026-01-0{i+1}T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "grep foo file.txt"}, "is_error": False}
        for i in range(3)
    ]
    prefs, stats = mine_preferences.extract(recs_low_trigger)
    check("偏好-低支持度不产出",
          len(prefs) == 0 and stats["rejected_by_min_trigger"] >= 1,
          f"prefs={len(prefs)} rejected={stats['rejected_by_min_trigger']}")

    # ② 单会话：情境出现够多次，但全在同一个会话里——只是一次性做法，
    #    不是习惯（mine_templates R2-22 bug③ 踩过"用出现次数冒充会话数"
    #    的坑，本模块判据同样用 set(session_id) 而不是 len(items)）
    recs_single_session = [
        {"record_type": "tool_execution", "session_id": "same-session",
         "timestamp": f"2026-01-01T00:0{i}:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "grep foo file.txt"}, "is_error": False}
        for i in range(9)
    ]
    prefs2, stats2 = mine_preferences.extract(recs_single_session)
    check("偏好-单会话不产出",
          len(prefs2) == 0 and stats2["rejected_by_min_sessions"] >= 1,
          f"prefs={len(prefs2)} rejected={stats2['rejected_by_min_sessions']}")

    # ③ 比例不足：情境下两种选择五五开，不构成倾向。用 grep/Select-String
    #    这对同属 content_search 的真实候选（不能用 cat，那属于不同 intent
    #    file_view，"跨情境各占一半"测不出"同情境内无稳定倾向"这件事）。
    recs_no_lean = []
    for i in range(10):
        cmd = "grep foo file.txt" if i % 2 == 0 else "Select-String foo file.txt"
        recs_no_lean.append({
            "record_type": "tool_execution", "session_id": f"sess{i}",
            "timestamp": f"2026-01-01T00:{i:02d}:00+00:00", "tool_name": "Bash",
            "tool_input": {"command": cmd}, "is_error": False,
        })
    prefs3, stats3 = mine_preferences.extract(recs_no_lean)
    check("偏好-无稳定倾向不产出",
          len(prefs3) == 0 and stats3["rejected_by_min_ratio"] >= 1,
          f"prefs={len(prefs3)} rejected={stats3['rejected_by_min_ratio']}")

    # ④ 切分不得切断会话——同一会话横跨两天，必须整体归入一侧
    recs_cross_day_session = [
        {"record_type": "tool_execution", "session_id": "cross-day-sess",
         "timestamp": "2026-01-01T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "grep a b"}, "is_error": False},
        {"record_type": "tool_execution", "session_id": "cross-day-sess",
         "timestamp": "2026-01-10T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "grep a b"}, "is_error": False},
    ] + [
        {"record_type": "tool_execution", "session_id": f"other{i}",
         "timestamp": f"2026-01-0{i+2}T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "cat x"}, "is_error": False}
        for i in range(6)
    ]
    tr, te = mine_preferences.split_by_time_keeping_sessions(recs_cross_day_session)
    tr_sess = set(r["session_id"] for r in tr)
    te_sess = set(r["session_id"] for r in te)
    check("偏好-跨天会话不被切断（同一session_id不得同时出现在两侧）",
          not (tr_sess & te_sess), f"overlap={tr_sess & te_sess}")

    # ④.5 切分必须真的按时间序，不是随便一种"不切断会话"的划分都算数——
    #    ④那组用例里 session_id 的字母序恰好和时间序一致（"cross-day-sess"
    #    <"other0"<...），如果把排序键从时间戳换成 session_id 本身（按id
    #    排序也不会切断任何会话，语法上仍然"合法"），④那条断言测不出来。
    #    这里刻意让 session_id 的字母序与时间序相反：final-to-run 时间上
    #    最早，字母序却排最后；按 id 排序会被误判成"最晚"、错误地分进
    #    留出期。
    recs_id_order_reversed = [
        {"record_type": "tool_execution", "session_id": "zzz-earliest",
         "timestamp": "2026-01-01T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "grep a b"}, "is_error": False},
    ] + [
        {"record_type": "tool_execution", "session_id": f"aaa-later{i}",
         "timestamp": f"2026-02-0{i+1}T00:00:00+00:00", "tool_name": "Bash",
         "tool_input": {"command": "cat x"}, "is_error": False}
        for i in range(4)
    ]
    tr2, te2 = mine_preferences.split_by_time_keeping_sessions(recs_id_order_reversed)
    check("偏好-切分按真实时间序（不是按session_id字母序）",
          "zzz-earliest" in set(r["session_id"] for r in tr2),
          f"train sessions={set(r['session_id'] for r in tr2)}")

    # ⑤ 不做会话完整性切分，会话确实会被腰斩——验证 §6.2 要防的风险是
    #    真实存在的，不是假想敌（见函数文档字符串关于为什么改这一条）
    def _naive_split_by_timestamp(records, train_ratio=0.6):
        timed = sorted([r for r in records if r.get("timestamp")],
                       key=lambda r: r["timestamp"])
        cut = int(len(timed) * train_ratio)
        cutoff = timed[cut]["timestamp"] if cut < len(timed) else "9999"
        return ([r for r in records if (r.get("timestamp") or "") < cutoff],
                [r for r in records if (r.get("timestamp") or "") >= cutoff])

    naive_tr, naive_te = _naive_split_by_timestamp(recs_cross_day_session)
    naive_overlap = (set(r["session_id"] for r in naive_tr)
                     & set(r["session_id"] for r in naive_te))
    check("偏好-朴素按时间戳切分确实会腰斩跨天会话（证明防线在防真实风险）",
          "cross-day-sess" in naive_overlap, f"naive_overlap={naive_overlap}")


def test_cmd_drift_negatives():
    """17. S1 命令输出漂移（`mine_conflicts.py` 必须拦住这五类假冲突，
    且环境探针翻转必须判 T3 而不是 T1）

    写法照 test_mine_cases_k1() / test_conflict_detector_negatives()：
    直接 import 真实模块、手造最小反例记录（字段与 memory_source_records.
    jsonl 真实 schema 一致）、调真实函数、check() 双重断言（"不该产出的
    没产出"且"对应 stats 计数器动了"——防止函数抛异常返回空列表造成假阳性）。

    ⚠️ 第④条（权限拦截）为什么把 `is_error` 写成 False：真实数据里权限
    拦截的输出几乎总是 `is_error=True`，会被 mine_command_output_drift()
    更早的那道 `if r.get("is_error"): continue` 挡掉，走不到
    PERMISSION_PATTERNS 这道防御性复查——与 test_conflict_detector_
    negatives() 里"rejected_by_permission 在当前架构下不可能被真实数据
    触发"是同一个道理（防的是"以后两处判断顺序被改乱"这类假设情况）。
    这里把 is_error 显式设成 False，专测这道防御性过滤本身有没有生效，
    不依赖它在当前调用顺序下是否可达。
    """
    print("\n17. S1 命令输出漂移（mine_conflicts.py 必须拦住这五类假冲突，"
          "环境探针翻转必须判T3而不是T1）")
    import mine_conflicts as mc

    # ① 非 Bash：Write 同一路径两次，不得当成「同一条命令跑了两次」
    #    （评审阶段的教训：82 组候选里大半是这么来的假阳性，见
    #    mine_command_output_drift() 文档字符串引用的 §四.1）
    recs_non_bash = [
        {"record_type": "tool_execution", "session_id": "d1", "tool_name": "Write",
         "timestamp": "2026-01-01T00:00:00+00:00",
         "tool_input": {"file_path": "/home/x/note.md", "content": "v1"},
         "tool_output": "File created successfully at: /home/x/note.md",
         "is_error": False, "execution_status": "success"},
        {"record_type": "tool_execution", "session_id": "d2", "tool_name": "Read",
         "timestamp": "2026-01-05T00:00:00+00:00",
         "tool_input": {"file_path": "/home/x/note.md"},
         "tool_output": "1\tv1", "is_error": False, "execution_status": "success"},
    ]
    _, stats1 = mc.mine_command_output_drift(recs_non_bash)
    check("S1-非Bash不产出",
          stats1["cmd_drift_candidates"] == 0 and stats1["rejected_by_non_bash"] >= 1,
          f"candidates={stats1['cmd_drift_candidates']} rejected={stats1['rejected_by_non_bash']}")

    # ② 同一天内的重复执行，不算知识过期
    recs_same_day = [
        {"record_type": "tool_execution", "session_id": "d3", "tool_name": "Bash",
         "timestamp": "2026-01-01T09:00:00+00:00", "tool_input": {"command": "df -m /"},
         "tool_output": "avail 1000", "is_error": False, "execution_status": "success"},
        {"record_type": "tool_execution", "session_id": "d4", "tool_name": "Bash",
         "timestamp": "2026-01-01T18:00:00+00:00", "tool_input": {"command": "df -m /"},
         "tool_output": "avail 500", "is_error": False, "execution_status": "success"},
    ]
    _, stats2 = mc.mine_command_output_drift(recs_same_day)
    check("S1-同日重复不产出",
          stats2["cmd_drift_candidates"] == 0 and stats2["rejected_by_same_day"] >= 1,
          f"candidates={stats2['cmd_drift_candidates']} rejected={stats2['rejected_by_same_day']}")

    # ③ 跨天但同会话（长会话可跨23天，实测 session_id=1cd1ff9f 横跨23天），
    #    是同一次工作的连续动作，不是知识过期
    recs_same_session = [
        {"record_type": "tool_execution", "session_id": "long-session", "tool_name": "Bash",
         "timestamp": "2026-01-01T09:00:00+00:00",
         "tool_input": {"command": "wc -l workspace/logs/*.log"},
         "tool_output": "1000 total", "is_error": False, "execution_status": "success"},
        {"record_type": "tool_execution", "session_id": "long-session", "tool_name": "Bash",
         "timestamp": "2026-01-20T09:00:00+00:00",
         "tool_input": {"command": "wc -l workspace/logs/*.log"},
         "tool_output": "5000 total", "is_error": False, "execution_status": "success"},
    ]
    _, stats3 = mc.mine_command_output_drift(recs_same_session)
    check("S1-同会话跨天不产出",
          stats3["cmd_drift_candidates"] == 0 and stats3["rejected_by_same_session"] >= 1,
          f"candidates={stats3['cmd_drift_candidates']} rejected={stats3['rejected_by_same_session']}")

    # ④ 权限拦截输出跨天变化，不是知识冲突（见函数文档字符串关于 is_error 的说明）
    recs_permission = [
        {"record_type": "tool_execution", "session_id": "d5", "tool_name": "Bash",
         "timestamp": "2026-01-01T00:00:00+00:00", "tool_input": {"command": "rm -rf /important"},
         "tool_output": "The user doesn't want to proceed with this tool use.",
         "is_error": False, "execution_status": "success"},
        {"record_type": "tool_execution", "session_id": "d6", "tool_name": "Bash",
         "timestamp": "2026-01-05T00:00:00+00:00", "tool_input": {"command": "rm -rf /important"},
         "tool_output": "The user doesn't want to proceed with this tool use. denied by policy",
         "is_error": False, "execution_status": "success"},
    ]
    _, stats4 = mc.mine_command_output_drift(recs_permission)
    check("S1-权限拦截不产出",
          stats4["cmd_drift_candidates"] == 0 and stats4["rejected_by_permission"] >= 1,
          f"candidates={stats4['cmd_drift_candidates']} rejected={stats4['rejected_by_permission']}")

    # ⑤ 输出高度相似（读数没变），不算漂移
    recs_high_sim = [
        {"record_type": "tool_execution", "session_id": "d7", "tool_name": "Bash",
         "timestamp": "2026-01-01T00:00:00+00:00", "tool_input": {"command": "df -m /"},
         "tool_output": "PARTITION / has 102400 MB free (workspace unaffected)",
         "is_error": False, "execution_status": "success"},
        {"record_type": "tool_execution", "session_id": "d8", "tool_name": "Bash",
         "timestamp": "2026-01-10T00:00:00+00:00", "tool_input": {"command": "df -m /"},
         "tool_output": "PARTITION / has 102401 MB free (workspace unaffected)",
         "is_error": False, "execution_status": "success"},
    ]
    _, stats5 = mc.mine_command_output_drift(recs_high_sim)
    check("S1-高相似度不产出",
          stats5["cmd_drift_candidates"] == 0 and stats5["rejected_by_high_similarity"] >= 1,
          f"candidates={stats5['cmd_drift_candidates']} rejected={stats5['rejected_by_high_similarity']}")

    # ⑥ 正向对照：环境探针（/usr 读写态）翻转必须判成 T3 而不是 T1——
    #    判型靠命令本身，不靠措辞（见 mine_conflicts.py 里 ENV_PROBE_COMMANDS）
    env_probe_pair = {
        "kind": "cmd_drift", "command": "报告 /usr 当前是只读还是可写。",
        "old": {"timestamp": "2026-01-01T00:00:00+00:00", "session_id": "d9", "output": "usr_mode=ro"},
        "new": {"timestamp": "2026-01-10T00:00:00+00:00", "session_id": "d10", "output": "usr_mode=rw"},
    }
    check("S1-环境探针判T3", mc.adjudicate(env_probe_pair)["type"] == "T3")

    # ⑦ 正向对照：普通读数变化（不是环境探针）判 T1
    normal_pair = {
        "kind": "cmd_drift", "command": "find workspace -type f | wc -l",
        "old": {"timestamp": "2026-01-01T00:00:00+00:00", "session_id": "d11", "output": "120"},
        "new": {"timestamp": "2026-01-10T00:00:00+00:00", "session_id": "d12", "output": "340"},
    }
    check("S1-普通读数判T1", mc.adjudicate(normal_pair)["type"] == "T1")


def test_retrieval_ablation_not_noop():
    """18. 关联检索消融（`关联检索消融-实施方案.md` §2.3/§四）：六个开关里
    任何一个都不许是 no-op。

    本项目有前科：`meta.low_value` 是死数据、`is_low_value_workflow()`
    定义了但零调用（R2-21/R3-26）；`search_knowledge.py` 里元数据过滤/
    MMR/BM25混合/supersede过滤四项优化写出来后**从未被任何一次召回率
    测量用到过**，这正是本轮要修的问题本身。防止修完之后又出现"接线了
    但某个开关其实没生效"的同类退化，这里直接调真实的
    `measure_recall.retrieve_ablation()`，用最小桩数据（不需要麒麟环境、
    不需要真实 embedding）逐一验证每个开关都真的改变了检索结果，而不是
    走完流程但候选顺序原封不动。
    """
    print("\n18. 关联检索消融（六个开关必须每个都真的改变检索结果，不是 no-op）")

    sys.path.insert(0, os.path.join(RAG, "评测集"))
    import measure_recall as mr

    class _FakeStore:
        def __init__(self, ranked):
            self.ranked = ranked

        def search(self, qvec, top_k):
            return self.ranked[:top_k]

    class _FakeDocs:
        def __init__(self, d):
            self.d = d

        def get(self, did):
            return self.d.get(did)

    # ① 元数据过滤：候选里混入一个不属于 gold_type 的单元，naive 会带着它，
    #    meta 必须把它挡在外面——rank 列表因此不同。
    docs_meta = _FakeDocs({
        1: {"meta": {"type": "workflow"}, "text": "不相关类型的候选"},
        2: {"meta": {"type": "case"}, "text": "正确类型的候选A"},
        3: {"meta": {"type": "case"}, "text": "正确类型的候选B"},
    })
    store_meta = _FakeStore([(1, 0.95), (2, 0.9), (3, 0.8)])
    naive_hits = mr.retrieve_ablation("naive", "q", None, store_meta, docs_meta, max_k=3)
    meta_hits = mr.retrieve_ablation("meta_oracle", "q", None, store_meta, docs_meta, max_k=3,
                                     gold_type="case")
    check("消融-meta_oracle过滤真的改变了结果（不属于gold_type的候选被挡掉）",
          naive_hits != meta_hits, f"naive={naive_hits} meta_oracle={meta_hits}")

    # ② BM25 混合：词面高度匹配的候选必须被拉进 top-k。
    #
    # ⚠️ 消融修复-执行方案.md §B.2②修好归一化之后（两路分数都做候选集内
    # min-max），只用 2 个候选的旧桩数据会失效——对恰好 2 个点做 min-max
    # （或 z-score）在数学上必然把它们拉到 {0,1}（或 {-1,+1}）这两个固定
    # 端点，与两点原始差距是 0.01 还是 0.5 无关，这是归一化定义本身的性质，
    # 不是新缺陷。补成 3 个候选，让归一化在真实候选池规模下有意义地起作用
    # （旧版此前能测出"改变结果"，只是因为它偷懒地只归一化了 BM25 一路，
    # 恰好让未归一化的向量分那 0.01 的原始差距继续生效——这本身正是
    # §B.2②要修的尺度失配缺陷，不能反过来拿它当"预期行为"焊死）。
    from search_knowledge import BM25
    docs_bm25 = _FakeDocs({
        1: {"meta": {"type": "case"}, "text": "甲乙丙丁的专用排障步骤说明"},
        2: {"meta": {"type": "case"}, "text": "完全不相关的另一段说明文字"},
        3: {"meta": {"type": "case"}, "text": "第三个同样不相关的候选文本"},
    })
    # _FakeStore 不自己排序，按调用方传入的顺序原样返回（真实 store.search()
    # 会自己按分数降序），这里手动按分数降序摆好：doc2 向量分明显领先，
    # doc3 略高于 doc1——纯向量 top2 是 [doc2, doc3]，doc1 排第三。
    store_bm25 = _FakeStore([(2, 0.60), (3, 0.51), (1, 0.50)])
    bm25_index = BM25([1, 2, 3], ["甲乙丙丁的专用排障步骤说明",
                                  "完全不相关的另一段说明文字",
                                  "第三个同样不相关的候选文本"])
    naive_hits2 = mr.retrieve_ablation("naive", "甲乙丙丁", None, store_bm25, docs_bm25, max_k=2)
    bm25_hits = mr.retrieve_ablation("bm25", "甲乙丙丁", None, store_bm25, docs_bm25, max_k=2,
                                     bm25=bm25_index)
    # 期望：doc1 是唯一与查询有词面重合的候选，BM25 归一化后的权重把它从
    # 第三名拉进 top2，顶替掉词面完全不沾边的 doc3。
    check("消融-BM25混合真的改变了结果（词面高匹配的候选被拉到前面）",
          naive_hits2 != bm25_hits, f"naive={naive_hits2} bm25={bm25_hits}")

    # ③ supersede 过滤：已被取代的候选必须被挡在结果外。
    edges = {"case:s1": {"supersedes": [], "superseded_by": ["case:s2"]},
             "case:s2": {"supersedes": ["case:s1"], "superseded_by": []}}
    docs_sup = _FakeDocs({
        1: {"meta": {"type": "case", "raw": {"session_id": "s1", "failed_at": "T1", "tool_name": "Bash"}}, "text": "x"},
        2: {"meta": {"type": "case", "raw": {"session_id": "s2", "failed_at": "T2", "tool_name": "Bash"}}, "text": "y"},
    })
    store_sup = _FakeStore([(1, 0.9), (2, 0.8)])
    import search_knowledge as sk
    orig_stable_key = sk._stable_key
    sk._stable_key = lambda raw, kind: "case:" + raw["session_id"]
    try:
        naive_hits3 = mr.retrieve_ablation("naive", "q", None, store_sup, docs_sup, max_k=2)
        sup_hits = mr.retrieve_ablation("supersede", "q", None, store_sup, docs_sup, max_k=2,
                                        edges=edges)
    finally:
        sk._stable_key = orig_stable_key
    check("消融-supersede过滤真的改变了结果（已被取代的候选被挡掉）",
          naive_hits3 != sup_hits, f"naive={naive_hits3} supersede={sup_hits}")

    # ④ MMR：两个候选文本高度相似（同质），第三个候选文本不同但分数略低——
    #    MMR 必须把同质的那个降权到后面，换第三个候选顶上来。
    docs_mmr = _FakeDocs({
        1: {"meta": {"type": "case"}, "text": "重复主题重复主题重复主题重复主题相似文本"},
        2: {"meta": {"type": "case"}, "text": "重复主题重复主题重复主题重复主题相似文本变体"},
        3: {"meta": {"type": "case"}, "text": "完全不同话题的第三个候选"},
    })
    store_mmr = _FakeStore([(1, 0.95), (2, 0.93), (3, 0.80)])
    naive_hits4 = mr.retrieve_ablation("naive", "q", None, store_mmr, docs_mmr, max_k=2)
    mmr_hits = mr.retrieve_ablation("mmr", "q", None, store_mmr, docs_mmr, max_k=2,
                                    mmr_lambda=0.5)
    check("消融-MMR真的改变了结果（同质候选被去重，非同质候选被优先）",
          naive_hits4 != mmr_hits, f"naive={naive_hits4} mmr={mmr_hits}")


def test_retrieval_gold_type_leak_detector():
    """19. 检索不得消费 gold 派生信息（`消融修复-执行方案.md` §C）。

    原 `meta`（现 `meta_oracle`）那个标签泄漏能溜进来，根因是**没有任何
    检查在盯"检索是否消费了 gold 派生的字段"**——第18项验证的是"开关生效"
    （改变了结果），没有一条反过来验证"不该碰的字段真的没被碰"。

    按本项目一贯做法：修类，不修实例。对 `RETRIEVAL_CONFIGS` 里除
    `meta_oracle` 外的每一个配置，用同一批桩数据跑两次
    `retrieve_ablation()`，第二次把 `gold_type` 换成不同的值（或
    `None`），断言两次的 doc_id 序列必须完全一致——不一致就是该配置在
    偷看标准答案。

    ⚠️ `meta_oracle` 的豁免是**在这条用例里硬编码列名**
    （`NON_ORACLE_CONFIGS`），不是从 `retrieve_ablation()` 内部的判断
    逻辑反推、也不是"名字里有 oracle 就跳过"这种字符串模式匹配——否则
    如果哪天有人把 `gold_type` 的读取范围悄悄放宽（比如改成
    `config in ("meta_oracle", "all")`），这条检测器和被它检测的代码会
    读同一份放宽后的名单，一起失明。`mutate.py` 里专门有一条变异验证
    这一点没有发生。

    同时做一次阳性对照（`meta_oracle` 上换 `gold_type` 结果必须不同）——
    独立评审反复点名"只做阳性验证的检查器等于没有检查器"，这里反过来给
    这条阴性检查本身补一次阳性对照：证明它真的有能力测出差异，不是因为
    桩数据太弱导致怎么测都不会变。
    """
    print("\n19. 检索不得消费 gold 派生信息（防 meta 式标签泄漏复发，修类不修实例）")

    sys.path.insert(0, os.path.join(RAG, "评测集"))
    import measure_recall as mr
    from search_knowledge import BM25

    class _FakeStore:
        def __init__(self, ranked):
            self.ranked = ranked

        def search(self, qvec, top_k):
            return self.ranked[:top_k]

    class _FakeDocs:
        def __init__(self, d):
            self.d = d

        def get(self, did):
            return self.d.get(did)

    docs_leak = _FakeDocs({
        1: {"meta": {"type": "case"}, "text": "甲乙丙丁的排障说明文本一号"},
        2: {"meta": {"type": "workflow"}, "text": "完全不相关的流程说明文本二号"},
        3: {"meta": {"type": "case"}, "text": "另一段案例文本三号带戊己庚辛"},
    })
    store_leak = _FakeStore([(1, 0.9), (2, 0.85), (3, 0.8)])
    bm25_leak = BM25([1, 2, 3], [
        "甲乙丙丁的排障说明文本一号", "完全不相关的流程说明文本二号", "另一段案例文本三号带戊己庚辛"])
    # 空边表：apply_supersede_filter 在 edges 为假值时直接原样放行——本用例
    # 只需要"跑过 supersede 分支"，该分支本身的过滤效果已由第18项验证过。
    edges_leak = {}
    centroids_leak = {"case": [0.2, 0.1, 0.05], "workflow": [0.05, 0.1, 0.2]}
    qvec_leak = [0.15, 0.1, 0.1]

    NON_ORACLE_CONFIGS = ("naive", "meta_prior", "mmr", "bm25", "supersede", "all")
    check("消融-泄漏检测覆盖全部非oracle配置（RETRIEVAL_CONFIGS 改动需同步这里的硬编码名单）",
          set(NON_ORACLE_CONFIGS) | {"meta_oracle"} == set(mr.RETRIEVAL_CONFIGS),
          f"当前 RETRIEVAL_CONFIGS={mr.RETRIEVAL_CONFIGS}")

    def _run(cfg, gold_type):
        return mr.retrieve_ablation(
            cfg, "甲乙丙丁", qvec_leak, store_leak, docs_leak, max_k=3,
            gold_type=gold_type, edges=edges_leak, bm25=bm25_leak,
            type_centroids=centroids_leak, prior_beta=0.5)

    for cfg in NON_ORACLE_CONFIGS:
        hits_case = _run(cfg, "case")
        hits_workflow = _run(cfg, "workflow")
        hits_none = _run(cfg, None)
        check(f"消融-{cfg} 不消费 gold_type（换成不同类型/None 结果必须不变）",
              hits_case == hits_workflow == hits_none,
              f"gold_type=case→{hits_case}  gold_type=workflow→{hits_workflow}  "
              f"gold_type=None→{hits_none}")

    # 阳性对照：证明这条检测器真的有辨别力，不是桩数据太弱怎么测都不变。
    oracle_case = _run("meta_oracle", "case")
    oracle_workflow = _run("meta_oracle", "workflow")
    check("消融-阳性对照：meta_oracle 确实消费 gold_type（证明本检测器有能力测出差异）",
          oracle_case != oracle_workflow,
          f"gold_type=case→{oracle_case}  gold_type=workflow→{oracle_workflow}")


def test_metric_coverage_disclosure():
    """🔴 第四轮独立复核 R4-16：篡改一个**未被任何主张覆盖**的指标
    （`M-111.recall_at_1`）→ 主张层 72/72 全绿未察觉，仅靠 MANIFEST 哈希兜住。

    `metric_coverage.py` 把"绿灯到底覆盖了多少数字"算出来公开报告
    （实测 376 个指标里只有 74 个被主张引用，19.7%）。但一份没人看的
    披露就是装饰，所以它带一道闸：**覆盖率不许静默下滑**。

    要钉死三件事：
      ① 数字是从数据算出来的，不是写死的（改数据，数字要跟着动）
      ② 删掉一条主张 → 必须报红（否则"删掉检查器什么都不会发生"再现）
      ③ 空变异基线：不动任何东西时必须是绿的
    """
    print("\n14. R4-16：记录指标覆盖率披露 + 不许静默下滑")

    import metric_coverage as mc

    covered, total, per_record = mc.compute()
    check("R4-16-覆盖率能从真实数据算出来（覆盖数与总数都为正）",
          covered > 0 and total > covered, f"covered={covered} total={total}")
    check("R4-16-申报基线与实测一致（EXPECTED_COVERED/TOTAL 没腐烂）",
          (covered, total) == (mc.EXPECTED_COVERED, mc.EXPECTED_TOTAL),
          f"实测 {covered}/{total}，申报 {mc.EXPECTED_COVERED}/{mc.EXPECTED_TOTAL}")

    # ★ 空变异基线：原样跑一遍必须绿。基线不绿说明这条判据混进了
    #   与"主张覆盖了哪些指标"无关的变量，下面的注入结果就都不作数。
    ok, _ = mc.check_no_regression(covered, total)
    check("R4-16-空变异基线：不动任何东西时不得报红", ok)

    # 注入：在副本里删掉一条主张，覆盖数必须下降且闸门必须报红
    with tempfile.TemporaryDirectory(prefix="r4_16_") as tmp:
        claims_copy = os.path.join(tmp, "claims.json")
        with open(os.path.join(BASE, "claims.json"), encoding="utf-8") as f:
            raw = json.load(f)
        cl = raw["claims"] if isinstance(raw, dict) else raw
        # 删一条**确实引用了某个记录指标**的主张，否则删了覆盖数也不变，
        # 这个注入就成了空操作——那才是真正要防的"恒真检查器"形态。
        victim = next(i for i, c in enumerate(cl)
                      if c.get("record_id") and c.get("metric"))
        removed = cl.pop(victim)
        with open(claims_copy, "w", encoding="utf-8", newline="\n") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

        cov2, tot2, _ = mc.compute(records_dir=os.path.join(BASE, "records"),
                                   claims_path=claims_copy)
        check("R4-16-删掉一条主张后覆盖数必须下降（数字是算的不是写死的）",
              cov2 < covered, f"删前 {covered} → 删后 {cov2}"
                              f"（被删的是 {removed.get('claim_id')}）")
        ok2, msg2 = mc.check_no_regression(cov2, tot2)
        check("R4-16-覆盖率下滑必须报红（删检查器不能什么都不发生）",
              not ok2, msg2 or "闸门没有报红")


ALL_TESTS = [
    test_anonymize,
    test_merge_validate,
    test_mine_cases_k1,
    test_mine_templates_r2_22,
    test_workflows_determinism,
    test_reproduce_core_checkers,
    test_no_real_secret_in_tracked_files,
    test_rescrub_actually_applied,
    test_corpus_hash_guard_bypasses_closed,
    test_structural_invariants,
    test_verify_reports_can_fail,
    test_manifest_detector,
    test_verify_reports_body_branch,
    test_m114_m115_selfchecks,
    test_identity_map_single_source,
    test_low_value_penalty_not_noop,
    test_l1_missing_script_is_fatal,
    test_metric_coverage_disclosure,
    test_conflict_detector_negatives,
    test_preference_extractor_negatives,
    test_cmd_drift_negatives,
    test_retrieval_ablation_not_noop,
    test_retrieval_gold_type_leak_detector,
]


def main():
    print("=" * 70)
    print("test_detectors.py —— 检验\"检验器\"本身")
    print("=" * 70)

    # 🔴 第三轮独立复核 R3-34：本文件此前**一条异常就炸全场**——某个用例
    # 里抛出未捕获异常，后面几组用例根本不会执行，而 stdout 上只看到一段
    # traceback，很容易被当成"环境问题"而不是"有 N 组检测根本没跑"。复核员
    # 实测过一次：用例 5/6/6.5/7 全部未执行。本轮我自己也当场踩到同一个坑
    # （改防复发检查时留了个 NameError，用例 6.5/7 直接没跑）。
    # 修法：每组用例单独包一层，异常转成一条 FAIL 记录而不是中断整轮——
    # "某组检测崩了"本身就是必须报红的信息，不该表现为"少跑了几组"。
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 —— 有意兜底：崩溃必须变成红灯
            import traceback
            print(f"  [FAIL] {fn.__name__} 抛出异常，本组检测未完成")
            traceback.print_exc()
            RESULTS.append((f"{fn.__name__}-整组执行完成", False,
                            f"{type(e).__name__}: {e}"))

    # ★ R4-15：申报总数恒定。放在统计之前追加，让它自己也算一条检查项——
    #   实际记录数 != 申报数，说明有整组用例没跑完（异常中断）或有人加减了
    #   用例却没同步常量与 .md 里那句自我描述。两种都必须红。
    #   注意它是**追加一条记录**，所以比对的是「追加前的条数」。
    actual_before = len(RESULTS)
    check(f"申报总数恒定-实际记录数须等于 DECLARED_CHECK_COUNT({DECLARED_CHECK_COUNT})",
          actual_before == DECLARED_CHECK_COUNT,
          f"实际 {actual_before} 条"
          + ("（有整组用例中途崩了，或用例增减后没同步常量与 .md）"
             if actual_before != DECLARED_CHECK_COUNT else ""))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[1]]

    print("\n" + "-" * 70)
    print(f"合计 {len(RESULTS)} 项检测能力验证：通过 {passed}，失败 {len(failed)}")
    if failed:
        print("\n以下检测器未能识别它本该识别的问题：")
        for name, _, detail in failed:
            print(f"  ✗ {name}  {detail}")
        return 1
    print("\n全部检测器的检测能力均已验证有效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
