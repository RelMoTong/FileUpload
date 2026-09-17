"""MVC boundary and worker-lifecycle tests for manual disk cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6 import QtWidgets

from src.controllers import CleanupController
from src.models import CleanupDeleteRequest, CleanupScanRequest
from src.repositories.cleanup_audit_repository import CleanupAuditRepository
from src.services.cleanup_service import CleanupService


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


def test_manual_scan_and_authorized_permanent_delete_run_through_controller(
    tmp_path: Path,
) -> None:
    _qt_app()
    old_jpg = tmp_path / "old.jpg"
    new_jpg = tmp_path / "new.jpg"
    old_txt = tmp_path / "old.txt"
    old_jpg.write_bytes(b"old-image")
    new_jpg.write_bytes(b"new-image")
    old_txt.write_bytes(b"old-text")
    old_time = time.time() - (3 * 86400)
    os.utime(old_jpg, (old_time, old_time))
    os.utime(old_txt, (old_time, old_time))

    audit = CleanupAuditRepository(tmp_path / "runtime")
    controller = CleanupController(CleanupService(audit_writer=audit))
    events: list[dict] = []
    controller.set_manual_listener(events.append)
    try:
        scan = controller.start_scan(
            CleanupScanRequest((str(tmp_path),), (".jpg",), keep_days=1)
        )
        assert scan.success
        _wait_until(
            lambda: any(event.get("type") == "scan_finished" for event in events)
            and not controller.is_scanning
        )

        finished = next(event for event in events if event.get("type") == "scan_finished")
        files = [
            item
            for event in events
            if event.get("type") == "scan_items"
            for item in event["files"]
        ]
        assert finished["file_count"] == 1
        assert [item.path for item in files] == [str(old_jpg)]

        events.clear()
        delete = controller.start_delete(
            CleanupDeleteRequest(
                tuple(files),
                use_trash=False,
                permanent_authorized=True,
                allowed_roots=(str(tmp_path),),
            )
        )
        assert delete.success
        _wait_until(
            lambda: any(event.get("type") == "delete_finished" for event in events)
            and not controller.is_deleting
        )

        result = next(event for event in events if event.get("type") == "delete_finished")
        assert result["deleted_count"] == 1
        assert result["failed_count"] == 0
        assert result["remaining_files"] == ()
        assert not old_jpg.exists()
        assert new_jpg.exists()
        assert old_txt.exists()
        audit_text = next((tmp_path / "runtime" / "logs").glob("cleanup_*.log")).read_text(
            encoding="utf-8"
        )
        assert "MANUAL_PERMANENT_DELETE_INTENT" in audit_text
        assert "MANUAL_PERMANENT_DELETE_OK" in audit_text
    finally:
        controller.shutdown()


def test_manual_shutdown_timeout_is_reported_and_references_are_retained() -> None:
    class Worker:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class Thread:
        def quit(self) -> None:
            pass

        def wait(self, _timeout_ms: int) -> bool:
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
    assert service.has_running_workers


def test_close_manual_requests_cancel_without_waiting_for_a_hung_scan() -> None:
    class Service:
        is_scanning = True
        is_deleting = False

        def __init__(self) -> None:
            self.cancelled = False
            self.shutdown_called = False

        def cancel(self) -> None:
            self.cancelled = True

        def shutdown_manual(self) -> None:
            self.shutdown_called = True

    service = Service()
    controller = CleanupController(service)
    controller.set_manual_listener(lambda _event: None)

    controller.close_manual()

    assert service.cancelled
    assert not service.shutdown_called
    assert controller._manual_listener is None

    received: list[dict] = []
    controller.set_manual_listener(received.append)
    controller._handle_manual_event("scan_items", {"files": ("old",)})
    controller._handle_manual_event("scan_finished", {})
    controller._handle_manual_event("log", {"message": "new dialog event"})

    assert received == [{"type": "log", "message": "new dialog event"}]
