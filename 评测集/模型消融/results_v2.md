# 模型消融 4×2 · 结果

> `score_ablation.py`，零 LLM 裁判。K 行 = Gemini 3.1 Pro（maxthinking），经 catie 中转；thinking 与本地三行（关）不同，见报告。
> headline = 严格子串口径；另给排除 must_not_contain 子串误伤后的宽口径。
> K 行只判最终 answer（content），不看 reasoning_content。

## 八格 · 全 13 题 · 严格口径

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/13 | 5/13 | +3 |
| Qwen3.5-4B Q4_K_M | 3/13 | 7/13 | +4 |
| Qwen3.5-9B Q4_K_M | 2/13 | 7/13 | +5 |
| Gemini-3.1-Pro maxthinking（云端） | 3/13 | 8/13 | +5 |

## 八格 · 全 13 题 · 宽口径（排除子串误伤）

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/13 | 5/13 | +3 |
| Qwen3.5-4B Q4_K_M | 3/13 | 8/13 | +5 |
| Qwen3.5-9B Q4_K_M | 2/13 | 8/13 | +6 |
| Gemini-3.1-Pro maxthinking（云端） | 4/13 | 9/13 | +5 |

## 去掉边缘题 T-11 / T-13 后：11 题（严格口径）

| 模型 | 无记忆 | 有记忆 | Δ |
|---|---|---|---|
| Qwen3.5-2B Q4_K_M | 2/11 | 5/11 | +3 |
| Qwen3.5-4B Q4_K_M | 2/11 | 6/11 | +4 |
| Qwen3.5-9B Q4_K_M | 1/11 | 6/11 | +5 |
| Gemini-3.1-Pro maxthinking（云端） | 2/11 | 6/11 | +4 |

## 边缘题单列（§3.3）· 严格口径 无·有

| 题 | 2B | 4B | 9B | K |
|---|---|---|---|---|
| T-11 | 0·0 | 1·1 | 1·1 | 1·1 |
| T-13 | 0·0 | 0·0 | 0·0 | 0·1 |

## 逐题矩阵（严格口径；1=对 0=错 ·=缺/错误）

| 题 | 边缘 | 2B无 | 2B有 | 4B无 | 4B有 | 9B无 | 9B有 | K无 | K有 |
|---|---|---|---|---|---|---|---|---|---|
| T-01 |  | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| T-02 |  | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| T-03 |  | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| T-04 |  | 0 | 0 | 1 | 1 | 0 | 0 | 1 | 1 |
| T-05 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-06 |  | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| T-07 |  | 1 | 1 | 0 | 1 | 0 | 1 | 0 | 0 |
| T-08 |  | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-09 |  | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 1 |
| T-10 |  | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| T-11 | ★ | 0 | 0 | 1 | 1 | 1 | 1 | 1 | 1 |
| T-12 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T-13 | ★ | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

## McNemar（§四）

**n=13，只列不一致对的条数与方向，不作显著性 / 等价推断。**

| 配对（前者 vs 后者） | 只前者对 | 只后者对 | 均对/均错 |
|---|---|---|---|
| A2（2B+记忆） vs K1（云端无记忆）·主论点 | 4 | 2 | 7 |
| A2（2B+记忆） vs K2（云端+记忆）·张力题 | 1 | 4 | 8 |
| B2（4B+记忆） vs K1（云端无记忆） | 4 | 0 | 9 |
| C2（9B+记忆） vs K2（云端+记忆） | 2 | 3 | 8 |
| K1（云端无记忆） vs K2（云端+记忆）·Δ | 0 | 5 | 8 |
| A1 vs A2 | 1 | 4 | 8 |
| B1 vs B2 | 0 | 4 | 9 |
| C1 vs C2 | 0 | 5 | 8 |
| A2 vs B2 | 1 | 3 | 9 |
| A2 vs C2 | 0 | 2 | 11 |
| B2 vs C2 | 2 | 2 | 9 |

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
    `ssh -i ~/.ssh/id_ed25519_seed -o BatchMode=yes root@[REDACTED_IP] "ss -ltnp | grep 48291" 2>&1`
  - k m0: **0**  hit=[] bad=[]
    `我会使用 `-i` 参数显式指定正确的私钥文件来尝试连接，或者加上 `-v` 参数查看详细的认证交互过程以排查具体原因。

`ssh -i ~/.ssh/id_rsa 用户名@目标IP`
`ssh -v 用户名@目标IP``
  - k m1: **1**  hit=['id_ed25519_seed'] bad=[] inj=[5, 69, 77, 31, 37]
    `请指定正确的私钥文件进行连接：
`ssh -i ~/.ssh/id_ed25519_seed root@目标IP``

