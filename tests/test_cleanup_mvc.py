# -*- coding: utf-8 -*-
"""MVC boundary and worker-lifecycle tests for manual disk cleanup."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets

from src.controllers import CleanupController
from src.models import CleanupDeleteRequest, CleanupFileItem, CleanupScanRequest
from src.services.cleanup_service import CleanupService, _DeleteWorker


def _qt_app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for cleanup worker lifecycle")


def test_manual_scan_and_permanent_delete_run_through_controller() -> None:
    _qt_app()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        old_jpg = root / "old.jpg"
        new_jpg = root / "new.jpg"
        old_txt = root / "old.txt"
        old_jpg.write_bytes(b"old-image")
        new_jpg.write_bytes(b"new-image")
        old_txt.write_bytes(b"old-text")
        old_time = time.time() - (3 * 86400)
        os.utime(old_jpg, (old_time, old_time))
        os.utime(old_txt, (old_time, old_time))

        controller = CleanupController(CleanupService())
        events: list[dict] = []
        controller.set_manual_listener(events.append)
        try:
            scan = controller.start_scan(
                CleanupScanRequest((str(root),), (".jpg",), keep_days=1)
            )
            assert scan.success
            _wait_until(
                lambda: any(event.get("type") == "scan_finished" for event in events)
                and not controller.is_scanning
            )

            finished = next(
                event for event in events if event.get("type") == "scan_finished"
            )
            files = finished["files"]
            assert [item.path for item in files] == [str(old_jpg)]

            events.clear()
            delete = controller.start_delete(
                CleanupDeleteRequest(tuple(files), use_trash=False)
            )
            assert delete.success
            _wait_until(
                lambda: any(event.get("type") == "delete_finished" for event in events)
                and not controller.is_deleting
            )

            result = next(
                event for event in events if event.get("type") == "delete_finished"
            )
            assert result["deleted_count"] == 1
            assert result["failed_count"] == 0
            assert result["remaining_files"] == ()
            assert not old_jpg.exists()
            assert new_jpg.exists()
            assert old_txt.exists()
        finally:
            controller.shutdown()


def test_manual_shutdown_waits_for_scan_and_delete_threads() -> None:
    calls: list[str] = []
    wait_timeouts: list[int] = []

    class Worker:
        def cancel(self) -> None: calls.append("cancel_scan")

    class Thread:
        def __init__(self, name: str) -> None: self.name = name
        def quit(self) -> None: calls.append(f"quit_{self.name}")
        def wait(self, timeout_ms: int) -> bool:
            calls.append(f"wait_{self.name}")
            wait_timeouts.append(timeout_ms)
            return True

    service = CleanupService()
    service._scan_worker = Worker()
    service._scan_thread = Thread("scan")
    service._delete_worker = object()
    service._delete_thread = Thread("delete")

    service.shutdown_manual()

    assert calls == [
        "cancel_scan",
        "quit_scan",
        "wait_scan",
        "quit_delete",
        "wait_delete",
    ]
    assert len(wait_timeouts) == 2
    assert all(0 <= timeout <= 10000 for timeout in wait_timeouts)
    assert not service.is_scanning
    assert not service.is_deleting


def test_delete_worker_honors_cancel_before_deleting_next_file() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "keep.jpg"
        path.write_bytes(b"keep")
        worker = _DeleteWorker(
            CleanupDeleteRequest(
                (CleanupFileItem(str(path), path.stat().st_size, path.stat().st_mtime),),
                use_trash=False,
            )
        )
        results: list[dict] = []
        worker.finished.connect(results.append)
        worker.cancel()

        worker.run()

        assert path.exists()
        assert results[0]["deleted_count"] == 0
        assert results[0]["remaining_files"][0].path == str(path)


def test_delete_worker_skips_file_changed_after_scan() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "changed.jpg"
        path.write_bytes(b"old")
        scanned = path.stat()
        item = CleanupFileItem(
            path=str(path),
            size=scanned.st_size,
            mtime=scanned.st_mtime,
            mtime_ns=scanned.st_mtime_ns,
            file_id=CleanupService.file_identity(scanned),
        )
        path.write_bytes(b"replacement-content")
        worker = _DeleteWorker(
            CleanupDeleteRequest((item,), use_trash=False)
        )
        results: list[dict] = []
        messages: list[str] = []
        worker.event.connect(
            lambda kind, payload: messages.append(payload["message"])
            if kind == "log"
            else None
        )
        worker.finished.connect(results.append)

        worker.run()

        assert path.exists()
        assert path.read_bytes() == b"replacement-content"
        assert results[0]["deleted_count"] == 0
        assert results[0]["skipped_changed_count"] == 1
        assert results[0]["remaining_files"][0].path == str(path)
        assert any("已跳过扫描后发生变化" in message for message in messages)


def test_manual_shutdown_timeout_marks_failed_hung_and_retains_references() -> None:
    class Worker:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class Thread:
        def __init__(self) -> None:
            self.quit_calls = 0
            self.wait_calls: list[int] = []

        def quit(self) -> None:
            self.quit_calls += 1

        def wait(self, timeout_ms: int) -> bool:
            self.wait_calls.append(timeout_ms)
            return False

        def isRunning(self) -> bool:
            return True

    worker = Worker()
    thread = Thread()
    service = CleanupService()
    service._delete_worker = worker
    service._delete_thread = thread
    service._delete_bridge = object()

    errors = service.shutdown_manual(timeout_ms=25)

    assert worker.cancelled
    assert errors and "失败挂起" in errors[0]
    assert service.manual_shutdown_status == "failed_hung"
    assert service._delete_worker is worker
    assert service._delete_thread is thread
    assert service._delete_bridge is not None
    assert service.has_running_workers


def test_timed_out_delete_thread_releases_itself_after_natural_finish() -> None:
    app = _qt_app()
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "slow-delete.jpg"
        path.write_bytes(b"slow")
        item = CleanupFileItem(str(path), path.stat().st_size, path.stat().st_mtime)
        entered_remove = threading.Event()
        original_remove = os.remove

        def slow_remove(target: str) -> None:
            entered_remove.set()
            time.sleep(0.15)
            original_remove(target)

        service = CleanupService()
        with mock.patch("src.services.cleanup_service.os.remove", side_effect=slow_remove):
            assert service.start_delete(
                CleanupDeleteRequest((item,), use_trash=False), lambda *_: None
            ).success
            deadline = time.monotonic() + 2
            while not entered_remove.is_set() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            assert entered_remove.is_set()

            errors = service.shutdown_manual(timeout_ms=5)
            assert errors
            assert service._delete_thread is not None

            deadline = time.monotonic() + 2
            while service.is_deleting and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)

        assert not service.is_deleting
        assert service._delete_thread is None
