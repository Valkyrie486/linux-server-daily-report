# Linux Server Daily Report

面向 Linux 家庭服务器的只读巡检与健康日报工具，使用 Python、systemd、Docker 和可选的 Beszel 数据；Python 日报汇总主机状态，并可选发送企业微信机器人消息。

> **运行状态说明：** 已知部署中有系统级 `old-asus-auto-check.service` / `.timer` 每日 08:00 执行只读巡检并生成报告与历史记录。这是此前核实的运行事实；生产巡检源码尚未取得。本次公开文件不包含本地 `scripts/inspect.sh`（特定 Rocky Linux 主机基线）或根目录的 `edit-me.sh`（部署副本），不能将二者称为生产实现。Python 日报曾成功执行 dry-run；其用户级 timer 之前未启用，且当时没有企业微信密钥，因此不宣称日报定时或自动通知已运行。

## 组件与边界

| 文件 | 用途 | 权限和写入 |
|---|---|---|
| `scripts/asus-daily-summary.py` | 采集主机、内存/Swap、温度、磁盘、Beszel、SMART、服务、所有 Docker 容器状态、journal 和现有 root 巡检报告；生成中文摘要 | 普通用户运行；Beszel SQLite 用 `mode=ro`；会写 `$HOME/asus-logs/daily-summary` 并清理其中超过 30 天的 `.log` |
| `systemd/asus-daily-summary.service` / `.timer` | 可选的 `systemd --user` Python 日报模板，默认每天本地时间 08:02 | 不会自动安装或启用；需先调整路径和配置 |

Python 会读取 `ROOT_PATROL_DIRS` 下符合自身文件名和格式约定的报告；这不是调用 `inspect.sh`。现有每日 08:00 root 巡检、Python 手动日报和可选的用户级 Python timer/通知是三个不同环节。具体主机的 Bash 巡检脚本不在本次发布文件中。

## 要求

- Linux；Python 日报面向 Linux 系统。实际命令可用性取决于发行版和安装的软件包。
- Python 3。日报尽可能逐项采集；Docker、Beszel、SMART 或 journal 不可用时会出现未知/失败状态。
- 可选：systemd、Docker CLI/权限、Beszel SQLite 数据库、带 SMART 数据的 Beszel agent、企业微信机器人 webhook key。

## 配置

Python 直接读取以下环境变量：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `BESZEL_DB` | `~/services/beszel/data/data.db` | Beszel SQLite 数据库路径；以只读 URI 打开 |
| `BESZEL_SYSTEM_ID` | 空 | 选择 Beszel 主机及其 SMART 记录；留空时报告无法匹配 |
| `BESZEL_URL` | 空 | 仅作为日报中的可选链接展示，不建立数据库连接 |
| `ROOT_PATROL_DIRS` | `~/asus-logs:/var/log/old-asus-auto-check`（Linux 使用 `:` 分隔） | 搜索当天 root 巡检报告的目录 |

用户级 unit 通过 `EnvironmentFile=-%h/.config/asus-daily/asus-daily-summary.env` 加载可选环境文件。复制示例后按本机情况填写：

```bash
mkdir -p "$HOME/.config/asus-daily"
cp systemd/asus-daily-summary.env.example "$HOME/.config/asus-daily/asus-daily-summary.env"
chmod 600 "$HOME/.config/asus-daily/asus-daily-summary.env"
$EDITOR "$HOME/.config/asus-daily/asus-daily-summary.env"
```

示例只提供 `BESZEL_SYSTEM_ID`、`BESZEL_URL`、`BESZEL_DB`。`ROOT_PATROL_DIRS` 也受程序支持，可按需自行加入 env 文件。改动 unit 后执行 `systemctl --user daemon-reload`；改环境文件后下一次新启动的 service 会读取新值，不需重新加载 unit。

## 手动运行

在项目目录执行：

```bash
python3 scripts/asus-daily-summary.py --dry-run
```

