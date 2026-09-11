"""Filesystem policies and worker lifecycles for manual and automatic cleanup."""

from __future__ import annotations

import ctypes
import datetime
from dataclasses import replace
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, Iterable, Optional, Protocol, Tuple
import uuid

from PySide6 import QtCore

from src.models import (
    AutoCleanupRequest,
    AutoCleanupResult,
    CleanupCommandResult,
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupIndexRecord,
    CleanupIndexResult,
    CleanupScanRequest,
    CleanupValidationResult,
)

try:
    from send2trash import send2trash as _send2trash
except ImportError:
    _send2trash = None


AUTO_CLEANUP_FAILURE_LIMIT = 20
CLEANUP_INDEX_WRITE_BATCH = 500
CLEANUP_INDEX_READ_LIMIT = 100
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
    changes: list[str] = []
    if int(stat_result.st_size) != int(item.size):
        changes.append("文件大小")
    current_mtime_ns = _stat_mtime_ns(stat_result)
    if item.mtime_ns:
        if current_mtime_ns != int(item.mtime_ns):
            changes.append("修改时间")
    elif abs(float(stat_result.st_mtime) - float(item.mtime)) > 0.000001:
        changes.append("修改时间")
    current_file_id = _stat_file_id(stat_result)
    if item.file_id and current_file_id != item.file_id:
        changes.append("文件标识")
    return tuple(changes)


class CleanupAuditWriter(Protocol):
    last_error: str

    def write(self, event: str, run_id: str, **fields: Any) -> bool: ...


class CleanupIndexWriter(Protocol):
    last_error: str

    @staticmethod
    def normalize_path(path: str) -> str: ...
    def scope_fingerprint(self, folders: Iterable[str], formats: Iterable[str]) -> str: ...
    def ensure_schema(self) -> bool: ...
    def prepare_scope(self, scope_fingerprint: str) -> bool: ...
    def is_scope_current(self, scope_fingerprint: str) -> bool: ...
    def is_ready(self, scope_fingerprint: str) -> bool: ...
    def upsert_many(
        self, records: Any, scope_fingerprint: str
    ) -> bool: ...
    def oldest(self, scope_fingerprint: str, limit: int = 100) -> Any: ...
    def remove(self, normalized_path: str, scope_fingerprint: str) -> bool: ...
    def count(self, scope_fingerprint: str) -> int: ...
    def mark_ready(self, scope_fingerprint: str) -> bool: ...
    def mark_dirty(self, clear_records: bool = False) -> bool: ...


def trash_supported() -> bool:
    return _send2trash is not None or os.name == "nt"


def send_to_trash(path: str) -> None:
    if _send2trash is not None:
        _send2trash(path)
        return
    if os.name != "nt":
        raise RuntimeError("Trash not supported without send2trash")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.UINT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    op = SHFILEOPSTRUCTW(
        0,
        3,
        path + "\0\0",
        None,
        0x40 | 0x10 | 0x4,
        False,
        None,
        None,
    )
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(rc, "Send to Recycle Bin failed", path)


class _ScanWorker(QtCore.QObject):
    event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(self, request: CleanupScanRequest) -> None:
        super().__init__()
        self.request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @QtCore.Slot()
    def run(self) -> None:
        files: list[CleanupFileItem] = []
        total_size = 0
        file_count = 0
        cutoff = time.time() - self.request.keep_days * 86400 if self.request.keep_days > 0 else 0
        self.event.emit("log", {"message": "开始扫描文件..."})
        for folder in self.request.folders:
            if self._cancelled:
                self.event.emit("log", {"message": "扫描已取消"})
                break
            self.event.emit("log", {"message": f"扫描目录: {folder}"})
            folder_count = 0
            folder_size = 0
            try:
                for root, _, names in os.walk(folder):
                    if self._cancelled:
                        break
                    self.event.emit(
                        "scan_progress",
                        {"current_dir": root, "file_count": file_count, "total_size": total_size},
                    )
                    for name in names:
                        if self._cancelled:
                            break
                        if not any(name.lower().endswith(ext) for ext in self.request.formats):
                            continue
                        path = os.path.join(root, name)
                        try:
                            stat = os.stat(path, follow_symlinks=False)
                            if cutoff and stat.st_mtime > cutoff:
                                continue
                            files.append(
                                CleanupFileItem(
                                    path=path,
                                    size=int(stat.st_size),
                                    mtime=float(stat.st_mtime),
                                    mtime_ns=_stat_mtime_ns(stat),
                                    file_id=_stat_file_id(stat),
                                )
                            )
                            folder_count += 1
                            folder_size += stat.st_size
                            file_count += 1
                            total_size += stat.st_size
                        except Exception as exc:
                            self.event.emit(
                                "log", {"message": f"无法访问文件 {name}: {exc}"}
                            )
                if not self._cancelled:
                    self.event.emit(
                        "log",
                        {
                            "message": f"目录中找到 {folder_count} 个文件，"
                            f"{folder_size / (1024 * 1024):.2f} MB"
                        },
                    )
            except Exception as exc:
                self.event.emit("log", {"message": f"扫描失败: {exc}"})
        self.finished.emit(files)


