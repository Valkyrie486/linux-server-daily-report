#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asus-daily-summary.py  —  Old Asus home server daily inspection summary.

Runs unprivileged as the invoking user from a systemd --user timer.
Collects read-only health data, renders a WeCom (企业微信群机器人) markdown
message, and (with --send) posts it.

Design / safety:
  * No secrets are ever printed or written to logs.
  * WeCom key is read at runtime from a 0600 file owned by the caller.
  * Every probe is independently guarded: failures are reported as
    未检查/检查失败, never silently rendered as 正常.
  * Read-only only: never restarts/stops/repairs anything.
  * SMART for BOTH disks comes from the Beszel PocketBase DB (agent-collected),
    because an unprivileged user cannot open /dev/sdb via smartctl.
  * Beszel DB is opened read-only (mode=ro).

Usage:
  asus-daily-summary.py --dry-run          # build & print message, do NOT send
  asus-daily-summary.py --send             # read key, POST to WeCom
  asus-daily-summary.py --send --key-file PATH

Optional environment variables (keep deployment-specific values out of source):
  BESZEL_DB          Path to the Beszel SQLite database (defaults under HOME)
  BESZEL_SYSTEM_ID   Beszel system ID used for host status and SMART matching
  BESZEL_URL         Optional dashboard link included in the rendered report
  ROOT_PATROL_DIRS   Patrol-report directories separated by the OS path separator
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".config", "asus-daily", "wecom.key")
BESZEL_URL = os.environ.get("BESZEL_URL", "").strip()
BESZEL_SYSTEM_ID = os.environ.get("BESZEL_SYSTEM_ID", "").strip()
BESZEL_DB = os.environ.get("BESZEL_DB") or os.path.join(
    HOME, "services", "beszel", "data", "data.db"
)
# The root 08:00 patrol writes a timestamped snapshot per run here (same dir is
# also exposed at /var/log/old-asus-auto-check). NOTE: the "-latest.txt" file in
# here is a stale manual run, so we must read TODAY's timestamped file instead.
ROOT_PATROL_DIRS = os.environ.get(
    "ROOT_PATROL_DIRS",
    os.pathsep.join([
        os.path.join(HOME, "asus-logs"),
        "/var/log/old-asus-auto-check",
    ]),
).split(os.pathsep)
LOG_DIR = os.path.join(HOME, "asus-logs", "daily-summary")
RETENTION_DAYS = 30
SMART_STALE_MIN = 30          # Beszel SMART older than this -> treat as stale
WECOM_MAX_BYTES = 4000        # WeCom markdown hard limit is 4096; keep margin

# ---- status levels -------------------------------------------------------
OK, WARN, CRIT, UNKNOWN = "OK", "WARN", "CRIT", "UNKNOWN"
LEVEL_CN = {OK: "正常", WARN: "注意", CRIT: "严重异常", UNKNOWN: "未知/未检查"}


