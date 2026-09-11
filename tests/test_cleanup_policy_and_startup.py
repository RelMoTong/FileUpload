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

from src.controllers import CleanupController  # noqa: E402
from src.models import AutoCleanupRequest, CleanupIndexRecord  # noqa: E402
from src.repositories import (  # noqa: E402
    CleanupAuditRepository,
    CleanupIndexRepository,
)
from src.services.cleanup_service import (  # noqa: E402
    AUTO_CLEANUP_FAILURE_LIMIT,
    CleanupService,
)
from src.services.runtime_service import RuntimeService, extract_startup_target  # noqa: E402

DiskUsage = namedtuple("DiskUsage", "total used free")


class MemoryAudit:
    def __init__(self, fail_event=None):
        self.records = []
        self.fail_event = fail_event
        self.last_error = ""

    def write(self, event, run_id, **fields):
        if event == self.fail_event:
            self.last_error = "forced failure"
            return False
        self.records.append({"event": event, "run_id": run_id, **fields})
        return True


def auto_request(root, **overrides):
    values = {
        "enabled": True,
        "folders": (str(root),),
        "trigger_percent": 90,
        "target_percent": 85,
        "formats": (),
        "use_trash": False,
        "trigger_source": "test",
    }
    values.update(overrides)
    return AutoCleanupRequest(**values)


def indexed_service(test_case, audit, request):
    index_dir = tempfile.TemporaryDirectory()
    test_case.addCleanup(index_dir.cleanup)
    repository = CleanupIndexRepository(Path(index_dir.name))
    service = CleanupService(audit, index_repository=repository)
    result = service.build_cleanup_index(request, threading.Event(), lambda message: None)
    test_case.assertTrue(result.success, result.error)
    return service, repository


