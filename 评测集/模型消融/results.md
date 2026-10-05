# 模型消融 4×2 · 结果

> `score_ablation.py`，零 LLM 裁判。K 行 = Gemini 3.1 Pro（maxthinking），经 catie 中转；thinking 与本地三行（关）不同，见报告。
> headline = 严格子串口径；另给排除 must_not_contain 子串误伤后的宽口径。
> K 行只判最终 answer（content），不看 reasoning_content。

## 八格 · 全 13 题 · 严格口径

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/13 | 3/13 | +1 |
| Qwen3.5-4B Q4_K_M | 1/13 | 5/13 | +4 |
| Qwen3.5-9B Q4_K_M | 1/13 | 4/13 | +3 |
| Gemini-3.1-Pro maxthinking（云端） | 1/13 | 6/13 | +5 |

## 八格 · 全 13 题 · 宽口径（排除子串误伤）

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/13 | 4/13 | +2 |
| Qwen3.5-4B Q4_K_M | 1/13 | 6/13 | +5 |
| Qwen3.5-9B Q4_K_M | 1/13 | 4/13 | +3 |
| Gemini-3.1-Pro maxthinking（云端） | 2/13 | 7/13 | +5 |

## 去掉边缘题 T-11 / T-13 后：11 题（严格口径）

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/11 | 3/11 | +1 |
| Qwen3.5-4B Q4_K_M | 0/11 | 4/11 | +4 |
| Qwen3.5-9B Q4_K_M | 0/11 | 4/11 | +4 |
| Gemini-3.1-Pro maxthinking（云端） | 0/11 | 5/11 | +5 |

## 边缘题单列（§3.3）· 严格口径 无·有

| 题 | 2B | 4B | 9B | K |
|---|---|---|---|---|
| T-11 | 0·0 | 1·1 | 1·0 | 1·1 |
| T-13 | 0·0 | 0·0 | 0·0 | 0·0 |

## 逐题矩阵（严格口径；1=对 0=错 ·=缺/错误）

| 题 | 边缘 | 2B无 | 2B有 | 4B无 | 4B有 | 9B无 | 9B有 | K无 | K有 |
|---|---|---|---|---|---|---|---|---|---|
| T-01 |  | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| T-02 |  | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| T-03 |  | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| T-04 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-05 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-06 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-07 |  | 1 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| T-08 |  | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-09 |  | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| T-10 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-11 | ★ | 0 | 0 | 1 | 1 | 1 | 0 | 1 | 1 |
| T-12 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-13 | ★ | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## McNemar（§四）

**n=13，只列不一致对的条数与方向，不作显著性 / 等价推断。**

| 配对（前者 vs 后者） | 只前者对 | 只后者对 | 均对/均错 |
|---|---|---|---|
| A2（2B+记忆） vs K1（云端无记忆）·主论点 | 3 | 1 | 9 |
| A2（2B+记忆） vs K2（云端+记忆）·张力题 | 0 | 3 | 10 |
| B2（4B+记忆） vs K1（云端无记忆） | 4 | 0 | 9 |
| C2（9B+记忆） vs K2（云端+记忆） | 0 | 2 | 11 |
| K1（云端无记忆） vs K2（云端+记忆）·Δ | 0 | 5 | 8 |
| A1 vs A2 | 1 | 2 | 10 |
| B1 vs B2 | 0 | 4 | 9 |
| C1 vs C2 | 1 | 4 | 8 |
| A2 vs B2 | 0 | 2 | 11 |
| A2 vs C2 | 0 | 1 | 12 |
| B2 vs C2 | 2 | 1 | 10 |

## 判分明细