### T-02  源:case-29 (doc 28)
- must_contain=['31879']  must_not_contain=['22', '重启 sshd']
  - 2b m0: **0**  hit=[] bad=['22']
    `1. 检查目标主机是否运行了 sshd 服务并监听 22 端口，执行 `sudo systemctl status sshd` 确认状态。
2. 若服务未运行，使用 `sudo systemctl start sshd` 启动服务，若服务已启动但连接失败，尝试使用 `sudo systemctl stop sshd` 停止服务后重新启动。
3. 若服务正常但连接仍被拒绝，检查防火墙设置，执行 `sudo ufw allow ssh` 或 `sudo firewall-cmd --add-service=ssh --permanent` 并重启防火墙。`
  - 2b m1: **0**  hit=[] bad=[] inj=[28, 13, 69, 77, 37]
    `timeout 20 "/d/putty/plink.exe" -ssh -pw '[REDACTED_SECRET]' -batch root@[REDACTED_IP] "echo CONNECTED && whoami && hostname" 2>&1`
  - 4b m0: **0**  hit=[] bad=[]
    `尝试使用 SSH 协议而非 PLINK 连接：`ssh user@target_host`。如果确认目标主机未开启 SSH 服务，则需检查目标主机防火墙规则或确认服务是否已安装。`
  - 4b m1: **1**  hit=['31879'] bad=[] inj=[28, 13, 69, 77, 37]
    `timeout 20 "/d/putty/plink.exe" -ssh -P 31879 -pw '[REDACTED_SECRET]' -batch root@[REDACTED_IP] "echo CONNECTED && whoami && hostname" 2>&1`
  - 9b m0: **0**  hit=[] bad=[]
    `检查目标主机防火墙是否拦截了 SSH 端口，并确认目标主机的 SSH 服务正在监听该端口。随后尝试使用 `-P` 参数指定正确的端口号，或暂时关闭防火墙后重试连接。

```bash
plink -P <目标端口> user@<目标主机 IP> <要执行的命令>
````
  - 9b m1: **0**  hit=[] bad=[] inj=[28, 13, 69, 77, 37]
    `检查目标主机的 SSH 服务是否正在监听端口，执行 `netstat -tlnp | grep ssh` 或 `ss -tlnp | grep ssh` 确认监听状态；若服务未启动，先启动 SSH 服务（如 `systemctl start sshd`）；若端口被占用或配置错误，检查 `/etc/ssh/sshd_config` 中的 `Port` 设置并重启服务。`
  - k m0: **0**  hit=[] bad=['22']
    `首先使用 `nc -zv <目标IP> 22` 探测目标主机的 SSH 端口是否开放。若目标主机修改了默认 SSH 端口，请使用指定端口重新发起连接：`plink -P <实际端口号> <用户名>@<目标IP>`。`
  - k m1: **1**  hit=['31879'] bad=[] inj=[28, 13, 69, 77, 37]
    `请指定正确的SSH端口并添加超时参数重新连接：

`timeout 20 plink -ssh -P 31879 -pw '你的密码' -batch root@目标IP "echo CONNECTED && whoami && hostname" 2>&1``

### T-03  源:case-17 (doc 16)
- must_contain=['proactive']  must_not_contain=['completed', 'success']
  - 2b m0: **0**  hit=[] bad=['success']
    ````bash
echo "status=success" | PushNotification
````
  - 2b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 19, 48, 14]
    `{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}`
  - 4b m0: **0**  hit=[] bad=['success']
    `检查 PushNotification 工具的文档或示例，确认 `status` 字段的合法枚举值（通常是 "success", "failed", "pending" 等）。如果不确定，尝试运行 `man push-notification` 或查看相关 API 定义文件来查找允许的输入值。`
  - 4b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 19, 48, 14]
    `{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}`
  - 9b m0: **0**  hit=[] bad=[]
    `请检查 PushNotification 工具的文档或帮助信息（例如运行 `push-notification --help` 或查看相关 API 定义），以确认 `status` 字段允许的有效枚举值或格式要求。`
  - 9b m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 19, 48, 14]
    `根据记忆 [1] 中的修复方式，`status` 字段应填 `proactive`。请执行以下命令：

```bash
PushNotification '{"message": "计算完成：Alice (19), Bob (8)", "status": "proactive"}'
````
  - k m0: **0**  hit=[] bad=[]
    `我会执行以下命令查看该工具的帮助文档，以确认 `status` 字段支持的有效枚举值。

`PushNotification --help | grep -A 3 -i "status"``
  - k m1: **1**  hit=['proactive'] bad=[] inj=[16, 17, 19, 48, 14]
    `将 `status` 字段的值填写为 `"proactive"`。在调用 PushNotification 时请确保参数包含 `{"status": "proactive"}`。`

