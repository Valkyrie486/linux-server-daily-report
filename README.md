# Linux Server Daily Report

面向 Linux 家庭服务器的健康日报脚本：用 Python 采集主机状态，生成中文报告；配置企业微信机器人后，可选择发送报告。它是服务器运维项目，不是安装在 Windows 上即可运行的桌面软件。

## A. 这是什么，适用于谁

项目适合想学习 Linux 运维、Python 状态采集和 systemd 用户级定时任务的人。公开文件包含 Python 日报和可选的 `systemd --user` 模板。**仓库不包含可直接运行的 Bash 巡检脚本**；审阅过的本地主机专用 Bash 草稿含特定挂载点、服务和容器基线，因此没有纳入发布。部署副本和服务器专属代码均不在下载内容中。

请区分运行环境：

- **Windows 原生 PowerShell**：可查看、编辑项目，但不能直接运行脚本中的 Linux `/proc`、`systemctl`、`journalctl` 等采集逻辑。不要在 PowerShell 输入 Linux 命令。
- **WSL**：可以在 WSL 的 Linux shell 中运行 Python 脚本。WSL 看到的是 WSL 自身的 Linux 环境；它不自动等于另一台 Linux 服务器，也不一定有 systemd、Docker、Beszel 数据库或服务器报告。
- **Linux 主机**：是目标运行环境。第一次只运行 `--dry-run`；脚本会采集状态并写本用户的报告文件，但不会发送企业微信消息或启用定时器。

本工作区已在 WSL Ubuntu 24.04 中运行 Python 编译、3 项模拟单元测试、一次隔离 HOME 的手动 dry-run，并用 `systemd-analyze verify` 检查 unit 格式。dry-run 实际读取的是 WSL 自身状态，不是服务器状态；这些验证不等于真实服务器端到端验收。具体范围见文末“验证范围”。

## B. 首次执行前的准备

### 必需

- 一个 Linux shell：Linux 主机终端，或 WSL 发行版终端。需要能进入项目根目录（该目录中应有 `README.md`、`scripts/` 和 `systemd/`）。
- Python 3。脚本使用 Python 标准库，不需要通过 pip 安装第三方 Python 包。
- 当前用户对自己的主目录有写权限。日报会保存到该用户的 `~/asus-logs/daily-summary/`。

### 可选数据源和功能

没有这些组件也可以运行。对应检查会显示“未知/未检查”，不能把未知当成正常：

| 组件/配置 | 用途 | 默认及缺失时的表现 |
|---|---|---|
| Beszel SQLite 数据库 | 读取 Beszel 主机状态、告警和 SMART 设备数据 | 默认 `~/services/beszel/data/data.db`，以 SQLite 只读模式打开；文件不存在或无权限时对应项目为未知 |
| `BESZEL_SYSTEM_ID` | 在数据库中匹配目标主机及 SMART 记录 | 默认空；未配置时主机匹配和 SMART 会显示未知，即使数据库可读 |
| Docker CLI 和可访问的 Docker daemon | 检查运行中及已退出的容器 | 不可用时显示采集失败/未知；退出容器会报告异常 |
| `systemctl`、`journalctl` | 读取服务状态及最近 24 小时日志 | 不可用或无权限时相关项目可能未知；WSL 中也可能没有 systemd/journal |
| SMART 数据 | 读取 Beszel 数据库里的设备数据 | 本脚本不直接运行 `smartctl`；缺失、未配置或超过 30 分钟未更新时标为未知 |
| root 巡检报告 | 在日报中引用独立 root 巡检结果 | 不是本仓库生成的文件；找不到符合格式的当天报告时标为未知 |
| 企业微信机器人和密钥文件 | 用 `--send` 主动发送消息 | 全部可选；`--dry-run` 不读取密钥，也不发送 |
| systemd 用户级服务/计时器 | 自动运行 Python 日报 | 可选；仓库中的 timer 默认未启用，也不会自行安装 |

当前 Python 代码会将 `sshd`、`smb`、`docker`、`firewalld`、`NetworkManager` 这几个服务的非 active 状态计为严重项。因此，在 WSL 或未安装这些服务的发行版上可能出现严重状态；它反映的是当前代码的固定服务基线，不等于主机整体故障。`nmb` 是备注项。Docker 不可访问、Beszel 数据库缺失、未配置系统 ID、SMART 数据过期或巡检报告缺失则会显示未知/未检查。