### T-01  源:case-06 (doc 5)
- must_contain=['id_ed25519_seed']  must_not_contain=['sshpass', '-pw']
  - 2b m0: **0**  hit=[] bad=[]
    `ssh -p 2222 -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -o UserKnownHostsFile=/dev/null -o PreferredAuthentications=publickey,password -o PubkeyAuthentication=no -o PasswordAuthentication=yes target_host
ssh -p 2222 -o StrictHostKeyChec`
  - 2b m1: **1**  hit=['id_ed25519_seed'] bad=[] inj=[5, 69, 77, 31, 37]
    `ssh -i ~/.ssh/id_ed25519_seed -o BatchMode=yes root@[REDACTED_IP] "ss -ltnp | grep 48291" 2>&1`
  - 4b m0: **0**  hit=[] bad=[]
    `尝试使用 `ssh -o PreferredAuthentications=publickey,password user@target_host` 命令连接，并手动输入密码。如果仍失败，请检查目标主机 `/etc/ssh/sshd_config` 是否禁用了密码认证，或确认本地私钥路径是否正确。`
  - 4b m1: **1**  hit=['id_ed25519_seed'] bad=[] inj=[5, 69, 77, 31, 37]
    `ssh -i ~/.ssh/id_ed25519_seed -o BatchMode=yes root@[REDACTED_IP] "ss -ltnp | grep 48291" 2>&1`
  - 9b m0: **0**  hit=[] bad=[]
    `先检查本地 SSH 密钥配置，运行 `ssh -v user@target_host` 查看详细错误信息；若确认密钥正确，尝试使用 `ssh -i /path/to/private_key user@target_host` 指定私钥连接；若仍失败，检查目标主机 `/etc/ssh/sshd_config` 中是否允许密码登录或密钥认证。`
  - 9b m1: **1**  hit=['id_ed25519_seed'] bad=[] inj=[5, 69, 77, 31, 37]
    `ssh -i ~/.ssh/id_ed25519_seed -o BatchMode=yes root@[REDACTED_IP] "echo 'Connection successful'" 2>&1`
  - k m0: **0**  hit=[] bad=[]
    `我会使用 `-v` 参数开启详细调试模式排查原因，并使用 `-i` 显式指定正确的私钥文件重新尝试连接。请执行以下命令（需替换实际的私钥路径、用户名和IP）：
`ssh -v -i ~/.ssh/id_rsa user@target_host``
  - k m1: **1**  hit=['id_ed25519_seed'] bad=[] inj=[5, 69, 77, 31, 37]
    `我会指定本机的特定私钥文件进行连接尝试。

`ssh -i ~/.ssh/id_ed25519_seed root@<目标IP>``

### T-02  源:case-29 (doc 28)
- must_contain=['31879']  must_not_contain=['22', '重启 sshd']
  - 2b m0: **0**  hit=[] bad=['22']
    `1. 检查目标主机是否运行了 sshd 服务并监听 22 端口，执行 `sudo systemctl status sshd` 确认状态。
2. 若服务未运行，使用 `sudo systemctl start sshd` 启动服务，若服务已启动但连接失败，尝试使用 `sudo systemctl stop sshd` 停止服务后重新启动。
3. 若服务正常但连接仍被拒绝，检查防火墙设置，执行 `sudo ufw allow ssh` 或 `sudo firewall-cmd --add-service=ssh --permanent` 并重启防火墙。`
  - 2b m1: **0**  hit=[] bad=[] inj=[28, 13, 69, 77, 37]
    ````bash
timeout 20 plink -ssh -pw '[REDACTED_SECRET]' -batch root@[REDACTED_IP] "echo CONNECTED && whoami && hostname"
````
  - 4b m0: **0**  hit=[] bad=[]
    `尝试使用 SSH 协议而非 PLINK 连接：`ssh user@target_host`。如果确认目标主机未开启 SSH 服务，则需检查目标主机防火墙规则或确认服务是否已安装。`
  - 4b m1: **1**  hit=['31879'] bad=[] inj=[28, 13, 69, 77, 37]
    `检查目标主机防火墙规则或 SSH 服务状态，确认端口是否开放。
尝试使用 `timeout 20 plink -ssh -P 31879 -pw 'your_password' -batch root@target_ip 'echo CONNECTED && whoami && hostname'` 命令进行带重试的测试连接。
若仍失败，检查目标主机 SSH 配置文件 `sshd_config` 中的 `PermitRootLogin` 和 `Port` 设置是否正确。`
  - 9b m0: **0**  hit=[] bad=[]
    `检查目标主机防火墙是否拦截了 SSH 端口，并确认目标主机的 SSH 服务正在监听该端口。随后尝试使用 `-P` 参数指定正确的端口号，或暂时关闭防火墙后重试连接。

```bash
plink -P <目标端口> user@<目标主机 IP> <要执行的命令>
````
  - 9b m1: **0**  hit=[] bad=[] inj=[28, 13, 69, 77, 37]
    `首先检查目标主机的 SSH 服务是否正在监听端口：`ss -tlnp | grep ssh`。如果服务未运行，启动它：`systemctl start sshd`；若服务正常但连接仍被拒，确认防火墙规则是否放行了对应端口：`firewall-cmd --list-all` 并根据需要开放端口。`
  - k m0: **0**  hit=[] bad=['22']
    `使用 `nc -zv <目标IP> 22` 检查目标主机的 SSH 端口是否开放。如果目标主机使用了非默认端口，请添加 `-P` 参数指定实际端口重新连接：`plink -P <实际端口> <用户名>@<目标IP>`。若目标端未开启 SSH 服务，需先在目标主机上执行 `sudo systemctl start sshd` 启动服务并检查防火墙放行情况。`
  - k m1: **1**  hit=['31879'] bad=[] inj=[28, 13, 69, 77, 37]
    `我会指定正确的自定义SSH端口（如历史记录中的31879）并加上超时控制重新尝试连接。

`timeout 20 plink -ssh -P 31879 -pw '密码' -batch root@目标IP "echo CONNECTED"``

