"""Controller for runtime logging, disk snapshots, and startup registration."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import threading
from typing import Any, Callable, Protocol

from src.models.runtime import RuntimeCommandResult, RuntimeInitializationResult


class _DaemonTaskExecutor:
    """Small bounded executor whose stuck tasks cannot hold the process open."""

    def __init__(self, max_workers: int = 2) -> None:
        self._slots = threading.BoundedSemaphore(max_workers)
        self._lock = threading.Lock()
        self._closing = False

    def submit(self, function: Callable[..., Any], *args: Any) -> Future[Any]:
        future: Future[Any] = Future()
        with self._lock:
            closing = self._closing
        if closing or not self._slots.acquire(blocking=False):
            future.cancel()
            return future

        def run() -> None:
            try:
                if not future.set_running_or_notify_cancel():
                    return
                future.set_result(function(*args))
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                self._slots.release()

        threading.Thread(
            target=run,
            daemon=True,
            name="RuntimeTask",
        ).start()
        return future

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        del wait, cancel_futures
        with self._lock:
            self._closing = True


class RuntimeBusinessService(Protocol):
    app_dir: Path

    def initialize(self) -> RuntimeInitializationResult: ...
    def append_log(self, line: str) -> bool: ...
    def disk_free_percent(self, path: str, network_available: bool = True) -> float: ...
    def reconcile_startup(self, auto_enabled: bool, explicit: bool = False) -> RuntimeCommandResult: ...
    def disable_startup(self) -> RuntimeCommandResult: ...


class RuntimeController:
    def __init__(self, service: RuntimeBusinessService, executor: Any = None) -> None:
        self._service = service
        self._executor = executor or _DaemonTaskExecutor(max_workers=2)
        self._closing = False
        self._future_lock = threading.Lock()
        self._futures: set[Any] = set()

    @property
    def app_dir(self) -> Path:
        return self._service.app_dir

    def initialize(self) -> RuntimeInitializationResult:
        return self._service.initialize()

    def append_log(self, line: str) -> None:
        if not self._closing:
            try:
                self._track_future(self._executor.submit(self._service.append_log, line))
            except Exception:
                pass

    def request_disk_space(
        self,
        target_path: str,
        backup_path: str,
        backup_enabled: bool,
        network_available: bool,
        callback: Callable[[str, float], None],
    ) -> None:
        if self._closing:
            return

        def run() -> None:
            callback(
                "target",
                self._service.disk_free_percent(target_path, network_available),
            )
            callback(
                "backup",
                self._service.disk_free_percent(backup_path, network_available)
                if backup_enabled
                else -1.0,
            )

        try:
            self._track_future(self._executor.submit(run))
        except Exception:
            pass

    def reconcile_startup(
        self, auto_enabled: bool, explicit: bool = False
    ) -> RuntimeCommandResult:
        return self._service.reconcile_startup(auto_enabled, explicit)

    def disable_startup(self) -> RuntimeCommandResult:
        return self._service.disable_startup()

    @property
    def has_running_workers(self) -> bool:
        with self._future_lock:
            return any(not future.done() for future in self._futures)

    def _track_future(self, future: Any) -> None:
        if not hasattr(future, "done") or not hasattr(future, "add_done_callback"):
            return
        with self._future_lock:
            self._futures.add(future)

        def release(completed: Any) -> None:
            with self._future_lock:
                self._futures.discard(completed)

        future.add_done_callback(release)

    def shutdown(self) -> None:
        self._closing = True
        with self._future_lock:
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)
