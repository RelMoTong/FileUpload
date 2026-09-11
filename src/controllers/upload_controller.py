"""Controller-owned upload state machine and worker event coordination."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Protocol

from src.models import (
    NetworkStatus,
    UploadCommandResult,
    UploadRuntimeState,
    UploadStatus,
    UploadTaskRequest,
    UploadValidationResult,
)


class UploadLifecycleService(Protocol):
    def validate_request(self, request: UploadTaskRequest) -> UploadValidationResult: ...
    def start(
        self,
        request: UploadTaskRequest,
        event_callback: Callable[[str, Dict[str, Any]], None],
    ) -> UploadCommandResult: ...
    def pause(self) -> UploadCommandResult: ...
    def resume(self) -> UploadCommandResult: ...
    def stop(self) -> UploadCommandResult: ...
    def resolve_duplicate(
        self, payload: Any, choice: str, apply_all: bool = False
    ) -> None: ...
    def ftp_client_status(self) -> Dict[str, Any]: ...
    def archive_queue_size(self) -> int: ...
    def request_stop_all(self) -> UploadCommandResult: ...
    @property
    def has_running_workers(self) -> bool: ...
    def shutdown(self, timeout_ms: int = 3000) -> None: ...


class UploadController:
    _ALLOWED_TRANSITIONS = {
        UploadStatus.STOPPED: {UploadStatus.RUNNING},
        UploadStatus.RUNNING: {UploadStatus.PAUSED, UploadStatus.STOPPED},
        UploadStatus.PAUSED: {UploadStatus.RUNNING, UploadStatus.STOPPED},
    }

    def __init__(
        self,
        service: UploadLifecycleService,
        state: UploadRuntimeState,
    ) -> None:
        self._service = service
        self._state = state
        self._event_listener: Optional[Callable[[dict], None]] = None

    @property
    def state(self) -> UploadRuntimeState:
        return self._state.snapshot()

    def set_event_listener(self, listener: Callable[[dict], None]) -> None:
        self._event_listener = listener

    def validate_request(self, request: UploadTaskRequest) -> UploadValidationResult:
        return self._service.validate_request(request)

    def start(self, request: UploadTaskRequest) -> UploadCommandResult:
        if self._state.status is not UploadStatus.STOPPED:
            return UploadCommandResult(False, "上传任务已在运行")
        validation = self.validate_request(request)
        if not validation.is_valid:
            return UploadCommandResult(False, errors=validation.errors)
        result = self._service.start(request, self._handle_service_event)
        if result.success:
            self._state = UploadRuntimeState(
                status=UploadStatus.RUNNING,
                start_time=time.time(),
            )
            self._notify(
                "stats", uploaded=0, failed=0, skipped=0, rate="0 MB/s"
            )
            self._notify(
                "network_status", status=NetworkStatus.UNKNOWN.value
            )
            self._notify("status", status=UploadStatus.RUNNING.value)
        return result

    def pause(self) -> UploadCommandResult:
        if self._state.status is not UploadStatus.RUNNING:
            return UploadCommandResult(False, "只有运行中的任务可以暂停")
        result = self._service.pause()
        if result.success:
            self._transition(UploadStatus.PAUSED)
        return result

    def resume(self) -> UploadCommandResult:
        if self._state.status is not UploadStatus.PAUSED:
            return UploadCommandResult(False, "只有暂停的任务可以恢复")
        result = self._service.resume()
        if result.success:
            self._transition(UploadStatus.RUNNING)
        return result

    def stop(self) -> UploadCommandResult:
        result = self._service.stop()
        if result.success and self._state.status is not UploadStatus.STOPPED:
            self._transition(UploadStatus.STOPPED)
        return result

    def request_stop_all(self) -> UploadCommandResult:
        result = self._service.request_stop_all()
        if result.success and self._state.status is not UploadStatus.STOPPED:
            self._transition(UploadStatus.STOPPED)
        return result

    @property
    def has_running_workers(self) -> bool:
        return self._service.has_running_workers

    def set_running(self, running: bool) -> None:
        """Compatibility setter; state remains controller-owned."""
        target = UploadStatus.RUNNING if running else UploadStatus.STOPPED
        if target is self._state.status:
            return
        if target is UploadStatus.RUNNING and self._state.status is UploadStatus.PAUSED:
            self._transition(target)
        elif target in self._ALLOWED_TRANSITIONS.get(self._state.status, set()):
            self._transition(target)

    def resolve_duplicate(
        self, payload: Any, choice: str, apply_all: bool = False
    ) -> None:
        self._service.resolve_duplicate(payload, choice, apply_all)

    def ftp_client_status(self) -> Dict[str, Any]:
        return self._service.ftp_client_status()

    def archive_queue_size(self) -> int:
        size = self._service.archive_queue_size()
        self._state.archive_queue_size = size
        return size

    def shutdown(self) -> None:
        self._service.shutdown()
        self._state.status = UploadStatus.STOPPED

    def _transition(self, target: UploadStatus) -> bool:
        current = self._state.status
        if target is current:
            return True
        if target not in self._ALLOWED_TRANSITIONS.get(current, set()):
            return False
        self._state.status = target
        if target is UploadStatus.STOPPED:
            self._state.network_status = NetworkStatus.UNKNOWN
            self._notify(
                "network_status", status=NetworkStatus.UNKNOWN.value
            )
        self._notify("status", status=target.value)
        return True

    def _handle_service_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind == "stats":
            self._state.uploaded = int(payload.get("uploaded", 0))
            self._state.failed = int(payload.get("failed", 0))
            self._state.skipped = int(payload.get("skipped", 0))
            self._state.rate = str(payload.get("rate", "0 MB/s"))
        elif kind == "progress":
            self._state.progress_current = int(payload.get("current", 0))
            self._state.progress_total = int(payload.get("total", 0))
            self._state.current_filename = str(payload.get("filename", ""))
        elif kind == "file_progress":
            self._state.current_filename = str(payload.get("filename", ""))
            self._state.file_progress = int(payload.get("progress", 0))
        elif kind == "network_status":
            try:
                self._state.network_status = NetworkStatus(str(payload.get("status", "unknown")))
            except ValueError:
                self._state.network_status = NetworkStatus.UNKNOWN
        elif kind == "status":
            try:
                self._transition(UploadStatus(str(payload.get("status", "stopped"))))
            except ValueError:
                return
            return
        elif kind == "upload_error":
            self._state.last_error = str(payload.get("message", ""))
        elif kind == "finished":
            if self._state.status is not UploadStatus.STOPPED:
                self._transition(UploadStatus.STOPPED)
        self._notify(kind, **payload)

    def _notify(self, kind: str, **payload: Any) -> None:
        if self._event_listener is not None:
            self._event_listener({"type": kind, **payload})