磁盘容量目前固定检查 `/`、`/home`、`/boot`、`/mnt/toshiba`；Beszel SMART 汇总固定匹配 `/dev/sda` 和 `/dev/sdb`。不同布局可能在这些项上得到未知或不匹配。将报告用于另一台主机前，应先理解这些固定基线，不能把所有严重/未知直接当成该主机故障或健康结论。

程序读取的环境变量：`BESZEL_DB`（数据库路径）、`BESZEL_SYSTEM_ID`（主机 ID）、`BESZEL_URL`（仅作为报告链接）、`ROOT_PATROL_DIRS`（巡检报告目录，Linux 下用冒号分隔）。它们不是 Python 自动读取 `.env`；用户级 systemd service 才会通过 `EnvironmentFile` 读取示例路径中的配置文件。

如需配置 Beszel，可在 Linux shell 中执行以下命令；它会创建配置目录并将示例复制到用户配置目录。用编辑器把占位 ID 改成自己的值；不知道 ID 时删除或注释 `BESZEL_SYSTEM_ID=replace-with-your-own-system-id` 这一行，不要保留占位字符串。报告会如实显示未知。**不要填写或提交服务器密钥、Webhook URL 或真实日志。**

```bash
mkdir -p "$HOME/.config/asus-daily"
cp systemd/asus-daily-summary.env.example "$HOME/.config/asus-daily/asus-daily-summary.env"
chmod 600 "$HOME/.config/asus-daily/asus-daily-summary.env"
nano "$HOME/.config/asus-daily/asus-daily-summary.env"
```

如果只做第一次安全运行，可以跳过此配置步骤。手动运行时若希望使用这些配置，在同一个 Linux shell 中加载：

```bash
set -a
. "$HOME/.config/asus-daily/asus-daily-summary.env"
set +a
```

### 如何确认项目目录和用户主目录

在 Linux shell 中，`pwd` 显示当前目录，`printf '%s\n' "$HOME"` 显示当前用户主目录。`~` 按实际运行程序的 Linux 用户展开；通过用户级 systemd 启动时，使用该 user manager 所属用户的主目录。不要把某个用户的绝对路径复制给另一台主机。

在 Windows 原生 PowerShell 中如需把 Windows 项目目录转换成 WSL 路径，可运行 `wsl.exe wslpath -u 'C:\你的目录\linux-server-daily-report'`，将示例中的路径替换为实际位置；然后在 WSL shell 使用返回的 Linux 路径 `cd`。这条转换命令只用于取得路径，不会运行项目。

## C. 第一次安全运行

以下步骤在 **Linux 主机终端或 WSL Linux shell** 中执行，均不需要 `sudo`。前提是已取得项目文件夹；把整个文件夹放在当前 Linux 用户主目录下，并命名为 `linux-server-daily-report`。若项目在其他位置，只需把第一条 `cd` 改成实际的项目根目录。

1. 进入项目目录。预期 `pwd` 以 `/linux-server-daily-report` 结尾；如果 `cd` 提示目录不存在，先找到并进入实际解压/克隆出来的目录，不要继续在错误目录执行。

   ```bash
   cd "$HOME/linux-server-daily-report"
   pwd
   test -f scripts/asus-daily-summary.py && echo "项目文件已找到"
   ```

2. 确认 Python 版本。预期显示 `Python 3.x`；如果 `python3` 找不到，按“常见问题”安装系统 Python 3 后重试。

   ```bash
   python3 --version
   ```

3. 做语法编译检查。成功时没有错误输出并返回 0；它会生成可忽略的 `scripts/__pycache__/` 缓存，不会检查服务器组件。

   ```bash
   python3 -m py_compile scripts/asus-daily-summary.py
   ```

4. 运行无通知的日报测试。该命令**不需要 sudo**，会采集当前 Linux 环境、打印完整报告、保存私有日志，并显示保存路径。它不读取密钥、不调用企业微信发送接口、不安装或启用 timer。首次执行可能由于没有 Beszel、Docker、systemd 或巡检报告而出现未知项，这是预期情况。

   ```bash
   python3 scripts/asus-daily-summary.py --dry-run
   ```

   成功标准是：退出码为 0，终端出现 `[dry-run] 不发送`、`报告已保存：.../latest.md` 和 `当日日志：.../YYYY-MM-DD.log`。报告里的“未知”不代表程序执行失败；它表示相应数据源未配置或不可读取。保存失败会输出 `REPORT_SAVE_ERROR` 并返回 3。

**仅当你主动准备真实企业微信机器人密钥并确实要向群里发消息时才运行 `--send`。** 第一次测试不要运行它。无密钥时 `--send` 会写入“跳过”记录并以 0 退出；有密钥时则会尝试发送真实消息。

