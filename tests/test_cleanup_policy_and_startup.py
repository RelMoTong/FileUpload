# -*- coding: utf-8 -*-
"""自动清理全局策略、审计日志与开机自启自愈测试。"""

import datetime
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.main_window import (  # noqa: E402
    AUTO_CLEANUP_FAILURE_LIMIT,
    MainWindow,
    _extract_startup_target,
)


DiskUsage = namedtuple("DiskUsage", "total used free")


class ImmediateFuture:
    def __init__(self, function):
        self.error = None
        try:
            function()
        except Exception as exc:
            self.error = exc

    def result(self):
        if self.error is not None:
            raise self.error


class ImmediateExecutor:
    def submit(self, function):
        return ImmediateFuture(function)


class FakeCleanupWindow:
    def __init__(self, folders):
        self.auto_delete_folders = list(folders)
        self.auto_delete_folder = ""
        self.auto_delete_threshold = 90
        self.auto_delete_target_percent = 85
        self.auto_delete_keep_days = 70
        self.auto_delete_formats = []
        self.auto_delete_use_trash = False
        self._auto_cleanup_lock = threading.Lock()
        self._auto_cleanup_running = True
        self.logs = []
        self.audit = []

    def _get_auto_cleanup_folders(self):
        return MainWindow._get_auto_cleanup_folders(self)

    def _validate_cleanup_folder_group(self, folders):
        return MainWindow._validate_cleanup_folder_group(folders)

    def _deduplicate_cleanup_roots(self, folders):
        return MainWindow._deduplicate_cleanup_roots(folders)

    def _sort_cleanup_candidates(self, files):
        return MainWindow._sort_cleanup_candidates(files)

    def _emit_async_log(self, message):
        self.logs.append(message)

    def _write_cleanup_audit(self, event, run_id, **fields):
        self.audit.append({"event": event, "run_id": run_id, **fields})
        return True

    def _record_cleanup_blocked(self, status, trigger_source, folders, error):
        return MainWindow._record_cleanup_blocked(
            self, status, trigger_source, folders, error
        )