### T-04  源:case-09 (doc 8)
- must_contain=[['any', 'pscp', 'scp ', 'base64', '/tmp/']]  must_not_contain=['chcp', '缩短']
  - 2b m0: **0**  hit=[] bad=[]
    ``python -c "import plink; plink.send_command('echo 你好', 'remote_host', 'utf-8')"``
  - 2b m1: **0**  hit=[] bad=[] inj=[6, 8, 11, 7, 17]
    `使用 plink 发送包含中文的命令时，需将命令转换为 UTF-8 编码并添加 `--utf8` 参数，同时确保 plink 版本支持该选项（如 `plink --utf8`），并避免在 plink 参数中直接包含中文字符串。`
  - 4b m0: **1**  hit=['any→/tmp/'] bad=[]
    `使用 `plink -e` 选项直接执行命令，避免将命令作为字符串参数传递：
`plink -e "echo 你好" 远端 IP`
或者在发送包含中文的命令时，先将其导出为文件再传输：
`plink -e "cat /tmp/zh_cn.txt" 远端 IP``
  - 4b m1: **1**  hit=['any→/tmp/'] bad=[] inj=[6, 8, 11, 7, 17]
    `使用 `plink.exe` 将命令写入临时文件，再通过 `plink.exe` 执行该文件以绕过命令行编码限制。

```bash
plink.exe -batch -pw "你的密码" 远程IP "echo '你的命令' > /tmp/cmd.txt"
plink.exe -batch -pw "你的密码" 远程IP "bash /tmp/cmd.txt"
````
  - 9b m0: **0**  hit=[] bad=[]
    `在命令前加上 `--unicode` 参数，例如：`plink --unicode user@host "echo 你好"`。如果问题依旧，尝试使用 `--charset UTF-8` 参数：`plink --charset UTF-8 user@host "echo 你好"`。`
  - 9b m1: **0**  hit=[] bad=[] inj=[6, 8, 11, 7, 17]
    `将命令中的中文字符替换为英文或数字，然后使用 `plink` 重新执行。例如：`plink -batch -pw "your_password" user@host "echo 'Hello World'"`。`
  - k m0: **1**  hit=['any→base64'] bad=[]
    `我会将包含中文的命令在本地进行 base64 编码，再通过 plink 发送到远端解码并执行。

`plink user@host -pw password "echo $(echo '你的中文命令' | base64) | base64 -d | bash"``
  - k m1: **1**  hit=['any→pscp'] bad=[] inj=[6, 8, 11, 7, 17]
    `将包含中文的命令写入本地脚本文件，使用 pscp 上传至远端，再通过 plink 执行该脚本：

```bash
echo "包含中文的具体命令" > /tmp/remote_cmd.sh
pscp -batch -pw [密码] /tmp/remote_cmd.sh user@[IP]:/tmp/remote_cmd.sh
plink -batch -pw [密码] user@[IP] "bash /tmp/remote_cmd.sh"
````

### T-05  源:case-19 (doc 18)
- must_contain=[['any', 'base64', '/tmp/', '临时文件']]  must_not_contain=['转义']
  - 2b m0: **0**  hit=[] bad=[]
    ``echo 'print("hello")' | plink -c 'python -c "import sys; print(sys.stdin.read())"' -o /dev/null 2>/dev/null``
  - 2b m1: **0**  hit=[] bad=[] inj=[18, 12, 50, 17, 69]
    `plink -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch "python3 -c 'import sys; print(sys.stdin.read())'"`
  - 4b m0: **0**  hit=['any→临时文件'] bad=['转义']
    `使用单引号包裹整个 Python 代码块，并转义内部的双引号：`plink -e "python3 -c 'print(\"hello\")'" user@host`。如果代码较长，可先将其保存为临时文件再传输执行。`
  - 4b m1: **0**  hit=[] bad=['转义'] inj=[18, 12, 50, 17, 69]
    `使用 plink 发送代码时，将包含引号的 Python 代码包裹在双引号内，并用反斜杠转义双引号，例如：`plink -ssh user@host -pw pass -batch "python3 -c 'print(\"hello\")'"`。`
  - 9b m0: **0**  hit=[] bad=['转义']
    `使用 `plink` 的 `-e` 参数直接执行 Python 代码，并将代码内容通过单引号包裹以正确转义内部的双引号，例如：`plink user@host -e "python3 -c 'print(\"hello\")'"`。如果代码中包含单引号，则需将代码整体用双引号包裹，并在内部单引号前添加反斜杠进行转义。`
  - 9b m1: **0**  hit=['any→临时文件'] bad=['转义'] inj=[18, 12, 50, 17, 69]
    `将 Python 代码中的双引号改为单引号，并用 plink 的 `-e` 参数转义，例如：`plink -ssh user@host -pw pass -batch "python3 -c 'print(\"hello\")'"`。如果代码本身包含复杂引号，建议将代码写入本地临时文件，通过 plink 远程执行该文件。`
  - k m0: **0**  hit=[] bad=['转义']
    `我会使用 Here-Doc 语法将代码通过标准输入传递，并对定界符加引号以彻底避免多重 Shell 的转义冲突。

```bash
plink user@host python3 - << 'EOF'
print("在此处放入包含 ' 和 \" 的 Python 代码")
EOF
````
  - k m1: **0**  hit=['any→base64'] bad=['转义'] inj=[18, 12, 50, 17, 69]
    `我会将要执行的 Python 代码先进行 Base64 编码，然后通过 plink 发送到远端解码并执行，以避开复杂的引号转义问题。

```bash
plink -ssh user@ip -pw pass -batch "echo 'BASE64_ENCODED_PYTHON_CODE' | base64 -d | python3"
````

