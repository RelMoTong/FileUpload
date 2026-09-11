"""Pure tests for upload request validation and controller state transitions."""

from __future__ import annotations

import os
import itertools
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.controllers import UploadController
from src.models import (
    NetworkStatus,
    UploadCommandResult,
    UploadRuntimeState,
    UploadStatus,
    UploadTaskRequest,
    UploadValidationResult,
)
from src.services import UploadService
from src.workers.upload_worker import UploadWorker
from PySide6 import QtCore, QtWidgets


class FakeQtWorker(QtCore.QObject):
    log = QtCore.Signal(str)
    stats = QtCore.Signal(int, int, int, str)
    progress = QtCore.Signal(int, int, str)
    file_progress = QtCore.Signal(str, int)
    network_status = QtCore.Signal(str)
    finished = QtCore.Signal()
    status = QtCore.Signal(str)
    ask_user_duplicate = QtCore.Signal(object)
    upload_error = QtCore.Signal(str, str)
    disk_warning = QtCore.Signal(float, float, int)
    disk_cleanup_needed = QtCore.Signal()
    local_file_generated = QtCore.Signal(str, str)
    instances: list["FakeQtWorker"] = []

    def __init__(self, *args: Any):
        super().__init__()
        self.args = args
        self.ftp_client = None
        self.archive_queue: queue.Queue = queue.Queue()
        self.instances.append(self)

    @QtCore.Slot()
    def start(self) -> None:
        self.status.emit("running")
        self.log.emit("started")

    def pause(self) -> None:
        self.status.emit("paused")

    def resume(self) -> None:
        self.status.emit("running")

    def stop(self) -> None:
        self.status.emit("stopped")
        self.finished.emit()


class FakeUploadService:
    def __init__(self) -> None:
        self.callback: Callable[[str, Dict[str, Any]], None] | None = None
        self.validation = UploadValidationResult()
        self.calls: list[str] = []
        self.duplicate: tuple[Any, str, bool] | None = None
        self.shutdown_called = False

    def validate_request(self, request: UploadTaskRequest) -> UploadValidationResult:
        self.calls.append("validate")
        return self.validation

    def start(self, request: UploadTaskRequest, event_callback) -> UploadCommandResult:
        self.calls.append("start")
        self.callback = event_callback
        return UploadCommandResult(True, "started")

    def pause(self) -> UploadCommandResult:
        self.calls.append("pause")
        return UploadCommandResult(True, "paused")

    def resume(self) -> UploadCommandResult:
        self.calls.append("resume")
        return UploadCommandResult(True, "resumed")

    def stop(self) -> UploadCommandResult:
        self.calls.append("stop")
        return UploadCommandResult(True, "stopped")

    def request_stop_all(self) -> UploadCommandResult:
        self.calls.append("request_stop_all")
        return UploadCommandResult(True, "stopped")

    @property
    def has_running_workers(self) -> bool:
        return False

    def resolve_duplicate(self, payload: Any, choice: str, apply_all: bool = False) -> None:
        self.duplicate = (payload, choice, apply_all)

    def ftp_client_status(self) -> dict:
        return {"connected": True, "host": "ftp.example.com"}

    def archive_queue_size(self) -> int:
        return 4

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.shutdown_called = True


def _request(tmp_path: Path, **overrides: Any) -> UploadTaskRequest:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    source.mkdir(exist_ok=True)
    target.mkdir(exist_ok=True)
    backup.mkdir(exist_ok=True)
    values = {
        "source": str(source),
        "target": str(target),
        "backup": str(backup),
        "interval": 30,
        "mode": "periodic",
        "disk_threshold_percent": 10,
        "retry_count": 3,
        "filters": (".jpg", ".png"),
        "app_dir": tmp_path,
        "file_upload_delay_seconds": 0.0,
    }
    values.update(overrides)
    return UploadTaskRequest(**values)