class TestCleanupPolicy(unittest.TestCase):
    def test_global_oldest_across_nested_roots_ignores_keep_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root_a = base / "产线A"
            root_b = base / "产线B" / "子目录"
            root_a.mkdir(parents=True)
            root_b.mkdir(parents=True)
            newer = root_a / "new.jpg"
            oldest = root_b / "old.jpg"
            newer.write_bytes(b"new")
            oldest.write_bytes(b"old")
            now = time.time()
            os.utime(oldest, (now - 100, now - 100))
            os.utime(newer, (now - 10, now - 10))

            fake = FakeCleanupWindow([str(root_a), str(root_b.parent)])
            deleted = []
            usage = [
                DiskUsage(1000, 900, 100),
                DiskUsage(1000, 840, 160),
            ]
            with mock.patch("shutil.disk_usage", side_effect=usage), \
                 mock.patch("os.remove", side_effect=lambda path: deleted.append(path)):
                MainWindow._auto_cleanup_task(fake, "test")

            self.assertEqual(deleted, [str(oldest)])
            self.assertTrue(any(item.get("status") == "达到目标" for item in fake.audit))
            self.assertFalse(fake._auto_cleanup_running)

    def test_scan_and_delete_failures_stop_at_shared_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(AUTO_CLEANUP_FAILURE_LIMIT + 5):
                path = root / f"{index:02d}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))

            fake = FakeCleanupWindow([str(root)])
            usage = DiskUsage(1000, 900, 100)
            with mock.patch("shutil.disk_usage", return_value=usage), \
                 mock.patch("os.remove", side_effect=PermissionError("locked")):
                MainWindow._auto_cleanup_task(fake, "test")

            failures = [item for item in fake.audit if item["event"] == "DELETE_FAIL"]
            self.assertEqual(len(failures), AUTO_CLEANUP_FAILURE_LIMIT)
            self.assertEqual(failures[-1]["failed_count"], AUTO_CLEANUP_FAILURE_LIMIT)
            self.assertTrue(any(item.get("status") == "失败达到20次" for item in fake.audit))

    def test_trash_mode_stops_when_free_space_does_not_increase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))

            fake = FakeCleanupWindow([str(root)])
            fake.auto_delete_use_trash = True
            moved = []
            usage = DiskUsage(1000, 900, 100)
            with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
                 mock.patch("src.ui.main_window.send_to_trash", side_effect=lambda path: moved.append(path)), \
                 mock.patch("shutil.disk_usage", return_value=usage):
                MainWindow._auto_cleanup_task(fake, "test")

            self.assertEqual(len(moved), 1)
            self.assertTrue(any(item.get("status") == "回收站未释放空间" for item in fake.audit))

    def test_cross_volume_group_is_rejected(self):
        with mock.patch.object(
            MainWindow,
            "_cleanup_volume_identity",
            side_effect=[("volume-a", "D:\\"), ("volume-b", "E:\\")],
        ):
            valid, error, details = MainWindow._validate_cleanup_folder_group(["D:\\A", "E:\\B"])
        self.assertFalse(valid)
        self.assertIn("必须位于同一磁盘", error)
        self.assertEqual(len(details), 2)

    def test_cleanup_audit_uses_independent_daily_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake = mock.Mock()
            fake.app_dir = Path(temp_dir)
            fake._log_executor = ImmediateExecutor()
            MainWindow._write_cleanup_audit(fake, "DELETE_OK", "run-1", path="中文路径\\图片.jpg")

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            log_path = Path(temp_dir) / "logs" / f"cleanup_{today}.log"
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["event"], "DELETE_OK")
            self.assertEqual(record["path"], "中文路径\\图片.jpg")

    def test_deleted_file_is_audited_when_disk_usage_refresh_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "old.jpg"
            file_path.write_bytes(b"old")
            fake = FakeCleanupWindow([str(root)])
            usage = DiskUsage(1000, 900, 100)

            with mock.patch("shutil.disk_usage", side_effect=[usage, OSError("drive unavailable")]), \
                 mock.patch("os.remove") as remove:
                MainWindow._auto_cleanup_task(fake, "test")

            remove.assert_called_once_with(str(file_path))
            deleted = [item for item in fake.audit if item["event"] == "DELETE_OK"]
            self.assertEqual(len(deleted), 1)
            self.assertEqual(deleted[0]["path"], str(file_path))
            self.assertIsNone(deleted[0]["disk_used_percent"])
            self.assertIn("drive unavailable", deleted[0]["disk_usage_error"])
            self.assertTrue(any(item.get("status") == "路径不可用" for item in fake.audit))

    def test_audit_start_failure_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "old.jpg").write_bytes(b"old")
            fake = FakeCleanupWindow([str(root)])
            fake._write_cleanup_audit = mock.Mock(return_value=False)
            usage = DiskUsage(1000, 900, 100)

            with mock.patch("shutil.disk_usage", return_value=usage), \
                 mock.patch("os.remove") as remove:
                MainWindow._auto_cleanup_task(fake, "test")

            remove.assert_not_called()
            self.assertFalse(fake._auto_cleanup_running)

    def test_audit_failure_after_delete_stops_before_next_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            fake = FakeCleanupWindow([str(root)])
            original_writer = fake._write_cleanup_audit

            def fail_delete_ok(event, run_id, **fields):
                if event == "DELETE_OK":
                    return False
                return original_writer(event, run_id, **fields)

            fake._write_cleanup_audit = fail_delete_ok
            removed = []
            usage = DiskUsage(1000, 900, 100)
            with mock.patch("shutil.disk_usage", return_value=usage), \
                 mock.patch("os.remove", side_effect=lambda path: removed.append(path)):
                MainWindow._auto_cleanup_task(fake, "test")

            self.assertEqual(len(removed), 1)
            self.assertTrue(any(item.get("status") == "任务异常" for item in fake.audit))

    def test_audit_writer_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake = mock.Mock()
            fake.app_dir = Path(temp_dir)
            fake._log_executor = ImmediateExecutor()
            fake._emit_async_log = mock.Mock()
            with mock.patch("builtins.open", side_effect=PermissionError("read only")):
                written = MainWindow._write_cleanup_audit(
                    fake, "START", "run-fail", path="中文路径"
                )

            self.assertFalse(written)
            fake._emit_async_log.assert_called_once()

    def test_cleanup_uses_target_snapshot_for_entire_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "01.jpg"
            second = root / "02.jpg"
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            os.utime(first, (1, 1))
            os.utime(second, (2, 2))
            fake = FakeCleanupWindow([str(root)])
            removed = []

            def remove_and_change_config(path):
                removed.append(path)
                fake.auto_delete_target_percent = 10

            with mock.patch(
                "shutil.disk_usage",
                side_effect=[DiskUsage(1000, 900, 100), DiskUsage(1000, 840, 160)],
            ), mock.patch("os.remove", side_effect=remove_and_change_config):
                MainWindow._auto_cleanup_task(fake, "test")

            self.assertEqual(removed, [str(first)])
            start = next(item for item in fake.audit if item["event"] == "START")
            self.assertEqual(start["target_percent"], 85)

    def test_submit_failure_resets_running_flag(self):
        class BrokenExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("executor closed")

        fake = mock.Mock()
        fake._is_closing = False
        fake._auto_cleanup_lock = threading.Lock()
        fake._auto_cleanup_running = False
        fake._auto_cleanup_cancel_event = threading.Event()
        fake._cleanup_executor = BrokenExecutor()
        fake._auto_cleanup_task = mock.Mock()
        fake._get_auto_cleanup_folders.return_value = ["E:\\监测目录"]
        fake._append_log = mock.Mock()
        fake._record_cleanup_blocked = mock.Mock()

        submitted = MainWindow._submit_auto_cleanup(fake, "test")

        self.assertFalse(submitted)
        self.assertFalse(fake._auto_cleanup_running)
        fake._record_cleanup_blocked.assert_called_once()

    def test_shutdown_waits_for_cleanup_before_log_executor(self):
        order = []

        class RecordingExecutor:
            def __init__(self, name):
                self.name = name

            def shutdown(self, wait, cancel_futures=True):
                order.append((self.name, wait, cancel_futures))

        fake = mock.Mock()
        fake._is_closing = False
        fake._auto_cleanup_timer = mock.Mock()
        fake._auto_cleanup_cancel_event = threading.Event()
        fake._cleanup_executor = RecordingExecutor("cleanup")
        fake._disk_executor = RecordingExecutor("disk")
        fake._log_executor = RecordingExecutor("log")
        fake._shutdown_executor = MainWindow._shutdown_executor

        MainWindow._shutdown_background_executors(fake)

        self.assertTrue(fake._is_closing)
        self.assertTrue(fake._auto_cleanup_cancel_event.is_set())
        self.assertEqual([item[0] for item in order], ["cleanup", "disk", "log"])
        self.assertTrue(all(item[1] for item in order))

    def test_cancel_event_stops_before_next_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            fake = FakeCleanupWindow([str(root)])
            fake._auto_cleanup_cancel_event = threading.Event()
            removed = []

            def remove_then_cancel(path):
                removed.append(path)
                fake._auto_cleanup_cancel_event.set()

            usage = DiskUsage(1000, 900, 100)
            with mock.patch("shutil.disk_usage", return_value=usage), \
                 mock.patch("os.remove", side_effect=remove_then_cancel):
                MainWindow._auto_cleanup_task(fake, "test")

            self.assertEqual(len(removed), 1)
            self.assertTrue(any(item.get("status") == "任务异常" for item in fake.audit))