### T-06  源:case-13 (doc 12)
- must_contain=[['any', '/tmp/', 'base64']]  must_not_contain=['转义', '单引号']
  - 2b m0: **0**  hit=[] bad=[]
    ``echo "until [ ... ]; do ... done" | plink -c "until [ ... ]; do ... done"``
  - 2b m1: **0**  hit=[] bad=[] inj=[12, 69, 18, 50, 17]
    `plink -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch "python3 -c 'import sys; print(sys.stdin.read().strip())'"`
  - 4b m0: **0**  hit=[] bad=['单引号']
    `尝试将双引号内的命令改为单引号包裹，例如 `plink -e 'until [ ... ]; do ... done' user@host`，因为单引号会防止远程 shell 将变量或特殊字符进行解释。如果问题依旧，尝试在远程命令内部使用 `[[ ... ]]` 替代 `[ ... ]` 来避免语法解析错误。`
  - 4b m1: **0**  hit=[] bad=[] inj=[12, 69, 18, 50, 17]
    `将远程命令中的 `until` 循环替换为 `while` 循环，因为 `until` 在部分远程环境（如某些 Python 脚本或特定 shell 配置）中可能不被识别或语法解析失败。

使用 plink 执行修正后的命令：
`plink -ssh user_a@[REDACTED_IP] -pw [REDACTED_SECRET] -batch "while [ -n "$result" ]; do ...; done"`

确保远程脚本使用标准的 Bash 语法，避免使用非标准关键字。`
  - 9b m0: **0**  hit=[] bad=['转义', '单引号']
    `在 plink 命令中，将远程循环命令用单引号包裹以禁用本地 shell 解析，例如：`plink user@host 'until [ ... ]; do ... done'`。如果命令中包含单引号，则需使用转义字符或改用双引号并转义内部的双引号。`
  - 9b m1: **1**  hit=['any→/tmp/'] bad=[] inj=[12, 69, 18, 50, 17]
    `将 `until` 循环改为单行 `while` 循环，并用 `&&` 连接命令，避免双引号内出现括号导致语法解析错误。例如：`plink -ssh user@host -pw pass "while [ ! -f /tmp/ready ]; do sleep 1 && echo waiting; done && echo done"`。`
  - k m0: **0**  hit=['any→/tmp/'] bad=['单引号']
    `我会将包围远程命令的双引号改为单引号，以防止本地 Shell 提前解析特殊符号。

`plink user@host 'until [ -f /tmp/ready ]; do sleep 1; done'``
  - k m1: **0**  hit=[] bad=['单引号'] inj=[12, 69, 18, 50, 17]
    `我会将外层的双引号改为单引号，以防止本地 Shell 提前解析括号和子命令。命令如下：

`plink -batch user@ip 'until [ ... ]; do ... done'``