### T-03  源:case-17 (doc 16)
- must_contain=['proactive']  must_not_contain=['completed', 'success']
  - 2b m0: **0**  hit=[] bad=[]
    ````bash
echo "1" | PushNotification
````
  - 2b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 23, 48, 14]
    `{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}`
  - 4b m0: **0**  hit=[] bad=['success']
    `检查 PushNotification 工具的文档或示例，确认 `status` 字段的合法枚举值（通常是 "success", "failed", "pending" 等）。
尝试将 `status` 替换为文档中指定的有效值后重新调用工具。
如果不确定，运行 `man push-notification` 或查看该工具的 README 文件获取详细参数说明。`
  - 4b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 23, 48, 14]
    `{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}`
  - 9b m0: **0**  hit=[] bad=[]
    `请检查 PushNotification 工具的文档或帮助信息（例如运行 `push-notification --help` 或查看相关 API 定义），以确认 `status` 字段允许的有效枚举值或格式要求。`
  - 9b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 23, 48, 14]
    `{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}`
  - k m0: **0**  hit=[] bad=[]
    `我会执行命令查看该工具的帮助文档或源码，以确认 `status` 字段支持的具体枚举值。

```bash
PushNotification --help
grep -iA 5 "status" $(which PushNotification)
````
  - k m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 23, 48, 14]
    `我会将 `status` 字段的值修改为 `"proactive"` 并重新调用 PushNotification 工具。具体执行的参数格式如下：
```json
{"message": "你的通知内容", "status": "proactive"}
````

### T-04  源:case-09 (doc 8)
- must_contain=['pscp', '/tmp/']  must_not_contain=['chcp', 'encode', '缩短']
  - 2b m0: **0**  hit=[] bad=[]
    ``python -c "import plink; plink.send('echo hello'，encoding='utf-8')"``
  - 2b m1: **0**  hit=[] bad=[] inj=[6, 8, 11, 69, 25]
    ````bash
D:/putty/plink.exe -batch -pw [REDACTED_SECRET] user_a@[REDACTED_IP] "curl -s 'http://[REDACTED_IP]:8080/health' -H 'Content-Type: application/json'"
````
  - 4b m0: **0**  hit=['/tmp/'] bad=[]
    `使用 `plink -e` 选项直接执行命令，避免将命令作为字符串参数传递：
`plink -e "echo 你好" 远端 IP`
或者在发送包含中文的命令时，先将其导出为文件再传输：
`plink -e "cat /tmp/zh_cn.txt" 远端 IP``
  - 4b m1: **0**  hit=[] bad=[] inj=[6, 8, 11, 69, 25]
    `使用 `plink.exe` 的 `-e` 参数直接执行远程命令，避免在命令行字符串中传递包含中文的文本。

```bash
plink.exe -e "curl -s 'http://[REDACTED_IP]:8080/...' | grep '中文内容'" user@[REDACTED_IP]
```

如果命令中包含特殊字符或需要复杂处理，建议先通过 `echo` 转义或使用 `plink.exe -e` 配合 `printf` 发送纯 ASCII 命令，再在远程服务器执行包含中文的逻辑。`
  - 9b m0: **0**  hit=[] bad=[]
    `在命令前加上 `--unicode` 参数，例如：`plink --unicode user@host "echo 你好"`。如果问题依旧，尝试使用 `--charset UTF-8` 参数：`plink --charset UTF-8 user@host "echo 你好"`。`
  - 9b m1: **0**  hit=[] bad=['chcp'] inj=[6, 8, 11, 69, 25]
    `将命令中的中文字符替换为英文或数字，然后使用 `plink` 执行。例如：`plink -batch -pw "your_password" user@host "echo 'Hello World'"`。如果必须执行中文命令，请确保远端系统支持 UTF-8 编码并尝试使用 `chcp 65001` 设置代码页。`
  - k m0: **0**  hit=[] bad=[]
    `我会将中文命令先进行 Base64 编码，再通过 plink 发送到远端解码并执行。

```bash
CMD=$(echo "你的中文命令" | base64 -w 0)
plink user@host "echo $CMD | base64 -d | bash"
````
  - k m1: **0**  hit=[] bad=[] inj=[6, 8, 11, 69, 25]
    `我会将命令进行 base64 编码后发送到远端解码执行。请使用以下格式运行命令：

`echo "你的中文命令" | base64 | plink -batch user@host "base64 -d | bash"``