class _DeleteWorker(QtCore.QObject):
    event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(self, request: CleanupDeleteRequest) -> None:
        super().__init__()
        self.request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @QtCore.Slot()
    def run(self) -> None:
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        skipped_changed_count = 0
        use_trash = self.request.use_trash and trash_supported()
        if self.request.use_trash and not trash_supported():
            self.event.emit("log", {"message": "回收站不可用，将使用永久删除。"})
        total = len(self.request.files)
        for index, item in enumerate(self.request.files, start=1):
            if self._cancelled:
                self.event.emit("log", {"message": "删除任务已取消"})
                break
            try:
                stat_result = os.stat(item.path, follow_symlinks=False)
                changes = _file_identity_changes(item, stat_result)
                if changes:
                    skipped_changed_count += 1
                    self.event.emit(
                        "log",
                        {
                            "message": (
                                f"已跳过扫描后发生变化的文件 {item.path}："
                                f"{', '.join(changes)}"
                            )
                        },
                    )
                    continue
                if use_trash:
                    send_to_trash(item.path)
                else:
                    os.remove(item.path)
                deleted_count += 1
                deleted_size += item.size
            except Exception as exc:
                failed_count += 1
                self.event.emit("log", {"message": f"删除失败 {item.path}: {exc}"})
            finally:
                self.event.emit("delete_progress", {"current": index, "total": total})
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
        self._callback("scan_finished", {"files": list(files)})

    @QtCore.Slot(object)
    def on_delete_finished(self, result: object) -> None:
        self._callback("delete_finished", dict(result) if isinstance(result, dict) else {})