def test_upload_service_validates_paths_without_a_main_window(tmp_path: Path) -> None:
    assert UploadService.validate_request(_request(tmp_path)).is_valid

    invalid = UploadService.validate_request(
        _request(tmp_path, target=str(tmp_path / "missing"), backup="")
    )

    assert not invalid.is_valid
    assert any("目标文件夹不存在" in error for error in invalid.errors)
    assert "备份文件夹路径为空" in invalid.errors


def test_worker_waits_configured_delay_only_when_files_were_found(tmp_path: Path) -> None:
    worker = UploadWorker(
        str(tmp_path),
        str(tmp_path),
        str(tmp_path),
        30,
        "periodic",
        10,
        3,
        [".jpg"],
        tmp_path,
        file_upload_delay_seconds=1.5,
    )

    with mock.patch("src.workers.upload_worker.time.sleep") as sleep:
        worker._wait_before_upload([])
        sleep.assert_not_called()

        worker._wait_before_upload([str(tmp_path / "image.jpg")])
        sleep.assert_called_once_with(1.5)


def test_source_scan_yields_paths_incrementally_without_materializing_all(
    tmp_path: Path,
) -> None:
    worker = UploadWorker(
        str(tmp_path), str(tmp_path), str(tmp_path), 30, "periodic", 10, 3,
        [".jpg"], tmp_path,
    )
    worker._running = True
    generated = 0

    def names():
        nonlocal generated
        for index in range(100_000):
            generated += 1
            yield f"{index:06d}.jpg"

    with mock.patch(
        "src.workers.upload_worker.os.walk",
        return_value=iter(((str(tmp_path), (), names()),)),
    ):
        stream = worker._get_image_files()
        first_batch = list(itertools.islice(stream, 500))

    assert len(first_batch) == 500
    assert generated == 500
    assert not isinstance(stream, list)


def test_unc_network_check_requires_shared_path_write_probe(tmp_path: Path) -> None:
    worker = UploadWorker(
        str(tmp_path),
        r"\\server\share",
        str(tmp_path),
        30,
        "periodic",
        10,
        3,
        [".jpg"],
        tmp_path,
    )
    completed = mock.Mock(returncode=1)

    with mock.patch(
        "src.workers.upload_worker.subprocess.run", return_value=completed
    ) as run:
        available = worker._safe_net_check(
            r"\\server\share", timeout=0.5, default=False, require_write=True
        )

    assert not available
    command = run.call_args.args[0]
    assert command[0] == "powershell.exe"
    assert "IMAGE_UPLOAD_HEALTH_PATH" in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"]["IMAGE_UPLOAD_HEALTH_PATH"] == r"\\server\share"


def test_network_jitter_pauses_once_and_resumes_after_recovery(tmp_path: Path) -> None:
    worker = UploadWorker(
        str(tmp_path), r"\\server\share", str(tmp_path), 30, "periodic", 10, 3,
        [".jpg"], tmp_path, network_check_interval=1,
    )
    worker._net_running = True
    worker._paused = False

    def mark_paused() -> None:
        worker._paused = True

    def mark_resumed() -> None:
        worker._paused = False

    with mock.patch.object(
        worker,
        "_evaluate_smb_network_status",
        side_effect=["disconnected", "unstable", "good"],
    ), mock.patch.object(
        worker._net_stop_event, "wait", side_effect=[False, False, True]
    ), mock.patch.object(
        worker, "pause", side_effect=mark_paused
    ) as pause, mock.patch.object(
        worker, "resume", side_effect=mark_resumed
    ) as resume:
        worker._network_monitor_loop()

    pause.assert_called_once_with()
    resume.assert_called_once_with()
    assert worker.current_network_status == "good"
    assert not worker.network_pause_by_auto
    assert not worker._paused