### T-05  源:case-19 (doc 18)
- must_contain=['base64']  must_not_contain=['转义', '\\"']
  - 2b m0: **0**  hit=[] bad=['\\"']
    ``echo 'print("hello")' | plink -c 'python -c "import sys; print(sys.stdin.read())"' -c 'echo "print(\"hello\")" | plink -c "python -c \"import sys; print(sys.stdin.read())\"" -c "echo "print(\"hello\")" | plink -c "python -c \"import sys; print(sys.stdin.read())\"" | plink -c "py`
  - 2b m1: **0**  hit=[] bad=['\\"'] inj=[18, 12, 50, 28, 69]
    `{"command": "timeout 10 \"python3 -c 'import sys; print(sys.stdin.read())'\" -pw '[REDACTED_SECRET]' -batch user_a@[REDACTED_IP] \"echo 'test'\""}`
  - 4b m0: **0**  hit=[] bad=['转义', '\\"']
    `使用单引号包裹整个 Python 代码块，并转义内部的双引号：`plink -e "python3 -c 'print(\"hello\")'" user@host`。如果代码较长，可先将其保存为临时文件再传输执行。`
  - 4b m1: **0**  hit=[] bad=['转义'] inj=[18, 12, 50, 28, 69]
    `使用 plink 发送命令时，将包含引号的 Python 代码先写入临时文件，再通过 plink 执行该文件，避免引号转义问题。

```bash
echo "import sys; print(sys.argv)" > /tmp/run_py.py && plink -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch "/d/putty/plink.exe -batch -pw [REDACTED_SECRET] user_a@[REDACTED_IP] python3 /tmp/run_py.`
  - 9b m0: **0**  hit=[] bad=['转义', '\\"']
    `使用 `plink` 时，将 Python 代码通过单引号包裹，并在代码内部的双引号前添加反斜杠进行转义，例如：`plink user@host "python3 'print(\"hello\")'"`。如果代码中包含单引号，则需将代码整体放入双引号中，并对代码内的双引号进行转义。`
  - 9b m1: **0**  hit=[] bad=[] inj=[18, 12, 50, 28, 69]
    `将 Python 代码写入远端临时文件后，通过 plink 执行该文件，避免在命令行中直接传递包含引号的复杂字符串。例如：`plink -ssh user@host -pw pass "python3 /tmp/script.py"`。`
  - k m0: **0**  hit=[] bad=['转义', '\\"']
    `我会使用 Here-Doc 语法将代码通过标准输入传递，并对定界符加引号以彻底避免多重 Shell 的转义冲突。

```bash
plink user@host python3 - << 'EOF'
print("在此处放入包含 ' 和 \" 的 Python 代码")
EOF
````
  - k m1: **0**  hit=[] bad=['转义'] inj=[18, 12, 50, 28, 69]
    `我会将Python代码写入本地文件，然后通过标准输入重定向传递给plink执行，以彻底避开命令行的引号转义冲突。

`plink user@remote_ip -pw password "python3 -" < /tmp/your_script.py``

### T-06  源:case-13 (doc 12)
- must_contain=['python3 /tmp/', '脚本']  must_not_contain=['转义', '单引号']
  - 2b m0: **0**  hit=[] bad=[]
    ``echo "until [ ... ]; do ... done" | plink -c "until [ ... ]; do ... done"``
  - 2b m1: **0**  hit=[] bad=[] inj=[12, 69, 50, 18, 28]
    `{"command": "timeout 10 \"python3 -c 'import sys; print(sys.stdin.read().strip().split()[0])'\" -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch \"until [\"; do curl -s 'http://[REDACTED_IP]:8096/Ite\"; done\"]"}`
  - 4b m0: **0**  hit=[] bad=['单引号']
    `尝试将双引号内的命令改为单引号包裹，例如 `plink -e 'until [ ... ]; do ... done' user@host`，因为单引号会防止远程 shell 对变量进行提前展开，从而避免语法解析错误。`
  - 4b m1: **0**  hit=['脚本'] bad=[] inj=[12, 69, 50, 18, 28]
    `将远程命令中的 `until` 循环替换为 `while` 循环，因为 `until` 在部分 Shell 环境或特定语法上下文中可能不被正确解析。

执行以下命令：
```bash
plink -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch "while [ -n \"$movie\" ]; do echo \"Checking...\"; sleep 1; done"
```

如果问题依旧，尝试使用 `bash -c` 显式调用 Bash 解释器来执行远程脚本：
```bash
plin`
  - 9b m0: **0**  hit=[] bad=['转义', '单引号']
    `在 plink 命令中，将远程循环命令用单引号包裹以禁用本地 shell 解析，例如：`plink user@host 'until [ ... ]; do ... done'`。如果命令中包含单引号，则需使用转义字符或改用双引号并正确转义内部单引号。`
  - 9b m1: **0**  hit=[] bad=['转义'] inj=[12, 69, 50, 18, 28]
    `将 `until` 循环改为使用 `while` 循环，因为 `until` 语法在通过 plink 传递时可能因引号转义问题导致解析错误，例如：`while ! [ condition ]; do ... done`。`
  - k m0: **0**  hit=[] bad=['单引号']
    `将包围远程命令的双引号替换为单引号，以防止本地 Shell 提前解析括号或变量等特殊字符。请执行以下格式的命令：