`--dry-run` 会采集并打印报告，不发送消息；但程序仍会写入日报日志及 `latest.md`，日志目录权限设为 0700，文件设为 0600，并执行 30 天日志保留清理。Beszel 数据库等可选数据源缺失时，相关项目为“未知/未检查”，不是健康结论。

本次公开文件不包含主机专用的 Bash 巡检实现。已知生产巡检源码尚未核实；不要将被忽略的本地 `scripts/inspect.sh` 或 `edit-me.sh` 当作生产脚本部署。

## 可选启用 Python 用户级定时器

先把仓库放在 `$HOME/linux-server-daily-report`，或编辑 service 的 `ExecStart` 指向真实 Python 脚本绝对路径。创建日志目录，并先 dry-run 验证：

```bash
mkdir -p "$HOME/asus-logs/daily-summary"
chmod 700 "$HOME/asus-logs/daily-summary"
python3 "$HOME/linux-server-daily-report/scripts/asus-daily-summary.py" --dry-run
```

程序默认密钥路径为 `~/.config/asus-daily/wecom.key`。若希望 `--send` 真正发送，先在企业微信创建群机器人，将**仅 key 字符串**保存到该文件，并限制属主与权限：

```bash
umask 077
mkdir -p "$HOME/.config/asus-daily"
$EDITOR "$HOME/.config/asus-daily/wecom.key"
chmod 600 "$HOME/.config/asus-daily/wecom.key"
```

脚本拒绝非当前用户属主，以及权限不是 0600/0400 的密钥文件。不要提交 key、完整 webhook URL、`.env`、数据库、报告或运行日志。没有 key 时 `--send` 会记录跳过并以 0 退出，不会发消息。真实发送会向机器人发消息；没有配置真实 key 时不要测试 `--send`。确认发送成功需查看企业微信 API 响应/服务日志，并确认目标群收到消息。

安装用户级 unit（这是用户主动选择的步骤，不影响现有系统级 root 巡检）：

```bash
mkdir -p "$HOME/.config/systemd/user"
cp systemd/asus-daily-summary.service systemd/asus-daily-summary.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user cat asus-daily-summary.service
systemd-analyze --user verify "$HOME/.config/systemd/user/asus-daily-summary.service" "$HOME/.config/systemd/user/asus-daily-summary.timer"
```

核实 service 路径、环境文件、密钥（如需发送）后，以下命令才会启用并安排每日 08:02 本地时间运行：

```bash
systemctl --user enable --now asus-daily-summary.timer
```

## 查看和验证 systemd

服务是一次性任务（`Type=oneshot`），timer 是触发计划；`.timer` 文件存在不代表已加载、已启用或成功执行。用户级命令：

```bash
systemctl --user list-timers --all asus-daily-summary.timer
systemctl --user is-enabled asus-daily-summary.timer
systemctl --user status asus-daily-summary.timer asus-daily-summary.service
systemctl --user start asus-daily-summary.service
journalctl --user -u asus-daily-summary.service --since today --no-pager
```

判断 timer 生效应同时核实 `list-timers` 的 NEXT/LAST、`is-enabled`、服务最近结果及 journal。手动 start 会运行一次服务；若 unit 使用 `--send` 且密钥存在，它会发送真实消息。

真实系统级巡检（只读查看，不改变服务）：

```bash
sudo systemctl list-timers --all old-asus-auto-check.timer
sudo systemctl status old-asus-auto-check.timer old-asus-auto-check.service
sudo journalctl -u old-asus-auto-check.service --since today --no-pager
```

若要手动触发真实巡检，可运行 `sudo systemctl start old-asus-auto-check.service`，这会执行该主机上的生产脚本并写报告；本仓库不会替你执行或安装它。

## 故障排查