class TestCleanupPolicy(unittest.TestCase):
    def test_global_oldest_across_nested_roots(self):
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
            audit = MemoryAudit()
            request = auto_request(base, folders=(str(root_a), str(root_b.parent)))
            service, repository = indexed_service(self, audit, request)
            fingerprint = service.cleanup_scope_fingerprint(request)
            newer_stat = newer.stat()
            oldest_stat = oldest.stat()
            repository.upsert_many(
                [
                    CleanupIndexRecord(
                        normalized_path=repository.normalize_path(str(newer)),
                        path=str(newer),
                        file_name=newer.name,
                        created_at=service.file_created_at(newer_stat),
                        size_bytes=newer_stat.st_size,
                        root_path=str(root_a),
                        source="test",
                        modified_at_ns=newer_stat.st_mtime_ns,
                        file_id=service.file_identity(newer_stat),
                    ),
                    CleanupIndexRecord(
                        normalized_path=repository.normalize_path(str(oldest)),
                        path=str(oldest),
                        file_name=oldest.name,
                        created_at=service.file_created_at(oldest_stat),
                        size_bytes=oldest_stat.st_size,
                        root_path=str(root_b.parent),
                        source="test",
                        modified_at_ns=oldest_stat.st_mtime_ns,
                        file_id=service.file_identity(oldest_stat),
                    ),
                ],
                fingerprint,
            )
            deleted = []

            with mock.patch(
                "shutil.disk_usage",
                side_effect=[DiskUsage(1000, 900, 100), DiskUsage(1000, 840, 160)],
            ), mock.patch(
                "src.services.cleanup_service.os.remove",
                side_effect=lambda path: deleted.append(path),
            ):
                result = service.run_auto_cleanup(
                    request,
                    threading.Event(),
                    lambda message: None,
                )

            self.assertEqual(deleted, [str(oldest)])
            self.assertEqual(result.status, "达到目标")
            self.assertTrue(any(item.get("status") == "达到目标" for item in audit.records))

    def test_scan_and_delete_failures_stop_at_shared_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(AUTO_CLEANUP_FAILURE_LIMIT + 5):
                path = root / f"{index:02d}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            audit = MemoryAudit()
            request = auto_request(root)
            service, _repository = indexed_service(self, audit, request)
            usage = DiskUsage(1000, 900, 100)

            with mock.patch("shutil.disk_usage", return_value=usage), mock.patch(
                "src.services.cleanup_service.os.remove",
                side_effect=PermissionError("locked"),
            ):
                result = service.run_auto_cleanup(
                    request, threading.Event(), lambda message: None
                )

            failures = [item for item in audit.records if item["event"] == "DELETE_FAIL"]
            self.assertEqual(len(failures), AUTO_CLEANUP_FAILURE_LIMIT)
            self.assertEqual(result.status, "失败达到20次")

    def test_trash_mode_stops_when_space_does_not_increase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            audit = MemoryAudit()
            request = auto_request(root, use_trash=True)
            service, _repository = indexed_service(self, audit, request)
            moved = []
            usage = DiskUsage(1000, 900, 100)

            with mock.patch(
                "src.services.cleanup_service.send_to_trash",
                side_effect=lambda path: moved.append(path),
            ), mock.patch("shutil.disk_usage", return_value=usage):
                result = service.run_auto_cleanup(
                    request,
                    threading.Event(),
                    lambda message: None,
                )

            self.assertEqual(len(moved), 1)
            self.assertEqual(result.status, "回收站未释放空间")

    def test_delete_mode_is_read_again_before_each_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
            audit = MemoryAudit()
            request = auto_request(root, use_trash=True)
            service, repository = indexed_service(self, audit, request)
            fingerprint = service.cleanup_scope_fingerprint(request)
            ordered = repository.oldest(fingerprint, 10)
            moved = []
            removed = []
            configured_modes = iter((True, True, False))

            with mock.patch(
                "src.services.cleanup_service.trash_supported", return_value=True
            ), mock.patch(
                "src.services.cleanup_service.send_to_trash",
                side_effect=lambda path: moved.append(path),
            ), mock.patch(
                "src.services.cleanup_service.os.remove",
                side_effect=lambda path: removed.append(path),
            ), mock.patch(
                "shutil.disk_usage",
                side_effect=[
                    DiskUsage(1000, 900, 100),
                    DiskUsage(1000, 890, 110),
                    DiskUsage(1000, 840, 160),
                ],
            ):
                result = service.run_auto_cleanup(
                    request,
                    threading.Event(),
                    lambda message: None,
                    lambda: next(configured_modes),
                )

            self.assertEqual(moved, [ordered[0].path])
            self.assertEqual(removed, [ordered[1].path])
            self.assertEqual(result.status, "达到目标")
            delete_records = [
                item for item in audit.records if item["event"] == "DELETE_OK"
            ]
            self.assertEqual(
                [item["delete_mode"] for item in delete_records],
                ["回收站", "永久删除"],
            )

    def test_cross_volume_group_is_rejected(self):
        with mock.patch.object(
            CleanupService,
            "cleanup_volume_identity",
            side_effect=[("volume-a", "D:\\"), ("volume-b", "E:\\")],
        ):
            valid, error, details = CleanupService.validate_cleanup_folder_group(
                ["D:\\A", "E:\\B"]
            )
        self.assertFalse(valid)
        self.assertIn("必须位于同一磁盘", error)
        self.assertEqual(len(details), 2)

    def test_candidate_policy_is_oldest_first_and_deduplicates_nested_roots(self):
        ordered = CleanupService.sort_cleanup_candidates(
            [(2, 1, "b.jpg"), (1, 1, "z.jpg"), (1, 1, "a.jpg")]
        )
        self.assertEqual([Path(item[2]).name for item in ordered], ["a.jpg", "z.jpg", "b.jpg"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            child = root / "child"
            child.mkdir()
            roots = CleanupService.deduplicate_cleanup_roots(
                [str(child), str(root), str(root)]
            )
            self.assertEqual(roots, [str(root.resolve())])

    def test_cleanup_audit_uses_independent_daily_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = CleanupAuditRepository(Path(temp_dir))
            self.assertTrue(
                repository.write(
                    "DELETE_OK", "run-1", path="中文路径\\图片.jpg"
                )
            )
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            path = Path(temp_dir) / "logs" / f"cleanup_{today}.log"
            record = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["event"], "DELETE_OK")
            self.assertEqual(record["path"], "中文路径\\图片.jpg")

    def test_deleted_file_is_audited_when_usage_refresh_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "old.jpg"
            file_path.write_bytes(b"old")
            audit = MemoryAudit()
            request = auto_request(root)
            service, _repository = indexed_service(self, audit, request)
            usage = DiskUsage(1000, 900, 100)

            with mock.patch(
                "shutil.disk_usage", side_effect=[usage, OSError("drive unavailable")]
            ), mock.patch("src.services.cleanup_service.os.remove") as remove:
                result = service.run_auto_cleanup(
                    request, threading.Event(), lambda message: None
                )

            remove.assert_called_once_with(str(file_path))
            deleted = [item for item in audit.records if item["event"] == "DELETE_OK"]
            self.assertEqual(deleted[0]["path"], str(file_path))
            self.assertIsNone(deleted[0]["disk_used_percent"])
            self.assertIn("drive unavailable", deleted[0]["disk_usage_error"])
            self.assertEqual(result.status, "路径不可用")

    def test_audit_start_failure_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "old.jpg").write_bytes(b"old")
            request = auto_request(root)
            service, _repository = indexed_service(
                self, MemoryAudit(fail_event="START"), request
            )
            with mock.patch(
                "shutil.disk_usage", return_value=DiskUsage(1000, 900, 100)
            ), mock.patch("src.services.cleanup_service.os.remove") as remove:
                result = service.run_auto_cleanup(
                    request, threading.Event(), lambda message: None
                )
            remove.assert_not_called()
            self.assertEqual(result.error, "清理审计日志写入失败")

    def test_audit_failure_after_delete_stops_before_next_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            request = auto_request(root)
            service, _repository = indexed_service(
                self, MemoryAudit(fail_event="DELETE_OK"), request
            )
            removed = []
            with mock.patch(
                "shutil.disk_usage", return_value=DiskUsage(1000, 900, 100)
            ), mock.patch(
                "src.services.cleanup_service.os.remove",
                side_effect=lambda path: removed.append(path),
            ):
                result = service.run_auto_cleanup(
                    request, threading.Event(), lambda message: None
                )
            self.assertEqual(len(removed), 1)
            self.assertEqual(result.status, "任务异常")

    def test_cancel_event_stops_before_next_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
            audit = MemoryAudit()
            request = auto_request(root)
            service, _repository = indexed_service(self, audit, request)
            cancel = threading.Event()
            removed = []

            def remove_then_cancel(path):
                removed.append(path)
                cancel.set()

            with mock.patch(
                "shutil.disk_usage", return_value=DiskUsage(1000, 900, 100)
            ), mock.patch(
                "src.services.cleanup_service.os.remove",
                side_effect=remove_then_cancel,
            ):
                result = service.run_auto_cleanup(
                    request, cancel, lambda message: None
                )
            self.assertEqual(len(removed), 1)
            self.assertEqual(result.status, "任务异常")

    def test_auto_cleanup_reads_ready_index_without_directory_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(5):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))

            audit = MemoryAudit()
            request = auto_request(root)
            service, repository = indexed_service(self, audit, request)
            marker = Path(repository.marker_path)
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.stat().st_size, 0)
            self.assertEqual(
                repository.count(service.cleanup_scope_fingerprint(request)), 5
            )

            usage = DiskUsage(1000, 900, 100)
            with mock.patch("src.services.cleanup_service.os.scandir") as scandir, mock.patch(
                "src.services.cleanup_service.os.remove"
            ), mock.patch("shutil.disk_usage", return_value=usage):
                result = service.run_auto_cleanup(
                    request, threading.Event(), lambda message: None
                )

            scandir.assert_not_called()
            self.assertEqual(result.scanned_count, 5)
            self.assertEqual(result.deleted_count, 5)
            self.assertEqual(result.status, "索引已耗尽")

    def test_controller_submission_failure_resets_running_flag(self):
        class BrokenExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("executor closed")

        service = mock.Mock()
        service.record_blocked = mock.Mock()
        controller = CleanupController(service, BrokenExecutor())
        request = auto_request("E:\\监测目录")

        self.assertFalse(controller.submit_auto_cleanup(request))
        self.assertFalse(controller.is_auto_running)
        service.record_blocked.assert_called_once()
