# Linux Server Daily Report

面向 **Debian GNU/Linux 9.8（Stretch）家庭服务器**的 Python 健康日报。脚本采集 Linux 状态并生成中文摘要；企业微信通知和用户级 systemd timer 都是需要用户自行配置的可选功能。

> 本项目的目标环境是 Debian 9.8。源码已按 Python 3.5 标准库接口做兼容处理，但本次没有 Debian 9.8 实机可供验证。Debian Stretch 的 Python 3 包为 Python 3.5 系列；请在目标机运行 `python3 --version` 确认。已在 WSL Ubuntu 24.04 运行的测试仅代表该测试环境，不能证明 Debian 9.8 上兼容。

Debian 官方 Stretch 页面说明该版本已停止安全更新；继续使用前应由系统管理员评估系统维护和暴露面：[Debian Stretch 发布信息](https://www.debian.org/releases/stretch/)。

## A. 获取项目和适用环境

公开仓库地址为 [Valkyrie486/linux-server-daily-report](https://github.com/Valkyrie486/linux-server-daily-report)。在 Debian 终端中运行以下命令获取项目。建议克隆到当前用户主目录，这与后续示例及 user-level service 的默认路径一致：

```bash
cd "$HOME"
git clone https://github.com/Valkyrie486/linux-server-daily-report.git
cd linux-server-daily-report
```

获取项目需要 Git 客户端；脚本运行本身不依赖 Git。可用 `command -v git` 检查。如果命令不存在，请由管理员通过已配置的 Debian 官方软件源安装 Git；Stretch 软件源已归档时，遵循上文关于官方归档源的说明。

克隆完成后，项目根目录应包含以下文件：

```text
linux-server-daily-report/
├── README.md
├── LICENSE
├── scripts/asus-daily-summary.py
├── systemd/asus-daily-summary.service
├── systemd/asus-daily-summary.timer
├── systemd/asus-daily-summary.env.example
└── tests/test_daily_summary.py
```

本项目不是 Windows 桌面应用。**所有 Linux 命令都在 Debian 9.8 终端执行**，不要双击 Python 文件，也不要在 Windows 原生 PowerShell 直接运行 Linux 采集脚本。WSL Ubuntu 24.04 只用于本地开发测试，不是本项目本次声明支持的目标系统。

本仓库不包含 Bash 巡检脚本、生产 systemd 单元或生产报告生成器。已知服务器上的系统级 `old-asus-auto-check` 每日巡检是独立部署；克隆或解压本项目不会安装或运行它。

## B. 首次使用前：软件、权限和数据源

### 必需条件

- Debian GNU/Linux 9.8 shell 和完整项目目录。
- Python **3.5 或更高版本**。脚本只用 Python 标准库，不要求 `pip` 或额外 Python 包。Debian Stretch 的 `python3` 包属于 Python 3.5 系列（见 [Debian Stretch Python 源码包](https://sources.debian.org/src/python3.5/)）。为兼容 3.5，源码已将 Python 3.7 才有的 `subprocess.run(capture_output=..., text=...)` 和 `datetime.fromisoformat()` 分别替换为较早可用的 subprocess 参数和本地 ISO 时间解析；目标机仍需实测。
- 运行脚本的普通用户可写自己的 `$HOME`。不需要 root；不要用 `sudo` 跑个人日报，否则日志会写入 root 的主目录并可能改变文件属主。

检查终端和 Python：

```bash
cd "$HOME/linux-server-daily-report"
pwd
command -v python3
python3 --version
```

`pwd` 应显示项目根目录；`command -v` 应显示 Python 路径；版本应为 `3.5` 或更高。若命令找不到，在 Debian 上可由管理员通过已配置的 Debian 官方软件源安装 `python3`：

```bash
sudo apt-get update
sudo apt-get install python3
```

Stretch 已归档；如果官方源不可用，不要运行来路不明的安装脚本、不要覆盖 `/usr/bin/python` 或系统 Python 链接。让系统管理员使用 Debian 官方归档源处理，随后重新检查 `python3 --version`。若低于 3.5，本脚本不支持该解释器；不要继续运行。

### 完整主机信息依赖

Python 能启动不等于所有巡检数据都能采集。脚本读取 `/proc`、`/sys`，并调用系统命令。按实际代码，信息来源如下：

| 依赖/配置 | 用途 | 缺失或不可读时 |
|---|---|---|
| Linux `/proc`、`/sys`、`df`、`uptime` | 负载、内存、温度和文件系统容量 | 相应项目未知/失败；磁盘检查路径固定为 `/`、`/home`、`/boot`、`/mnt/toshiba` |
| `systemctl`（完整服务状态需要） | 检查 `sshd`、`smb`、`docker`、`firewalld`、`NetworkManager` 和 `nmb` 状态 | 前五个预设服务中任何一个不是 active 都会产生严重告警，目标机未安装该服务时也可能如此；`nmb` 的 active/inactive 都接受，其他状态产生注意项 |
| `journalctl`（完整日志检查需要） | 最近 24 小时内核和系统错误日志 | 仅在命令成功且 stderr 无权限提示或其他警告时计数；权限不足、非零退出或读取范围不完整会显示未知/未检查，绝不当作零条错误 |
| Beszel SQLite 数据库 | 主机状态、告警、SMART 记录 | 默认路径 `~/services/beszel/data/data.db`；缺失/不可读时对应项目未知。数据库以只读模式打开 |
| `BESZEL_SYSTEM_ID` | 在数据库中匹配目标主机和 SMART | 默认未设置；空值时主机及 SMART 匹配未知，即使数据库存在 |
| Docker CLI 和 daemon 权限 | 检查全部容器，包括已退出容器 | 命令/daemon 不可用时显示未知；退出容器或 unhealthy 容器会显示异常 |
| Beszel SMART 上报 | 读取 `/dev/sda` 和 `/dev/sdb` 的数据库记录 | 脚本不直接运行 `smartctl`；设备未上报、ID 不匹配或数据超过 30 分钟会显示未知 |
| 既有 root 巡检报告 | 在日报中汇总独立的每日 root 巡检结果 | 报告不由本仓库生成；当天文件缺失、命名/内容不匹配或不可读时显示未知 |
| 企业微信机器人密钥和网络 | `--send` 发送通知 | 可选；未配置密钥时跳过；网络/API 错误时记录发送失败 |
| systemd user manager | 可选的用户级定时任务 | 没有运行中的 user manager 时无法安装/触发 timer |

脚本还固定检查上述 systemd 服务名单和磁盘/设备基线。前五个固定服务中的缺失/非 active 服务会被计为严重告警；尤其 Debian 9.8 未必使用这些服务名称。`nmb` 是例外：`active` 和 `inactive` 均视为可接受，其他状态计为注意。磁盘挂载布局或设备名若不同，报告也可能出现未知或不匹配。这些状态既不等于自动确认系统故障，也不等于检查正常。应先核对报告中的具体项目，不要仅为消除告警而安装或启动服务。

### Beszel 配置（可选）

只有你确实使用 Beszel 并希望匹配本机数据时才需要设置。示例文件由 systemd service 通过 `EnvironmentFile` 读取；Python 不会自动加载 `.env`。以下命令在项目根目录、Debian 普通用户终端执行，不需要 `sudo`：

```bash
mkdir -p "$HOME/.config/asus-daily"
cp systemd/asus-daily-summary.env.example "$HOME/.config/asus-daily/asus-daily-summary.env"
chmod 600 "$HOME/.config/asus-daily/asus-daily-summary.env"
nano "$HOME/.config/asus-daily/asus-daily-summary.env"
```

在文件中将 `BESZEL_SYSTEM_ID=replace-with-your-own-system-id` 替换为自己 Beszel 中的系统 ID。暂时没有 ID 时，删除或注释该占位行；不要保留占位字符串。可选变量：`BESZEL_DB` 覆盖数据库路径，`BESZEL_URL` 只作为报告链接，`ROOT_PATROL_DIRS` 覆盖 root 报告搜索目录（Debian/Linux 用冒号分隔）。不要把密钥、真实 webhook URL、数据库或报告提交进 Git。

如果没有 `nano`，先用系统中已有的文本编辑器；也可以跳过 Beszel 配置进行第一次 dry-run。手动运行要使用配置时，在同一个 Debian shell 中显式加载：

```bash
set -a
. "$HOME/.config/asus-daily/asus-daily-summary.env"
set +a
```

没有配置文件时不要执行上述加载命令；未设置的可选变量由程序使用默认值。

### 企业微信机器人通知（完全可选）

通知不会因下载项目而自动可用。只有在自己的企业微信环境中创建群机器人并从机器人设置中取得 Webhook key 后，才能发送。请不要把真实 key 粘贴到聊天、工单或代码仓库；拥有 key 的人可以向对应群发送消息。

key 文件固定为 `$HOME/.config/asus-daily/wecom.key`，内容只能是 key 本身，不能是完整 Webhook URL。以下在 Debian 普通用户 Bash 终端中执行，不需要 `sudo`。`read -s` 会隐藏输入；输入仅写入本机文件，不显示在终端：

```bash
umask 077
mkdir -p "$HOME/.config/asus-daily"
read -r -s -p "粘贴企业微信机器人 key: " WECOM_KEY
printf '\n'
printf '%s' "$WECOM_KEY" > "$HOME/.config/asus-daily/wecom.key"
unset WECOM_KEY
chmod 600 "$HOME/.config/asus-daily/wecom.key"
stat -c 'owner=%U mode=%a path=%n' "$HOME/.config/asus-daily/wecom.key"
```

输出中的 owner 必须是运行脚本的当前用户，mode 必须为 `600`（代码也接受 `400`）；目录由 `umask 077` 创建，只有当前用户可访问。错误属主或权限会被拒绝。不要用 `sudo` 创建文件，否则属主可能错误。若 key 已存在，不要重复覆盖，先核对 `stat` 输出。

`python3 scripts/asus-daily-summary.py --dry-run` 是不发送的检查方式：它不读取 key 文件，也不调用企业微信接口。只有你确认愿意向真实群聊发送时，才在 Debian 项目根目录运行 `python3 scripts/asus-daily-summary.py --send`；有效 key 下的这条命令、手动启动下文的 systemd service，及 timer 到时执行都会发送真实消息。超长时仅发送内容会被截断，本机日志仍保存完整报告。

行为按代码区分：key 文件不存在时 `--send` 记录 `SKIPPED` 和完整报告并以 0 退出；文件属主/权限/内容不合要求时拒绝发送、记录校验失败和完整报告并非零退出；网络异常返回 1 并记录完整报告；企业微信返回非零 `errcode` 时返回 2 并记录完整报告；`errcode=0` 表示接口报告成功并记录完整报告。网络错误只记录异常类型以避免把 URL 中的 key 写入日志。即使接口返回成功，也应按自己的企业微信设置确认消息到达。key 不能提交到 GitHub，因为公开后任何人都可能使用它向群里发消息；也不要发到聊天窗口，因为聊天记录和转发可能扩大泄露范围。

## C. 第一次安全运行：只生成，不发送

下面命令在 Debian 9.8 终端运行，不需要 `sudo`。项目应位于 `$HOME/linux-server-daily-report`；否则将 `cd` 命令改成你的实际项目根目录。

1. 确认位置和核心文件：

   ```bash
   cd "$HOME/linux-server-daily-report"
   pwd
   test -f scripts/asus-daily-summary.py && echo "项目文件已找到"
   ```

   预期末行显示“项目文件已找到”。若没有，停止并找到正确的解压/克隆目录；不要在错误目录继续。

2. 编译检查：

   ```bash
   python3 -m py_compile scripts/asus-daily-summary.py
   ```

   成功时无错误输出并返回 0；失败时记录完整错误并确认 `python3 --version` 至少为 3.5。此命令会创建被 `.gitignore` 忽略的 `__pycache__`。

3. 生成报告：

   ```bash
   python3 scripts/asus-daily-summary.py --dry-run
   ```

   成功时终端打印完整中文摘要，并出现 `[dry-run] 不发送`、`报告已保存：.../latest.md`、`当日日志：.../YYYY-MM-DD.log`。它不会读取企业微信密钥、调用发送接口、启用 timer 或修改系统服务。它会在自己的日志目录写文件，并尝试清理其中超过 30 天的 `.log`。

   退出码 0 表示报告采集流程和保存完成，不表示所有巡检项正常。缺少 Beszel、Docker、systemd/journal、传感器或 root 报告时出现未知/严重项，第一次测试中可能是预期结果。出现 `REPORT_SAVE_ERROR` 或退出码 3 表示报告保存失败；检查自己主目录权限和磁盘空间。

**首次不要运行 `--send`。** 只有在已经配置真实企业微信机器人密钥且确认愿意向群发送时才使用该参数；有密钥的手动 service 测试也会尝试真实发送。

## D. 报告、日志与执行记录分别在哪里

`~` 按运行程序的 Linux 用户展开。先用 `printf '%s\n' "$HOME"` 查看实际主目录；例如若它是 `/home/你的用户名`，Python 日报目录就是 `/home/你的用户名/asus-logs/daily-summary/`。root 运行时路径会变成 `/root/...`，所以 Python 日报应以普通用户运行。

### 1. Python 中文摘要

`--dry-run` 把完整摘要打印到终端，并同步保存。日志目录由代码中的 `LOG_DIR` 确定：`$HOME/asus-logs/daily-summary/`，目录权限 0700。

- `latest.md`：最近一次报告，每次运行覆盖，权限 0600。
- `YYYY-MM-DD.log`：按运行当天日期命名，追加每次运行的状态和报告，例如 `2026-10-25.log`；dry-run 记录明确的 `result=DRY RUN (not sent)`，超过 30 天的 `.log` 会尝试清理，权限 0600。

报告时间取 Debian 系统本地时钟，但当前代码在标题中固定附加 `Asia/Shanghai` 字样。若目标机时区不是 Asia/Shanghai，这个标签可能与实际本地时区不符；用 `timedatectl` 核对系统时区。timer 也按系统本地时区触发。

查看刚生成的摘要和当天日志（Debian 普通用户终端，无需 sudo）：

```bash
ls -la "$HOME/asus-logs/daily-summary"
cat "$HOME/asus-logs/daily-summary/latest.md"
tail -n 40 "$HOME/asus-logs/daily-summary/$(date +%F).log"
```

### 2. systemd service 执行记录

这是 systemd 捕获的 service 标准输出、标准错误和退出状态，**不是**上面的摘要文件。查看当前用户的服务状态和最近日志：

```bash
systemctl --user status asus-daily-summary.service
journalctl --user -u asus-daily-summary.service --since today --no-pager
```

unit 尚未安装时会显示找不到单元；service 尚未运行时 journal 没有记录是正常的。Debian 9.8 上需确认 systemd user manager 正在运行；WSL Ubuntu 24.04 的结果不验证 Debian 9.8 的 user manager 行为。

### 3. 系统级 root 巡检报告

这是一项服务器外部的既有生产任务，不是 Python 日报日志，也不是 systemd 的执行日志。已知生产任务每天 08:00 运行；本仓库不含其已核实生产脚本或 system-level unit，也不会生成它的报告。

Python 只在 `ROOT_PATROL_DIRS` 指定目录中读取符合命名和内容格式的既有报告：`old-asus-auto-check-YYYYMMDD-*.txt`，或 `history/old-asus-auto-check-YYYY-MM-DD.log`。默认目录是运行用户的 `~/asus-logs` 和 `/var/log/old-asus-auto-check`。列出候选文件：

```bash
find "$HOME/asus-logs" /var/log/old-asus-auto-check -type f \( -name 'old-asus-auto-check-*.txt' -o -name 'old-asus-auto-check-*.log' \) -print 2>/dev/null
```

查看生产 unit 状态（仅查看，不修改；这两条命令需要 sudo 读取系统级 unit）：

```bash
sudo systemctl status old-asus-auto-check.timer old-asus-auto-check.service
sudo journalctl -u old-asus-auto-check.service --since today --no-pager
```

如果 unit 或报告不存在，说明这台系统没有该任务、目录不同或权限不足；不要把 Python 的未知状态当成 root 巡检已经执行。

## E. 如何判断成功

以下是**示例格式，不是 Debian 服务器实测结果**：

```text
[dry-run] 不发送；严重=... 注意=... 未知=...；字节=...
[dry-run] 报告已保存：/home/你的用户名/asus-logs/daily-summary/latest.md
[dry-run] 当日日志：/home/你的用户名/asus-logs/daily-summary/2026-10-25.log
```

- 报告生成成功：命令退出码为 0，并打印两个保存路径；可以用 `cat "$HOME/asus-logs/daily-summary/latest.md"` 查看。
- 巡检结果正常：每个相关数据源可读，检查项达到脚本阈值。报告本身成功生成不代表这一点。
- `正常` 表示对应检查有数据且达到阈值；`注意` 表示有告警但没有达到严重级别；`严重异常` 表示命中严重规则；`未知/未检查` 表示缺配置、无权限、依赖不存在、数据过期或报告缺失。总体状态按严重异常、注意、未知/未检查、正常的优先级汇总，并同时列出各类计数；未知不会算作正常。
- 没有 Beszel DB、`BESZEL_SYSTEM_ID`、Docker 权限或当天 root 报告时，相关项目可能是未知。未知不能自动解释成机器坏了，也不能解释成机器健康。
- 日志保存失败时，报告文字仍可能已打印到终端，但不能认为文件已保存；根据 `REPORT_SAVE_ERROR` 的异常类型检查目录权限和剩余磁盘空间。

## F. 常见问题

除明确标注 `sudo` 外，以下命令均在 Debian 普通用户终端执行。

- **Python 找不到或版本过低**：`command -v python3; python3 --version`。最低 3.5；低于 3.5 时不要运行。通过 Debian 官方包源安装 Python 3；若 Stretch 软件源不可用，交由管理员使用 Debian 官方归档源处理，不要下载第三方安装脚本，也不要改系统 Python 链接。
- **命令找不到**：`command -v df uptime systemctl journalctl docker find`。缺哪个命令就会影响相应采集：`systemctl`/`journalctl` 可能导致服务/日志未知，`docker` 导致容器未知，`find` 影响过期日志清理。不要把命令缺失误判为服务故障。
- **配置变量未设置**：`printf 'BESZEL_DB=%s\nBESZEL_SYSTEM_ID=%s\nROOT_PATROL_DIRS=%s\n' "${BESZEL_DB:-默认数据库路径}" "${BESZEL_SYSTEM_ID:-未设置}" "${ROOT_PATROL_DIRS:-默认目录}"`。先确认配置文件已编辑；手动启动需按 B 节显式加载，systemd 启动由 `EnvironmentFile` 加载。
- **Beszel 数据库不存在或不可读**：`ls -l "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"`、`namei -l "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"`、`test -r "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"; echo $?`。文件不存在或权限不足会显示未知；不要授予写权限，程序以只读 SQLite 打开。
- **`BESZEL_SYSTEM_ID` 没配或不匹配**：`printf '%s\n' "${BESZEL_SYSTEM_ID:-未设置}"`。空值或错误 ID 会让主机/SMART 匹配未知；到自己的 Beszel 实例核对 ID。
- **当天 root 巡检报告缺失**：运行 D 节的 `find` 命令，并检查 `ROOT_PATROL_DIRS`、日期、文件名和普通用户读权限。该报告来自独立生产任务，本仓库不会创建它。
- **Docker 命令不可用或权限不足**：`command -v docker; docker ps -a`。命令不存在、daemon 未运行或 socket 不可访问时，容器项为未知；已退出容器会被检查并报异常。不要为了试运行随意给用户 Docker 高权限。
- **SMART 没有上报**：确认 Beszel agent/数据库含目标设备、`BESZEL_SYSTEM_ID` 正确、数据更新时间在 30 分钟内。缺失或过期时是未知，不代表磁盘通过 SMART 检查。
- **journalctl 权限不足或读取不完整**：报告会将对应的 24 小时内核/系统日志项标为未知/未检查，不会将不可见日志当作 0 条错误。可在当前普通用户终端运行 `journalctl --since '24 hours ago' -p err --no-pager` 查看是否显示权限提示，并运行 `id` 查看当前组；程序不会自动使用 sudo 或更改日志访问组。若命令非零退出、stderr 有权限提示或其他警告，必须先由管理员判断是否授权完整读取，再重试。
- **目标服务不存在**：固定的 `sshd`、`smb`、`docker`、`firewalld`、`NetworkManager` 任一不是 active 都会按严重项计数，未安装也会触发；`nmb` 的 inactive 状态可接受，未知状态为注意。可检查 `systemctl is-active sshd smb docker firewalld NetworkManager nmb`。Debian 9.8 的服务配置可能与该基线不同，不要仅为消除告警而安装或启动服务。
- **systemd service 不存在/失败**：`systemctl --user status asus-daily-summary.service`、`journalctl --user -u asus-daily-summary.service -b --no-pager`。不存在通常是 unit 未复制或未 `daemon-reload`；失败时核对 service `ExecStart` 文件路径、Python 路径、配置文件权限和日志目录。若提示无法连接 user bus，检查该系统的 user manager；这项尚未在 Debian 9.8 实机验证。
- **timer 没启用或没按时触发**：`systemctl --user is-enabled asus-daily-summary.timer`、`systemctl --user list-timers --all asus-daily-summary.timer`。看 `NEXT`、`LAST` 和 service journal；退出登录后不触发时再检查 `loginctl show-user "$USER" -p Linger`。只有在你确认需要注销后继续运行时，才让管理员评估 linger 设置。
- **企业微信密钥不存在**：`test -e "$HOME/.config/asus-daily/wecom.key"; echo $?`。不存在时 `--send` 只记 skip、不发送并返回 0；dry-run 不读取密钥。
- **密钥权限错误**：`stat -c '%U %a' "$HOME/.config/asus-daily/wecom.key"`。文件属主必须是运行用户，权限必须 600 或 400；可用 `chmod 600 "$HOME/.config/asus-daily/wecom.key"` 修正。错误时脚本拒绝发送并非零退出。
- **企业微信网络/API 请求失败**：查看 `journalctl --user -u asus-daily-summary.service --since today --no-pager` 或终端 `SEND_ERROR`。脚本只输出异常类型以避免泄露 URL 中的密钥；确认网络、DNS/TLS 和机器人配置后再决定是否手动重试。发送会向真实群聊发消息。
- **报告生成了但找不到**：运行 `printf '%s\n' "$HOME/asus-logs/daily-summary/latest.md"`、`ls -la "$HOME/asus-logs/daily-summary"`。查看最新报告用 `cat "$HOME/asus-logs/daily-summary/latest.md"`；查看当天日志用 `tail -n 40 "$HOME/asus-logs/daily-summary/$(date +%F).log"`。

## G. 每天 20:00 运行（可选 user-level timer）

当前 timer 模板默认每天 **08:02** 本地时间运行，**不是 20:00**。要改成每天晚上 8 点，需要复制单元、编辑 `OnCalendar` 并由当前普通用户启用。此 timer 只运行 Python 日报，不等于已有系统级每日 08:00 root 巡检。不要在第一次 dry-run 前启用。

启用前先确认目标机使用 systemd、当前用户有可用的 user manager 和用户总线（下列 `systemctl --user` 命令能连接），项目确实位于 service 中的 `$HOME/linux-server-daily-report` 路径，且你已经成功 dry-run。用户 manager 通常随登录会话运行；若用户退出后会话结束，timer 可能停止。`loginctl show-user "$USER" -p Linger` 显示 `Linger=yes` 时，manager 可在无登录会话时继续运行；启用 linger 会改变用户服务生命周期，应由管理员按需评估，通常需要管理员权限，不是本指南自动执行的步骤。

模板的 `Persistent=false` 表示 timer 停止期间错过的触发时间不会在 manager 恢复时补跑；它只等待未来的下一次日历时间。`EnvironmentFile=-%h/...` 中前导 `-` 表示配置文件不存在时 systemd 仍启动 service，未配置的 Beszel 项目会显示未知；文件存在时按该文件提供变量。部署前核对其中不含占位 system ID。**service 的 ExecStart 使用 `--send`：有效 key 存在时，手动启动 service 或定时触发都会向真实群聊发送消息。**

### 1. 先完成配置并验证手动运行

确保已按 C 节成功运行 `--dry-run`，且能找到 `$HOME/asus-logs/daily-summary/latest.md`。配置好 Beszel（如需）和 `EnvironmentFile`（可选）。脚本会在创建报告时建立日志目录；也可先显式创建：

```bash
mkdir -p "$HOME/asus-logs/daily-summary"
chmod 700 "$HOME/asus-logs/daily-summary"
```

### 2. 安装并改 timer 时间

以下都在 Debian 9.8 普通用户终端执行，不需要 `sudo`。确保项目位于 `$HOME/linux-server-daily-report`，因为 service 模板的 `ExecStart` 使用这个路径。

```bash
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/asus-daily"
cp systemd/asus-daily-summary.service systemd/asus-daily-summary.timer "$HOME/.config/systemd/user/"
nano "$HOME/.config/systemd/user/asus-daily-summary.timer"
```

如果此前已按 B 节配置 Beszel，保留现有 env 文件，不要用示例覆盖。若不使用 Beszel，可以没有该文件；service 的 `EnvironmentFile=-...` 前缀表示文件缺失时不报错。需要添加配置时，按 B 节复制示例、删除 ID 占位值，再编辑并限制权限：

```bash
nano "$HOME/.config/asus-daily/asus-daily-summary.env"
chmod 600 "$HOME/.config/asus-daily/asus-daily-summary.env"
```

在 timer 文件中把这一行：

```ini
OnCalendar=*-*-* 08:02:00
```

改成：

```ini
OnCalendar=*-*-* 20:00:00
```

该时间按 Debian 主机本地时区解释。保存文件后重新加载用户级 systemd；如果已改 service，也一起重新加载：

```bash
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/asus-daily-summary.service" "$HOME/.config/systemd/user/asus-daily-summary.timer"
```

verify 命令不可用或报错时不要继续启用，先根据诊断修正 unit，并确认 Debian 9.8 的 systemd 版本支持这些模板指令。

### 3. 查看状态、手动触发和日志

安装 unit 不等于 timer 已启用。启用前先确认 key：unit 的 `ExecStart` 固定带 `--send`；若有效密钥文件已存在，手动 start 或到点触发会向真实企业微信群发消息。没有密钥时 service 只记 skip。

查看 timer 是否启用、下一次触发时间和服务状态：

```bash
systemctl --user is-enabled asus-daily-summary.timer
systemctl --user list-timers --all asus-daily-summary.timer
systemctl --user status asus-daily-summary.timer asus-daily-summary.service
```

用户确认配置和发送行为后，才执行启用命令：

```bash
systemctl --user enable --now asus-daily-summary.timer
```

这会启用并启动 timer，不会立即运行 service。随后确认它确实处于启用状态，并查看 `NEXT`（下一次）和 `LAST`（最近一次）触发时间：

```bash
systemctl --user is-enabled asus-daily-summary.timer
systemctl --user list-timers --all asus-daily-summary.timer
systemctl --user status asus-daily-summary.timer
```

首次启用后 `LAST` 为空是正常的，尚未触发过；service 的执行结果需在手动触发或 timer 到时后查看。手动触发 service 的命令如下；再次提醒，有密钥时会真实发送：

```bash
systemctl --user start asus-daily-summary.service
```

查看最近执行的标准输出、错误和退出状态：

```bash
journalctl --user -u asus-daily-summary.service --since today --no-pager
```

### 4. 停用

停止并取消当前用户的日报定时器（不影响系统级 root 巡检）：

```bash
systemctl --user disable --now asus-daily-summary.timer
```

若还要移除 unit，只删除这两个明确的用户级文件，再 reload：

```bash
rm -f "$HOME/.config/systemd/user/asus-daily-summary.timer" "$HOME/.config/systemd/user/asus-daily-summary.service"
systemctl --user daemon-reload
```

## 验证边界

此前和本次可执行的本地检查包括：Python 编译、15 项单元测试、Python 3.5 语法特性模式下的静态语法解析、隔离 HOME 下的 WSL CLI dry-run（检查文件生成及权限）、以及 WSL Ubuntu 24.04 上的 systemd unit 静态检查。WSL dry-run 读写的是 WSL 自身的临时环境，不是 Debian 9.8 服务器。

**本次没有 Debian GNU/Linux 9.8 实机或虚拟机**，因此 Python 3.5 运行兼容性、Stretch systemd 指令兼容性及下列真实集成未完成端到端验证：Beszel schema/权限、Docker daemon、SMART 上报、root 巡检报告、journald 权限、user-level timer 实际触发、企业微信网络和消息发送。请在目标机先手动 dry-run，再自行按 G 节逐步启用；不要把 WSL 测试或静态检查当成 Debian 9.8 验收。

## 反馈问题与参与贡献

遇到缺陷时，请使用 [缺陷反馈表单](https://github.com/Valkyrie486/linux-server-daily-report/issues/new?template=bug_report.yml)；有新功能想法时，可以提交 [功能建议](https://github.com/Valkyrie486/linux-server-daily-report/issues/new?template=feature_request.yml)。也欢迎通过 Pull Request 改进代码、测试和中文文档。

提交前请阅读 [贡献指南](CONTRIBUTING.md)。请勿在 Issue、PR 或截图中公开企业微信 key、Webhook URL、私人配置、Beszel 数据库或未脱敏的生产日志。由于目标环境和部分外部集成尚未完成端到端验证，反馈时请注明真实测试环境，并明确区分已验证结果与推测。

## AI 辅助开发说明

开发过程中使用 AI 编程助手协助梳理需求和代码结构、检查 Bash 巡检脚本的发布边界、编写与修改 Python 代码、处理 Python 版本兼容性、分析问题、设计和补充自动化测试，以及整理安装配置步骤、README 和故障排查内容。未经确认的生产 Bash 脚本没有因此被纳入公开仓库。AI 是提高开发与文档工作的辅助工具；项目维护者提供运维背景和目标、确定需求、审阅并选择修改、核对可执行的检查结果，并负责是否发布的决定。项目不是由 AI 自主决定需求或独立验证上线。

AI 提供的代码、命令和说明不默认正确，仍需代码审查、静态检查、自动化测试，并在适用环境进行实际验证。本项目已有的本地检查包括 Python 编译、单元测试、Python 3.5 语法模式静态解析、隔离 HOME 下的 WSL CLI dry-run 和文件权限检查、Bash 命令块语法检查，以及 WSL Ubuntu 24.04 上的 systemd 单元静态检查。这些结果只证明相应检查在本机/WSL 环境通过；Debian GNU/Linux 9.8 未在真实机器或虚拟机验证。Beszel、Docker、SMART、root 巡检报告、journald 权限、timer 实际触发和企业微信通知均未完成端到端验证。测试环境与限制详见本节前的“验证边界”。

## 许可证

本项目采用 [GNU General Public License version 3.0（GPL-3.0）](LICENSE)，完整许可条款见仓库中的 [`LICENSE`](LICENSE) 文件及 [GNU 官方许可证页面](https://www.gnu.org/licenses/gpl-3.0.html)。个人和企业均可使用、修改和分发本项目，也可以收费分发；收费不免除遵守 GPL-3.0 条件的义务。分发本项目或修改版本时，必须按适用条款保留相关声明、提供 GPL-3.0 许可，并履行相应的对应源码提供等义务。