## D. 报告和日志在哪里

路径按运行程序的 Linux 用户计算。若 `printf '%s\n' "$HOME"` 输出 `/home/你的用户名`，日志目录就是 `/home/你的用户名/asus-logs/daily-summary/`；若由 root 运行，`~` 会变为 root 的主目录。因此不要用 root 运行个人 Python 日报。

### 1. 手动 `--dry-run` 输出

完整报告先打印到当前终端；现在也会同步保存到日报目录。结尾会给出 `latest.md` 和当日日志的确切绝对路径。报告保存失败时终端会显示错误类型和目标目录；报告文本仍已经打印，可按报错检查目录权限和磁盘空间。

### 2. Python 日报文件

实际代码写入：

- 日志目录：`$HOME/asus-logs/daily-summary/`，目录权限 0700。
- 每日追加日志：`YYYY-MM-DD.log`，例如 `2026-10-25.log`；文件权限 0600。每次运行有一条 `result=` 标记，dry-run 明确记录 `DRY RUN (not sent)`。
- 最新报告：`latest.md`，每次运行覆盖，权限 0600。
- `.log` 保留：超过 30 天的日志由程序尝试清理；因此该目录不是永久归档。

在 Linux shell 中查看目录、打开最新报告和查看最近日志：

```bash
ls -la "$HOME/asus-logs/daily-summary"
cat "$HOME/asus-logs/daily-summary/latest.md"
tail -n 40 "$HOME/asus-logs/daily-summary/$(date +%F).log"
```

### 3. systemd 用户级服务日志

服务标准输出、标准错误以及执行失败状态由 systemd journal 收集。先查看 service 状态，再查看今天的日志：

```bash
systemctl --user status asus-daily-summary.service
journalctl --user -u asus-daily-summary.service --since today --no-pager
```

尚未安装 unit 时，`status` 会显示找不到单元；尚未运行过时，journal 可能没有记录。Python 的 `latest.md`/日期日志与 systemd journal 是两套不同输出。

### 4. 独立系统级巡检报告

已知服务器上另有每日 08:00 的系统级 `old-asus-auto-check.service` / `.timer`，此前核实它会生成带时间的报告和历史记录。但生产脚本和服务器部署配置没有包含在本仓库，下载仓库不会安装或生成这份 root 报告。

Python 仅按配置目录查找符合以下命名格式的既有文件：`old-asus-auto-check-YYYYMMDD-*.txt`，或 `history/old-asus-auto-check-YYYY-MM-DD.log`。默认搜索 `~/asus-logs` 和 `/var/log/old-asus-auto-check`；权限不足、路径不同或当天文件不存在时会显示未知。先列出候选文件：

```bash
find "$HOME/asus-logs" /var/log/old-asus-auto-check -type f \( -name 'old-asus-auto-check-*.txt' -o -name 'old-asus-auto-check-*.log' \) -print 2>/dev/null
```

如果找到了文件，可用 `less '/实际找到的/报告文件'` 查看。若 `/var/log` 文件普通用户不可读，不要修改生产权限；请由服务器管理员检查，或在自己的配置中设置一个当前用户可读的 `ROOT_PATROL_DIRS`。查看 systemd 生产任务状态（仅查看，不改变任务）可用：

```bash
sudo systemctl status old-asus-auto-check.timer old-asus-auto-check.service
sudo journalctl -u old-asus-auto-check.service --since today --no-pager
```

这些命令只适用于已部署该单元的服务器；在 WSL 或其他主机上单元不存在是正常的。

## E. 运行成功的判断标准

以下为**格式示例，不是真实服务器结果**；主机数据、时间、计数和路径会随环境变化：

```text
## 🖥️ asus 每日巡检摘要
> 2026-10-25 08:04 CST（Asia/Shanghai）
> 总体：<font color="comment">未检查/失败</font> ｜ 严重 0 ｜ 注意 0 ｜ 未检查/失败 8
...
[dry-run] 不发送；严重=0 注意=0 未知=8；字节=1234
[dry-run] 报告已保存：/home/你的用户名/asus-logs/daily-summary/latest.md
[dry-run] 当日日志：/home/你的用户名/asus-logs/daily-summary/2026-10-25.log
```

