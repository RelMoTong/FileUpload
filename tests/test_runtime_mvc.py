# -*- coding: utf-8 -*-
"""Runtime infrastructure tests for the phase 7 MVC boundary."""

from __future__ import annotations

import tempfile
import os
import subprocess
import threading
import sys
import time
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.controllers import RuntimeController
from src.repositories import DailyLogRepository
from src.services.runtime_service import RuntimeService


DiskUsage = namedtuple("DiskUsage", "total used free")


class MemoryStartup:
    def __init__(self, command: str = "") -> None:
        self.command = command
        self.last_error = ""

    def read(self) -> str: return self.command
    def write(self, command: str) -> None: self.command = command
    def delete(self) -> None: self.command = ""


def _service(root: Path) -> RuntimeService:
    executable = root / "ImageUploadTool_v3.4.1.exe"
    executable.write_bytes(b"")
    return RuntimeService(
        root,
        DailyLogRepository(root),
        MemoryStartup(),
        executable=str(executable),
        main_script=root / "src" / "main.py",
        frozen=True,
        app_version="3.4.1",
    )


def test_daily_log_repository_initializes_header_and_appends() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        repository = DailyLogRepository(root)
        assert repository.initialize()
        assert repository.append("runtime event")
        logs = list((root / "logs").glob("upload_*.txt"))
        assert len(logs) == 1
        content = logs[0].read_text(encoding="utf-8")
        assert "图片异步上传工具" in content
        assert "runtime event" in content


def test_daily_log_repository_prunes_files_older_than_retention(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old_log = log_dir / "upload_2020-01-01.txt"
    old_log.write_text("old", encoding="utf-8")
    old_timestamp = time.time() - (31 * 86400)
    os.utime(old_log, (old_timestamp, old_timestamp))

    repository = DailyLogRepository(tmp_path)

    assert repository.initialize()
    assert not old_log.exists()


def test_runtime_service_calculates_disk_free_percent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service = _service(root)
        with mock.patch(
            "src.services.runtime_service.shutil.disk_usage",
            return_value=DiskUsage(1000, 750, 250),
        ):
            assert service.disk_free_percent(str(root)) == 25.0


def test_runtime_controller_reports_both_disk_snapshots() -> None:
    class FakeService:
        app_dir = Path.cwd()
        def initialize(self): return None
        def append_log(self, line): return True
        def disk_free_percent(self, path, network_available=True):
            return 25.0 if path == "target" else 50.0
        def reconcile_startup(self, auto_enabled, explicit=False): return None
        def disable_startup(self): return None

    controller = RuntimeController(FakeService())
    received: list[tuple[str, float]] = []
    complete = threading.Event()

    def callback(kind: str, percent: float) -> None:
        received.append((kind, percent))
        if len(received) == 2:
            complete.set()

    try:
        controller.request_disk_space(
            "target", "backup", True, True, callback
        )
        assert complete.wait(2.0)
        assert received == [("target", 25.0), ("backup", 50.0)]
    finally:
        controller.shutdown()


def test_runtime_shutdown_is_bounded_while_log_write_is_blocked() -> None:
    entered = threading.Event()
    released = threading.Event()

    class BlockingService:
        app_dir = Path.cwd()
        def initialize(self): return None
        def append_log(self, _line):
            entered.set()
            released.wait(5)
            return True
        def disk_free_percent(self, _path, network_available=True): return -1.0
        def reconcile_startup(self, auto_enabled, explicit=False): return None
        def disable_startup(self): return None

    controller = RuntimeController(BlockingService())
    controller.append_log("blocked")
    assert entered.wait(1)
    assert controller.has_running_workers
    runtime_threads = [
        thread for thread in threading.enumerate() if thread.name == "RuntimeTask"
    ]
    assert runtime_threads and all(thread.daemon for thread in runtime_threads)

    started = time.monotonic()
    controller.shutdown()
    elapsed = time.monotonic() - started
    released.set()
    deadline = time.monotonic() + 1
    while controller.has_running_workers and time.monotonic() < deadline:
        time.sleep(0.01)

    assert elapsed < 0.2
    assert not controller.has_running_workers


def test_network_disk_query_has_hard_timeout_fallback(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with mock.patch.object(service, "is_network_path", return_value=True), mock.patch(
        "src.services.runtime_service.subprocess.run",
        side_effect=subprocess.TimeoutExpired("powershell.exe", 2),
    ) as run:
        started = time.monotonic()
        result = service.disk_free_percent(r"\\server\share")

    assert result == -1.0
    assert time.monotonic() - started < 0.2
    assert run.call_args.kwargs["timeout"] == 2.0
