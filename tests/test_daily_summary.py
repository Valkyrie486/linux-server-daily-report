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

    def test_journal_permission_warning_never_counts_as_zero(self):
        warning = (
            "Hint: You are currently not seeing messages from other users and the system."
        )
        with mock.patch.object(daily_summary, "run", side_effect=[
            (0, "", warning), (0, "", warning),
        ]) as run_mock:
            result = daily_summary.collect_journal_errors()

        self.assertIsNone(result.kernel_count)
        self.assertIsNone(result.system_count)
        self.assertEqual(result.kernel_state, daily_summary.JOURNAL_PERMISSION)
        self.assertEqual(result.system_state, daily_summary.JOURNAL_PERMISSION)
        self.assertEqual(run_mock.call_count, 2)
        self.assertTrue(all("-q" not in call[0][0] for call in run_mock.call_args_list))

    def test_journal_nonzero_exit_is_unknown_but_other_complete_scope_counts(self):
        with mock.patch.object(daily_summary, "run", side_effect=[
            (1, "", "journalctl command failed"), (0, "", ""),
        ]):
            result = daily_summary.collect_journal_errors()

        self.assertIsNone(result.kernel_count)
        self.assertEqual(result.kernel_state, daily_summary.JOURNAL_FAILED)
        self.assertEqual(result.system_count, 0)
        self.assertEqual(result.system_state, daily_summary.JOURNAL_COMPLETE)

    def test_journal_other_stderr_warning_marks_scope_incomplete(self):
        with mock.patch.object(daily_summary, "run", return_value=(0, "", "Warning: journal files not fully readable")):
            result = daily_summary.collect_journal_errors()

        self.assertIsNone(result.kernel_count)
        self.assertIsNone(result.system_count)
        self.assertEqual(result.kernel_state, daily_summary.JOURNAL_INCOMPLETE)
        self.assertEqual(result.system_state, daily_summary.JOURNAL_INCOMPLETE)

    def test_journal_complete_empty_reads_count_as_zero(self):
        with mock.patch.object(daily_summary, "run", return_value=(0, "", "")):
            result = daily_summary.collect_journal_errors()

        self.assertEqual(result.kernel_count, 0)
        self.assertEqual(result.system_count, 0)
        self.assertEqual(result.kernel_state, daily_summary.JOURNAL_COMPLETE)
        self.assertEqual(result.system_state, daily_summary.JOURNAL_COMPLETE)

    def test_incomplete_journal_read_renders_unknown_items(self):
        result = daily_summary.JournalResult(
            None, False, None, 0,
            daily_summary.JOURNAL_PERMISSION, daily_summary.JOURNAL_INCOMPLETE,
        )
        col = daily_summary.Col()
        with mock.patch.object(daily_summary, "collect_journal_errors", return_value=result):
            daily_summary.evaluate(
                {"uptime": "up", "load": None, "cores": 1},
                {"ok": False}, {"ok": False}, [],
                {"system_id_missing": True, "ok": False}, [], "inactive",
                [], None, None, col,
            )

        items = {label: (level, detail) for level, label, detail in col.items}
        self.assertEqual(items["24h 内核错误"][0], daily_summary.UNKNOWN)
        self.assertIn("权限不足", items["24h 内核错误"][1])
        self.assertEqual(items["24h 系统错误"][0], daily_summary.UNKNOWN)
        self.assertIn("读取范围不完整", items["24h 系统错误"][1])

    def test_render_uses_consistent_overall_and_item_statuses(self):
        cases = [
            ([(daily_summary.WARN, "warning", "detail")], "注意", "注意", "注意 1"),
            ([(daily_summary.CRIT, "critical", "detail")], "严重异常", "严重异常", "严重 1"),
            ([(daily_summary.UNKNOWN, "unknown", "detail")], "未知/未检查", "未知/未检查", "未检查/失败 1"),
            ([(daily_summary.OK, "ok", "detail")], "正常", "正常", "严重 0"),
        ]
        for items, overall_label, item_label, count_label in cases:
            with self.subTest(overall=overall_label):
                col = daily_summary.Col()
                for level, label, detail in items:
                    col.add(level, label, detail)
                report, overall = daily_summary.render(col, {})
                self.assertEqual(daily_summary.LEVEL_CN[overall], overall_label)
                self.assertIn("总体：", report)
                self.assertIn(item_label, report)
                self.assertIn(count_label, report)
        self.assertEqual(daily_summary.Col().worst(), daily_summary.OK)

    def test_long_report_is_only_truncated_for_wecom(self):
        full_report = "报告头\n" + ("异常详情-" * 1200)
        outbound = daily_summary.limit_wecom_message(full_report)

        self.assertGreater(len(full_report.encode("utf-8")), daily_summary.WECOM_MAX_BYTES)
        self.assertLessEqual(len(outbound.encode("utf-8")), daily_summary.WECOM_MAX_BYTES)
        self.assertIn("企业微信消息已截断", outbound)
        day_path, latest_path = daily_summary.write_log(
            full_report, daily_summary.WARN, "SENT"
        )
        saved = Path(latest_path).read_text(encoding="utf-8")
        daily = Path(day_path).read_text(encoding="utf-8")
        self.assertEqual(saved, full_report + "\n")
        self.assertIn(full_report, daily)
        self.assertNotIn("企业微信消息已截断", saved)

    def test_normal_wecom_message_is_unchanged(self):
        report = "短报告内容"
        self.assertEqual(daily_summary.limit_wecom_message(report), report)

    def test_send_limits_payload_but_logs_full_report(self):
        full_report = "完整报告\n" + ("详细巡检项-" * 1000)
        key_file = os.path.join(self.temp_dir.name, "wecom.key")
        Path(key_file).write_text("test-key", encoding="utf-8")
        payloads = []
        collectors = {
            "collect_host": {}, "collect_mem": {}, "collect_temps": {},
            "collect_disk_space": [], "collect_beszel_db": {},
            "collect_services": ([], "unknown"),
            "collect_containers": ([], None), "collect_root_patrol": None,
        }
        with contextlib.ExitStack() as stack:
            for name, value in collectors.items():
                stack.enter_context(mock.patch.object(daily_summary, name, return_value=value))
            stack.enter_context(mock.patch.object(daily_summary, "evaluate"))
            stack.enter_context(mock.patch.object(
                daily_summary, "render", return_value=(full_report, daily_summary.WARN)
            ))
            stack.enter_context(mock.patch.object(daily_summary, "KEY_FILE", key_file))
            stack.enter_context(mock.patch.object(daily_summary, "load_key", return_value="test-key"))
            stack.enter_context(mock.patch.object(
                daily_summary, "send_wecom",
                side_effect=lambda key, payload: payloads.append(payload) or {"errcode": 0, "errmsg": "ok"},
            ))
            stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT), "--send"]))
            with contextlib.redirect_stdout(io.StringIO()):
                rc = daily_summary.main()

        self.assertEqual(rc, 0)
        self.assertEqual(len(payloads), 1)
        self.assertLessEqual(len(payloads[0].encode("utf-8")), daily_summary.WECOM_MAX_BYTES)
        self.assertIn("企业微信消息已截断", payloads[0])
        self.assertEqual(
            Path(daily_summary.LOG_DIR, "latest.md").read_text(encoding="utf-8"),
            full_report + "\n",
        )

    def test_invalid_key_is_logged_without_sending(self):
        full_report = "完整但未发送的报告"
        key_file = os.path.join(self.temp_dir.name, "wecom.key")
        Path(key_file).write_text("invalid-test-key", encoding="utf-8")
        collectors = {
            "collect_host": {}, "collect_mem": {}, "collect_temps": {},
            "collect_disk_space": [], "collect_beszel_db": {},
            "collect_services": ([], "unknown"),
            "collect_containers": ([], None), "collect_root_patrol": None,
        }
        with contextlib.ExitStack() as stack:
            for name, value in collectors.items():
                stack.enter_context(mock.patch.object(daily_summary, name, return_value=value))
            stack.enter_context(mock.patch.object(daily_summary, "evaluate"))
            stack.enter_context(mock.patch.object(
                daily_summary, "render", return_value=(full_report, daily_summary.OK)
            ))
            stack.enter_context(mock.patch.object(daily_summary, "KEY_FILE", key_file))
            stack.enter_context(mock.patch.object(
                daily_summary, "load_key", side_effect=SystemExit("permission detail")
            ))
            send_mock = stack.enter_context(mock.patch.object(daily_summary, "send_wecom"))
            stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT), "--send"]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = daily_summary.main()

        self.assertEqual(rc, 1)
        send_mock.assert_not_called()
        log_text = Path(daily_summary.LOG_DIR, "latest.md").read_text(encoding="utf-8")
        day_text = next(Path(daily_summary.LOG_DIR).glob("*.log")).read_text(encoding="utf-8")
        self.assertEqual(log_text, full_report + "\n")
        self.assertIn("FAILED: WeCom key validation (SystemExit)", day_text)
        self.assertNotIn("permission detail", output.getvalue())

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
            "collect_journal_errors": daily_summary.JournalResult(
                0, False, 0, 0, daily_summary.JOURNAL_COMPLETE, daily_summary.JOURNAL_COMPLETE
            ),
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
            "collect_journal_errors": daily_summary.JournalResult(
                0, False, 0, 0, daily_summary.JOURNAL_COMPLETE, daily_summary.JOURNAL_COMPLETE
            ),
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