`plink user@host 'until [ ... ]; do ... done'``
  - k m1: **0**  hit=[] bad=['转义'] inj=[12, 69, 50, 18, 28]
    `我会将包含循环的复杂命令进行 Base64 编码，再传给远端解码执行，以彻底避免括号和引号的转义冲突。请执行以下命令：

`cmd=$(echo 'until [ ... ]; do ... done' | base64 -w 0)`
`plink -batch user@ip -pw password "echo $cmd | base64 -d | bash"``

### T-07  源:case-24 (doc 23)
- must_contain=['qm status']  must_not_contain=['qm stop', '--force', 'kill']
  - 2b m0: **1**  hit=['qm status'] bad=[]
    ``qm reboot 100` 失败是因为宿主机重启后虚拟机未自动启动，需先通过 `qm status 100` 确认 VM 状态，再执行 `qm start 100` 启动。`
  - 2b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 31, 28]
    `ssh root@[REDACTED_IP] "qm status 100"`
  - 4b m0: **0**  hit=[] bad=['kill']
    `执行 `lsof -i -P -n | grep 100` 检查是否有进程占用虚拟机 100 的端口。如果存在占用进程，使用 `kill -9 <PID>` 强制终止该进程，然后再次运行 `qm reboot 100`。`
  - 4b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 31, 28]
    `qm status 100`
  - 9b m0: **0**  hit=[] bad=['qm stop', '--force']
    `尝试强制停止虚拟机：`qm stop 100 --force`。如果该命令仍超时，则手动进入宿主机目录删除虚拟机状态文件：`rm -f /var/lib/vz/dump/vmstate/100`。最后重启虚拟机：`qm start 100`。`
  - 9b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 31, 28]
    `ssh root@[REDACTED_IP] "qm status 100"`
  - k m0: **0**  hit=[] bad=['qm stop']
    `我会强制停止该虚拟机并重新启动，或者直接对其执行硬重置。请执行以下命令：

`qm stop 100 && qm start 100`

或者直接运行：
`qm reset 100``
  - k m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 31, 28]
    `我会通过 SSH 重新检查该虚拟机的当前状态。

```bash
ssh root@[REDACTED_IP] "qm status 100"
````

### T-08  源:case-21/22 (doc 21)
- must_contain=['login/oauth/access_token', 'curl']  must_not_contain=['ls', 'cat']
  - 2b m0: **1**  hit=['login/oauth/access_token', 'curl'] bad=[]
    ````bash
curl -s https://github.com/login/oauth/access_token -u "user:password" -o token.txt
````
  - 2b m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 21, 37, 46, 18]
    ````bash
for i in $(seq 1 100); do RESP=$(curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -H "Authorization: Bearer $DEVICE_CODE" -o /dev/null 2>&1; if [ $? -eq 0 ]; then echo "Token found"; break; fi; done
````
  - 4b m0: **0**  hit=[] bad=['cat']
    `检查浏览器控制台获取的 token 并手动创建文件：`echo "YOUR_ACCESS_TOKEN" > ~/.github/token`，然后使用 `cat ~/.github/token` 验证内容。`
  - 4b m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 21, 37, 46, 18]
    ````bash
curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET&code=YOUR_CODE"
````
  - 9b m0: **0**  hit=[] bad=['ls', 'cat']
    `检查 `~/.ssh/github_device_flow` 目录下的文件内容，或查看系统日志确认 token 是否已写入：`ls -la ~/.ssh/github_device_flow/` 和 `cat ~/.ssh/github_device_flow/token`。如果文件不存在，请确认浏览器授权回调地址是否正确且本地服务仍在监听。`
  - 9b m1: **0**  hit=[] bad=[] inj=[20, 21, 37, 46, 18]
    `由于当前环境为银河麒麟（Linux），无法直接访问 Windows 路径下的 token 文件，请直接在浏览器授权页面点击“复制访问令牌”或“复制设备代码”，然后手动粘贴到终端变量中，例如：`export GITHUB_TOKEN="…"`。`
  - k m0: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat']
    `请使用之前申请时的 `client_id` 和 `device_code`，直接通过 `curl` 请求 GitHub 接口获取 access token。执行以下命令：