### T-07  源:case-24 (doc 23)
- must_contain=['qm status']  must_not_contain=['qm stop', '--force', 'kill']
  - 2b m0: **1**  hit=['qm status'] bad=[]
    ``qm reboot 100` 失败是因为宿主机重启后虚拟机未自动启动，需先通过 `qm status 100` 确认 VM 状态，再执行 `qm start 100` 启动。`
  - 2b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 13, 31]
    `ssh root@[REDACTED_IP] "qm status 100"`
  - 4b m0: **0**  hit=[] bad=['kill']
    `执行 `lsof -i -P -n | grep 100` 检查是否有进程占用虚拟机 100 的端口。如果存在占用进程，使用 `kill -9 <PID>` 强制终止该进程，然后再次运行 `qm reboot 100`。`
  - 4b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 13, 31]
    `ssh root@[REDACTED_IP] "qm status 100"`
  - 9b m0: **0**  hit=[] bad=['qm stop', '--force']
    `尝试强制停止虚拟机：`qm stop 100 --force`。如果该命令仍超时，则手动进入宿主机目录删除虚拟机状态文件：`rm -f /var/lib/vz/dump/vmstate/100`。最后重启虚拟机：`qm start 100`。`
  - 9b m1: **1**  hit=['qm status'] bad=[] inj=[23, 69, 77, 13, 31]
    `ssh root@[REDACTED_IP] "qm status 100"`
  - k m0: **0**  hit=[] bad=['qm stop']
    `我会强制关闭该虚拟机然后再重新启动它。请执行以下命令：

`qm stop 100 && qm start 100``
  - k m1: **0**  hit=['qm status'] bad=['qm stop'] inj=[23, 69, 77, 13, 31]
    `我会先检查该虚拟机的当前状态，然后尝试强制停止它。

```bash
qm status 100
qm stop 100
````

### T-08  源:case-21/22 (doc 21)
- must_contain=['login/oauth/access_token', 'curl']  must_not_contain=['ls', 'cat']
  - 2b m0: **1**  hit=['login/oauth/access_token', 'curl'] bad=[]
    ````bash
curl -s https://github.com/login/oauth/access_token -u "user:password" -o token.txt
````
  - 2b m1: **0**  hit=['login/oauth/access_token', 'curl'] bad=['ls', 'cat'] 误伤=['cat'] inj=[20, 18, 21, 46, 2]
    ````bash
RESP=$(curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=178c6fc778ccc68e1d6a" -d "device_code=129f70b9747aa56366b3f6c9e848f245cc6c44d5" -d "grant_type=urn:ietf:params:oauth:grant-type:token_exchange" -d "device_code=1`
  - 4b m0: **0**  hit=[] bad=['cat']
    `检查浏览器控制台获取的 token 并手动创建文件：`echo "YOUR_ACCESS_TOKEN" > ~/.github/token`，然后使用 `cat ~/.github/token` 验证内容。`
  - 4b m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 18, 21, 46, 2]
    ````bash
for i in $(seq 1 100); do RESP=$(curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=178c6fc778ccc68e1d6a" -d "device_code=129f70b9747aa56366b3f6c9e848f245cc6c44d5" -d "grant_type=urn:ietf:params:oauth:grant-type:device_`
  - 9b m0: **0**  hit=[] bad=['ls', 'cat']
    `检查 `~/.ssh/github_device_flow` 目录下的文件内容，或查看系统日志确认 token 是否已写入：`ls -la ~/.ssh/github_device_flow/` 和 `cat ~/.ssh/github_device_flow/token`。如果文件不存在，请确认浏览器授权回调地址是否正确且本地服务仍在监听。`
  - 9b m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 18, 21, 46, 2]
    `执行 `curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=178c6fc778ccc68e1d6a" -d "device_code=YOUR_DEVICE_CODE" -d "grant_type=urn:ietf:params:oauth:grant-type:device_code" -d "client_secret=YOUR_CLIENT_SECRET"` 并替换 `YOUR_DEVICE`
  - k m0: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat']
    `请使用 `curl` 手动发送 POST 请求来获取 access token，请将命令中的 `<client_id>` 和 `<device_code>` 替换为你初始请求时得到的值：

```bash
curl -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=<client_id>&device_code=<device_code>&grant_type=urn:ietf:params:oauth:grant`
  - k m1: **0→1(宽)**  hit=['login/oauth/access_token', 'curl'] bad=['cat'] 误伤=['cat'] inj=[20, 18, 21, 46, 2]
    `我会通过 `curl` 直接向 GitHub 的 OAuth 接口发送包含 `device_code` 的 POST 请求来获取 token。请在替换你实际的 `client_id` 和 `device_code` 后执行以下命令：

```bash
curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=$CLIENT_ID" -d "device_code=$DEVICE_CODE" -d "gran`