- **Permission denied**：本地被排除的 Bash 示例需 root 写 `/var/log` 和 `/run/lock`；Python 日报应以普通用户运行。检查 `namei -l` 目标路径，不要用 root 运行 Python 日报以免生成 root 所有的私密日志。
- **timer 未加载/未启用**：执行 `systemctl --user daemon-reload`、`systemctl --user is-enabled asus-daily-summary.timer`、`systemctl --user list-timers --all`。用户 manager 未运行时检查 `loginctl show-user "$USER" -p Linger`；按自身需求决定是否配置 linger。
- **service 失败**：`systemctl --user status asus-daily-summary.service` 与 `journalctl --user -u asus-daily-summary.service -b --no-pager`；检查 `ExecStart` 路径、Python 路径、环境文件语法及目录访问权限。
- **找不到今日 root 巡检报告**：检查 `ROOT_PATROL_DIRS`、报告是否为当天、报告是否符合 `old-asus-auto-check-YYYYMMDD-*.txt` 或 `history/old-asus-auto-check-YYYY-MM-DD.log` 命名及预期汇总格式；未知表示没有读到符合条件的报告。
- **Beszel DB 不可访问 / SQLite 权限错误**：检查 `BESZEL_DB` 实际路径、父目录搜索权限及文件读取权限（`namei -l "$BESZEL_DB"`、`test -r "$BESZEL_DB"`）。不要授予不必要的写权限；数据库以只读方式打开。
- **Docker 不可用**：执行 `command -v docker`、`docker ps -a`；普通用户需有 Docker socket 访问权限。Docker group 通常等同于高权限，按安全策略配置。程序通过 `docker ps -aq` 和 `docker inspect` 检查已退出容器。
- **SMART 未上报/数据过期**：确认 Beszel agent 有 SMART 采集能力、`BESZEL_SYSTEM_ID` 匹配、设备记录存在且更新时间不超过 30 分钟。缺失或过期会报告未知，不判为健康。
- **企业微信 key 缺失/权限不正确**：检查 `stat -c '%U %a' ~/.config/asus-daily/wecom.key`；必须为当前用户且 600 或 400。没有 key 时消息跳过。
- **企业微信请求失败/消息过长**：检查 `journalctl --user -u asus-daily-summary.service` 的网络/HTTP 错误；脚本将 markdown 控制在约 4000 字节内并可能截断。确认机器人仍有效、主机 DNS/TLS/网络正常；不要打印或粘贴 key。
- **告警出现未知**：未知表示采集失败、未配置、报告缺失或数据陈旧；分别检查对应数据源，不应把未知改读为正常。
- **timer 存在但未触发**：查看 `list-timers --all` 的 NEXT/LAST、主机时区 `timedatectl`、用户 manager/linger 状态及 journal；`Persistent=false` 时关机错过的触发不会补跑。

## 安全与卸载

采集系统信息以只读为原则，但生成报告、历史记录、日志和保留清理会写入本机文件。日报日志可能包含主机状态，按私密数据保护。不要发布真实报告、数据库、密钥或服务器专属配置。`edit-me.sh` 为本地部署副本，已加入忽略规则，不应公开。

停用并移除**仅此用户日报 unit**：

```bash
systemctl --user disable --now asus-daily-summary.timer
rm -f "$HOME/.config/systemd/user/asus-daily-summary.timer" "$HOME/.config/systemd/user/asus-daily-summary.service"
systemctl --user daemon-reload
```

如需清理配置或日报日志，先确认其中没有需要保留的数据，再仅删除对应的 `~/.config/asus-daily/` 配置文件或 `~/asus-logs/daily-summary/` 内容。不要删除系统级巡检服务、其日志或其他用户文件。

## 检查范围与限制

本地可做的检查与真实 Linux 端到端运行不同。Windows 上的语法编译不能证明 Linux 命令、systemd 权限、Beszel schema、Docker socket、SMART 数据和企业微信网络均正常。未发布的本地 `inspect.sh` 含面向 Rocky Linux/特定挂载布局及服务基线的检查；它不是 `old-asus-auto-check.sh` 生产源码。当前未随仓库提供系统级 root unit，也不提供生产服务器登录凭据。
