"""Controller for manual cleanup operations and automatic cleanup lifecycle."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any, Callable, Optional, Protocol, Tuple

from src.models import (
    AutoCleanupRequest,
    AutoCleanupResult,
    CleanupCommandResult,
    CleanupDeleteRequest,
    CleanupIndexResult,
    CleanupScanRequest,
    CleanupValidationResult,
)


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
    def run_auto_cleanup(
        self,
        request: AutoCleanupRequest,
        cancel_event: Any,
        log: Callable[[str], None],
        delete_mode_provider: Optional[Callable[[], bool]] = None,
    ) -> AutoCleanupResult: ...
    def cleanup_scope_fingerprint(self, request: AutoCleanupRequest) -> str: ...
    def is_index_ready(self, request: AutoCleanupRequest) -> bool: ...
    def mark_index_dirty(self, clear_records: bool = False) -> bool: ...
    def build_cleanup_index(
        self, request: AutoCleanupRequest, cancel_event: Any, log: Callable[[str], None]
    ) -> CleanupIndexResult: ...
    def index_generated_file(
        self, path: str, request: AutoCleanupRequest, source: str = "upload"
    ) -> bool: ...
    @property
    def index_last_error(self) -> str: ...


class CleanupController:
    def __init__(
        self,
        service: CleanupBusinessService,
        executor: Any = None,
        index_executor: Any = None,
    ) -> None:
        self._service = service
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="AutoCleanup"
        )
        self._index_executor = index_executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="CleanupIndex"
        )
        self._manual_listener: Optional[Callable[[dict], None]] = None
        self._auto_listener: Optional[Callable[[dict], None]] = None
        self._auto_lock = threading.Lock()
        self._auto_cancel = threading.Event()
        self._auto_running = False
        self._auto_config_lock = threading.Lock()
        self._live_auto_use_trash: Optional[bool] = None
        self._index_lock = threading.Lock()
        self._index_cancel = threading.Event()
        self._index_running = False
        self._configured_request: Optional[AutoCleanupRequest] = None
        self._pending_index_request: Optional[AutoCleanupRequest] = None
        self._pending_cleanup_request: Optional[AutoCleanupRequest] = None
        self._pending_cleanup_reason = ""
        self._closing = False

    @property
    def trash_available(self) -> bool:
        return self._service.trash_available

    @property
    def is_scanning(self) -> bool:
        return self._service.is_scanning

    @property
    def is_deleting(self) -> bool:
        return self._service.is_deleting

    @property
    def is_auto_running(self) -> bool:
        with self._auto_lock:
            return self._auto_running

    @property
    def is_index_running(self) -> bool:
        with self._index_lock:
            return self._index_running

    def set_manual_listener(self, listener: Optional[Callable[[dict], None]]) -> None:
        self._manual_listener = listener

    def set_auto_listener(self, listener: Callable[[dict], None]) -> None:
        self._auto_listener = listener

    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult:
        return self._service.validate_scan_request(request)

    def start_scan(self, request: CleanupScanRequest) -> CleanupCommandResult:
        return self._service.start_scan(request, self._handle_manual_event)

    def cancel_scan(self) -> None:
        self._service.cancel_scan()

    def start_delete(self, request: CleanupDeleteRequest) -> CleanupCommandResult:
        return self._service.start_delete(request, self._handle_manual_event)

    def close_manual(self) -> None:
        self._service.shutdown_manual()
        self._manual_listener = None

    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult:
        return self._service.validate_auto_request(request)

    def validate_folder_group(self, folders: Any) -> Tuple[bool, str, Any]:
        return self._service.validate_cleanup_folder_group(folders)

    def maybe_trigger_auto_cleanup(
        self, request: AutoCleanupRequest, reason: str = ""
    ) -> bool:
        self._update_live_delete_mode(request)
        if not request.enabled or not request.folders or self._closing:
            return False
        should_run, error = self._service.should_trigger(request)
        if error:
            status = "跨盘配置无效" if "同一磁盘" in error else "路径不可用"
            self._notify_auto("log", message=f"⚠️ {error}")
            self._service.record_blocked(request, status, error)
            return False
        if not should_run:
            return False
        if not self._service.is_index_ready(request):
            self._pending_cleanup_request = request
            self._pending_cleanup_reason = reason or "磁盘空间不足"
            self._notify_auto(
                "log", message="ℹ️ 清理索引尚未就绪，将先完成首次遍历再自动清理"
            )
            return self.ensure_index(request)
        if not self.submit_auto_cleanup(request):
            return False
        self._notify_auto(
            "log", message=f"⚠️ {reason or '磁盘空间不足'}，触发自动清理"
        )
        return True

    def configure_index(self, request: AutoCleanupRequest) -> bool:
        self._update_live_delete_mode(request)
        previous = self._configured_request
        self._configured_request = request
        if self._closing or not request.enabled or not request.folders:
            self._index_cancel.set()
            return False
        changed = bool(
            previous
            and self._service.cleanup_scope_fingerprint(previous)
            != self._service.cleanup_scope_fingerprint(request)
        )
        return self.ensure_index(request, force=changed)

    def ensure_index(self, request: AutoCleanupRequest, force: bool = False) -> bool:
        if self._closing or not request.enabled or not request.folders:
            return False
        if not force and self._service.is_index_ready(request):
            return True
        with self._index_lock:
            if self._index_running:
                self._pending_index_request = request
                if force:
                    self._index_cancel.set()
                return True
            self._index_running = True
            self._index_cancel.clear()
        try:
            self._index_executor.submit(self._run_index, request)
            self._notify_auto("log", message="ℹ️ 已启动磁盘清理数据库索引线程")
            return True
        except Exception as exc:
            with self._index_lock:
                self._index_running = False
            self._notify_auto(
                "log", message=f"❌ 清理索引任务提交失败: {type(exc).__name__}: {exc}"
            )
            return False

    def record_generated_file(self, path: str, source: str = "upload") -> bool:
        request = self._configured_request
        if self._closing or request is None or not request.enabled or not path:
            return False
        try:
            self._index_executor.submit(self._record_generated_file, path, request, source)
            return True
        except Exception as exc:
            self._notify_auto(
                "log", message=f"⚠️ 新文件索引任务提交失败: {type(exc).__name__}: {exc}"
            )
            return False

    def submit_auto_cleanup(
        self, request: AutoCleanupRequest, after_rebuild: bool = False
    ) -> bool:
        if self._closing:
            return False
        with self._auto_lock:
            if self._auto_running:
                return False
            self._auto_running = True
            self._auto_cancel.clear()
        try:
            self._executor.submit(self._run_auto_cleanup, request, after_rebuild)
            return True
        except Exception as exc:
            with self._auto_lock:
                self._auto_running = False
            error = f"自动清理任务提交失败: {type(exc).__name__}: {exc}"
            self._notify_auto("log", message=f"❌ {error}")
            self._service.record_blocked(request, "任务异常", error)
            return False

    def cancel_auto_cleanup(self) -> None:
        self._auto_cancel.set()

    def cancel(self) -> None:
        """非阻塞取消所有清理任务，供退出编排调用。"""
        self._auto_cancel.set()
        self._index_cancel.set()
        self._service.cancel()

    @property
    def has_running_workers(self) -> bool:
        return (
            self._service.has_running_workers
            or self.is_auto_running
            or self.is_index_running
        )

    def shutdown(self) -> None:
        self._closing = True
        self.cancel()
        self._service.shutdown_manual()
        try:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=False)
            try:
                self._index_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._index_executor.shutdown(wait=False)
        finally:
            self._pending_index_request = None
            self._pending_cleanup_request = None

    def _run_auto_cleanup(
        self, request: AutoCleanupRequest, after_rebuild: bool = False
    ) -> None:
        rebuild = False
        try:
            result = self._service.run_auto_cleanup(
                request,
                self._auto_cancel,
                lambda message: self._notify_auto("log", message=message),
                lambda: self._current_delete_mode(request.use_trash),
            )
            self._notify_auto("auto_finished", result=result)
            rebuild = (
                not after_rebuild
                and result.status in {"索引已耗尽", "索引异常"}
            )
            if rebuild:
                self._pending_cleanup_request = request
                self._pending_cleanup_reason = "索引重建后继续清理"
        finally:
            with self._auto_lock:
                self._auto_running = False
        if rebuild and not self._closing:
            self.ensure_index(request, force=True)

    def _update_live_delete_mode(self, request: AutoCleanupRequest) -> None:
        """Publish the latest saved automatic-cleanup delete mode."""
        with self._auto_config_lock:
            self._live_auto_use_trash = bool(request.use_trash)

    def _current_delete_mode(self, fallback: bool) -> bool:
        """Return the latest delete mode, falling back for direct submissions."""
        with self._auto_config_lock:
            if self._live_auto_use_trash is None:
                return bool(fallback)
            return self._live_auto_use_trash

    def _run_index(self, request: AutoCleanupRequest) -> None:
        try:
            result = self._service.build_cleanup_index(
                request,
                self._index_cancel,
                lambda message: self._notify_auto("log", message=message),
            )
        except Exception as exc:
            result = CleanupIndexResult(
                "建立失败", f"{type(exc).__name__}: {exc}"
            )
            self._notify_auto(
                "log", message=f"❌ 清理索引任务异常: {result.error}"
            )
        self._notify_auto("index_finished", result=result)
        with self._index_lock:
            self._index_running = False
            pending_index = self._pending_index_request
            self._pending_index_request = None
        if self._closing:
            return
        if pending_index is not None and (
            not result.success
            or self._service.cleanup_scope_fingerprint(pending_index)
            != result.scope_fingerprint
        ):
            self.ensure_index(pending_index, force=True)
            return
        if result.success and self._pending_cleanup_request is not None:
            pending_cleanup = self._pending_cleanup_request
            reason = self._pending_cleanup_reason
            self._pending_cleanup_request = None
            self._pending_cleanup_reason = ""
            after_rebuild = reason == "索引重建后继续清理"
            if self.submit_auto_cleanup(pending_cleanup, after_rebuild=after_rebuild):
                self._notify_auto("log", message=f"ℹ️ {reason}，开始使用数据库索引清理")

    def _record_generated_file(
        self, path: str, request: AutoCleanupRequest, source: str
    ) -> None:
        try:
            written = self._service.index_generated_file(path, request, source)
            detail = self._service.index_last_error
        except Exception as exc:
            written = False
            detail = f"{type(exc).__name__}: {exc}"
        if not written:
            self._notify_auto(
                "log", message=f"⚠️ 新文件未能写入清理索引: {path}{(': ' + detail) if detail else ''}"
            )

    def _handle_manual_event(self, kind: str, payload: dict) -> None:
        if self._manual_listener is not None:
            self._manual_listener({"type": kind, **payload})

    def _notify_auto(self, kind: str, **payload: Any) -> None:
        if self._auto_listener is not None:
            self._auto_listener({"type": kind, **payload})