```bash
curl -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=<你的client_id>&device_code=<你的device_code>&grant_type=urn:ietf:params:oauth:grant-typ`
  - k m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 21, 37, 46, 18]
    `我会直接使用 `curl` 携带 `device_code` 向 GitHub 接口请求获取 access token。请在填入实际的 `CLIENT_ID` 和 `DEVICE_CODE` 后执行以下命令：

```bash
curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=$CLIENT_ID&device_code=$DEVICE_CODE&grant_type=urn:ietf:params`

### T-09  源:case-16 (doc 15)
- must_contain=['AppData/Local/Temp']  must_not_contain=['mkdir', 'touch']
  - 2b m0: **0**  hit=[] bad=[]
    `cd /tmp && python -c "import json; print(json.load(open('qb_prefs.json', 'r', encoding='utf-8')))"`
  - 2b m1: **0**  hit=[] bad=[] inj=[15, 20, 52, 17, 12]
    `{"command": "D:/putty/plink.exe -batch -pw [REDACTED_SECRET] user_a@[REDACTED_IP] \"python3 /tmp/scan_state.py\" && python3 -c \"import json; d=json.load(open('C:/Users/devi/qb_prefs.json', encoding='utf-8')); print(d)\""}`
  - 4b m0: **0**  hit=[] bad=[]
    `先使用 `ls -l /tmp/qb_prefs.json` 确认文件是否存在及确切路径，若文件不存在则尝试 `find / -name "qb_prefs.json" 2>/dev/null` 搜索其位置。找到路径后，使用 `python3 -c "import json; print(json.load(open('/path/to/file.json')))"` 读取文件内容。`
  - 4b m1: **0**  hit=[] bad=[] inj=[15, 20, 52, 17, 12]
    `先通过 `ls -la /tmp/` 确认文件是否存在及确切路径，若文件确实在 `/tmp/` 但被误判，尝试使用 `cat /tmp/qb_prefs.json` 直接读取内容；若文件不存在，检查是否因跨系统路径映射导致路径错误，并尝试在 Windows 侧重新导出或确认路径映射。`
  - 9b m0: **0**  hit=[] bad=[]
    `在 Git Bash 中，Windows 路径 `/tmp/qb_prefs.json` 通常对应 Linux 的 `/tmp/qb_prefs.json`，但需确认文件是否已正确复制到当前 Linux 环境。请先执行 `ls -l /tmp/qb_prefs.json` 检查文件是否存在；若不存在，需先通过 `scp` 或其他方式从 Windows 同步该文件到 Linux 的 `/tmp` 目录，然后再用 `python` 读取。`
  - 9b m1: **1**  hit=['AppData/Local/Temp'] bad=[] inj=[15, 20, 52, 17, 12]
    `由于 Git Bash 运行在 Windows 上且 `/tmp` 目录通常为空，文件实际位于 Windows 路径 `C:\Users\当前用户\AppData\Local\Temp\qb_prefs.json`。请使用 `python -c "import json; d=json.load(open(r'C:/Users/$(whoami)/AppData/Local/Temp/qb_prefs.json', encoding='utf-8')); print(d)"` 读取该文件。`
  - k m0: **0**  hit=[] bad=[]
    `文件实际位于 Git Bash 安装目录下的 `tmp` 文件夹中（例如 `C:\Program Files\Git\tmp`）。
我会使用 `cygpath` 将其转换为 Windows 原生路径后再传给 Python 读取：
`python -c "import sys; print(open(sys.argv[1], encoding='utf-8').read())" "$(cygpath -w /tmp/qb_prefs.json)"``
  - k m1: **1**  hit=['AppData/Local/Temp'] bad=[] inj=[15, 20, 52, 17, 12]
    ````bash
python -c "import json; d=json.load(open('C:/Users/device_a/AppData/Local/Temp/qb_prefs.json', encoding='utf-8')); print(d)"
````

### T-10  源:case-25 (doc 24)
- must_contain=['scp', 'pscp']  must_not_contain=['touch', 'vi ', 'cat >']
  - 2b m0: **0**  hit=[] bad=[]
    ``sudo cp /tmp/mc-step1-base.sh /tmp/mc-step1-base.sh.bak && sudo chmod +x /tmp/mc-step1-base.sh``
  - 2b m1: **0**  hit=['scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `scp "/tmp/mc-step1-base.sh" "C:\Users\device_a\AppData\Local\Temp\claude\C--Users-device_a\6954807b-f355-4d37"`
  - 4b m0: **0**  hit=['scp'] bad=[]
    ``scp /tmp/mc-step1-base.sh user@remote_host:/tmp/` 将脚本上传到远程服务器。`
  - 4b m1: **0**  hit=['scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `ssh root@REDACTED_IP 'mkdir -p /tmp && scp "C:\Users\device_a\AppData\Local\Temp\claude\...\mc-step1-base.sh" /tmp/'`
  - 9b m0: **0**  hit=['scp'] bad=[]
    `在远端服务器执行 `scp /tmp/mc-step1-base.sh 用户名@远端IP:/tmp/` 将脚本文件从本地上传到远端的 `/tmp` 目录。`
  - 9b m1: **0**  hit=[] bad=[] inj=[24, 31, 69, 37, 78]
    `首先通过 `ssh` 连接远端服务器，检查 `/tmp` 目录的挂载状态及权限，确认临时目录是否持久化或需要重新创建。