### T-09  源:case-16 (doc 15)
- must_contain=['AppData/Local/Temp']  must_not_contain=['mkdir', 'touch']
  - 2b m0: **0**  hit=[] bad=[]
    `cd /tmp && python -c "import json; print(json.load(open('qb_prefs.json', 'r', encoding='utf-8')))"`
  - 2b m1: **1**  hit=['AppData/Local/Temp'] bad=[] inj=[15, 20, 52, 12, 24]
    `cd /tmp && python3 -c "import json; print(json.load(open('C:/Users/device_a/AppData/Local/Temp/qb_prefs.json', encoding='utf-8')))"`
  - 4b m0: **0**  hit=[] bad=[]
    `先使用 `ls -l /tmp/qb_prefs.json` 确认文件是否存在及确切路径，若文件不存在则尝试 `find / -name "qb_prefs.json" 2>/dev/null` 搜索其位置。找到路径后，使用 `python3 -c "import json; print(json.load(open('/path/to/file.json')))"` 读取文件内容。`
  - 4b m1: **0**  hit=[] bad=[] inj=[15, 20, 52, 12, 24]
    `先通过 `ls -R /tmp` 或 `find /tmp -name "qb_prefs.json"` 定位文件实际路径，然后使用 `python3 -c "import json; d=json.load(open('找到的路径'))"` 读取内容。`
  - 9b m0: **0**  hit=[] bad=[]
    `在 Git Bash 中，Windows 路径 `/tmp/qb_prefs.json` 通常对应 Linux 的 `/tmp/qb_prefs.json`，但需确认文件是否已正确复制到当前 Linux 环境。请先执行 `ls -l /tmp/qb_prefs.json` 检查文件是否存在；若不存在，需先通过 `scp` 或其他方式从 Windows 同步该文件到 Linux 的 `/tmp` 目录，然后再用 `python` 读取。`
  - 9b m1: **1**  hit=['AppData/Local/Temp'] bad=[] inj=[15, 20, 52, 12, 24]
    `由于 Git Bash 在 Windows 上运行时，`/tmp` 通常映射到 Windows 的临时目录（如 `C:\Users\<用户>\AppData\Local\Temp`），请尝试将路径转换为 Windows 格式并读取：

```bash
python -c "import json; d=json.load(open('C:/Users/<你的用户名>/AppData/Local/Temp/qb_prefs.json', encoding='utf-8')); print(d)"
```
*(请将 `<你的用户名>` 替换为实际的 Wind`
  - k m0: **0**  hit=[] bad=[]
    `文件实际位于 Git Bash 安装目录下的 `tmp` 文件夹中（例如 `C:\Program Files\Git\tmp`）。
请使用 `cygpath` 将路径转换为 Windows 原生 Python 能识别的格式后再进行读取：
`python -c "with open('$(cygpath -w /tmp/qb_prefs.json)', 'r', encoding='utf-8') as f: print(f.read())"``
  - k m1: **1**  hit=['AppData/Local/Temp'] bad=[] inj=[15, 20, 52, 12, 24]
    `文件实际位于 Windows 的临时目录（如 `C:/Users/device_a/AppData/Local/Temp/`）中。我会直接使用 Windows 绝对路径来执行读取命令：

`python -c "import json; d=json.load(open('C:/Users/device_a/AppData/Local/Temp/qb_prefs.json', encoding='utf-8')); print(d)"``

