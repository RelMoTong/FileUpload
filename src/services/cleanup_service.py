"""Filesystem policies and worker lifecycles for manual and automatic cleanup."""

from __future__ import annotations

import datetime
import ctypes
from ctypes import wintypes
from dataclasses import replace
import heapq
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Protocol, Tuple, cast
import uuid

from PySide6 import QtCore

from src.models import (
    AutoCleanupRequest,
    AutoCleanupResult,
    CleanupCandidate,
    CleanupCommandResult,
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupScanRequest,
    CleanupValidationResult,
)
from src.core.safe_deletion import (
    SafeDeletionPolicy,
    SafeDeletionRequest,
    send_to_trash,
    trash_supported,
)


AUTO_CLEANUP_FAILURE_LIMIT = 20
CLEANUP_RECORD_READ_LIMIT = 100
# A scan can encounter tens of thousands of files.  Posting an event for every
# file floods the GUI event queue, which in turn makes a cancellation appear to
# hang even after the worker has stopped.  The payload is an object, so the byte
# total remains a Python integer rather than a Qt 32-bit integer.
SCAN_PROGRESS_INTERVAL_SECONDS = 0.2
CleanupEventCallback = Callable[[str, Dict[str, Any]], None]
logger = logging.getLogger(__name__)


def _stat_mtime_ns(stat_result: Any) -> int:
    """Return a stable, integer modification timestamp for identity checks."""
    value = getattr(stat_result, "st_mtime_ns", None)
    if value is not None:
        return int(value)
    return int(round(float(stat_result.st_mtime) * 1_000_000_000))


def _stat_file_id(stat_result: Any) -> str:
    """Return a best-effort filesystem identity without weakening portability."""
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    if inode <= 0:
        return ""
    device = int(getattr(stat_result, "st_dev", 0) or 0)
    return f"{device}:{inode}"


def _file_identity_changes(item: CleanupFileItem, stat_result: Any) -> tuple[str, ...]:
    return _candidate_identity_changes(
        CleanupCandidate(
            path=item.path,
            root_path="",
            size=item.size,
            mtime=item.mtime,
            mtime_ns=item.mtime_ns,
            file_id=item.file_id,
        ),
        stat_result,
    )


def _candidate_identity_changes(candidate: CleanupCandidate, stat_result: Any) -> tuple[str, ...]:
    """Compare a scan snapshot with the current file without deleting it."""
    changes: list[str] = []
    if int(stat_result.st_size) != int(candidate.size):
        changes.append("文件大小")
    if candidate.mtime_ns and _stat_mtime_ns(stat_result) != int(candidate.mtime_ns):
        changes.append("修改时间")
    if candidate.file_id and _stat_file_id(stat_result) != candidate.file_id:
        changes.append("文件标识")
    return tuple(changes)


