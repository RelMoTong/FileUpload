"""Controller for manual cleanup and bounded automatic-cleanup lifecycle."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any, Callable, Optional, Protocol, Tuple

from src.models import AutoCleanupRequest, AutoCleanupResult, CleanupCommandResult, CleanupDeleteRequest, CleanupScanRequest, CleanupValidationResult


class CleanupBusinessService(Protocol):
    trash_available: bool
    is_scanning: bool
    is_deleting: bool
    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult: ...
    def start_scan(self, request: CleanupScanRequest, callback: Callable) -> CleanupCommandResult: ...
    def cancel_scan(self) -> None: ...
    def cancel(self) -> None: ...
    @property
    def has_running_workers(self) -> bool: ...
    def start_delete(self, request: CleanupDeleteRequest, callback: Callable) -> CleanupCommandResult: ...
    def shutdown_manual(self) -> None: ...
    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult: ...
    def validate_cleanup_folder_group(self, folders: Any) -> Tuple[bool, str, Any]: ...
    def should_trigger(self, request: AutoCleanupRequest) -> Tuple[bool, str]: ...
    def record_blocked(self, request: AutoCleanupRequest, status: str, error: str) -> None: ...
    def run_auto_cleanup(self, request: AutoCleanupRequest, cancel_event: Any, log: Callable[[str], None], delete_mode_provider: Optional[Callable[[], bool]] = None) -> AutoCleanupResult: ...


class CleanupController:
    def __init__(self, service: CleanupBusinessService, executor: Any = None) -> None:
        self._service = service
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="AutoCleanup")
        self._manual_listener: Optional[Callable[[dict], None]] = None
        self._auto_listener: Optional[Callable[[dict], None]] = None
        self._auto_lock = threading.Lock()
        self._auto_cancel = threading.Event()
        self._auto_running = False
        self._auto_config_lock = threading.Lock()
        self._live_auto_use_trash: Optional[bool] = None
        self._closing = False
        self._discard_manual_events_until_finished = False

    @property
    def trash_available(self) -> bool: return self._service.trash_available
    @property
    def is_scanning(self) -> bool: return self._service.is_scanning
    @property
    def is_deleting(self) -> bool: return self._service.is_deleting
    @property
    def is_auto_running(self) -> bool:
        with self._auto_lock: return self._auto_running

    def set_manual_listener(self, listener: Optional[Callable[[dict], None]]) -> None: self._manual_listener = listener
    def set_auto_listener(self, listener: Callable[[dict], None]) -> None: self._auto_listener = listener
    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult: return self._service.validate_scan_request(request)
    def start_scan(self, request: CleanupScanRequest) -> CleanupCommandResult: return self._service.start_scan(request, self._handle_manual_event)
    def cancel_scan(self) -> None: self._service.cancel_scan()
    def start_delete(self, request: CleanupDeleteRequest) -> CleanupCommandResult: return self._service.start_delete(request, self._handle_manual_event)
    def close_manual(self) -> None:
        # 对话框关闭必须立即返回 GUI 事件循环。网络盘 I/O 若尚未返回，Worker 会在
        # 可取消点自行收尾；服务对象继续持有线程引用，避免 QThread 被提前销毁。
        self._discard_manual_events_until_finished = self.is_scanning
        self._service.cancel()
        self._manual_listener = None
    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult: return self._service.validate_auto_request(request)
    def validate_folder_group(self, folders: Any) -> Tuple[bool, str, Any]: return self._service.validate_cleanup_folder_group(folders)

    def configure_auto_cleanup(self, request: AutoCleanupRequest) -> bool:
        """Publish current policy; candidates are scanned only when cleanup runs."""
        self._update_live_delete_mode(request)
        return not self._closing and request.enabled and bool(request.folders)

    def maybe_trigger_auto_cleanup(self, request: AutoCleanupRequest, reason: str = "") -> bool:
        self._update_live_delete_mode(request)
        if not request.enabled or not request.folders or self._closing: return False
        should_run, error = self._service.should_trigger(request)
        if error:
            status = "跨盘配置无效" if "同一磁盘" in error else "路径不可用"
            self._notify_auto("log", message=f"⚠️ {error}")
            self._service.record_blocked(request, status, error)
            return False
        if not should_run or not self.submit_auto_cleanup(request): return False
        self._notify_auto("log", message=f"⚠️ {reason or '磁盘空间不足'}，触发自动清理")
        return True

    def submit_auto_cleanup(self, request: AutoCleanupRequest) -> bool:
        if self._closing: return False
        with self._auto_lock:
            if self._auto_running: return False
            self._auto_running = True
            self._auto_cancel.clear()
        try:
            self._executor.submit(self._run_auto_cleanup, request)
            return True
        except Exception as exc:
            with self._auto_lock: self._auto_running = False
            error = f"自动清理任务提交失败: {type(exc).__name__}: {exc}"
            self._notify_auto("log", message=f"❌ {error}")
            self._service.record_blocked(request, "任务异常", error)
            return False

    def cancel_auto_cleanup(self) -> None: self._auto_cancel.set()
    def cancel(self) -> None:
        self._auto_cancel.set()
        self._service.cancel()
    @property
    def has_running_workers(self) -> bool: return self._service.has_running_workers or self.is_auto_running

    def shutdown(self) -> None:
        self._closing = True
        self.cancel()
        self._service.shutdown_manual()
        try: self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError: self._executor.shutdown(wait=False)

    def _run_auto_cleanup(self, request: AutoCleanupRequest) -> None:
        try:
            result = self._service.run_auto_cleanup(request, self._auto_cancel, lambda message: self._notify_auto("log", message=message), lambda: self._current_delete_mode(request.use_trash))
            self._notify_auto("auto_finished", result=result)
        finally:
            with self._auto_lock: self._auto_running = False

    def _update_live_delete_mode(self, request: AutoCleanupRequest) -> None:
        with self._auto_config_lock: self._live_auto_use_trash = bool(request.use_trash)
    def _current_delete_mode(self, fallback: bool) -> bool:
        with self._auto_config_lock: return bool(fallback if self._live_auto_use_trash is None else self._live_auto_use_trash)
    def _handle_manual_event(self, kind: str, payload: dict) -> None:
        # 关闭旧对话框后，不能把其残余扫描事件错误地投递给随后打开的新对话框。
        if self._discard_manual_events_until_finished:
            if kind == "scan_finished":
                self._discard_manual_events_until_finished = False
            return
        if self._manual_listener is not None: self._manual_listener({"type": kind, **payload})
    def _notify_auto(self, kind: str, **payload: Any) -> None:
        if self._auto_listener is not None: self._auto_listener({"type": kind, **payload})
