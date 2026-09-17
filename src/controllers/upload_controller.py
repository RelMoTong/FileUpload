"""上传控制器：维护上传状态机并协调 Worker 事件。

控制器不扫描文件、不复制文件，也不直接操作 Qt 线程。它只负责检查“当前状态下
允许什么操作”，调用服务层，并把后台事件同步到可供界面读取的运行时状态。
"""

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
from src.models.path_probe import PathProbeResult


class UploadLifecycleService(Protocol):
    """上传服务向控制器提供的生命周期接口约定。

    用途：让控制器依赖抽象行为，测试时可以替换真实 QThread/Worker。
    输入：上传请求、暂停/停止命令、路径探测回调和重复文件处理结果。
    输出：同步命令结果与异步事件。
    关键步骤：接口覆盖启动、暂停、停止、路径探测、重复处理和有序退出。
    风险点：控制器不能绕过该接口操作 Worker，否则状态机和线程生命周期会失去一致性。
    """
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
    def probe_request_async(
        self,
        request: UploadTaskRequest,
        callback: Callable[[PathProbeResult], None],
        *,
        timeout: float = 2.0,
    ) -> int: ...
    def cancel_path_probes(self) -> int: ...
    @property
    def has_running_workers(self) -> bool: ...
    def shutdown(self, timeout_ms: int = 3000) -> None: ...