- **命令成功**：`--dry-run` 的退出码为 0，表示采集流程和报告保存完成；它不保证每个数据源都正常。
- **日报已生成**：输出有两行保存路径；用 `ls -l` 确认文件存在，用 `cat latest.md` 查看内容。
- **正常**：该检查项有数据且通过脚本阈值。
- **注意/异常**：发现需人工关注的条件；例如资源阈值、日志或某些设备信号。
- **严重**：命中脚本定义的严重阈值或异常服务/容器状态。
- **未知/未检查**：没配置、无权限、组件不存在、数据过期或报告缺失。比如未设置 `BESZEL_SYSTEM_ID` 时，即使数据库能打开，也无法匹配主机；未知不等于正常。
- **保存失败**：看到 `REPORT_SAVE_ERROR` 或退出码 3，表示报告未能完整保存。按错误类型检查 `$HOME/asus-logs/daily-summary` 的父目录权限、磁盘空间及文件系统只读状态。

程序退出码只表达脚本执行结果。`--dry-run` 可能在报告有严重/未知项时仍以 0 退出，因为它不是自动修复工具，也不是所有组件都配置齐备的证明。

## F. 常见问题

所有诊断命令在 Linux 主机终端或 WSL shell 运行，除明确标注外不需要 `sudo`。

- **找不到 Python**：运行 `command -v python3`。若无输出，需在 Linux 发行版安装 Python 3；Ubuntu/Debian 可由管理员运行 `sudo apt update && sudo apt install python3`，Rocky/Fedora 可用 `sudo dnf install python3`。安装包会修改本机软件环境，先确认你有权限。
- **在 Windows 原生 PowerShell 运行失败**：`python scripts/asus-daily-summary.py --dry-run` 即使能启动 Windows Python，也无法提供 `/proc`、Linux systemd 等数据。改在 WSL Linux shell 或 Linux 主机终端运行；PowerShell 只适合查看/编辑文件。
- **缺少 Linux 系统命令**：运行 `command -v systemctl journalctl docker df uptime`。缺某条命令时，对应项目可能是未知/失败；安装 `systemd`、Docker CLI 或其他系统包取决于发行版，不要为了一次 dry-run 盲目安装。
- **缺少 Beszel 数据库**：`ls -l "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"`。文件不存在会导致 Beszel/SMART 项未知；没有 Beszel 时可继续手动报告，先接受这些未知项。
- **无法读取数据库**：`namei -l "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"` 和 `test -r "${BESZEL_DB:-$HOME/services/beszel/data/data.db}"; echo $?`。需要父目录搜索权限和数据库读取权限；脚本只读打开，不要给数据库写权限。
- **`BESZEL_SYSTEM_ID` 未配置**：运行 `printf '%s\n' "${BESZEL_SYSTEM_ID:-未设置}"`。ID 为空时匹配状态和 SMART 设备未知；按“配置”章节从自己的 Beszel 获取 ID。不要使用他人的真实 ID。
- **找不到当天 root 巡检报告**：检查目录 `printf '%s\n' "${ROOT_PATROL_DIRS:-默认目录}"` 并运行 D 章节的 `find` 命令。确认文件名日期、目录权限、报告内容格式；它必须来自独立 root 任务，本仓库不会创建它。
- **Docker 命令不可用**：运行 `command -v docker; docker ps -a`。若命令不存在或 daemon 不可达，日报显示 Docker 未知；若容器已退出，代码会检查全部容器（包括停止的）并报告异常。普通用户访问 Docker socket 通常需要高权限，勿为测试随意加入 `docker` 组。
- **systemd 服务不存在或失败**：运行 `systemctl --user status asus-daily-summary.service`、`journalctl --user -u asus-daily-summary.service -b --no-pager`。不存在通常表示 unit 未安装或未 `daemon-reload`；失败时核对 `ExecStart` 文件路径、Python 路径、env 文件语法和日志目录权限。若 `systemctl --user` 报无法连接 user bus，当前环境可能没有运行 user manager（常见于未启用 systemd 的 WSL）。
- **timer 未启用**：运行 `systemctl --user is-enabled asus-daily-summary.timer` 和 `systemctl --user list-timers --all`。`disabled` 表示不会按计划触发；unit 文件存在或安装了都不等于启用。
- **退出登录后 timer 不再触发**：检查 `loginctl show-user "$USER" -p Linger`。user manager 未运行时，timer 不能触发；若确实要求用户注销后仍运行，需由管理员评估并主动启用 linger（例如 `sudo loginctl enable-linger "$USER"`）。这会改变该 Linux 用户的系统运行策略，不是本项目安装步骤。
- **企业微信密钥不存在**：`test -e "$HOME/.config/asus-daily/wecom.key"; echo $?`。不存在时 `--send` 会跳过、不发消息，记一条 skip 并退出 0。首次测试用 `--dry-run`。
- **企业微信密钥权限不正确**：运行 `stat -c '%U %a' "$HOME/.config/asus-daily/wecom.key"`。所有者必须是当前运行用户，权限必须严格为 600 或 400；修正可用 `chmod 600 "$HOME/.config/asus-daily/wecom.key"`。错误时脚本拒绝发送并以非零退出，不会记录本次报告。
- **报告生成了但不知道在哪**：重新运行 `--dry-run`，看最后的“报告已保存”和“当日日志”绝对路径；或执行 `printf '%s\n' "$HOME/asus-logs/daily-summary/latest.md"`、`ls -la "$HOME/asus-logs/daily-summary"`。不要猜用户名拼路径。