class MemoryRuntimeLog:
    last_error = ""
    def initialize(self): return True
    def append(self, line): return True


class MemoryStartupRepository:
    def __init__(self, existing=""):
        self.existing = existing
        self.writes = []
        self.last_error = ""

    def read(self): return self.existing
    def write(self, command):
        self.existing = command
        self.writes.append(command)
    def delete(self): self.existing = ""


def startup_service(executable, repository, version="3.4.1", frozen=True, main_script=None):
    return RuntimeService(
        Path.cwd(),
        MemoryRuntimeLog(),
        repository,
        executable=str(executable),
        main_script=Path(main_script or __file__),
        frozen=frozen,
        app_version=version,
    )


class TestStartupRepair(unittest.TestCase):
    def test_chinese_space_path_is_quoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe = Path(temp_dir) / "中文 目录" / "ImageUploadTool_v3.4.1.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            service = startup_service(exe, MemoryStartupRepository())
            command = service.current_startup_command()
            self.assertEqual(command, f'"{exe}"')
            self.assertEqual(extract_startup_target(command), str(exe))

    def test_source_command_requires_existing_main_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_script = Path(temp_dir) / "中文 目录" / "src" / "main.py"
            command = f'"{sys.executable}" "{missing_script}"'
            self.assertFalse(missing_script.exists())
            service = startup_service(
                sys.executable,
                MemoryStartupRepository(),
                frozen=False,
                main_script=missing_script,
            )
            self.assertFalse(service.startup_target_exists(command))

    def test_higher_current_version_replaces_old_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_exe = base / "ImageUploadTool_v3.3.0.exe"
            current_exe = base / "ImageUploadTool_v3.4.1.exe"
            old_exe.write_bytes(b"")
            current_exe.write_bytes(b"")
            repository = MemoryStartupRepository(f'"{old_exe}"')
            result = startup_service(current_exe, repository).reconcile_startup(True)
            self.assertTrue(result.enabled)
            self.assertEqual(repository.writes, [f'"{current_exe}"'])

    def test_same_target_unquoted_registration_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe = Path(temp_dir) / "中文 目录" / "ImageUploadTool_v3.4.1.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            repository = MemoryStartupRepository(str(exe))
            result = startup_service(exe, repository).reconcile_startup(True)
            self.assertTrue(result.enabled)
            self.assertEqual(repository.writes, [f'"{exe}"'])

    def test_lower_current_version_cannot_replace_valid_newer_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            current_exe = base / "ImageUploadTool_v3.4.1.exe"
            newer_exe = base / "ImageUploadTool_v9.0.0.exe"
            current_exe.write_bytes(b"")
            newer_exe.write_bytes(b"")
            repository = MemoryStartupRepository(f'"{newer_exe}"')
            result = startup_service(current_exe, repository).reconcile_startup(
                True, explicit=True
            )
            self.assertTrue(result.enabled)
            self.assertEqual(repository.writes, [])
            self.assertEqual(repository.existing, f'"{newer_exe}"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