class CleanupService:
    def __init__(
        self,
        audit_writer: Optional[CleanupAuditWriter] = None,
        thread_factory: Callable[[], Any] = QtCore.QThread,
        index_repository: Optional[CleanupIndexWriter] = None,
    ) -> None:
        self._audit_writer = audit_writer
        self._index_repository = index_repository
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
            worker.event.connect(bridge.on_event, queued)
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
        if self._delete_worker is not None:
            return CleanupCommandResult(False, "删除任务已在运行")
        try:
            worker = _DeleteWorker(request)
            thread = self._thread_factory()
            bridge = _ManualEventBridge(callback)
            worker.moveToThread(thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.event.connect(bridge.on_event, queued)
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
    def sort_cleanup_candidates(
        files: Iterable[Tuple[float, int, str]]
    ) -> list[Tuple[float, int, str]]:
        result = [(float(mtime), max(0, int(size)), path) for mtime, size, path in files]
        result.sort(key=lambda item: (item[0], os.path.normcase(os.path.abspath(item[2]))))
        return result

    @classmethod
    def select_cleanup_candidates(
        cls, files: Iterable[Tuple[float, int, str]], bytes_to_free: int
    ) -> Tuple[list[Tuple[float, int, str]], int]:
        if bytes_to_free <= 0:
            return [], 0
        ordered = cls.sort_cleanup_candidates(files)
        selected: list[Tuple[float, int, str]] = []
        size = 0
        for item in ordered:
            selected.append(item)
            size += item[1]
            if size >= bytes_to_free:
                break
        return selected, len(ordered)

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

    def cleanup_scope_fingerprint(self, request: AutoCleanupRequest) -> str:
        if self._index_repository is None:
            return ""
        return self._index_repository.scope_fingerprint(request.folders, request.formats)

    def is_index_ready(self, request: AutoCleanupRequest) -> bool:
        if self._index_repository is None:
            return False
        fingerprint = self.cleanup_scope_fingerprint(request)
        return bool(fingerprint and self._index_repository.is_ready(fingerprint))

    def mark_index_dirty(self, clear_records: bool = False) -> bool:
        if self._index_repository is None:
            return False
        return self._index_repository.mark_dirty(clear_records=clear_records)

    @property
    def index_last_error(self) -> str:
        if self._index_repository is None:
            return "清理索引仓库未配置"
        return self._index_repository.last_error

    def _matching_cleanup_root(
        self, path: str, request: AutoCleanupRequest
    ) -> Tuple[str, str]:
        if self._index_repository is None:
            return "", ""
        normalized = self._index_repository.normalize_path(path)
        for root in self.deduplicate_cleanup_roots(request.folders):
            normalized_root = self._index_repository.normalize_path(root)
            try:
                if os.path.commonpath([normalized_root, normalized]) == normalized_root:
                    return root, normalized
            except ValueError:
                continue
        return "", normalized

    def _iter_index_files(
        self,
        roots: Iterable[str],
        cancel_event: Any,
        on_error: Callable[[str, BaseException], None],
    ) -> Iterable[Tuple[str, str]]:
        for monitor_root in roots:
            stack: list[Any] = []
            try:
                try:
                    stack.append(os.scandir(monitor_root))
                except OSError as exc:
                    on_error(getattr(exc, "filename", None) or monitor_root, exc)
                    continue
                while stack and not cancel_event.is_set():
                    try:
                        entry = next(stack[-1])
                    except StopIteration:
                        stack.pop().close()
                        continue
                    except OSError as exc:
                        on_error(getattr(exc, "filename", None) or monitor_root, exc)
                        stack.pop().close()
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                stack.append(os.scandir(entry.path))
                            except OSError as exc:
                                on_error(getattr(exc, "filename", None) or entry.path, exc)
                        elif entry.is_file(follow_symlinks=False):
                            yield entry.path, monitor_root
                    except OSError as exc:
                        on_error(getattr(exc, "filename", None) or entry.path, exc)
            finally:
                while stack:
                    stack.pop().close()

    def build_cleanup_index(
        self,
        request: AutoCleanupRequest,
        cancel_event: Any,
        log: Callable[[str], None],
    ) -> CleanupIndexResult:
        if self._index_repository is None:
            return CleanupIndexResult("建立失败", "清理索引仓库未配置")
        validation = self.validate_auto_request(request)
        fingerprint = self.cleanup_scope_fingerprint(request)
        if not validation.is_valid:
            return CleanupIndexResult(
                "建立失败", "；".join(validation.errors), scope_fingerprint=fingerprint
            )
        if self._index_repository.is_ready(fingerprint):
            return CleanupIndexResult(
                "已就绪",
                indexed_count=self._index_repository.count(fingerprint),
                scope_fingerprint=fingerprint,
            )
        if not self._index_repository.prepare_scope(fingerprint):
            return CleanupIndexResult(
                "建立失败",
                self._index_repository.last_error or "无法初始化清理索引",
                scope_fingerprint=fingerprint,
            )

        formats = {str(ext).strip().lower() for ext in request.formats if str(ext).strip()}
        roots = self.deduplicate_cleanup_roots(request.folders)
        buffer: list[CleanupIndexRecord] = []
        indexed_count = 0
        failed_count = 0

        def scan_failure(path: str, exc: BaseException) -> None:
            nonlocal failed_count
            failed_count += 1
            log(f"⚠️ 清理索引无法访问: {path}: {type(exc).__name__}: {exc}")

        def flush() -> bool:
            nonlocal buffer
            if not buffer:
                return True
            snapshot = tuple(buffer)
            buffer.clear()
            if self._index_repository.upsert_many(snapshot, fingerprint):
                return True
            log(f"❌ 清理索引写入失败: {self._index_repository.last_error}")
            return False

        log("ℹ️ 开始首次建立磁盘清理数据库索引")
        for path, root in self._iter_index_files(roots, cancel_event, scan_failure):
            if cancel_event.is_set() or failed_count >= AUTO_CLEANUP_FAILURE_LIMIT:
                break
            if formats and os.path.splitext(path)[1].lower() not in formats:
                continue
            try:
                stat_result = os.stat(path, follow_symlinks=False)
                normalized = self._index_repository.normalize_path(path)
                buffer.append(
                    CleanupIndexRecord(
                        normalized_path=normalized,
                        path=path,
                        file_name=os.path.basename(path),
                        created_at=self.file_created_at(stat_result),
                        size_bytes=int(stat_result.st_size),
                        root_path=root,
                        source="scan",
                        modified_at_ns=self.file_modified_at_ns(stat_result),
                        file_id=self.file_identity(stat_result),
                    )
                )
                indexed_count += 1
                if len(buffer) >= CLEANUP_INDEX_WRITE_BATCH and not flush():
                    failed_count += 1
                    break
                if indexed_count % 5000 == 0:
                    log(f"ℹ️ 清理数据库建立中：已录入 {indexed_count} 个文件")
            except Exception as exc:
                scan_failure(path, exc)

        if cancel_event.is_set():
            self._index_repository.mark_dirty()
            return CleanupIndexResult(
                "已取消", "应用正在退出，索引建立已取消", indexed_count,
                failed_count, fingerprint,
            )
        if failed_count or not flush():
            self._index_repository.mark_dirty()
            return CleanupIndexResult(
                "建立失败",
                self._index_repository.last_error or f"索引遍历失败 {failed_count} 次",
                indexed_count,
                max(1, failed_count),
                fingerprint,
            )
        if not self._index_repository.mark_ready(fingerprint):
            return CleanupIndexResult(
                "建立失败",
                self._index_repository.last_error or "无法生成索引就绪标志",
                indexed_count,
                failed_count,
                fingerprint,
            )
        log(f"✅ 磁盘清理数据库索引建立完成：共 {indexed_count} 个文件")
        return CleanupIndexResult(
            "建立完成", indexed_count=indexed_count, scope_fingerprint=fingerprint
        )

    def index_generated_file(
        self, path: str, request: AutoCleanupRequest, source: str = "upload"
    ) -> bool:
        if self._index_repository is None or not request.enabled or not path:
            return False
        fingerprint = self.cleanup_scope_fingerprint(request)
        if not self._index_repository.is_scope_current(fingerprint):
            return False
        root, normalized = self._matching_cleanup_root(path, request)
        if not root:
            return True
        formats = {str(ext).strip().lower() for ext in request.formats if str(ext).strip()}
        if formats and os.path.splitext(path)[1].lower() not in formats:
            return True
        try:
            stat_result = os.stat(path, follow_symlinks=False)
            record = CleanupIndexRecord(
                normalized_path=normalized,
                path=path,
                file_name=os.path.basename(path),
                created_at=self.file_created_at(stat_result),
                size_bytes=int(stat_result.st_size),
                root_path=root,
                source=source,
                modified_at_ns=self.file_modified_at_ns(stat_result),
                file_id=self.file_identity(stat_result),
            )
            if self._index_repository.upsert_many((record,), fingerprint):
                return True
        except Exception as exc:
            self._index_repository.last_error = f"{type(exc).__name__}: {exc}"
        self._index_repository.mark_dirty()
        return False

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

    def run_auto_cleanup(
        self,
        request: AutoCleanupRequest,
        cancel_event: Any,
        log: Callable[[str], None],
        delete_mode_provider: Optional[Callable[[], bool]] = None,
    ) -> AutoCleanupResult:
        """仅根据已就绪的 SQLite 索引执行自动清理，删除时动态读取模式。"""
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        audit_started = False
        audit_finished = False
        start_usage = None
        final_usage = None
        deleted_count = failed_count = attempted_bytes = 0
        indexed_count = 0
        result = AutoCleanupResult("任务异常", "任务未正常结束")

        def used_percent(usage: Any) -> Optional[float]:
            if usage is None or usage.total <= 0:
                return None
            return ((usage.total - usage.free) / usage.total) * 100

        def current_use_trash() -> bool:
            if delete_mode_provider is None:
                return bool(request.use_trash)
            return bool(delete_mode_provider())

        def finish(status: str, error: str = "") -> AutoCleanupResult:
            nonlocal audit_finished
            audit_finished = True
            released = (
                max(0, int(final_usage.free - start_usage.free))
                if start_usage is not None and final_usage is not None
                else 0
            )
            self._write_audit(
                "END",
                run_id,
                status=status,
                error=error,
                indexed_count=indexed_count,
                deleted_count=deleted_count,
                failed_count=failed_count,
                start_used_percent=used_percent(start_usage),
                final_used_percent=used_percent(final_usage),
                actual_released_bytes=released,
                attempted_delete_bytes=attempted_bytes,
            )
            final = used_percent(final_usage)
            text = f"{final:.1f}%" if final is not None else "未知"
            log(
                f"✅ 自动清理结束：状态={status}，索引={indexed_count}，"
                f"删除={deleted_count}，失败={failed_count}，实际占用率={text}"
            )
            return AutoCleanupResult(
                status,
                error,
                indexed_count,
                deleted_count,
                failed_count,
                attempted_bytes,
                released,
            )

        try:
            if cancel_event.is_set():
                return AutoCleanupResult("任务异常", "应用正在退出，自动清理已取消")
            if self._index_repository is None:
                return AutoCleanupResult("索引未就绪", "清理索引仓库未配置")
            initial_use_trash = current_use_trash()
            effective_request = replace(request, use_trash=initial_use_trash)
            validation = self.validate_auto_request(effective_request)
            if not validation.is_valid:
                error = "；".join(validation.errors)
                self.record_blocked(effective_request, "路径不可用", error)
                log(f"⚠️ {error}")
                return AutoCleanupResult("路径不可用", error)
            fingerprint = self.cleanup_scope_fingerprint(request)
            if not self._index_repository.is_ready(fingerprint):
                return AutoCleanupResult(
                    "索引未就绪",
                    self._index_repository.last_error or "首次索引尚未完成",
                )
            roots = self.deduplicate_cleanup_roots(request.folders)
            indexed_count = self._index_repository.count(fingerprint)
            start_usage = shutil.disk_usage(roots[0])
            final_usage = start_usage
            start_percent = used_percent(start_usage)
            if start_percent is None or start_percent < request.trigger_percent:
                return AutoCleanupResult("未达到阈值", scanned_count=indexed_count)
            if not self._write_audit(
                "START",
                run_id,
                trigger_source=request.trigger_source,
                folders=list(request.folders),
                effective_roots=roots,
                disk=validation.volume_details[0][1] if validation.volume_details else "",
                used_percent=start_percent,
                trigger_percent=request.trigger_percent,
                target_percent=request.target_percent,
                delete_mode="回收站" if initial_use_trash else "永久删除",
                time_basis="file_modification_time",
                format_filter=sorted(set(request.formats)),
                ordering="sqlite_global_oldest_modified_first",
                indexed_count=indexed_count,
            ):
                log("❌ 自动清理已取消：无法写入 START 审计记录")
                return AutoCleanupResult("任务异常", "清理审计日志写入失败")
            audit_started = True
            log(
                f"⚠️ 磁盘使用率 {start_percent:.1f}% 达到触发阈值 "
                f"{request.trigger_percent}%，将根据数据库按全局修改时间最旧优先清理至 "
                f"{request.target_percent}%"
            )
            while not cancel_event.is_set():
                records = self._index_repository.oldest(
                    fingerprint, CLEANUP_INDEX_READ_LIMIT
                )
                if not records:
                    final_usage = shutil.disk_usage(roots[0])
                    current = used_percent(final_usage)
                    if self._index_repository.last_error:
                        self._index_repository.mark_dirty()
                        return finish("索引异常", self._index_repository.last_error)
                    if current is not None and current > request.target_percent:
                        self._index_repository.mark_dirty()
                        return finish(
                            "索引已耗尽",
                            "数据库已无候选文件，但磁盘仍未达到目标阈值",
                        )
                    return finish("达到目标")

                for record in records:
                    if cancel_event.is_set():
                        return finish("任务异常", "应用正在退出，自动清理已取消")
                    if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT:
                        return finish("失败达到20次")
                    root, normalized = self._matching_cleanup_root(record.path, request)
                    if not root or normalized != record.normalized_path:
                        if not self._index_repository.remove(record.normalized_path, fingerprint):
                            self._index_repository.mark_dirty()
                            return finish("索引异常", self._index_repository.last_error)
                        continue
                    try:
                        stat_result = os.stat(record.path, follow_symlinks=False)
                    except FileNotFoundError:
                        if not self._index_repository.remove(record.normalized_path, fingerprint):
                            self._index_repository.mark_dirty()
                            return finish("索引异常", self._index_repository.last_error)
                        continue
                    except Exception as exc:
                        failed_count += 1
                        if not self._write_audit(
                            "DELETE_FAIL",
                            run_id,
                            path=record.path,
                            file_name=record.file_name,
                            error_type=type(exc).__name__,
                            error=str(exc),
                            failed_count=failed_count,
                        ):
                            return finish("任务异常", "清理审计日志写入失败")
                        continue

                    created_at = self.file_created_at(stat_result)
                    modified_at_ns = self.file_modified_at_ns(stat_result)
                    file_id = self.file_identity(stat_result)
                    changes: list[str] = []
                    if abs(created_at - record.created_at) > 0.001:
                        changes.append("创建时间")
                    if int(stat_result.st_size) != int(record.size_bytes):
                        changes.append("文件大小")
                    if record.modified_at_ns and modified_at_ns != record.modified_at_ns:
                        changes.append("修改时间")
                    if record.file_id and file_id != record.file_id:
                        changes.append("文件标识")
                    if changes:
                        refreshed = CleanupIndexRecord(
                            normalized_path=record.normalized_path,
                            path=record.path,
                            file_name=os.path.basename(record.path),
                            created_at=created_at,
                            size_bytes=int(stat_result.st_size),
                            root_path=root,
                            source="revalidate",
                            modified_at_ns=modified_at_ns,
                            file_id=file_id,
                        )
                        if not self._index_repository.upsert_many((refreshed,), fingerprint):
                            self._index_repository.mark_dirty()
                            return finish("索引异常", self._index_repository.last_error)
                        if not self._write_audit(
                            "DELETE_SKIP_CHANGED",
                            run_id,
                            path=record.path,
                            file_name=record.file_name,
                            changes=changes,
                            indexed_size_bytes=record.size_bytes,
                            current_size_bytes=int(stat_result.st_size),
                            indexed_modified_at_ns=record.modified_at_ns,
                            current_modified_at_ns=modified_at_ns,
                        ):
                            return finish("任务异常", "清理审计日志写入失败")
                        log(f"⚠️ 已跳过索引后发生变化的文件 {record.path}：{', '.join(changes)}")
                        return finish(
                            "索引已刷新",
                            "检测到同路径文件已被替换或修改，已更新索引并跳过本次删除",
                        )

                    before = final_usage or start_usage
                    use_trash = current_use_trash()
                    if use_trash and not trash_supported():
                        error = "回收站不可用，自动清理已停止（避免意外永久删除）"
                        log(f"⚠️ {error}")
                        return finish("路径不可用", error)
                    try:
                        send_to_trash(record.path) if use_trash else os.remove(record.path)
                        deleted_count += 1
                        attempted_bytes += int(stat_result.st_size)
                    except Exception as exc:
                        failed_count += 1
                        if not self._write_audit(
                            "DELETE_FAIL",
                            run_id,
                            path=record.path,
                            file_name=record.file_name,
                            size_bytes=int(stat_result.st_size),
                            created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"),
                            modified_at_ns=modified_at_ns,
                            delete_mode="回收站" if use_trash else "永久删除",
                            error_type=type(exc).__name__,
                            error=str(exc),
                            failed_count=failed_count,
                        ):
                            return finish("任务异常", "清理审计日志写入失败")
                        continue

                    usage_error = ""
                    try:
                        final_usage = shutil.disk_usage(roots[0])
                    except Exception as exc:
                        final_usage = None
                        usage_error = f"{type(exc).__name__}: {exc}"
                    current = used_percent(final_usage)
                    if not self._write_audit(
                        "DELETE_OK",
                        run_id,
                        path=record.path,
                        file_name=record.file_name,
                        size_bytes=int(stat_result.st_size),
                        created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"),
                        modified_at_ns=modified_at_ns,
                        delete_mode="回收站" if use_trash else "永久删除",
                        disk_used_percent=current,
                        disk_usage_error=usage_error,
                        index_source=record.source,
                    ):
                        return finish("任务异常", "清理审计日志写入失败")
                    if not self._index_repository.remove(record.normalized_path, fingerprint):
                        self._index_repository.mark_dirty()
                        return finish("索引异常", self._index_repository.last_error)
                    if usage_error:
                        return finish("路径不可用", usage_error)
                    if use_trash and final_usage.free <= before.free:
                        log("⚠️ 文件移入回收站后磁盘空间未增加，已停止自动清理；请清空回收站或改用永久删除")
                        return finish("回收站未释放空间")
                    if current is not None and current <= request.target_percent:
                        return finish("达到目标")
            return finish("任务异常", "应用正在退出，自动清理已取消")
        except Exception as exc:
            if not audit_started:
                self.record_blocked(request, "任务异常", str(exc))
            elif not audit_finished:
                result = finish("任务异常", str(exc))
            log(f"⚠️ 自动清理任务异常: {exc}")
            return result
        finally:
            if audit_started and not audit_finished:
                finish("任务异常", "任务未正常结束")

    def _write_audit(self, event: str, run_id: str, **fields: Any) -> bool:
        return bool(self._audit_writer and self._audit_writer.write(event, run_id, **fields))