def run(cmd, timeout=20):
    """run a command, return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return -1, "", "%r" % e


class Col:
    def __init__(self):
        self.items = []          # (level, label, text)

    def add(self, level, label, text):
        self.items.append((level, label, text))

    def count(self, lv):
        return sum(1 for l, _, _ in self.items if l == lv)

    def worst(self):
        if self.count(CRIT):
            return CRIT
        if self.count(WARN):
            return WARN
        if self.count(UNKNOWN):
            return UNKNOWN
        return OK


def color(lv):
    return {OK: "info", WARN: "comment", CRIT: "warning", UNKNOWN: "comment"}.get(lv, "comment")


def tag(lv):
    # WeCom markdown font colors: info(green) warning(orange-red) comment(gray)
    return '<font color="%s">%s</font>' % (color(lv), LEVEL_CN[lv])


# ---- collectors ----------------------------------------------------------
def collect_host():
    d = {}
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        d["load"] = (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:  # noqa: BLE001
        d["load"] = None
    try:
        d["cores"] = os.cpu_count() or 1
    except Exception:  # noqa: BLE001
        d["cores"] = 1
    rc, out, _ = run(["uptime", "-p"])
    d["uptime"] = out if rc == 0 else "未知"
    return d


def collect_mem():
    d = {}
    try:
        m = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                m[k] = int(rest.strip().split()[0]) * 1024
        total = m.get("MemTotal", 0)
        avail = m.get("MemAvailable", 0)
        d["used_pct"] = round((total - avail) * 100.0 / total, 1) if total else None
        d["avail_gib"] = round(avail / 1073741824, 2)
        st, sa = m.get("SwapTotal", 0), m.get("SwapFree", 0)
        d["swap_used_mib"] = round((st - sa) / 1048576)
        d["swap_total_gib"] = round(st / 1073741824, 2)
        d["ok"] = True
    except Exception:  # noqa: BLE001
        d["ok"] = False
    return d


def collect_temps():
    """coretemp package/core + pch thermal zone. Returns dict or {'ok':False}."""
    res = {"package": None, "cores": [], "pch": None, "ok": False}
    try:
        for hw in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                name = open(os.path.join(hw, "name")).read().strip()
            except Exception:  # noqa: BLE001
                continue
            if name != "coretemp":
                continue
            for inp in glob.glob(os.path.join(hw, "temp*_input")):
                base = inp[: -len("_input")]
                try:
                    val = int(open(inp).read().strip()) / 1000.0
                    lbl = open(base + "_label").read().strip() if os.path.exists(base + "_label") else ""
                except Exception:  # noqa: BLE001
                    continue
                if lbl.lower().startswith("package"):
                    res["package"] = (lbl, round(val, 1))
                elif lbl.lower().startswith("core"):
                    res["cores"].append((lbl, round(val, 1)))
        # PCH from thermal zones
        for z in glob.glob("/sys/class/thermal/thermal_zone*"):
            try:
                ztype = open(os.path.join(z, "type")).read().strip()
                t = int(open(os.path.join(z, "temp")).read().strip()) / 1000.0
            except Exception:  # noqa: BLE001
                continue
            if "pch" in ztype.lower():
                res["pch"] = (ztype, round(t, 1))
        res["ok"] = res["package"] is not None
    except Exception:  # noqa: BLE001
        res["ok"] = False
    return res


def collect_disk_space():
    out_rows = []
    for path in ["/", "/home", "/boot", "/mnt/toshiba"]:
        rc, out, err = run(["df", "-P", path])
        if rc != 0:
            out_rows.append((path, None))
            continue
        try:
            line = out.splitlines()[1].split()
            pct = int(line[4].rstrip("%"))
            out_rows.append((path, pct))
        except Exception:  # noqa: BLE001
            out_rows.append((path, None))
    return out_rows


def _parse_smart_attrs(attrs_json):
    want = {
        "Reallocated_Sector_Ct": "realloc",
        "Current_Pending_Sector": "pending",
        "Offline_Uncorrectable": "uncorrect",
    }
    found = {"realloc": None, "pending": None, "uncorrect": None}
    try:
        for a in json.loads(attrs_json or "[]"):
            n = a.get("n")
            if n in want:
                try:
                    found[want[n]] = int(a.get("rv", a.get("rs", 0)))
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    return found


def parse_timestamp(value):
    """Parse Beszel ISO timestamps on Python 3.5, which lacks fromisoformat()."""
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
        r"(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$",
        (value or "").strip(),
    )
    if not match:
        raise ValueError("invalid ISO timestamp")
    base, fraction, zone = match.groups()
    fmt = "%Y-%m-%dT%H:%M:%S" if "T" in base else "%Y-%m-%d %H:%M:%S"
    if fraction:
        parsed = datetime.strptime(base + "." + (fraction + "000000")[:6], fmt + ".%f")
    else:
        parsed = datetime.strptime(base, fmt)

    if not zone or zone == "Z":
        return parsed.replace(tzinfo=timezone.utc)
    offset_match = re.match(r"^([+-])(\d{2}):?(\d{2})$", zone)
    if not offset_match:
        raise ValueError("invalid ISO timezone")
    sign, hours, minutes = offset_match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        offset = -offset
    return parsed.replace(tzinfo=timezone(offset))


def collect_beszel_db(col):
    """Read Beszel status, current alerts, and SMART data using read-only SQLite."""
    info = {
        "status": None,
        "status_updated": None,
        "alerts_firing": [],
        "alerts_total": None,
        "smart": {},
        "system_id_missing": not bool(BESZEL_SYSTEM_ID),
    }
    try:
        import sqlite3

        # Path.as_uri() safely escapes spaces and URI-special characters.
        db_uri = Path(BESZEL_DB).expanduser().resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(db_uri, uri=True)
        try:
            cur = con.cursor()

            if BESZEL_SYSTEM_ID:
                try:
                    row = cur.execute(
                        "SELECT status,updated FROM systems WHERE id=?",
                        (BESZEL_SYSTEM_ID,),
                    ).fetchone()
                    if row:
                        info["status"], info["status_updated"] = row[0], row[1]
                except Exception:  # noqa: BLE001
                    pass

            try:
                info["alerts_total"] = cur.execute(
                    "SELECT COUNT(*) FROM alerts"
                ).fetchone()[0]
                info["alerts_firing"] = [
                    r[0] for r in cur.execute(
                        "SELECT name FROM alerts WHERE triggered=1 ORDER BY name"
                    )
                ]
            except Exception:  # noqa: BLE001
                pass

            if BESZEL_SYSTEM_ID:
                try:
                    now = datetime.now(timezone.utc)
                    rows = cur.execute(
                        "SELECT name,model,state,temp,hours,updated,attributes "
                        "FROM smart_devices WHERE system=? ORDER BY name",
                        (BESZEL_SYSTEM_ID,),
                    )
                    for name, model, state, temp, hours, updated, attrs in rows:
                        try:
                            ts = parse_timestamp(updated)
                            age_min = (now - ts).total_seconds() / 60.0
                            stale = age_min > SMART_STALE_MIN
                        except Exception:  # noqa: BLE001
                            stale = True
                        info["smart"][name] = {
                            "model": model,
                            "state": state,
                            "temp": temp,
                            "hours": hours,
                            "attrs": _parse_smart_attrs(attrs),
                            "stale": bool(stale),
                        }
                except Exception as e:  # noqa: BLE001
                    col.add(
                        UNKNOWN,
                        "SMART(Beszel DB)",
                        "读取 smart_devices 失败：%s" % type(e).__name__,
                    )
        finally:
            con.close()
        info["ok"] = True
    except Exception as e:  # noqa: BLE001
        info["ok"] = False
        # Avoid exposing paths or database details in a routine status report.
        info["err"] = type(e).__name__
    return info


def collect_services():
    res = []
    for svc in ["sshd", "smb", "docker", "firewalld", "NetworkManager"]:
        rc, out, _ = run(["systemctl", "is-active", svc])
        res.append((svc, out.strip() or "unknown"))
    rc, nmb, _ = run(["systemctl", "is-active", "nmb"])
    return res, (nmb.strip() or "unknown")


def collect_containers():
    """Inspect all Docker containers, including exited/stopped containers."""
    fmt = "{{.Name}}|{{.State.Status}}|{{.State.ExitCode}}|{{.RestartCount}}|" \
          "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    rc, id_out, err = run(["docker", "ps", "-aq"], timeout=20)
    if rc != 0:
        return None, err or "docker ps 失败"
    ids = id_out.splitlines()
    if not ids:
        return [], None

    rc, out, err = run(["docker", "inspect", "--format", fmt] + ids, timeout=30)
    if rc != 0:
        return None, err or "docker inspect 失败"

    rows = []
    for line in out.splitlines():
        p = line.split("|")
        if len(p) == 5:
            rows.append({
                "name": p[0].lstrip("/"),
                "status": p[1],
                "exit": p[2],
                "restarts": p[3],
                "health": p[4],
            })
    if len(rows) != len(ids):
        return None, "docker inspect 返回记录数与容器数不一致"
    return rows, None


def collect_journal_errors():
    rc, k, _ = run(["journalctl", "-k", "--since", "24 hours ago", "-p", "err",
                    "--no-pager", "-q", "-o", "cat"], timeout=30)
    kcount = len([l for l in k.splitlines() if l.strip()]) if rc == 0 else None
    storage_hit = False
    if kcount:
        storage_hit = bool(re.search(
            r"I/O error|Buffer I/O|EXT4-fs error|XFS.*error|uncorrectable|"
            r"blk_update_request|ata[0-9].*error", k, re.I))
    rc2, s, _ = run(["journalctl", "--since", "24 hours ago", "-p", "err",
                     "--no-pager", "-q", "-o", "cat"], timeout=30)
    if rc2 != 0:
        return kcount, storage_hit, None, 0
    sys_lines = [l for l in s.splitlines() if l.strip()]
    # Benign noise: non-interactive `sudo -n` that is refused for lack of a
    # password is logged at LOG_ERR but is an expected authorization refusal,
    # not a system fault. Count separately and disclose rather than hide.
    benign = re.compile(r"a password is required")
    benign_n = sum(1 for l in sys_lines if benign.search(l))
    scount = len(sys_lines) - benign_n
    return kcount, storage_hit, scount, benign_n


def _parse_patrol_text(txt):
    m = re.search(r"巡检汇总：(\w+)\s*\|\s*OK=(\d+)\s*WARN=(\d+)\s*CRITICAL=(\d+)", txt)
    ts = re.search(r"Time:\s*(.+)", txt)
    if not m:
        return None
    return {"overall": m.group(1), "ok": int(m.group(2)),
            "warn": int(m.group(3)), "crit": int(m.group(4)),
            "time": ts.group(1).strip() if ts else ""}


def collect_root_patrol():
    """Read TODAY's existing ROOT 08:00 patrol report; verify freshness.

    Prefer the per-run timestamped snapshot; fall back to the day history log.
    The '-latest.txt' file is a stale manual run and is intentionally ignored.
    """
    today = datetime.now().strftime("%Y%m%d")
    today_dash = datetime.now().strftime("%Y-%m-%d")
    candidates = []
    for d in ROOT_PATROL_DIRS:
        candidates += sorted(glob.glob(os.path.join(
            d, "old-asus-auto-check-%s-*.txt" % today)))
        candidates += sorted(glob.glob(os.path.join(
            d, "history", "old-asus-auto-check-%s.log" % today_dash)))
    for path in reversed(candidates):  # newest filename first
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:  # noqa: BLE001
            continue
        parsed = _parse_patrol_text(txt)
        if not parsed:
            continue
        parsed["stale"] = (today_dash not in parsed["time"])
        parsed["source"] = os.path.basename(path)
        return parsed
    return None


# ---- build message -------------------------------------------------------
def evaluate(host, mem, temps, disks, db, svcs, nmb, containers, cerr, rootp, col):
    # host status from Beszel
    if db.get("system_id_missing"):
        col.add(
            UNKNOWN,
            "主机在线",
            "未配置 BESZEL_SYSTEM_ID，无法匹配本机状态",
        )
    elif db.get("status") == "up":
        col.add(OK, "主机在线", "Beszel up（心跳 %s UTC）" % (db.get("status_updated") or "?")[:19])
    elif db.get("status") is None:
        col.add(UNKNOWN, "主机在线", "无法从 Beszel DB 读取指定系统状态")
    else:
        col.add(CRIT, "主机在线", "Beszel 状态=%s（可能主机离线/Agent 失联）" % db.get("status"))
    col.add(OK, "运行时间/负载", "uptime=%s；负载 %s（核数 %s）" % (
        host.get("uptime"),
        "/".join(str(x) for x in host["load"]) if host.get("load") else "未知",
        host.get("cores")))
    l1 = host["load"][0] if host.get("load") else None
    if l1 is not None:
        if l1 > host["cores"] * 4:
            col.add(CRIT, "负载水位", "1 分钟负载 %.2f > 4×核数" % l1)
        elif l1 > host["cores"] * 2:
            col.add(WARN, "负载水位", "1 分钟负载 %.2f > 2×核数" % l1)

    if mem.get("ok"):
        lv = OK
        if mem["used_pct"] >= 90:
            lv = CRIT
        elif mem["used_pct"] >= 80:
            lv = WARN
        col.add(lv, "内存", "使用 %s%%，可用 %.2f GiB" % (mem["used_pct"], mem["avail_gib"]))
        slv = WARN if mem["swap_used_mib"] > 2048 else OK
        col.add(slv, "Swap", "已用 %s MiB / %.2f GiB（Beszel 无独立 Swap 告警，日报持续检查）"
                             % (mem["swap_used_mib"], mem["swap_total_gib"]))
    else:
        col.add(UNKNOWN, "内存/Swap", "读取 /proc/meminfo 失败")

    if temps.get("ok"):
        pkg = temps["package"][1] if temps["package"] else None
        plv = CRIT if pkg is not None and pkg >= 80 else OK
        core_txt = "，".join("%s %.1f°C" % (l, v) for l, v in temps["cores"]) or "无 Core 传感器"
        pch_txt = ""
        if temps["pch"]:
            pz, pv = temps["pch"]
            pch_txt = "；PCH(%s) %.1f°C" % (pz, pv)
        col.add(plv, "CPU 温度", "Package %.1f°C；%s%s（来源 coretemp/sysfs）"
                             % (pkg if pkg is not None else -1, core_txt, pch_txt))
    else:
        col.add(UNKNOWN, "CPU 温度", "未读取到 coretemp 传感器")

    for path, pct in disks:
        if pct is None:
            col.add(UNKNOWN, "容量 " + path, "df 读取失败")
        elif pct >= 90:
            col.add(CRIT, "容量 " + path, "%d%%" % pct)
        elif pct >= 85:
            col.add(WARN, "容量 " + path, "%d%%" % pct)
        else:
            col.add(OK, "容量 " + path, "%d%%" % pct)

    # SMART from Beszel DB
    if not db.get("ok"):
        col.add(UNKNOWN, "SMART", "Beszel DB 不可读：%s" % db.get("err", "?"))
    else:
        for dev in ["/dev/sda", "/dev/sdb"]:
            s = db["smart"].get(dev)
            if not s:
                if db.get("system_id_missing"):
                    detail = "未配置 BESZEL_SYSTEM_ID，无法匹配 Beszel 设备记录"
                else:
                    detail = "Beszel 未上报该设备（非特权账号无法直跑 smartctl）"
                col.add(UNKNOWN, "SMART " + dev, detail)
                continue
            a = s["attrs"]
            if s["stale"]:
                col.add(UNKNOWN, "SMART " + dev, "%s 数据陈旧（>%dmin 未更新），不据此判定健康"
                        % (s["model"], SMART_STALE_MIN))
                continue
            if str(s["state"]).upper() != "PASSED":
                lv = CRIT
            elif (a["pending"] or 0) > 0 or (a["uncorrect"] or 0) > 0:
                lv = CRIT
            elif (a["realloc"] or 0) > 0:
                lv = WARN
            else:
                lv = OK
            def nv(x):
                # attribute not reported by this drive/vendor -> say so explicitly
                return x if x is not None else "未上报"
            col.add(lv, "SMART " + dev,
                    "%s %s，%s°C，通电 %sh；重映射 %s/待处理 %s/不可校正 %s（数据源 Beszel/smartctl）"
                    % (s["model"], s["state"], s["temp"], s["hours"],
                       nv(a["realloc"]), nv(a["pending"]), nv(a["uncorrect"])))

    # services
    for svc, state in svcs:
        col.add(OK if state == "active" else CRIT, "服务 " + svc, state)
    col.add(OK if nmb in ("active", "inactive") else WARN, "服务 nmb(备注)",
            "%s（NetBIOS，现代 SMB 走 445 通常不需要，记为备注）" % nmb)

    # containers
    if containers is None:
        col.add(UNKNOWN, "Docker 容器", "采集失败：%s" % cerr)
    elif not containers:
        col.add(UNKNOWN, "Docker 容器", "Docker 可用，但当前未发现容器")
    else:
        bad = [c for c in containers if c["status"] != "running" or c["health"] == "unhealthy"]
        restart_warn = [c for c in containers if c["restarts"].isdigit() and int(c["restarts"]) >= 5]
        total_restart = sum(int(c["restarts"]) for c in containers if c["restarts"].isdigit())
        n_health = sum(1 for c in containers if c["health"] == "healthy")
        if bad:
            for c in bad:
                col.add(CRIT, "容器 " + c["name"],
                        "status=%s exit=%s health=%s restarts=%s"
                        % (c["status"], c["exit"], c["health"], c["restarts"]))
        else:
            col.add(OK, "Docker 容器",
                    "%d 个全部 running，异常退出 0，重启合计 %d，healthy %d（无 healthcheck 的记为备注非异常）"
                    % (len(containers), total_restart, n_health))
        for c in restart_warn:
            col.add(WARN, "容器重启 " + c["name"], "restarts=%s" % c["restarts"])

    # journal
    kcount, storage_hit, scount, benign_n = collect_journal_errors()
    if kcount is None:
        col.add(UNKNOWN, "24h 内核错误", "journalctl 读取失败")
    elif kcount == 0:
        col.add(OK, "24h 内核错误", "0 条 err 及以上")
    else:
        col.add(CRIT if storage_hit else WARN, "24h 内核错误",
                "%d 条%s" % (kcount, "（含疑似存储/文件系统错误）" if storage_hit else ""))
    note = "；另剔除 %d 条非交互 sudo 授权拒绝（良性）" % benign_n if benign_n else ""
    if scount is None:
        col.add(UNKNOWN, "24h 系统错误", "journalctl 读取失败")
    else:
        col.add(WARN if scount else OK, "24h 系统错误", "%d 条 err 及以上%s" % (scount, note))

    # alerts
    if db.get("alerts_total") is None:
        col.add(UNKNOWN, "Beszel 告警", "无法读取 alerts 表")
    elif db["alerts_firing"]:
        col.add(CRIT, "未恢复告警", "、".join(db["alerts_firing"]))
    else:
        col.add(OK, "Beszel 告警", "%d 条规则，当前 0 条触发/未恢复" % db["alerts_total"])

    # existing root patrol
    if rootp:
        if rootp.get("stale"):
            col.add(UNKNOWN, "08:00 root 巡检",
                    "未找到今日 root 巡检报告（最近来源 %s，时间 %s），该来源标为未检查"
                    % (rootp.get("source", "?"), rootp.get("time", "?")))
        else:
            lv = {"OK": OK, "WARN": WARN, "CRITICAL": CRIT}.get(rootp["overall"], UNKNOWN)
            col.add(lv, "08:00 root 巡检",
                    "%s（OK=%d/WARN=%d/CRITICAL=%d，%s）"
                    % (rootp["overall"], rootp["ok"], rootp["warn"], rootp["crit"],
                       rootp.get("time", "?")))
    else:
        col.add(UNKNOWN, "08:00 root 巡检", "未读到今日 root 巡检报告")


def render(col, host):
    now = datetime.now().strftime("%Y-%m-%d %H:%M %Z")
    n_crit, n_warn = col.count(CRIT), col.count(WARN)
    n_unk = col.count(UNKNOWN)
    overall = col.worst()
    title_color = color(overall)
    L = []
    L.append("## 🖥️ asus 每日巡检摘要")
    L.append("> %s（Asia/Shanghai）" % now)
    L.append('> 总体：<font color="%s">%s</font> ｜ 严重 %d ｜ 注意 %d ｜ 未检查/失败 %d'
             % (title_color, LEVEL_CN[overall], n_crit, n_warn, n_unk))
    L.append("")
    # group by section in insertion order, but compact: one line per item
    for lv, label, text in col.items:
        L.append("> %s　%s：%s" % (tag(lv), label, text))
    L.append("")
    if BESZEL_URL:
        L.append("> Beszel：" + BESZEL_URL)
    return "\n".join(L), overall


def limit_wecom_message(msg):
    """Limit only the outbound message; local reports keep the complete text."""
    encoded = msg.encode("utf-8")
    if len(encoded) <= WECOM_MAX_BYTES:
        return msg
    suffix = "\n> …（企业微信消息已截断；完整报告见本机 latest.md 和当日日志）"
    budget = WECOM_MAX_BYTES - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", "ignore") + suffix


def write_log(msg, overall, result):
    # Ensure newly created files are private even when launched from a shell
    # whose umask is more permissive than the systemd unit's UMask=0077.
    previous_umask = os.umask(0o077)
    try:
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
        os.chmod(LOG_DIR, 0o700)
        day = os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d") + ".log")
        if os.path.exists(day):
            os.chmod(day, 0o600)
        with open(day, "a", encoding="utf-8") as f:
            f.write("\n===== %s | overall=%s | result=%s =====\n"
                    % (datetime.now().strftime("%F %T %Z"), overall, result))
            f.write(msg + "\n")
        latest = os.path.join(LOG_DIR, "latest.md")
        if os.path.exists(latest):
            os.chmod(latest, 0o600)
        with open(latest, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
        os.chmod(day, 0o600)
        os.chmod(latest, 0o600)
    finally:
        os.umask(previous_umask)
    # retention
    rc, out, _ = run(["find", LOG_DIR, "-name", "*.log", "-mtime", "+%d" % RETENTION_DAYS,
                      "-delete"])
    return day, latest


def load_key(path):
    st = os.stat(path)
    if st.st_uid != os.getuid():
        raise SystemExit("key 文件属主不是当前用户，拒绝发送：%s" % path)
    mode = st.st_mode & 0o777
    if mode not in (0o600, 0o400):
        raise SystemExit("key 文件权限必须为 600（当前 %04o），拒绝发送" % mode)
    with open(path, encoding="utf-8") as f:
        key = f.read().strip()
    if not key or "/" in key or key.startswith("http"):
        raise SystemExit("key 文件内容无效（应只含 key，不含完整 URL 或斜杠）")
    return key


def send_wecom(key, msg):
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s" % key
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": msg}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data  # {errcode:0,errmsg:'ok'} on success


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--key-file", default=KEY_FILE)
    args = ap.parse_args()
    if not args.send and not args.dry_run:
        ap.error("choose --send or --dry-run")

    col = Col()
    host = collect_host()
    mem = collect_mem()
    temps = collect_temps()
    disks = collect_disk_space()
    db = collect_beszel_db(col)
    svcs, nmb = collect_services()
    containers, cerr = collect_containers()
    rootp = collect_root_patrol()
    evaluate(host, mem, temps, disks, db, svcs, nmb, containers, cerr, rootp, col)
    msg, overall = render(col, host)

    if args.dry_run:
        print(msg)
        try:
            day_path, latest_path = write_log(msg, overall, "DRY RUN (not sent)")
        except OSError as e:
            print("REPORT_SAVE_ERROR unable to save report under %s (%s)"
                  % (LOG_DIR, type(e).__name__), file=sys.stderr)
            return 3
        print("\n[dry-run] 不发送；严重=%d 注意=%d 未知=%d；字节=%d"
              % (col.count(CRIT), col.count(WARN), col.count(UNKNOWN), len(msg.encode())))
        print("[dry-run] 报告已保存：%s" % latest_path)
        print("[dry-run] 当日日志：%s" % day_path)
        return 0

    # Scheduled run before the operator has provisioned a key: skip quietly
    # (exit 0) so an enabled timer does not generate daily failures.
    if not os.path.exists(args.key_file):
        write_log(msg, overall, "SKIPPED: key file not provisioned")
        print("SEND_SKIP no key file at %s (message rendered & logged only)" % args.key_file)
        return 0

    try:
        key = load_key(args.key_file)
    except (OSError, SystemExit) as e:
        error_type = type(e).__name__
        try:
            write_log(msg, overall, "FAILED: WeCom key validation (%s)" % error_type)
        except OSError as log_error:
            print("REPORT_SAVE_ERROR unable to save report under %s (%s)"
                  % (LOG_DIR, type(log_error).__name__), file=sys.stderr)
            return 3
        print("SEND_ERROR key file could not be validated (%s); no message sent" % error_type)
        return 1
    try:
        resp = send_wecom(key, limit_wecom_message(msg))
    except Exception as e:  # noqa: BLE001
        # urllib exceptions can include the request URL, which contains the bot key.
        # Log only the exception class; never print exception text or formatted exception representations.
        error_type = type(e).__name__
        write_log(msg, overall, "ERROR: WeCom request failed (%s)" % error_type)
        print("SEND_ERROR request failed (%s); details withheld for secret safety" % error_type)
        return 1
    ok = (resp.get("errcode") == 0)
    write_log(msg, overall, "SENT" if ok else "FAILED: WeCom errcode=%s" % resp.get("errcode"))
    # do not print the key; only errcode/errmsg
    print("SEND_RESULT errcode=%s errmsg=%s" % (resp.get("errcode"), resp.get("errmsg")))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