def test_repeated_remote_fileop_timeouts_are_bounded_and_open_circuit(
    tmp_path: Path,
) -> None:
    worker = UploadWorker(
        str(tmp_path), r"\\server\share", str(tmp_path), 30, "periodic", 10, 3,
        [".jpg"], tmp_path,
    )

    class TimedOutProcess:
        def __init__(self):
            self.returncode = None
            self.killed = False

        def communicate(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("powershell.exe", timeout)
            return b"", None

        def kill(self):
            self.killed = True
            self.returncode = -9

    with mock.patch(
        "src.workers.upload_worker.subprocess.Popen",
        side_effect=lambda *_args, **_kwargs: TimedOutProcess(),
    ) as popen:
        assert not worker._safe_path_exists(r"\\server\share\a", timeout=0.01)
        assert not worker._safe_path_exists(r"\\server\share\b", timeout=0.01)
        assert not worker._safe_path_exists(r"\\server\share\c", timeout=0.01)
        # 加速模拟每秒一次、持续30分钟的网络探测。
        for index in range(1800):
            assert not worker._safe_path_exists(
                rf"\\server\share\blocked-{index}", timeout=0.01
            )

    health = worker.get_health_status()
    assert popen.call_count == 3
    assert health["fileop_active_count"] == 0
    assert health["fileop_legacy_task_count"] == 0
    assert health["fileop_timeout_count"] == 3
    assert health["fileop_circuit_open"]
    assert not any(thread.name.startswith("FileOp") for thread in threading.enumerate())


def test_stop_kills_and_releases_active_remote_fileop_process(tmp_path: Path) -> None:
    worker = UploadWorker(
        str(tmp_path), r"\\server\share", str(tmp_path), 30, "periodic", 10, 3,
        [".jpg"], tmp_path,
    )
    entered = threading.Event()
    released = threading.Event()

    class BlockingProcess:
        def __init__(self):
            self.returncode = None

        def communicate(self, timeout=None):
            entered.set()
            released.wait(5)
            return b"", None

        def kill(self):
            self.returncode = -9
            released.set()

    with mock.patch(
        "src.workers.upload_worker.subprocess.Popen", return_value=BlockingProcess()
    ):
        operation = threading.Thread(
            target=lambda: worker._safe_path_exists(
                r"\\server\share\blocked", timeout=5
            )
        )
        operation.start()
        assert entered.wait(1)
        assert worker.get_health_status()["fileop_active_count"] == 1
        worker.stop(wait=False)
        operation.join(1)

    assert not operation.is_alive()
    assert worker.get_health_status()["fileop_active_count"] == 0
    assert not worker.has_running_tasks()


def test_fileop_subprocess_handles_chinese_paths_and_returns_scan_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文目录"
    root.mkdir()
    image = root / "测试图片.jpg"
    image.write_bytes(b"image")
    worker = UploadWorker(
        str(root), str(root), str(root), 30, "periodic", 10, 3,
        [".jpg"], tmp_path,
    )

    assert worker._run_remote_fileop("exists", str(root), 2, False)
    scanned = worker._run_remote_fileop(
        "scan", str(root), 2, [], filters=[".jpg"]
    )
    usage = worker._run_remote_fileop("disk_usage", str(root), 2, None)

    assert str(image) in scanned
    assert usage is not None
    assert usage[0] > 0
    assert usage[1] >= 0


def test_network_monitor_marks_unavailable_backup_as_unstable(tmp_path: Path) -> None:
    worker = UploadWorker(
        str(tmp_path),
        str(tmp_path / "target"),
        str(tmp_path / "backup"),
        30,
        "periodic",
        10,
        3,
        [".jpg"],
        tmp_path,
        enable_backup=True,
    )
    statuses: list[str] = []
    worker.network_status.connect(statuses.append)
    worker._net_running = True

    with mock.patch.object(
        worker, "_safe_net_check", side_effect=[True, False]
    ) as check, mock.patch.object(worker._net_stop_event, "wait", return_value=True):
        worker._network_monitor_loop()

    assert statuses == ["unstable"]
    assert [call.args[0] for call in check.call_args_list] == [
        str(tmp_path / "target"),
        str(tmp_path / "backup"),
    ]
    assert all(call.kwargs["require_write"] for call in check.call_args_list)
    assert not worker._is_backup_path_ready()
    assert check.call_count == 2


def test_controller_owns_start_pause_resume_stop_state(tmp_path: Path) -> None:
    service = FakeUploadService()
    controller = UploadController(service, UploadRuntimeState())

    assert controller.start(_request(tmp_path)).success
    assert controller.state.status is UploadStatus.RUNNING
    assert controller.pause().success
    assert controller.state.status is UploadStatus.PAUSED
    assert controller.resume().success
    assert controller.state.status is UploadStatus.RUNNING
    assert controller.stop().success
    assert controller.state.status is UploadStatus.STOPPED
    assert service.calls == ["validate", "start", "pause", "resume", "stop"]


def test_controller_resets_cards_on_start_and_network_on_stop(tmp_path: Path) -> None:
    service = FakeUploadService()
    state = UploadRuntimeState(
        network_status=NetworkStatus.GOOD,
        uploaded=7,
        failed=2,
        skipped=1,
        rate="8 MB/s",
    )
    controller = UploadController(service, state)
    events: list[dict] = []
    controller.set_event_listener(events.append)

    assert controller.start(_request(tmp_path)).success

    snapshot = controller.state
    assert (snapshot.uploaded, snapshot.failed, snapshot.skipped, snapshot.rate) == (
        0,
        0,
        0,
        "0 MB/s",
    )
    assert snapshot.network_status is NetworkStatus.UNKNOWN
    assert {event["type"] for event in events[:3]} == {
        "stats",
        "network_status",
        "status",
    }
    assert next(event for event in events if event["type"] == "stats") == {
        "type": "stats",
        "uploaded": 0,
        "failed": 0,
        "skipped": 0,
        "rate": "0 MB/s",
    }

    assert service.callback is not None
    service.callback("network_status", {"status": "good"})
    events.clear()
    assert controller.stop().success

    assert controller.state.network_status is NetworkStatus.UNKNOWN
    assert events[0] == {"type": "network_status", "status": "unknown"}
    assert events[1] == {"type": "status", "status": "stopped"}


def test_controller_rejects_invalid_transitions_and_requests(tmp_path: Path) -> None:
    service = FakeUploadService()
    controller = UploadController(service, UploadRuntimeState())

    assert not controller.pause().success
    service.validation = UploadValidationResult(("源文件夹不存在",))
    result = controller.start(_request(tmp_path))

    assert not result.success
    assert result.errors == ("源文件夹不存在",)
    assert controller.state.status is UploadStatus.STOPPED
    assert "start" not in service.calls


def test_worker_events_update_one_controller_snapshot(tmp_path: Path) -> None:
    service = FakeUploadService()
    controller = UploadController(service, UploadRuntimeState())
    events: list[dict] = []
    controller.set_event_listener(events.append)
    assert controller.start(_request(tmp_path)).success
    assert service.callback is not None

    service.callback("stats", {"uploaded": 3, "failed": 1, "skipped": 2, "rate": "4 MB/s"})
    service.callback("progress", {"current": 3, "total": 8, "filename": "a.jpg"})
    service.callback("file_progress", {"filename": "a.jpg", "progress": 75})
    service.callback("network_status", {"status": "unstable"})
    service.callback("upload_error", {"filename": "a.jpg", "message": "network"})

    state = controller.state
    assert (state.uploaded, state.failed, state.skipped, state.rate) == (3, 1, 2, "4 MB/s")
    assert (state.progress_current, state.progress_total, state.file_progress) == (3, 8, 75)
    assert state.network_status is NetworkStatus.UNSTABLE
    assert state.last_error == "network"
    assert {event["type"] for event in events} >= {
        "stats",
        "progress",
        "file_progress",
        "network_status",
        "upload_error",
    }


def test_controller_delegates_worker_details_and_shutdown(tmp_path: Path) -> None:
    service = FakeUploadService()
    controller = UploadController(service, UploadRuntimeState())
    payload = {"file": "duplicate.jpg"}

    controller.resolve_duplicate(payload, "rename", True)

    assert service.duplicate == (payload, "rename", True)
    assert controller.ftp_client_status()["connected"]
    assert controller.archive_queue_size() == 4
    controller.shutdown()
    assert service.shutdown_called
    assert controller.state.status is UploadStatus.STOPPED


def test_controller_requests_non_blocking_stop_all(tmp_path: Path) -> None:
    service = FakeUploadService()
    controller = UploadController(service, UploadRuntimeState())
    assert controller.start(_request(tmp_path)).success

    result = controller.request_stop_all()

    assert result.success
    assert controller.state.status is UploadStatus.STOPPED
    assert not controller.has_running_workers
    assert "request_stop_all" in service.calls


def test_upload_shutdown_timeout_retains_worker_and_thread_references() -> None:
    class Worker:
        def __init__(self) -> None:
            self.stop_calls: list[tuple[bool, float]] = []

        def stop(self, wait: bool = False, timeout: float = 5.0) -> None:
            self.stop_calls.append((wait, timeout))

        def has_running_tasks(self) -> bool:
            return True

    class Thread:
        def __init__(self) -> None:
            self.quit_calls = 0
            self.wait_calls: list[int] = []

        def quit(self) -> None:
            self.quit_calls += 1

        def isRunning(self) -> bool:
            return True

        def wait(self, timeout_ms: int) -> bool:
            self.wait_calls.append(timeout_ms)
            return False

    worker = Worker()
    thread = Thread()
    service = UploadService()
    service._worker = worker
    service._thread = thread
    service._bridge = object()

    service.shutdown(timeout_ms=25)

    assert worker.stop_calls == [(False, 0.025)]
    assert thread.wait_calls == [25]
    assert thread.quit_calls >= 2
    assert service._worker is worker
    assert service._thread is thread
    assert service._bridge is not None
    assert service.has_running_workers


def test_upload_service_owns_worker_and_qthread_lifecycle(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    FakeQtWorker.instances.clear()
    events: list[tuple[str, dict]] = []
    service = UploadService(worker_factory=FakeQtWorker)

    try:
        result = service.start(_request(tmp_path), lambda kind, payload: events.append((kind, payload)))
        assert result.success

        deadline = time.monotonic() + 2
        while not events and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

        assert FakeQtWorker.instances
        assert FakeQtWorker.instances[0].args[0] == str(tmp_path / "source")
        assert FakeQtWorker.instances[0].args[-1] == 0.0
        assert any(kind == "status" and payload["status"] == "running" for kind, payload in events)

        generated = str(tmp_path / "target" / "renamed.jpg")
        FakeQtWorker.instances[0].local_file_generated.emit(generated, "upload")
        deadline = time.monotonic() + 2
        while not any(kind == "local_file_generated" for kind, _ in events) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert ("local_file_generated", {"path": generated, "source": "upload"}) in events

        assert service.pause().success
        assert service.resume().success
        assert service.stop().success
        deadline = time.monotonic() + 2
        while not any(kind == "finished" for kind, _ in events) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert any(kind == "finished" for kind, _ in events)
    finally:
        service.shutdown()


def test_real_smb_upload_copies_and_archives_file(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    request = _request(
        tmp_path,
        interval=1,
        upload_protocol="smb",
        disk_threshold_percent=10,
    )
    source_file = Path(request.source) / "sample.jpg"
    source_file.write_bytes(b"mvc-smb-regression")
    target_file = Path(request.target) / source_file.name
    backup_file = Path(request.backup) / source_file.name
    controller = UploadController(UploadService(), UploadRuntimeState())

    try:
        # 该用例验证上传/归档，磁盘空间由专门用例覆盖。
        # 显式注入健康值，避免宿主盘低于 5% 时随机失败。
        with mock.patch.object(
            UploadWorker, "_disk_ok", return_value=(75.0, 100.0, 75.0)
        ):
            assert controller.start(request).success
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.processEvents()
                if target_file.exists() and backup_file.exists():
                    break
                time.sleep(0.02)

        assert target_file.read_bytes() == b"mvc-smb-regression"
        assert backup_file.read_bytes() == b"mvc-smb-regression"
        assert not source_file.exists()
    finally:
        controller.shutdown()