```bash
ssh root@[REDACTED_IP] "ls -ld /tmp && mount | grep tmp"
````
  - k m0: **0**  hit=['scp'] bad=[]
    `使用 `scp` 命令将本地的脚本重新上传到远端节点。请执行以下命令：
`scp mc-step1-base.sh user@remote_ip:/tmp/``
  - k m1: **0**  hit=['scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `重新将本地的脚本文件上传到远端服务器。

`scp mc-step1-base.sh root@<远端IP>:/tmp/mc-step1-base.sh``

### T-11  ★边缘  源:case-10 (doc 9)
- must_contain=['-L']  must_not_contain=['--ignore', '解压参数']
  - 2b m0: **0**  hit=[] bad=[]
    ``curl -s -o go.tar.gz <URL>` 无法直接下载 .tar.gz 文件，因为 `curl` 默认只支持 HTTP/HTTPS 协议，不支持直接下载本地文件。你需要先通过 `wget` 下载文件，再用 `tar` 解压。

```bash
wget -O go.tar.gz <URL> && tar -xzf go.tar.gz
````
  - 2b m1: **0**  hit=[] bad=[] inj=[9, 69, 14, 85, 2]
    ``curl -s -o go.tar.gz <URL> | gzip -d``
  - 4b m0: **1**  hit=['-L'] bad=[]
    ``curl -s -L -o go.tar.gz <URL>` 下载文件，然后使用 `tar -xzf go.tar.gz` 解压。如果文件损坏，可尝试 `curl -s -L -o go.tar.gz <URL>` 后手动用 `gunzip -c go.tar.gz > go.tar.gz` 修复或重新下载。`
  - 4b m1: **1**  hit=['-L'] bad=[] inj=[9, 69, 14, 85, 2]
    `检查文件是否为空或损坏，尝试使用 `curl -L -s -o go.tar.gz <URL>` 添加 `-L` 参数以自动重定向，或先验证下载内容：`file go.tar.gz`。如果文件确实为空，尝试使用 `wget` 替代 `curl` 下载。`
  - 9b m0: **1**  hit=['-L'] bad=[]
    `先检查 `<URL>` 是否返回了正确的 HTTP 状态码，若返回 404 或 301 等错误，请改用 `-L` 参数跟随重定向：`curl -sL -o go.tar.gz <URL>`。如果下载后文件仍异常，可尝试使用 `wget` 或检查网络代理设置。`
  - 9b m1: **0**  hit=[] bad=[] inj=[9, 69, 14, 85, 2]
    `先检查下载源是否支持 gzip 压缩，若不支持则改用 `wget` 或调整 URL 参数；若需强制下载，可尝试 `curl -s -o go.tar.gz <URL> --compressed` 或确认服务器是否返回了正确的 Content-Type。如果文件确实不是 gzip 格式，应改用 `tar -xzf` 前验证文件头，或直接下载正确的压缩包。`
  - k m0: **1**  hit=['-L'] bad=[]
    `添加 `-L` 参数让 curl 跟随重定向即可获取真实文件。请执行以下命令重新下载：

`curl -s -L -o go.tar.gz <URL>``
  - k m1: **1**  hit=['-L'] bad=[] inj=[9, 69, 14, 85, 2]
    `请在原命令中添加 `-L` 参数以跟随 HTTP 重定向：