def iter_cleanup_candidates(
    roots: Iterable[str],
    formats: Iterable[str],
    *,
    keep_days: int = 0,
    cancel_event: Any = None,
    on_error: Optional[Callable[[str, BaseException], None]] = None,
) -> Iterable[CleanupCandidate]:
    """Stream the authoritative cleanup candidate definition for all callers."""
    normalized_formats = {
        str(ext).strip().lower() for ext in formats if str(ext).strip()
    }
    cutoff = time.time() - keep_days * 86400 if keep_days > 0 else 0
    cancelled = cancel_event or _NeverCancelled()
    for monitor_root in CleanupService.deduplicate_cleanup_roots(roots):
        stack: list[Any] = []
        try:
            stack.append(os.scandir(monitor_root))
            while stack and not cancelled.is_set():
                try:
                    entry = next(stack[-1])
                except StopIteration:
                    stack.pop().close()
                    continue
                except OSError as exc:
                    if on_error:
                        on_error(getattr(exc, "filename", None) or monitor_root, exc)
                    stack.pop().close()
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(os.scandir(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if normalized_formats and Path(entry.name).suffix.lower() not in normalized_formats:
                            continue
                        stat_result = os.stat(entry.path, follow_symlinks=False)
                        if cutoff and stat_result.st_mtime > cutoff:
                            continue
                        yield CleanupCandidate(
                            path=entry.path,
                            root_path=monitor_root,
                            size=int(stat_result.st_size),
                            mtime=float(stat_result.st_mtime),
                            mtime_ns=_stat_mtime_ns(stat_result),
                            file_id=_stat_file_id(stat_result),
                            created_at=CleanupService.file_created_at(stat_result),
                        )
                except OSError as exc:
                    if on_error:
                        on_error(getattr(exc, "filename", None) or entry.path, exc)
        except OSError as exc:
            if on_error:
                on_error(getattr(exc, "filename", None) or monitor_root, exc)
        finally:
            while stack:
                stack.pop().close()


class _NewestCandidate:
    """Reverse heap ordering so a fixed heap retains the oldest candidates."""

    def __init__(self, candidate: CleanupCandidate) -> None:
        self.candidate = candidate
        self.key = (
            int(candidate.mtime_ns),
            os.path.normcase(os.path.abspath(candidate.path)),
        )

    def __lt__(self, other: "_NewestCandidate") -> bool:
        return self.key > other.key


def oldest_cleanup_candidates(
    roots: Iterable[str], formats: Iterable[str], *, limit: int,
    cancel_event: Any = None, on_error: Optional[Callable[[str, BaseException], None]] = None,
) -> tuple[tuple[CleanupCandidate, ...], int]:
    """Scan once while retaining only the oldest bounded batch in memory."""
    capacity = max(1, int(limit))
    heap: list[_NewestCandidate] = []
    scanned = 0
    for candidate in iter_cleanup_candidates(
        roots, formats, cancel_event=cancel_event, on_error=on_error
    ):
        scanned += 1
        entry = _NewestCandidate(candidate)
        if len(heap) < capacity:
            heapq.heappush(heap, entry)
        elif entry.key < heap[0].key:
            heapq.heapreplace(heap, entry)
    return tuple(
        entry.candidate for entry in sorted(heap, key=lambda item: item.key)
    ), scanned


def sort_cleanup_file_items(files: Iterable[CleanupFileItem]) -> list[CleanupFileItem]:
    """Apply the same deterministic oldest-first order to manual previews."""
    return sorted(
        files,
        key=lambda item: (
            int(item.mtime_ns or round(item.mtime * 1_000_000_000)),
            os.path.normcase(os.path.abspath(item.path)),
        ),
    )


class _NeverCancelled:
    def is_set(self) -> bool:
        return False


class CleanupAuditWriter(Protocol):
    last_error: str

    def write(self, event: str, run_id: str, **fields: Any) -> bool: ...


class _ScanWorker(QtCore.QObject):
    worker_event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(self, request: CleanupScanRequest) -> None:
        super().__init__()
        self.request = request
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_set(self) -> bool:
        return self._cancel_event.is_set()

    @QtCore.Slot()
    def run(self) -> None:
        files: list[CleanupFileItem] = []
        total_size_bytes = 0
        file_count = 0
        last_progress_at = 0.0
        self.worker_event.emit("log", {"message": "开始扫描文件..."})
        for folder in CleanupService.deduplicate_cleanup_roots(self.request.folders):
            self.worker_event.emit("log", {"message": f"扫描目录: {folder}"})

        def on_error(path: str, exc: BaseException) -> None:
            self.worker_event.emit("log", {"message": f"无法访问文件 {path}: {exc}"})

        for candidate in iter_cleanup_candidates(
            self.request.folders,
            self.request.formats,
            keep_days=self.request.keep_days,
            cancel_event=self,
            on_error=on_error,
        ):
            if self.is_set():
                break
            files.append(candidate.as_file_item())
            file_count += 1
            # Keep this in Python's arbitrary-precision integer domain.  This
            # must never traverse a Qt `int`, because real deployments can
            # scan well beyond 100 TB in aggregate.
            total_size_bytes += int(candidate.size)
            now = time.monotonic()
            if file_count == 1 or now - last_progress_at >= SCAN_PROGRESS_INTERVAL_SECONDS:
                last_progress_at = now
                self.worker_event.emit(
                    "scan_progress",
                    {
                        "current_dir": os.path.dirname(candidate.path),
                        "file_count": file_count,
                        "total_size_bytes": total_size_bytes,
                    },
                )
        if self.is_set():
            self.worker_event.emit("log", {"message": "扫描已取消"})
        self.finished.emit(sort_cleanup_file_items(files))


class _DeleteWorker(QtCore.QObject):
    worker_event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(
        self,
        request: CleanupDeleteRequest,
        audit_writer: Optional[CleanupAuditWriter] = None,
    ) -> None:
        super().__init__()
        self.request = request
        self._audit_writer = audit_writer
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @QtCore.Slot()
    def run(self) -> None:
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        skipped_changed_count = 0
        policy = SafeDeletionPolicy(trash_supported, send_to_trash, os.remove)
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        total = len(self.request.files)
        for index, item in enumerate(self.request.files, start=1):
            if self._cancelled:
                self.worker_event.emit("log", {"message": "删除任务已取消"})
                break
            try:
                def verify_identity() -> tuple[bool, str]:
                    stat_result = os.stat(item.path, follow_symlinks=False)
                    changes = _file_identity_changes(item, stat_result)
                    return (
                        not changes,
                        "、".join(changes) if changes else "",
                    )

                if not self.request.use_trash:
                    identity_matches, identity_reason = verify_identity()
                    if not identity_matches:
                        skipped_changed_count += 1
                        self.worker_event.emit(
                            "log",
                            {"message": f"已跳过扫描后发生变化的文件 {item.path}: {identity_reason}"},
                        )
                        continue
                    if self._audit_writer is None or not self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_INTENT",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                        allowed_roots=list(self.request.allowed_roots),
                        authorization="ui_second_confirmation",
                    ):
                        failed_count += 1
                        self.worker_event.emit(
                            "log",
                            {"message": f"删除失败 {item.path}: 永久删除审计日志写入失败，文件已保留"},
                        )
                        continue

                result = policy.delete(
                    SafeDeletionRequest(
                        path=item.path,
                        allowed_roots=self.request.allowed_roots,
                        identity_verifier=verify_identity,
                        mode="trash" if self.request.use_trash else "permanent",
                        permanent_authorized=self.request.permanent_authorized,
                    )
                )
                if not result.success:
                    if not self.request.use_trash and self._audit_writer is not None:
                        self._audit_writer.write(
                            "MANUAL_PERMANENT_DELETE_FAIL",
                            run_id,
                            path=item.path,
                            size_bytes=int(item.size),
                            status=result.status,
                            error=result.message,
                        )
                    if result.status == "identity_changed":
                        skipped_changed_count += 1
                        message = f"已跳过扫描后发生变化的文件 {item.path}: {result.message}"
                    else:
                        failed_count += 1
                        message = f"删除失败 {item.path}: {result.message}"
                    self.worker_event.emit("log", {"message": message})
                    continue
                deleted_count += 1
                deleted_size += item.size
                if not self.request.use_trash and self._audit_writer is not None:
                    if not self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_OK",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                    ):
                        self.worker_event.emit(
                            "log",
                            {
                                "message": (
                                    f"审计告警 {item.path}: 文件已永久删除，"
                                    "但结果审计写入失败，请立即保留当前日志"
                                )
                            },
                        )
            except Exception as exc:
                failed_count += 1
                if not self.request.use_trash and self._audit_writer is not None:
                    self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_FAIL",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                        status="exception",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self.worker_event.emit("log", {"message": f"删除失败 {item.path}: {exc}"})
            finally:
                self.worker_event.emit("delete_progress", {"current": index, "total": total})
        remaining = tuple(item for item in self.request.files if os.path.exists(item.path))
        self.finished.emit(
            {
                "deleted_count": deleted_count,
                "deleted_size": deleted_size,
                "failed_count": failed_count,
                "skipped_changed_count": skipped_changed_count,
                "remaining_files": remaining,
            }
        )


class _ManualEventBridge(QtCore.QObject):
    def __init__(self, callback: CleanupEventCallback) -> None:
        super().__init__()
        self._callback = callback

    @QtCore.Slot(str, object)
    def on_event(self, kind: str, payload: object) -> None:
        self._callback(kind, dict(payload) if isinstance(payload, dict) else {})

    @QtCore.Slot(object)
    def on_scan_finished(self, files: object) -> None:
        self._callback(
            "scan_finished",
            {"files": list(cast(Iterable[CleanupFileItem], files))},
        )

    @QtCore.Slot(object)
    def on_delete_finished(self, result: object) -> None:
        self._callback("delete_finished", dict(result) if isinstance(result, dict) else {})


class CleanupService:
    def __init__(
        self,
        audit_writer: Optional[CleanupAuditWriter] = None,
        thread_factory: Callable[[], Any] = QtCore.QThread,
    ) -> None:
        self._audit_writer = audit_writer
        self._thread_factory = thread_factory
        self._scan_worker: Optional[_ScanWorker] = None
        self._scan_thread: Any = None
        self._scan_bridge: Optional[_ManualEventBridge] = None
        self._delete_worker: Optional[_DeleteWorker] = None
        self._delete_thread: Any = None
        self._delete_bridge: Optional[_ManualEventBridge] = None
        self.last_shutdown_errors: Tuple[str, ...] = ()
        self.manual_shutdown_status = "idle"

    @property
    def trash_available(self) -> bool:
        return trash_supported()

    @staticmethod
    def validate_folder_access(path: str, require_write: bool = False) -> str:
        if not os.path.exists(path):
            return "路径不存在"
        if not os.path.isdir(path):
            return "不是文件夹"
        try:
            with os.scandir(path) as iterator:
                next(iterator, None)
        except PermissionError:
            return "无读取权限"
        except OSError as exc:
            return f"无法读取目录: {exc}"
        if require_write:
            probe = ""
            try:
                fd, probe = tempfile.mkstemp(prefix=".cleanup_probe_", dir=path)
                os.close(fd)
                os.remove(probe)
            except PermissionError:
                return "无删除/写入权限"
            except OSError as exc:
                return f"无删除/写入权限: {exc}"
            finally:
                if probe and os.path.exists(probe):
                    try:
                        os.remove(probe)
                    except OSError:
                        pass
        return ""

    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult:
        if not request.folders:
            return CleanupValidationResult(errors=("请至少选择一个文件夹进行扫描",))
        if not request.formats:
            return CleanupValidationResult(errors=("请至少选择一种文件格式进行扫描",))
        valid: list[str] = []
        invalid: list[str] = []
        for path in request.folders:
            reason = self.validate_folder_access(path, require_write=False)
            if reason:
                invalid.append(f"{path}：{reason}")
            else:
                valid.append(path)
        errors = () if valid else ("没有可用的扫描路径，请检查路径与权限",)
        return CleanupValidationResult(tuple(valid), tuple(invalid), errors)

    def start_scan(
        self, request: CleanupScanRequest, callback: CleanupEventCallback
    ) -> CleanupCommandResult:
        validation = self.validate_scan_request(request)
        if not validation.is_valid:
            return CleanupCommandResult(False, errors=validation.errors)
        if self._scan_worker is not None:
            return CleanupCommandResult(False, "扫描任务已在运行")
        effective = CleanupScanRequest(
            validation.valid_folders, request.formats, request.keep_days
        )
        try:
            worker = _ScanWorker(effective)
            thread = self._thread_factory()
            bridge = _ManualEventBridge(callback)
            worker.moveToThread(thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.worker_event.connect(bridge.on_event, queued)
            worker.finished.connect(bridge.on_scan_finished, queued)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            thread.finished.connect(self._release_scan)
            self._scan_worker, self._scan_thread, self._scan_bridge = worker, thread, bridge
            thread.start()
            return CleanupCommandResult(True, "扫描任务已启动")
        except Exception as exc:
            self._release_scan()
            return CleanupCommandResult(False, str(exc))

    def cancel_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel()

    def cancel(self) -> None:
        """非阻塞请求取消扫描与删除 Worker。"""
        self.cancel_scan()
        cancel_delete = getattr(self._delete_worker, "cancel", None)
        if callable(cancel_delete):
            cancel_delete()

    def start_delete(
        self, request: CleanupDeleteRequest, callback: CleanupEventCallback
    ) -> CleanupCommandResult:
        if not request.files:
            return CleanupCommandResult(False, "没有选中任何文件")
        if not request.allowed_roots:
            return CleanupCommandResult(False, "删除请求缺少已验证的清理目录")
        if not request.use_trash and not request.permanent_authorized:
            return CleanupCommandResult(False, "永久删除需要显式二次授权")
        if not request.use_trash and self._audit_writer is None:
            return CleanupCommandResult(False, "永久删除需要可用的审计日志")
        if self._delete_worker is not None:
            return CleanupCommandResult(False, "删除任务已在运行")
        try:
            worker = _DeleteWorker(request, self._audit_writer)
            thread = self._thread_factory()
            bridge = _ManualEventBridge(callback)
            worker.moveToThread(thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.worker_event.connect(bridge.on_event, queued)
            worker.finished.connect(bridge.on_delete_finished, queued)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            thread.finished.connect(self._release_delete)
            self._delete_worker, self._delete_thread, self._delete_bridge = worker, thread, bridge
            thread.start()
            return CleanupCommandResult(True, "删除任务已启动")
        except Exception as exc:
            self._release_delete()
            return CleanupCommandResult(False, str(exc))

    @property
    def is_scanning(self) -> bool:
        return self._scan_worker is not None

    @property
    def is_deleting(self) -> bool:
        return self._delete_worker is not None

    @property
    def has_running_workers(self) -> bool:
        for thread, worker in (
            (self._scan_thread, self._scan_worker),
            (self._delete_thread, self._delete_worker),
        ):
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    return True
            except Exception:
                if worker is not None:
                    return True
        return False

    def shutdown_manual(self, timeout_ms: int = 10000) -> Tuple[str, ...]:
        self.cancel()
        scan_thread = self._scan_thread
        delete_thread = self._delete_thread
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
        errors: list[str] = []
        for name, thread, release in (
            ("扫描", scan_thread, self._release_scan),
            ("删除", delete_thread, self._release_delete),
        ):
            if thread is None:
                continue
            thread.quit()
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            try:
                stopped = thread.wait(remaining_ms)
            except TypeError as exc:
                error = (
                    f"{name} QThread 不支持带超时的 wait({remaining_ms})，"
                    "任务已标记为失败挂起"
                )
                logger.error("%s: %s", error, exc)
                errors.append(error)
                thread.quit()
                continue
            if stopped is False:
                error = f"{name} QThread 在 {timeout_ms}ms 内未停止，任务已标记为失败挂起"
                logger.error(error)
                errors.append(error)
                thread.quit()
                continue
            release()
        self.last_shutdown_errors = tuple(errors)
        self.manual_shutdown_status = "failed_hung" if errors else "stopped"
        return self.last_shutdown_errors

    def _release_scan(self) -> None:
        self._scan_worker = None
        self._scan_thread = None
        self._scan_bridge = None

    def _release_delete(self) -> None:
        self._delete_worker = None
        self._delete_thread = None
        self._delete_bridge = None

    @staticmethod
    def cleanup_volume_identity(path: str) -> Tuple[str, str]:
        normalized = os.path.abspath(path)
        drive, _ = os.path.splitdrive(normalized)
        fallback = os.path.normcase(drive or Path(normalized).anchor or normalized)
        display = drive or Path(normalized).anchor or normalized
        if os.name != "nt":
            return fallback, display
        try:
            volume_path = ctypes.create_unicode_buffer(32768)
            get_volume_path = ctypes.windll.kernel32.GetVolumePathNameW
            get_volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_path.restype = wintypes.BOOL
            if not get_volume_path(normalized, volume_path, len(volume_path)):
                return fallback, display
            root = volume_path.value
            volume_name = ctypes.create_unicode_buffer(32768)
            get_volume_name = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW
            get_volume_name.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_name.restype = wintypes.BOOL
            if get_volume_name(root, volume_name, len(volume_name)):
                return os.path.normcase(volume_name.value), root
            return os.path.normcase(root), root
        except Exception:
            return fallback, display

    @classmethod
    def validate_cleanup_folder_group(
        cls, folders: Iterable[str]
    ) -> Tuple[bool, str, Tuple[Tuple[str, str], ...]]:
        details: list[Tuple[str, str]] = []
        identities = set()
        for folder in folders:
            identity, display = cls.cleanup_volume_identity(folder)
            identities.add(identity)
            details.append((folder, display))
        if len(identities) > 1:
            mapping = "；".join(f"{path} -> {volume}" for path, volume in details)
            return False, f"自动清理目录必须位于同一磁盘：{mapping}", tuple(details)
        return True, "", tuple(details)

    @staticmethod
    def deduplicate_cleanup_roots(folders: Iterable[str]) -> list[str]:
        candidates: list[Tuple[str, str]] = []
        seen = set()
        for folder in folders:
            absolute = os.path.abspath(folder)
            normalized = os.path.normcase(os.path.realpath(absolute))
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((absolute, normalized))
        candidates.sort(key=lambda item: (len(Path(item[1]).parts), item[1]))
        result: list[Tuple[str, str]] = []
        for absolute, normalized in candidates:
            is_nested = False
            for _, parent in result:
                try:
                    if os.path.commonpath([parent, normalized]) == parent:
                        is_nested = True
                        break
                except ValueError:
                    # Different Windows drives have no common path.
                    continue
            if is_nested:
                continue
            result.append((absolute, normalized))
        return [absolute for absolute, _ in result]

    @staticmethod
    def file_created_at(stat_result: Any) -> float:
        birth_time = getattr(stat_result, "st_birthtime", None)
        if birth_time is not None:
            return float(birth_time)
        return float(stat_result.st_ctime)

    @staticmethod
    def file_modified_at_ns(stat_result: Any) -> int:
        return _stat_mtime_ns(stat_result)

    @staticmethod
    def file_identity(stat_result: Any) -> str:
        return _stat_file_id(stat_result)

    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult:
        if not request.enabled:
            return CleanupValidationResult(errors=("自动清理未启用",))
        if not request.folders:
            return CleanupValidationResult(errors=("已启用自动清理但未设置清理路径",))
        invalid = tuple(path for path in request.folders if not os.path.isdir(path))
        if invalid:
            return CleanupValidationResult(
                invalid_reasons=invalid,
                errors=(f"自动清理路径不可用: {'; '.join(invalid)}",),
            )
        valid, error, details = self.validate_cleanup_folder_group(request.folders)
        if not valid:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=(error,),
                volume_details=details,
            )
        if not request.use_trash:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("自动清理仅允许回收站模式",),
                volume_details=details,
            )
        if request.use_trash and not trash_supported():
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("回收站不可用，自动清理无法启用",),
                volume_details=details,
            )
        if request.target_percent >= request.trigger_percent:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("自动清理目标阈值必须小于触发阈值",),
                volume_details=details,
            )
        return CleanupValidationResult(
            valid_folders=request.folders, volume_details=details
        )

    def should_trigger(self, request: AutoCleanupRequest) -> Tuple[bool, str]:
        validation = self.validate_auto_request(request)
        if not validation.is_valid:
            return False, "；".join(validation.errors)
        try:
            disk = shutil.disk_usage(request.folders[0])
        except Exception as exc:
            return False, f"无法获取自动清理磁盘信息: {exc}"
        used = ((disk.total - disk.free) / disk.total) * 100 if disk.total > 0 else 0.0
        return used >= request.trigger_percent, ""

    def record_blocked(self, request: AutoCleanupRequest, status: str, error: str) -> None:
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self._write_audit(
            "START",
            run_id,
            trigger_source=request.trigger_source,
            folders=list(request.folders),
            disk=None,
            used_percent=None,
            trigger_percent=request.trigger_percent,
            target_percent=request.target_percent,
            delete_mode="回收站" if request.use_trash else "永久删除",
            time_basis="file_modification_time",
        )
        self._write_audit(
            "END",
            run_id,
            status=status,
            error=error,
            scanned_count=0,
            deleted_count=0,
            failed_count=0,
            start_used_percent=None,
            final_used_percent=None,
            actual_released_bytes=0,
        )

    def _write_audit(self, event: str, run_id: str, **fields: Any) -> bool:
        return bool(self._audit_writer and self._audit_writer.write(event, run_id, **fields))

    def run_auto_cleanup(
        self,
        request: AutoCleanupRequest,
        cancel_event: Any,
        log: Callable[[str], None],
        delete_mode_provider: Optional[Callable[[], bool]] = None,
    ) -> AutoCleanupResult:
        """Delete oldest candidates in bounded scan batches; never retain a directory index."""
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        roots = self.deduplicate_cleanup_roots(request.folders)
        scanned_count = deleted_count = failed_count = attempted_bytes = skipped_changed_count = 0
        start_usage = final_usage = None
        audit_started = False

        def used_percent(usage: Any) -> Optional[float]:
            return None if usage is None or usage.total <= 0 else ((usage.total - usage.free) / usage.total) * 100

        def finish(status: str, error: str = "") -> AutoCleanupResult:
            released = max(0, int(final_usage.free - start_usage.free)) if start_usage is not None and final_usage is not None else 0
            if audit_started:
                self._write_audit("END", run_id, status=status, error=error, scanned_count=scanned_count, deleted_count=deleted_count, failed_count=failed_count, skipped_changed_count=skipped_changed_count, start_used_percent=used_percent(start_usage), final_used_percent=used_percent(final_usage), actual_released_bytes=released, attempted_delete_bytes=attempted_bytes)
            final = used_percent(final_usage)
            log(f"✅ 自动清理结束：状态={status}，扫描={scanned_count}，删除={deleted_count}，失败={failed_count}，实际占用率={final:.1f}%" if final is not None else f"✅ 自动清理结束：状态={status}，扫描={scanned_count}，删除={deleted_count}，失败={failed_count}，实际占用率=未知")
            return AutoCleanupResult(status, error, scanned_count, deleted_count, failed_count, attempted_bytes, released, skipped_changed_count)

        try:
            if cancel_event.is_set():
                return AutoCleanupResult("任务异常", "应用正在退出，自动清理已取消")
            use_trash = bool(delete_mode_provider() if delete_mode_provider else request.use_trash)
            effective_request = replace(request, use_trash=use_trash)
            validation = self.validate_auto_request(effective_request)
            if not validation.is_valid:
                error = "；".join(validation.errors)
                self.record_blocked(effective_request, "路径不可用", error)
                return AutoCleanupResult("路径不可用", error)
            start_usage = final_usage = shutil.disk_usage(roots[0])
            if (start_percent := used_percent(start_usage)) is None or start_percent < request.trigger_percent:
                return AutoCleanupResult("未达到阈值")
            if not self._write_audit("START", run_id, trigger_source=request.trigger_source, folders=list(request.folders), effective_roots=roots, disk=validation.volume_details[0][1] if validation.volume_details else "", used_percent=start_percent, trigger_percent=request.trigger_percent, target_percent=request.target_percent, delete_mode="回收站", time_basis="file_modification_time", format_filter=sorted(set(request.formats)), ordering="bounded_stream_oldest_modified_first", candidate_batch_limit=CLEANUP_RECORD_READ_LIMIT):
                return AutoCleanupResult("任务异常", "清理审计日志写入失败")
            audit_started = True
            log(f"⚠️ 磁盘使用率 {start_percent:.1f}% 达到触发阈值 {request.trigger_percent}%，按全局修改时间最旧优先流式清理至 {request.target_percent}%")

            while not cancel_event.is_set():
                scan_failures = 0
                def scan_error(path: str, exc: BaseException) -> None:
                    nonlocal scan_failures
                    scan_failures += 1
                    log(f"⚠️ 清理候选无法访问: {path}: {type(exc).__name__}: {exc}")
                candidates, scanned = oldest_cleanup_candidates(roots, request.formats, limit=CLEANUP_RECORD_READ_LIMIT, cancel_event=cancel_event, on_error=scan_error)
                scanned_count += scanned
                if cancel_event.is_set():
                    return finish("任务异常", "应用正在退出，自动清理已取消")
                if scan_failures >= AUTO_CLEANUP_FAILURE_LIMIT:
                    return finish("失败达到20次", "候选扫描连续失败")
                if not candidates:
                    final_usage = shutil.disk_usage(roots[0])
                    final_percent = used_percent(final_usage)
                    return finish("达到目标" if final_percent is not None and final_percent <= request.target_percent else "候选已耗尽", "候选文件已耗尽，但磁盘仍未达到目标阈值")

                for candidate in candidates:
                    if cancel_event.is_set(): return finish("任务异常", "应用正在退出，自动清理已取消")
                    if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT: return finish("失败达到20次")
                    try:
                        stat_result = os.stat(candidate.path, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except Exception as exc:
                        failed_count += 1
                        self._write_audit("DELETE_FAIL", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), error_type=type(exc).__name__, error=str(exc), failed_count=failed_count)
                        continue
                    changes = list(_candidate_identity_changes(candidate, stat_result))
                    created_at = self.file_created_at(stat_result)
                    if abs(created_at - candidate.created_at) > 0.001: changes.append("创建时间")
                    if changes:
                        skipped_changed_count += 1
                        self._write_audit("DELETE_SKIP_CHANGED", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), changes=changes, scanned_size_bytes=candidate.size, current_size_bytes=int(stat_result.st_size), scanned_modified_at_ns=candidate.mtime_ns, current_modified_at_ns=self.file_modified_at_ns(stat_result))
                        log(f"⚠️ 已跳过扫描后发生变化的文件 {candidate.path}：{', '.join(changes)}")
                        return finish("候选已刷新", "检测到同路径文件已被替换或修改，已跳过本次删除")
                    if not bool(delete_mode_provider() if delete_mode_provider else request.use_trash):
                        return finish("路径不可用", "自动清理仅允许回收站模式，已停止")
                    if not trash_supported(): return finish("路径不可用", "回收站不可用，自动清理已停止（避免意外永久删除）")
                    def verify_identity() -> tuple[bool, str]:
                        current = os.stat(candidate.path, follow_symlinks=False)
                        current_changes = list(_candidate_identity_changes(candidate, current))
                        if abs(self.file_created_at(current) - candidate.created_at) > 0.001: current_changes.append("创建时间")
                        return not current_changes, "、".join(current_changes)
                    try:
                        result = SafeDeletionPolicy(trash_supported, send_to_trash, os.remove).delete(SafeDeletionRequest(path=candidate.path, allowed_roots=tuple(roots), identity_verifier=verify_identity, mode="trash", automatic=True))
                        if not result.success: raise OSError(result.message)
                        deleted_count += 1
                        attempted_bytes += int(stat_result.st_size)
                    except Exception as exc:
                        failed_count += 1
                        if not self._write_audit("DELETE_FAIL", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), size_bytes=int(stat_result.st_size), created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"), modified_at_ns=self.file_modified_at_ns(stat_result), delete_mode="回收站", error_type=type(exc).__name__, error=str(exc), failed_count=failed_count): return finish("任务异常", "清理审计日志写入失败")
                        continue
                    try: final_usage = shutil.disk_usage(roots[0])
                    except Exception as exc:
                        final_usage = None
                        usage_error = f"{type(exc).__name__}: {exc}"
                    else: usage_error = ""
                    current_percent = used_percent(final_usage)
                    if not self._write_audit("DELETE_OK", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), size_bytes=int(stat_result.st_size), created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"), modified_at_ns=self.file_modified_at_ns(stat_result), delete_mode="回收站", disk_used_percent=current_percent, disk_usage_error=usage_error, candidate_source="stream_scan"): return finish("任务异常", "清理审计日志写入失败")
                    if usage_error: return finish("路径不可用", usage_error)
                    if final_usage is not None and start_usage is not None and final_usage.free <= start_usage.free:
                        return finish("回收站未释放空间")
                    if current_percent is not None and current_percent <= request.target_percent: return finish("达到目标")
            return finish("任务异常", "应用正在退出，自动清理已取消")
        except Exception as exc:
            if not audit_started: self.record_blocked(request, "任务异常", str(exc))
            log(f"⚠️ 自动清理任务异常: {exc}")
            return finish("任务异常", str(exc))
