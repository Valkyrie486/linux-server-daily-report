import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "asus-daily-summary.py"
SPEC = importlib.util.spec_from_file_location("daily_summary", SCRIPT)
daily_summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(daily_summary)


class DailySummaryDryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        daily_summary.LOG_DIR = os.path.join(self.temp_dir.name, "private", "daily-summary")

    def test_parse_beszel_timestamps_without_python37_api(self):
        utc = daily_summary.parse_timestamp("2024-01-02T03:04:05Z")
        offset = daily_summary.parse_timestamp("2024-01-02T11:04:05.123456789+08:00")
        naive = daily_summary.parse_timestamp("2024-01-02 03:04:05")

        self.assertEqual(utc.tzinfo, timezone.utc)
        self.assertEqual(offset.astimezone(timezone.utc), utc.replace(microsecond=123456))
        self.assertEqual(naive.tzinfo, timezone.utc)
        with self.assertRaises(ValueError):
            daily_summary.parse_timestamp("not-a-timestamp")

    def test_write_log_marks_dry_run_and_uses_private_paths(self):
        with mock.patch.object(daily_summary, "run", return_value=(0, "", "")):
            day_path, latest_path = daily_summary.write_log(
                "example report", daily_summary.OK, "DRY RUN (not sent)"
            )

        self.assertTrue(os.path.isfile(day_path))
        self.assertTrue(os.path.isfile(latest_path))
        self.assertEqual(Path(latest_path).read_text(encoding="utf-8"), "example report\n")
        self.assertIn("result=DRY RUN (not sent)", Path(day_path).read_text(encoding="utf-8"))
        self.assertEqual(Path(day_path).name[-4:], ".log")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(daily_summary.LOG_DIR).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(day_path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(latest_path).st_mode), 0o600)

    def test_dry_run_saves_output_without_loading_key_or_sending(self):
        collectors = {
            "collect_host": {"uptime": "up", "load": (0.1, 0.2, 0.3), "cores": 2},
            "collect_mem": {
                "ok": True,
                "used_pct": 20.0,
                "avail_gib": 1.0,
                "swap_used_mib": 0,
                "swap_total_gib": 0.0,
            },
            "collect_temps": {"ok": False},
            "collect_disk_space": [("/", 20)],
            "collect_beszel_db": {
                "ok": False,
                "err": "OperationalError",
                "system_id_missing": True,
                "smart": {},
                "alerts_total": None,
                "alerts_firing": [],
            },
            "collect_services": ([], "unknown"),
            "collect_containers": ([], None),
            "collect_root_patrol": None,
            "collect_journal_errors": (0, False, 0, 0),
        }

        with contextlib.ExitStack() as stack:
            for name, value in collectors.items():
                stack.enter_context(mock.patch.object(daily_summary, name, return_value=value))
            run_mock = stack.enter_context(mock.patch.object(daily_summary, "run", return_value=(0, "", "")))
            key_mock = stack.enter_context(mock.patch.object(
                daily_summary, "load_key", side_effect=AssertionError("dry-run must not read a key")
            ))
            send_mock = stack.enter_context(mock.patch.object(
                daily_summary, "send_wecom", side_effect=AssertionError("dry-run must not send")
            ))
            stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT), "--dry-run"]))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = daily_summary.main()

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run] 不发送", output)
        self.assertIn(os.path.join(daily_summary.LOG_DIR, "latest.md"), output)
        self.assertIn("[dry-run] 当日日志：", output)
        self.assertEqual(key_mock.call_count, 0)
        self.assertEqual(send_mock.call_count, 0)
        self.assertEqual(run_mock.call_count, 1)  # only the local log-retention check
        daily_logs = list(Path(daily_summary.LOG_DIR).glob("*.log"))
        self.assertEqual(len(daily_logs), 1)
        self.assertIn("result=DRY RUN (not sent)", daily_logs[0].read_text(encoding="utf-8"))

    def test_dry_run_reports_log_directory_failure(self):
        blocking_file = os.path.join(self.temp_dir.name, "not-a-directory")
        Path(blocking_file).write_text("block", encoding="utf-8")
        daily_summary.LOG_DIR = blocking_file
        collectors = {
            "collect_host": {"uptime": "up", "load": (0.1, 0.2, 0.3), "cores": 2},
            "collect_mem": {"ok": False},
            "collect_temps": {"ok": False},
            "collect_disk_space": [],
            "collect_beszel_db": {
                "ok": False, "err": "OperationalError", "system_id_missing": True,
                "smart": {}, "alerts_total": None, "alerts_firing": [],
            },
            "collect_services": ([], "unknown"),
            "collect_containers": ([], None),
            "collect_root_patrol": None,
            "collect_journal_errors": (0, False, 0, 0),
        }
        with contextlib.ExitStack() as stack:
            for name, value in collectors.items():
                stack.enter_context(mock.patch.object(daily_summary, name, return_value=value))
            stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT), "--dry-run"]))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = daily_summary.main()

        self.assertEqual(rc, 3)
        self.assertIn("REPORT_SAVE_ERROR", stderr.getvalue())
        self.assertIn("FileExistsError", stderr.getvalue())

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires a Linux runtime")
    def test_real_linux_cli_dry_run_writes_private_report_files(self):
        isolated_home = Path(self.temp_dir.name) / "isolated-home"
        isolated_home.mkdir(mode=0o700)
        env = os.environ.copy()
        env["HOME"] = str(isolated_home)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
            timeout=90,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[dry-run] 不发送", proc.stdout)
        self.assertIn("报告已保存：", proc.stdout)
        self.assertIn("当日日志：", proc.stdout)
        report_dir = isolated_home / "asus-logs" / "daily-summary"
        latest = report_dir / "latest.md"
        day_logs = list(report_dir.glob("*.log"))
        self.assertTrue(latest.is_file())
        self.assertEqual(len(day_logs), 1)
        self.assertIn("DRY RUN (not sent)", day_logs[0].read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(report_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(latest.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(day_logs[0].stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