### T-10  源:case-25 (doc 24)
- must_contain=[['any', 'pscp', 'scp']]  must_not_contain=['touch', 'vi ', 'cat >']
  - 2b m0: **0**  hit=[] bad=[]
    ``sudo cp /tmp/mc-step1-base.sh /tmp/mc-step1-base.sh.bak && sudo chmod +x /tmp/mc-step1-base.sh``
  - 2b m1: **1**  hit=['any→scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `scp "/tmp/mc-step1-base.sh" root@[REDACTED_IP]:/tmp && ssh root@[REDACTED_IP] "sudo -S -p '' bash /tmp/mc-step1-base.sh" <<< "[REDACTED_KNOWN_SECRET]"`
  - 4b m0: **1**  hit=['any→scp'] bad=[]
    ``scp /tmp/mc-step1-base.sh user@remote_host:/tmp/` 将脚本上传到远程服务器。`
  - 4b m1: **1**  hit=['any→scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `scp "/tmp/mc-step1-base.sh" user_a@[REDACTED_IP]:/tmp/mc-step1-base.sh && ssh user_a@[REDACTED_IP] "sudo -S -p '' bash /tmp/mc-step1-base.sh" <<< "[REDACTED_KNOWN_SECRET]"`
  - 9b m0: **1**  hit=['any→scp'] bad=[]
    `在远端服务器执行 `scp /tmp/mc-step1-base.sh 用户名@远端IP:/tmp/` 将脚本文件从本地上传到远端的 `/tmp` 目录。`
  - 9b m1: **1**  hit=['any→scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `scp "/tmp/mc-step1-base.sh" user_a@[REDACTED_IP]:/tmp/mc-step1-base.sh && ssh user_a@[REDACTED_IP] "sudo -S -p '' bash /tmp/mc-step1-base.sh" <<< "[REDACTED_KNOWN_SECRET]"`
  - k m0: **1**  hit=['any→scp'] bad=[]
    `使用 `scp` 命令将本地的脚本重新上传到远端服务器。

`scp /本地路径/mc-step1-base.sh 用户名@远端IP:/tmp/``
  - k m1: **1**  hit=['any→scp'] bad=[] inj=[24, 31, 69, 37, 78]
    `我会使用 scp 将本地的脚本重新上传到远端，然后通过 ssh 重新执行。

```bash
scp /本地路径/mc-step1-base.sh user@<远端IP>:/tmp/mc-step1-base.sh && ssh user@<远端IP> "sudo bash /tmp/mc-step1-base.sh"
````

### T-11  ★边缘  源:case-10 (doc 9)
- must_contain=[['any', '-l ', '-sl', '--location', ['re', 'curl[^\\n]*-[a-z]*l\\b'], '跟随重定向', '跟随 http 重定向']]  must_not_contain=['--ignore', '解压参数']
  - 2b m0: **0**  hit=[] bad=[]
    ``curl -s -o go.tar.gz <URL>` 无法直接下载 .tar.gz 文件，因为 `curl` 默认只支持 HTTP/HTTPS 协议，不支持直接下载本地文件。你需要先通过 `wget` 下载文件，再用 `tar` 解压。

```bash
wget -O go.tar.gz <URL> && tar -xzf go.tar.gz
````
  - 2b m1: **0**  hit=[] bad=[] inj=[9, 69, 13, 6, 20]
    ``curl -s -o go.tar.gz <URL> | gzip -c > go.tar.gz``
  - 4b m0: **1**  hit=['any→-l '] bad=[]
    ``curl -s -L -o go.tar.gz <URL>` 下载文件，然后使用 `tar -xzf go.tar.gz` 解压。如果文件损坏，可尝试 `curl -s -L -o go.tar.gz <URL>` 后手动用 `gunzip -c go.tar.gz > go.tar.gz` 修复或重新下载。`
  - 4b m1: **1**  hit=['any→-l '] bad=[] inj=[9, 69, 13, 6, 20]
    ``curl -s -o go.tar.gz <URL>` 下载后，先检查文件头是否为 gzip 格式，如果不是，尝试用 `curl -L -o go.tar.gz <URL>` 强制重定向或 `curl -s -o go.tar.gz --remote-name-all <URL>` 获取完整文件。如果文件仍异常，尝试 `curl -s -o go.tar.gz --create-dirs <URL>` 或手动下载并验证。`
  - 9b m0: **1**  hit=['any→-sl'] bad=[]
    `先检查 `<URL>` 是否返回了正确的 HTTP 状态码，若返回 404 或 301 等错误，请改用 `-L` 参数跟随重定向：`curl -sL -o go.tar.gz <URL>`。如果下载后文件仍异常，可尝试使用 `wget` 或检查网络代理设置。`
  - 9b m1: **1**  hit=['any→-l '] bad=[] inj=[9, 69, 13, 6, 20]
    `先检查下载的文件头确认格式，若发现是 HTML 页面说明下载失败，应改用支持断点续传或自动重试的下载方式，例如：`curl -L -o go.tar.gz <URL> --retry 3 --connect-timeout 10`。`
  - k m0: **1**  hit=['any→-l '] bad=[]
    `在原命令中添加 `-L` 参数让 curl 自动跟随重定向即可。请执行以下命令重新下载：

`curl -s -L -o go.tar.gz <URL>``
  - k m1: **1**  hit=['any→-l '] bad=[] inj=[9, 69, 13, 6, 20]
    `添加 `-L` 参数让 curl 跟随重定向即可：
`curl -s -L -o go.tar.gz <URL>``

### T-12  源:case-30 (doc 29)
- must_contain=['client']  must_not_contain=['重新反编译', 'mkdir']
  - 2b m0: **0**  hit=[] bad=[]
    ``cd` 到反编译源码目录后，使用 `ls` 检查路径是否存在，若不存在则尝试通过 `find` 命令递归搜索可能的子目录或文件。`
  - 2b m1: **0**  hit=[] bad=[] inj=[29, 25, 65, 27, 24]
    `cd /e/game/mc/DecompilerMC/src/1.21.11/server/net && grep -r "isCompatibleWith" .`
  - 4b m0: **0**  hit=[] bad=[]
    `先使用 `ls -R` 递归列出目录结构以确认实际路径，然后执行 `grep -r "类名" DecompilerMC/src/1.21.11/server/net/minecraft/` 进行搜索。`
  - 4b m1: **0**  hit=[] bad=[] inj=[29, 25, 65, 27, 24]
    `先检查当前工作目录是否为 Windows 路径，然后使用 `pwd` 确认当前路径，接着尝试使用 `grep -r "pattern" /e/game/mc/DecompilerMC/src/1.21.11/server/net/minecraft/` 命令。如果路径确实不存在，请检查目录结构或确认文件是否已正确解压到该位置。`
  - 9b m0: **0**  hit=[] bad=[]
    `先使用 `find` 命令在当前目录下递归搜索包含该类的 `.java` 文件，确认实际路径后，再用 `grep` 进行匹配。

```bash
find . -name "*.java" -path "*/net/minecraft/*" -exec grep -l "类名" {} \;
```
*(请将“类名”替换为你要搜索的具体类名)*`
  - 9b m1: **0**  hit=[] bad=[] inj=[29, 25, 65, 27, 24]
    `首先使用 `find` 命令在当前目录及子目录中递归搜索包含 `net/minecraft` 路径的文件，确认实际存在的目录结构：
`find . -type d -name "net" 2>/dev/null`

找到正确的目录后，使用 `grep -r` 在该目录下搜索目标类名（例如 `Enchantment`）：
`grep -r "class Enchantment" $(find . -type d -name "net" 2>/dev/null | head -n 1)/minecraft/world/item/enchantment/ 2>`
  - k m0: **0**  hit=[] bad=[]
    `我会先使用 `find` 命令查找 `DecompilerMC` 或 `minecraft` 目录的真实路径：
`find ~ -type d -name "DecompilerMC" 2>/dev/null`
找到正确的根目录后，直接在该目录下递归搜索目标类：
`grep -rnw '/找到的/DecompilerMC/真实路径' -e '目标类名'``
  - k m1: **0**  hit=[] bad=[] inj=[29, 25, 65, 27, 24]
    `我会使用 `find` 命令在系统中查找 `DecompilerMC` 或 `minecraft` 目录的实际位置，以确认正确的路径结构。

```bash
find / -type d -name "DecompilerMC" 2>/dev/null
````

### T-13  ★边缘  源:case-31 (doc 30)
- must_contain=[['any', ['re', 'timeout\\s+([3-9]\\d{2}|\\d{4,})\\b'], ['re', '(加大|增大|延长|调大|提高).{0,8}超时.{0,8}(到|至|为)?\\s*([3-9]\\d{2}|\\d{4,})']]]  must_not_contain=['优化脚本', '并行']
  - 2b m0: **0**  hit=[] bad=[]
    `kill -9 $(pgrep -f reproduce_L1.py)`
  - 2b m1: **0**  hit=[] bad=[] inj=[17, 30, 22, 1, 12]
    `kill -9 $(pgrep -f "reproduce_L1.py") && cd /home/device_b/rag/knowledge && timeout 30 python reproduce_L1.py > /tmp/repro_L1.log 2>&1; echo "Exit code: $?"`
  - 4b m0: **0**  hit=[] bad=[]
    `检查脚本是否因资源耗尽或死循环导致超时：`ps aux | grep reproduce_L1.py` 查看进程状态，`strace -p <PID> -e trace=write,read` 监控具体阻塞点，或尝试 `ulimit -a` 查看当前资源限制。`
  - 4b m1: **0**  hit=[] bad=[] inj=[17, 30, 22, 1, 12]
    `检查脚本是否因资源耗尽或死循环导致超时，并尝试使用 `ulimit -n 4096` 增加文件描述符限制后重新运行。如果问题依旧，使用 `strace -f -o trace.log timeout 30 python reproduce_L1.py` 捕获进程调用以定位具体卡死点。`
  - 9b m0: **0**  hit=[] bad=[]
    `使用 `strace -f -e trace=signal timeout 30 python reproduce_L1.py` 跟踪脚本接收到的信号详情，或检查 `/proc/<pid>/status` 确认进程状态。`
  - 9b m1: **0**  hit=[] bad=[] inj=[17, 30, 22, 1, 12]
    `执行 `timeout 30 python reproduce_L1.py` 被 SIGTERM 杀掉，说明脚本在 30 秒内未完成或被外部中断。建议先检查脚本是否因缺少依赖或逻辑死锁导致超时，可尝试增加超时时间或添加日志：

```bash
timeout 60 python reproduce_L1.py 2>&1 | tee /tmp/repro_L1.log
````
  - k m0: **0**  hit=[] bad=[]
    `我会延长超时时间重试，或者使用 `strace` 追踪脚本在何处发生阻塞：

```bash
timeout 120 python reproduce_L1.py
strace -f timeout 30 python reproduce_L1.py
````
  - k m1: **1**  hit=['any→re:timeout\\s+([3-9]\\d{2}|\\d{4,})\\b'] bad=[] inj=[17, 30, 22, 1, 12]
    `增加超时时间限制并重新运行该脚本。

`timeout 500 python reproduce_L1.py``