`curl -s -L -o go.tar.gz <URL>``

### T-12  源:case-30 (doc 29)
- must_contain=['client']  must_not_contain=['重新反编译', 'mkdir']
  - 2b m0: **0**  hit=[] bad=[]
    ``cd` 到反编译源码目录后，使用 `grep` 搜索类名并输出结果：`grep "类名" .../DecompilerMC/src/1.21.11/server/net/minecraft/...``
  - 2b m1: **0**  hit=[] bad=[] inj=[29, 65, 10, 27, 31]
    `cd /e/game/mc/DecompilerMC/src/1.21.11/server/net && grep -r "isCompatibleWith" .`
  - 4b m0: **0**  hit=[] bad=[]
    `先使用 `ls -R` 递归列出目录结构以确认实际路径，然后执行 `grep -r "类名" DecompilerMC/src/1.21.11/server/net/minecraft/` 进行搜索。`
  - 4b m1: **0**  hit=[] bad=[] inj=[29, 65, 10, 27, 31]
    `先检查当前工作目录是否为 `C:\Users\device_a`，然后使用 `grep` 命令时显式指定绝对路径，例如：`grep -r "pattern" /e/game/mc/DecompilerMC/src/1.21.11/server/net/minecraft/`。如果路径确实不存在，尝试先列出该目录结构确认路径是否正确。`
  - 9b m0: **0**  hit=[] bad=[]
    `先使用 `find` 命令在当前目录下递归搜索包含该类的 `.java` 文件，确认实际路径后，再用 `grep` 进行匹配。

```bash
find . -name "*.java" -path "*/net/minecraft/*" -exec grep -l "类名" {} \;
```
*(请将“类名”替换为你要搜索的具体类名)*`
  - 9b m1: **0**  hit=[] bad=[] inj=[29, 65, 10, 27, 31]
    `首先使用 `find` 命令在当前目录下递归搜索包含 `net/minecraft` 路径的文件，确认实际存在的目录结构：
`find . -type d -name "net" 2>/dev/null`

找到正确的相对路径后，再执行 `grep` 命令：
`grep -r "isCompatibleWith" . --include="*.java"``
  - k m0: **0**  hit=[] bad=[]
    `我会先使用 `find` 命令查找 `DecompilerMC` 或 `minecraft` 目录的真实路径：
`find ~ -type d -name "DecompilerMC" 2>/dev/null`
找到正确的根目录后，直接在该目录下递归搜索目标类：
`grep -rnw '/找到的/DecompilerMC/真实路径' -e '目标类名'``
  - k m1: **0**  hit=[] bad=[] inj=[29, 65, 10, 27, 31]
    `我会先使用 `find` 命令查找 `DecompilerMC` 目录的实际路径，确认正确的目录结构后再进行搜索。

```bash
find / -type d -name "DecompilerMC" 2>/dev/null
````

### T-13  ★边缘  源:case-31 (doc 30)
- must_contain=['timeout 500', '加大超时']  must_not_contain=['优化脚本', '并行']
  - 2b m0: **0**  hit=[] bad=[]
    `kill -9 $(pgrep -f "python reproduce_L1.py")`
  - 2b m1: **0**  hit=[] bad=[] inj=[14, 23, 17, 1, 12]
    `{"command": "timeout 30 python reproduce_L1.py"}`
  - 4b m0: **0**  hit=[] bad=[]
    `检查脚本是否卡死在 I/O 等待中：`strace -f -o trace.log python reproduce_L1.py`。若确认卡死，尝试增加超时时间或检查脚本逻辑：`timeout 60 python reproduce_L1.py`。`
  - 4b m1: **0**  hit=[] bad=['优化脚本'] inj=[14, 23, 17, 1, 12]
    `检查 `reproduce_L1.py` 脚本是否因超时或资源耗尽而卡死，并尝试手动运行 `python reproduce_L1.py` 查看具体错误信息。如果确认是超时问题，考虑增加超时时间或优化脚本逻辑。`
  - 9b m0: **0**  hit=[] bad=[]
    `使用 `strace -f -e trace=signal timeout 30 python reproduce_L1.py` 跟踪脚本接收到的信号详情，或检查 `/proc/<pid>/status` 确认进程状态。`
  - 9b m1: **0**  hit=[] bad=[] inj=[14, 23, 17, 1, 12]
    `检查脚本是否因超时被强制终止，并查看其输出日志：`cat reproduce_L1.py.log 2>/dev/null || echo "No log found"`。如果脚本仍在运行或需要重试，请手动终止残留进程：`pkill -f reproduce_L1.py`。`
  - k m0: **0**  hit=[] bad=[]
    `我会延长超时时间，并利用 `PYTHONFAULTHANDLER` 在脚本再次被 SIGTERM 终止时打印当前的执行堆栈，以排查卡死位置。

```bash
PYTHONFAULTHANDLER=1 timeout 120 python reproduce_L1.py
````
  - k m1: **0**  hit=[] bad=[] inj=[14, 23, 17, 1, 12]
    `延长超时时间并使用 strace 追踪系统调用，查看脚本具体阻塞在何处：
`timeout 120 strace -tt python reproduce_L1.py``