class UploadController:
    """上传功能唯一的状态机入口。

    用途：确保上传只在 STOPPED、RUNNING、PAUSED 三种受控状态间转换。
    输入：界面命令和服务层发出的结构化事件。
    输出：更新后的运行时状态，以及转发给 UI 的统一事件字典。
    关键步骤：先校验状态转换，再调用服务；服务成功后才更新状态并通知界面。
    风险点：不能仅靠按钮禁用来限制操作；非法状态转换必须在这里被拒绝。
    """
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
        """保存服务层和运行时状态；状态的写入权只属于此控制器。"""
        self._service = service
        self._state = state
        self._event_listener: Optional[Callable[[dict], None]] = None

    @property
    def state(self) -> UploadRuntimeState:
        """返回运行时状态快照，避免调用方直接修改控制器内部状态。"""
        return self._state.snapshot()

    def set_event_listener(self, listener: Callable[[dict], None]) -> None:
        """登记 UI 的统一事件接收函数。"""
        self._event_listener = listener

    def validate_request(self, request: UploadTaskRequest) -> UploadValidationResult:
        """转交上传前的同步路径和配置校验。"""
        return self._service.validate_request(request)

    def probe_request_async(
        self,
        request: UploadTaskRequest,
        callback: Callable[[PathProbeResult], None],
        *,
        timeout: float = 2.0,
    ) -> int:
        """将可能阻塞的路径可达性检测移到后台，返回本次探测任务数量。"""
        return self._service.probe_request_async(request, callback, timeout=timeout)

    def cancel_path_probes(self) -> int:
        """取消仍未完成的路径探测，供设置变更或窗口关闭时调用。"""
        return self._service.cancel_path_probes()

    def start(self, request: UploadTaskRequest) -> UploadCommandResult:
        """校验并启动上传，将状态从 STOPPED 过渡到 RUNNING。

        用途：防止重复启动，并在 Worker 确实创建成功后才让 UI 显示“运行中”。
        输入：已由界面收集的上传请求。
        输出：启动命令结果；成功时重置统计并发布初始状态事件。
        关键步骤：检查当前状态、校验请求、调用服务、创建新的运行时状态、通知 UI。
        风险点：服务启动失败时不能提前修改状态，否则 UI 会显示运行但没有任何 Worker。
        """
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
        """仅在 RUNNING 状态请求暂停；成功后状态转为 PAUSED。"""
        if self._state.status is not UploadStatus.RUNNING:
            return UploadCommandResult(False, "只有运行中的任务可以暂停")
        result = self._service.pause()
        if result.success:
            self._transition(UploadStatus.PAUSED)
        return result

    def resume(self) -> UploadCommandResult:
        """仅在 PAUSED 状态请求恢复，并保留网络/磁盘等其他暂停原因。

        手动恢复只会清除手动暂停原因；若 Worker 仍因网络或磁盘不足暂停，控制器不会
        错误地提前向 UI 发布 RUNNING 状态。
        """
        if self._state.status is not UploadStatus.PAUSED:
            return UploadCommandResult(False, "只有暂停的任务可以恢复")
        result = self._service.resume()
        if result.success:
            reasons = getattr(self._service, "worker_pause_reasons", frozenset())
            if not reasons:
                self._transition(UploadStatus.RUNNING)
        return result

    def stop(self) -> UploadCommandResult:
        """请求停止当前上传；服务确认接受命令后将状态转为 STOPPED。"""
        result = self._service.stop()
        if result.success and self._state.status is not UploadStatus.STOPPED:
            self._transition(UploadStatus.STOPPED)
        return result

    def request_stop_all(self) -> UploadCommandResult:
        """供应用退出路径使用的非阻塞停止请求。"""
        result = self._service.request_stop_all()
        if result.success and self._state.status is not UploadStatus.STOPPED:
            self._transition(UploadStatus.STOPPED)
        return result

    @property
    def has_running_workers(self) -> bool:
        """返回服务层是否仍持有运行中的 QThread 或内部后台线程。"""
        return self._service.has_running_workers

    def set_running(self, running: bool) -> None:
        """兼容旧调用方的状态设置入口，实际仍严格遵守状态机转换规则。

        用途：保留历史集成/测试代码的调用方式。
        输入：期望是否运行。
        输出：只有合法转换时才更新状态。
        关键步骤：把布尔值换算成目标状态，再检查允许转换集合。
        风险点：不能直接写 ``self._state.status``，否则会跳过状态事件和网络状态重置。
        """
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
        """把 UI 对重复文件的选择交回服务层，解除 Worker 的等待。"""
        self._service.resolve_duplicate(payload, choice, apply_all)

    def ftp_client_status(self) -> Dict[str, Any]:
        """读取 FTP 客户端状态，供 UI 仅展示诊断信息。"""
        return self._service.ftp_client_status()

    def archive_queue_size(self) -> int:
        """读取归档队列长度并写入运行时快照，用于显示上传后待归档工作量。"""
        size = self._service.archive_queue_size()
        self._state.archive_queue_size = size
        return size

    def shutdown(self) -> None:
        """应用整体退出时关闭服务层并复位状态机。

        普通“停止上传”使用 ``stop``；此方法由生命周期控制器调用，服务层会负责
        取消路径探测、请求 Worker 停止并有限等待线程回收。
        """
        self._service.shutdown()
        self._state.status = UploadStatus.STOPPED

    def _transition(self, target: UploadStatus) -> bool:
        """执行一次合法状态转换，并把新状态和必要的派生状态通知 UI。

        用途：集中维护状态机，避免多个事件处理分支各自修改状态。
        输入：目标枚举状态。
        输出：转换成功返回 ``True``；相同状态也视为成功；非法转换返回 ``False``。
        关键步骤：读取当前状态、查允许转换表、更新状态、停止时重置网络状态、发送事件。
        风险点：STOPPED 后必须把网络状态恢复为 UNKNOWN，否则下次会话会显示旧网络结果。
        """
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
        """消费服务层事件，更新对应运行时字段后再转发给 UI。

        用途：把 Worker 信号转换为 UI 可稳定读取的单一状态来源。
        输入：事件名称和事件字段。
        输出：运行时状态更新；除状态事件外，原事件继续通知 UI。
        关键步骤：按事件类别更新统计、进度、网络、错误或状态机，再调用 ``_notify``。
        风险点：未知状态字符串必须降级为 UNKNOWN/忽略，不能因一个异常事件中断上传控制流程。
        """
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
        """向已登记 UI 监听器发送包含统一 ``type`` 字段的事件。"""
        if self._event_listener is not None:
            self._event_listener({"type": kind, **payload})
