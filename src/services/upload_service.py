"""Upload worker creation and Qt thread lifecycle service."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

from PySide6 import QtCore

from src.models import UploadCommandResult, UploadTaskRequest, UploadValidationResult
from src.services.path_safety import (
    describe_local_path_conflict,
    find_local_path_conflicts,
)
from src.workers.upload_worker import UploadWorker


UploadEventCallback = Callable[[str, Dict[str, Any]], None]
logger = logging.getLogger(__name__)


class _UploadEventBridge(QtCore.QObject):
    """Deliver worker signals to the controller on the bridge's UI thread."""

    def __init__(
        self,
        callback: UploadEventCallback,
        finished_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._callback = callback
        self._finished_callback = finished_callback

    @QtCore.Slot(str)
    def on_log(self, message: str) -> None:
        self._callback("log", {"message": message})

    @QtCore.Slot(int, int, int, str)
    def on_stats(self, uploaded: int, failed: int, skipped: int, rate: str) -> None:
        self._callback(
            "stats",
            {"uploaded": uploaded, "failed": failed, "skipped": skipped, "rate": rate},
        )

    @QtCore.Slot(int, int, str)
    def on_progress(self, current: int, total: int, filename: str) -> None:
        self._callback(
            "progress", {"current": current, "total": total, "filename": filename}
        )

    @QtCore.Slot(str, int)
    def on_file_progress(self, filename: str, progress: int) -> None:
        self._callback("file_progress", {"filename": filename, "progress": progress})

    @QtCore.Slot(str)
    def on_network_status(self, status: str) -> None:
        self._callback("network_status", {"status": status})

    @QtCore.Slot()
    def on_finished(self) -> None:
        self._callback("finished", {})
        if self._finished_callback is not None:
            self._finished_callback()

    @QtCore.Slot(str)
    def on_status(self, status: str) -> None:
        self._callback("status", {"status": status})

    @QtCore.Slot(object)
    def on_duplicate(self, payload: object) -> None:
        self._callback("duplicate", {"payload": payload})

    @QtCore.Slot(str, str)
    def on_upload_error(self, filename: str, message: str) -> None:
        self._callback("upload_error", {"filename": filename, "message": message})

    @QtCore.Slot(float, float, int)
    def on_disk_warning(
        self, target_percent: float, backup_percent: float, threshold: int
    ) -> None:
        self._callback(
            "disk_warning",
            {
                "target_percent": target_percent,
                "backup_percent": backup_percent,
                "threshold": threshold,
            },
        )

    @QtCore.Slot()
    def on_disk_cleanup_needed(self) -> None:
        self._callback("disk_cleanup_needed", {})

    @QtCore.Slot(str, str)
    def on_local_file_generated(self, path: str, source: str) -> None:
        self._callback(
            "local_file_generated", {"path": path, "source": source}
        )


class UploadService:
    """Own the concrete UploadWorker and QThread for one upload session."""

    def __init__(
        self,
        worker_factory: Callable[..., Any] = UploadWorker,
        thread_factory: Callable[[], Any] = QtCore.QThread,
    ) -> None:
        self._worker_factory = worker_factory
        self._thread_factory = thread_factory
        self._worker: Any = None
        self._thread: Any = None
        self._bridge: Optional[_UploadEventBridge] = None
        self._release_check_scheduled = False

    @staticmethod
    def validate_request(request: UploadTaskRequest) -> UploadValidationResult:
        errors: list[str] = []
        protocol = str(request.upload_protocol or "smb").lower()
        uses_smb_target = protocol in {"smb", "both"}
        paths = [("源文件夹", request.source)]
        if uses_smb_target:
            paths.append(("目标文件夹", request.target))
        for label, path in paths:
            if not path:
                errors.append(f"{label}路径为空")
            elif not os.path.exists(path):
                errors.append(f"{label}不存在: {path}")

        if request.enable_backup:
            if not request.backup:
                errors.append("备份文件夹路径为空")
            elif not os.path.exists(request.backup):
                errors.append(f"备份文件夹不存在: {request.backup}")

        local_paths = [("源文件夹", request.source)]
        if uses_smb_target:
            local_paths.append(("目标文件夹", request.target))
        if request.enable_backup:
            local_paths.append(("备份文件夹", request.backup))
        for conflict in find_local_path_conflicts(local_paths):
            errors.append(describe_local_path_conflict(conflict))
        return UploadValidationResult(tuple(errors))

    def start(
        self,
        request: UploadTaskRequest,
        event_callback: UploadEventCallback,
    ) -> UploadCommandResult:
        validation = self.validate_request(request)
        if not validation.is_valid:
            return UploadCommandResult(False, errors=validation.errors)
        if self._worker is not None:
            return UploadCommandResult(False, "上传任务已存在")

        try:
            worker = self._worker_factory(
                request.source,
                request.target,
                request.backup,
                request.interval,
                request.mode,
                request.disk_threshold_percent,
                request.retry_count,
                list(request.filters),
                request.app_dir,
                request.enable_deduplication,
                request.hash_algorithm,
                request.duplicate_strategy,
                request.network_check_interval,
                request.network_auto_pause,
                request.network_auto_resume,
                request.enable_auto_delete,
                request.auto_delete_threshold,
                request.auto_delete_target_percent,
                request.upload_protocol,
                request.ftp_client_config,
                request.enable_backup,
                request.limit_upload_rate,
                request.max_upload_rate_mbps,
                request.file_upload_delay_seconds,
            )
            thread = self._thread_factory()
            bridge = _UploadEventBridge(event_callback, self._schedule_release_check)
            worker.moveToThread(thread)
            thread.started.connect(worker.start)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.log.connect(bridge.on_log, queued)
            worker.stats.connect(bridge.on_stats, queued)
            worker.progress.connect(bridge.on_progress, queued)
            worker.file_progress.connect(bridge.on_file_progress, queued)
            worker.network_status.connect(bridge.on_network_status, queued)
            worker.finished.connect(bridge.on_finished, queued)
            worker.status.connect(bridge.on_status, queued)
            worker.ask_user_duplicate.connect(bridge.on_duplicate, queued)
            worker.upload_error.connect(bridge.on_upload_error, queued)
            worker.disk_warning.connect(bridge.on_disk_warning, queued)
            worker.disk_cleanup_needed.connect(bridge.on_disk_cleanup_needed, queued)
            generated_signal = getattr(worker, "local_file_generated", None)
            if generated_signal is not None:
                generated_signal.connect(bridge.on_local_file_generated, queued)
            worker.finished.connect(thread.quit)
            self._worker = worker
            self._thread = thread
            self._bridge = bridge
            thread.start()
            return UploadCommandResult(True, "上传任务已启动")
        except Exception as exc:
            self._worker = None
            self._thread = None
            self._bridge = None
            return UploadCommandResult(False, str(exc))

    def pause(self) -> UploadCommandResult:
        if self._worker is None:
            return UploadCommandResult(False, "上传任务未运行")
        try:
            self._worker.pause()
            return UploadCommandResult(True, "上传任务已暂停")
        except Exception as exc:
            return UploadCommandResult(False, str(exc))

    def resume(self) -> UploadCommandResult:
        if self._worker is None:
            return UploadCommandResult(False, "上传任务未运行")
        try:
            self._worker.resume()
            return UploadCommandResult(True, "上传任务已恢复")
        except Exception as exc:
            return UploadCommandResult(False, str(exc))

    def stop(self) -> UploadCommandResult:
        if self._worker is None:
            return UploadCommandResult(True, "上传任务未运行")
        try:
            self._worker.stop()
            if self._thread is not None:
                self._thread.quit()
            self._schedule_release_check()
            return UploadCommandResult(True, "上传任务已停止")
        except Exception as exc:
            return UploadCommandResult(False, str(exc))

    def resolve_duplicate(self, payload: Any, choice: str, apply_all: bool = False) -> None:
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if isinstance(result, dict):
            result["choice"] = choice
            result["apply_all"] = apply_all
        event = payload.get("event")
        if event is not None and hasattr(event, "set"):
            event.set()

    def ftp_client_status(self) -> Dict[str, Any]:
        try:
            client = getattr(self._worker, "ftp_client", None)
            return client.get_status() if client is not None else {}
        except Exception:
            return {}

    def archive_queue_size(self) -> int:
        try:
            queue = getattr(self._worker, "archive_queue", None)
            return int(queue.qsize()) if queue is not None else 0
        except Exception:
            return 0

    def request_stop_all(self) -> UploadCommandResult:
        """非阻塞请求停止当前上传及其内部后台任务。"""
        return self.stop()

    @property
    def has_running_workers(self) -> bool:
        running = self._workers_running_raw()
        if not running and self._worker is not None:
            self._release_resources()
        return running

    def shutdown(self, timeout_ms: int = 3000) -> None:
        worker = self._worker
        thread = self._thread
        if worker is not None:
            try:
                try:
                    worker.stop(wait=False, timeout=max(timeout_ms, 0) / 1000.0)
                except TypeError:
                    # Test doubles and legacy workers may expose stop() only.
                    worker.stop()
            except Exception:
                pass
        if thread is not None:
            thread.quit()
            if thread.isRunning() and not thread.wait(timeout_ms):
                logger.error(
                    "上传 QThread 在 %dms 内未停止，保留 Worker/QThread 引用并继续请求退出",
                    timeout_ms,
                )
                thread.quit()
                self._schedule_release_check()
                return
        if self._workers_running_raw():
            logger.error(
                "上传 Worker 内部后台任务在 %dms 后仍未停止，保留资源引用",
                timeout_ms,
            )
            self._schedule_release_check()
            return
        self._release_resources()

    def _workers_running_raw(self) -> bool:
        thread = self._thread
        if thread is not None:
            try:
                if thread.isRunning():
                    return True
            except Exception:
                return True
        worker = self._worker
        if worker is not None and hasattr(worker, "has_running_tasks"):
            try:
                if worker.has_running_tasks():
                    return True
            except Exception:
                return True
        return False

    def _schedule_release_check(self) -> None:
        if self._worker is None or self._release_check_scheduled:
            return
        self._release_check_scheduled = True

        def check() -> None:
            if not self._workers_running_raw():
                self._release_resources()
                return
            QtCore.QTimer.singleShot(50, check)

        app = QtCore.QCoreApplication.instance()
        if app is None:
            self._release_check_scheduled = False
            return
        QtCore.QTimer.singleShot(0, check)

    def _release_resources(self) -> None:
        if self._workers_running_raw():
            return
        self._worker = None
        self._thread = None
        self._bridge = None
        self._release_check_scheduled = False