## G. 安装、启用与卸载用户级 timer

`service` 描述一次运行要执行什么；`timer` 描述什么时候触发 service。模板是 `systemd --user` 单元：由安装并启动该用户 manager 的 Linux 账号运行，不是 root 服务。仓库不提供系统级 root unit，也不会自动修改任何服务器服务。timer 默认每天服务器本地时间 **08:02** 触发，`Persistent=false`；错过的触发不补跑。

先完成配置章节中需要的配置，并手动跑过 `--dry-run`、确认日志路径和报告结果。service 的 `ExecStart` 使用 `--send`：启用 timer 前检查 key 文件；如果 key 已存在，每天会尝试向企业微信群发送真实消息。无 key 时只记录跳过。不要在尚未确认这个行为时启用。

以下步骤在 Linux shell 执行，不需要 `sudo`。先把 unit 复制到当前用户的 systemd 配置目录并重新加载；**安装不等于启用**：

```bash
mkdir -p "$HOME/.config/systemd/user"
cp systemd/asus-daily-summary.service systemd/asus-daily-summary.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/asus-daily-summary.service" "$HOME/.config/systemd/user/asus-daily-summary.timer"
```

运行前检查 `ExecStart` 指向的 Python 文件确实存在。若项目不在 `~/linux-server-daily-report`，编辑复制到 `~/.config/systemd/user/` 的 service 并改成脚本的绝对路径，然后再次 `daemon-reload`。用户级配置 env 文件位置为 `~/.config/asus-daily/asus-daily-summary.env`；改环境文件供下次新启动读取，无须 reload；改 unit 文件后必须 reload。

查看是否启用、下一次触发时间和状态：

```bash
systemctl --user is-enabled asus-daily-summary.timer
systemctl --user list-timers --all asus-daily-summary.timer
systemctl --user status asus-daily-summary.timer asus-daily-summary.service
```

只有在已确认环境、日志和真实发送行为后，才由用户自己启用：

```bash
systemctl --user enable --now asus-daily-summary.timer
```

这会立即启动 timer，之后按 08:02 计划触发 service；不会立刻执行一次日报。手动执行一次 service 用 `systemctl --user start asus-daily-summary.service`，但 service 带 `--send`，有 key 就会发真实消息。查看其 stdout/stderr 和失败细节：

```bash
journalctl --user -u asus-daily-summary.service --since today --no-pager
```

停用只影响当前用户的这个日报 timer，不会触碰系统级生产巡检：

```bash
systemctl --user disable --now asus-daily-summary.timer
```

确认不再需要 unit 后，可删除**仅这两个明确文件**并 reload：

```bash
rm -f "$HOME/.config/systemd/user/asus-daily-summary.timer" "$HOME/.config/systemd/user/asus-daily-summary.service"
systemctl --user daemon-reload
```

删除日报或配置前请先检查并备份需要保留的内容；不要删除其他服务文件、root 巡检数据或整个日志目录。

## 验证范围

本地完成的检查：

- Python 语法编译。
- `tests/test_daily_summary.py` 的 4 项 WSL/Linux 测试：替代系统采集器验证报告输出和不读取密钥/不发送；检查日志保存失败；并在 Linux 中真实执行一次 CLI dry-run，确认两种报告文件和私有权限。
- WSL Linux 文件权限测试：日志目录 0700、日报文件 0600。
- CLI dry-run 在隔离 HOME 中运行，确认报告路径和文件写入；缺少 Beszel、Docker、温度传感器等项目按未知/失败显示。该结果仅代表 WSL 环境。
- WSL Ubuntu 24.04 `systemd-analyze verify` 检查 service/timer 文件格式；unit 没有安装或启动。

未完成的验证：真实 Linux 服务器上的 Beszel 数据库 schema/权限、Docker daemon 与容器、SMART 上报、root 巡检报告读取、journald 权限、用户级 systemd timer 触发和企业微信发送。不要把本地模拟或 WSL 的结果当成这些端到端验证。
