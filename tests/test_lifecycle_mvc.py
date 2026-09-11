# -*- coding: utf-8 -*-
"""Lifecycle Controller tests using Fake View and Fake services."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.controllers import LifecycleController


class FakeParticipant:
    def __init__(
        self,
        name: str,
        calls: List[str],
        error: Optional[Exception] = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.error = error

    def shutdown(self) -> None:
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error


class FakeView:
    def __init__(self, calls: List[str]) -> None:
        self.calls = calls

    def prepare_for_shutdown(self) -> None:
        self.calls.append("view_prepare")

    def release_view_resources(self) -> None:
        self.calls.append("view_release")


class AsyncParticipant(FakeParticipant):
    def __init__(self, name: str, calls: List[str], running: bool = False) -> None:
        super().__init__(name, calls)
        self.running = running

    @property
    def has_running_workers(self) -> bool:
        return self.running

    def request_stop_all(self):
        self.calls.append(f"request_{self.name}")

    def cancel(self) -> None:
        self.calls.append(f"cancel_{self.name}")


class AsyncView(FakeView):
    def set_all_tasks_pending_stop(self) -> None:
        self.calls.append("view_pending")

    def abort_pending_exit(self, errors: tuple[str, ...]) -> None:
        self.calls.append("view_abort")


def _process_until(predicate, timeout: float = 1.0) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate()


class FakeSignal:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def connect(self, handler) -> None:
        self.handlers.append(handler)

    def disconnect(self, handler) -> None:
        if handler in self.handlers:
            self.handlers.remove(handler)

    def emit(self) -> None:
        for handler in tuple(self.handlers):
            handler()


class FakeQtApplication:
    def __init__(self, *, confirm_quit: bool, confirm_exit: bool = True) -> None:
        self.aboutToQuit = FakeSignal()
        self.confirm_quit = confirm_quit
        self.confirm_exit = confirm_exit
        self.quit_calls = 0
        self.exit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1
        if self.confirm_quit:
            self.aboutToQuit.emit()

    def exit(self, code: int) -> None:
        assert code == 0
        self.exit_calls += 1
        if self.confirm_exit:
            self.aboutToQuit.emit()


def test_shutdown_order_and_idempotency_with_fake_view_and_services() -> None:
    calls: List[str] = []
    controller = LifecycleController(
        FakeParticipant("cleanup", calls),
        FakeParticipant("upload", calls),
        FakeParticipant("ftp", calls),
        FakeParticipant("runtime", calls),
    )
    view = FakeView(calls)

    first = controller.shutdown(view)
    second = controller.shutdown(view)

    assert calls == [
        "view_prepare",
        "cleanup",
        "upload",
        "ftp",
        "runtime",
        "view_release",
    ]
    assert first is second
    assert first.success
    assert first.order == tuple(calls)


def test_shutdown_continues_after_one_participant_fails() -> None:
    calls: List[str] = []
    controller = LifecycleController(
        FakeParticipant("cleanup", calls, RuntimeError("cleanup failed")),
        FakeParticipant("upload", calls),
        FakeParticipant("ftp", calls),
        FakeParticipant("runtime", calls),
    )

    result = controller.shutdown(FakeView(calls))

    assert calls[-1] == "view_release"
    assert not result.success
    assert "cleanup failed" in result.errors[0]


def test_request_shutdown_waits_in_qeventloop_then_quits_once() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls: List[str] = []
    upload = AsyncParticipant("upload", calls, running=True)
    cleanup = AsyncParticipant("cleanup", calls)
    ftp = AsyncParticipant("ftp", calls)
    runtime = AsyncParticipant("runtime", calls)
    quits: list[str] = []
    controller = LifecycleController(
        cleanup, upload, ftp, runtime, quit_callback=lambda: quits.append("quit")
    )
    view = AsyncView(calls)
    QtCore.QTimer.singleShot(20, lambda: setattr(upload, "running", False))

    first = controller.request_shutdown(view, timeout_ms=200, poll_interval_ms=5)
    second = controller.request_shutdown(view, timeout_ms=200, poll_interval_ms=5)
    _process_until(lambda: quits == ["quit"])

    assert app is not None
    assert first is second
    assert first.success
    assert quits == ["quit"]
    assert calls.count("request_upload") == 1
    assert calls.count("view_pending") == 1
    assert "view_abort" not in calls


def test_request_shutdown_timeout_aborts_exit_and_keeps_process_alive() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls: List[str] = []
    upload = AsyncParticipant("upload", calls, running=True)
    cleanup = AsyncParticipant("cleanup", calls)
    ftp = AsyncParticipant("ftp", calls)
    runtime = AsyncParticipant("runtime", calls)
    quits: list[str] = []
    controller = LifecycleController(
        cleanup, upload, ftp, runtime, quit_callback=lambda: quits.append("quit")
    )

    result = controller.request_shutdown(
        AsyncView(calls), timeout_ms=20, poll_interval_ms=5
    )

    assert not result.success
    assert app is not None
    assert any("upload" in error and "20ms" in error for error in result.errors)
    assert calls.count("view_abort") == 1
    assert quits == []
    assert upload.running
    assert "view_release" not in calls


def test_request_shutdown_queues_real_app_quit_and_finalizes_on_confirmation() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls: List[str] = []
    fake_app = FakeQtApplication(confirm_quit=True)
    controller = LifecycleController(
        AsyncParticipant("cleanup", calls),
        AsyncParticipant("upload", calls),
        AsyncParticipant("ftp", calls),
        AsyncParticipant("runtime", calls),
    )
    controller._application_provider = lambda: fake_app

    result = controller.request_shutdown(AsyncView(calls))
    assert "view_release" not in calls

    _process_until(lambda: "view_release" in calls)

    assert result.success
    assert fake_app.quit_calls == 1
    assert fake_app.exit_calls == 0
    assert not fake_app.aboutToQuit.handlers


def test_request_shutdown_retries_with_exit_when_quit_is_not_confirmed() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls: List[str] = []
    fake_app = FakeQtApplication(confirm_quit=False, confirm_exit=True)
    controller = LifecycleController(
        AsyncParticipant("cleanup", calls),
        AsyncParticipant("upload", calls),
        AsyncParticipant("ftp", calls),
        AsyncParticipant("runtime", calls),
    )
    controller._application_provider = lambda: fake_app
    controller._quit_watchdog_ms = 10

    controller.request_shutdown(AsyncView(calls))
    _process_until(lambda: "view_release" in calls)

    assert fake_app.quit_calls == 1
    assert fake_app.exit_calls == 1
    assert "view_abort" not in calls


def test_request_shutdown_restores_view_when_qt_never_confirms_exit() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls: List[str] = []
    fake_app = FakeQtApplication(confirm_quit=False, confirm_exit=False)
    controller = LifecycleController(
        AsyncParticipant("cleanup", calls),
        AsyncParticipant("upload", calls),
        AsyncParticipant("ftp", calls),
        AsyncParticipant("runtime", calls),
    )
    controller._application_provider = lambda: fake_app
    controller._quit_watchdog_ms = 10

    controller.request_shutdown(AsyncView(calls))
    _process_until(lambda: "view_abort" in calls)

    assert fake_app.quit_calls == 1
    assert fake_app.exit_calls == 1
    assert "view_release" not in calls
    assert not fake_app.aboutToQuit.handlers
    assert controller._result is None