class FakeStartupWindow:
    def __init__(self, existing, current):
        self.existing = existing
        self.current = current
        self.auto_start_windows = True
        self.logs = []
        self.writes = []

    def _read_startup_command(self):
        return self.existing

    def _write_startup_command(self, command):
        self.existing = command
        self.writes.append(command)

    def _current_startup_command(self):
        return self.current

    def _startup_target_exists(self, command):
        return MainWindow._startup_target_exists(command)

    def _append_log(self, message):
        self.logs.append(message)


class TestStartupRepair(unittest.TestCase):
    def test_chinese_space_path_is_quoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe = Path(temp_dir) / "中文 目录" / "ImageUploadTool_v3.4.1.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            fake = mock.Mock()
            fake._quote_startup_arg = MainWindow._quote_startup_arg
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe)):
                command = MainWindow._current_startup_command(fake)
            self.assertEqual(command, f'"{exe}"')
            self.assertEqual(_extract_startup_target(command), str(exe))

    def test_source_command_requires_existing_main_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_script = Path(temp_dir) / "中文 目录" / "src" / "main.py"
            command = f'"{sys.executable}" "{missing_script}"'

            self.assertTrue(Path(sys.executable).is_file())
            self.assertFalse(missing_script.exists())
            self.assertFalse(MainWindow._startup_target_exists(command))

    def test_higher_current_version_replaces_old_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_exe = base / "ImageUploadTool_v3.3.0.exe"
            current_exe = base / "ImageUploadTool_v3.4.1.exe"
            old_exe.write_bytes(b"")
            current_exe.write_bytes(b"")
            fake = FakeStartupWindow(f'"{old_exe}"', f'"{current_exe}"')

            enabled = MainWindow._reconcile_startup_registration(fake)

            self.assertTrue(enabled)
            self.assertEqual(fake.writes, [f'"{current_exe}"'])

    def test_same_target_unquoted_registration_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe = Path(temp_dir) / "中文 目录" / "ImageUploadTool_v3.4.1.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            fake = FakeStartupWindow(str(exe), f'"{exe}"')

            enabled = MainWindow._reconcile_startup_registration(fake)

            self.assertTrue(enabled)
            self.assertEqual(fake.writes, [f'"{exe}"'])

    def test_lower_current_version_cannot_replace_valid_newer_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            current_exe = base / "ImageUploadTool_v3.4.1.exe"
            newer_exe = base / "ImageUploadTool_v9.0.0.exe"
            current_exe.write_bytes(b"")
            newer_exe.write_bytes(b"")
            fake = FakeStartupWindow(f'"{newer_exe}"', f'"{current_exe}"')

            enabled = MainWindow._reconcile_startup_registration(fake, explicit=True)

            self.assertTrue(enabled)
            self.assertEqual(fake.writes, [])
            self.assertEqual(fake.existing, f'"{newer_exe}"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
