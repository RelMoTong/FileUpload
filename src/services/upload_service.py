"""上传 Worker 的创建、Qt 线程生命周期与路径探测服务。

本模块负责把纯业务请求转换成一个真实的 ``UploadWorker + QThread`` 会话。它不维护
界面状态；控制器接收本模块桥接后的事件并维护状态机，避免 Worker 直接依赖窗口。
"""

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
from src.services.path_probe_service import PathProbe, PathProbeResult, PathProbeService
from src.workers.upload_worker import UploadWorker


UploadEventCallback = Callable[[str, Dict[str, Any]], None]
logger = logging.getLogger(__name__)


class _UploadEventBridge(QtCore.QObject):
    """把 Worker 信号安全投递到控制器所在的主线程。

    用途：隔离 Worker 的 QThread 与 UI/控制器线程，所有跨线程事件都在这里转为字典。
    输入：Worker 的日志、统计、进度、网络、完成、重复文件等 Qt 信号。
    输出：控制器接收 ``(事件类型, 字段字典)`` 回调。
    关键步骤：每个 Slot 只封装数据并立即调用回调；完成时额外触发资源释放检查。
    风险点：桥接对象必须由服务层持有到线程结束，不能被当作局部变量提前回收。
    """

    def __init__(
        self,
        callback: UploadEventCallback,
        finished_callback: Optional[Callable[[], None]] = None,
    ):
        """保存控制器回调与可选的会话结束回调。"""
        super().__init__()
        self._callback = callback
        self._finished_callback = finished_callback

    @QtCore.Slot(str)
    def on_log(self, message: str) -> None:
        """转发 Worker 日志文本。"""
        self._callback("log", {"message": message})

    @QtCore.Slot(int, int, int, str)
    def on_stats(self, uploaded: int, failed: int, skipped: int, rate: str) -> None:
        """转发累计统计；数值由控制器写入运行时快照。"""
        self._callback(
            "stats",
            {"uploaded": uploaded, "failed": failed, "skipped": skipped, "rate": rate},
        )

    @QtCore.Slot(int, int, str)
    def on_progress(self, current: int, total: int, filename: str) -> None:
        """转发本轮文件进度。"""
        self._callback(
            "progress", {"current": current, "total": total, "filename": filename}
        )

    @QtCore.Slot(str, int)
    def on_file_progress(self, filename: str, progress: int) -> None:
        """转发单文件的百分比进度。"""
        self._callback("file_progress", {"filename": filename, "progress": progress})

    @QtCore.Slot(str)
    def on_network_status(self, status: str) -> None:
        """转发网络健康状态。"""
        self._callback("network_status", {"status": status})

    @QtCore.Slot()
    def on_finished(self) -> None:
        """先通知控制器会话结束，再异步检查能否安全释放 Worker 引用。"""
        self._callback("finished", {})
        if self._finished_callback is not None:
            self._finished_callback()

    @QtCore.Slot(str)
    def on_status(self, status: str) -> None:
        """转发 Worker 的运行/暂停/停止状态文本。"""
        self._callback("status", {"status": status})

    @QtCore.Slot(object)
    def on_duplicate(self, payload: object) -> None:
        """转发重复文件询问对象；对象中包含解除 Worker 等待的 Event。"""
        self._callback("duplicate", {"payload": payload})

    @QtCore.Slot(str, str)
    def on_upload_error(self, filename: str, message: str) -> None:
        """转发单文件上传失败信息。"""
        self._callback("upload_error", {"filename": filename, "message": message})

    @QtCore.Slot(float, float, int)
    def on_disk_warning(
        self, target_percent: float, backup_percent: float, threshold: int
    ) -> None:
        """转发上传 Worker 检测到的磁盘空间不足告警。"""
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
        """转发“需要统一自动清理引擎判断”的通知，而非 Worker 自行删除。"""
        self._callback("disk_cleanup_needed", {})

    @QtCore.Slot(str, str)
    def on_local_file_generated(self, path: str, source: str) -> None:
        """转发 SMB 上传或归档产生的本地文件路径，供其他模块记录。"""
        self._callback(
            "local_file_generated", {"path": path, "source": source}
        )


class UploadService:
    """持有一个上传会话的真实 ``UploadWorker``、``QThread`` 和事件桥。

    用途：集中管理 Worker 创建、线程连接、停止请求和延迟资源释放。
    输入：上传请求、控制器回调、可替换的工厂和路径探测服务。
    输出：启动/暂停/停止命令结果，以及经桥接转发的异步事件。
    关键步骤：校验请求、创建三类 Qt 对象、使用队列连接信号、线程停止后再释放强引用。
    风险点：网络或归档内部线程可能晚于 QThread 停止；不能提前清空 Worker 引用。
    """

    def __init__(
        self,
        worker_factory: Callable[..., Any] = UploadWorker,
        thread_factory: Callable[[], Any] = QtCore.QThread,
        path_probe_service: Optional[PathProbeService] = None,
    ) -> None:
        """初始化可注入的 Worker/线程工厂，以及独立的异步路径探测服务。"""
        self._worker_factory = worker_factory
        self._thread_factory = thread_factory
        self._worker: Any = None
        self._thread: Any = None
        self._bridge: Optional[_UploadEventBridge] = None
        self._release_check_scheduled = False
        self._path_probe_service = path_probe_service or PathProbeService()

    @staticmethod
    def validate_request(request: UploadTaskRequest) -> UploadValidationResult:
        """进行不阻塞网络的上传请求校验。

        用途：启动前尽早发现空路径、本地不存在路径与源/目标/备份互相嵌套的问题。
        输入：协议、源/目标/备份路径和备份开关组成的请求。
        输出：包含全部问题的校验结果，而不是只返回第一个错误。
        关键步骤：依据协议确定需要的路径；本地路径做存在性检查；统一检测路径关系冲突。
        风险点：UNC 路径的实际可达性可能阻塞，必须由 ``probe_request_async`` 在后台检查。
        """
        errors: list[str] = []
        protocol = str(request.upload_protocol or "smb").lower()
        uses_smb_target = protocol in {"smb", "both"}
        paths = [("源文件夹", request.source)]
        if uses_smb_target:
            paths.append(("目标文件夹", request.target))
        for label, path in paths:
            if not path:
                errors.append(f"{label}路径为空")
            elif not UploadService._is_remote_path(path) and not os.path.exists(path):
                errors.append(f"{label}不存在: {path}")

        if request.enable_backup:
            if not request.backup:
                errors.append("备份文件夹路径为空")
            elif not UploadService._is_remote_path(request.backup) and not os.path.exists(request.backup):
                errors.append(f"备份文件夹不存在: {request.backup}")

        local_paths = [("源文件夹", request.source)]
        if uses_smb_target:
            local_paths.append(("目标文件夹", request.target))
        if request.enable_backup:
            local_paths.append(("备份文件夹", request.backup))
        for conflict in find_local_path_conflicts(local_paths):
            errors.append(describe_local_path_conflict(conflict))
        return UploadValidationResult(tuple(errors))

    @staticmethod
    def _is_remote_path(path: str) -> bool:
        """判断 UNC 路径；此处只用于决定同步校验是否跳过网络 I/O。"""
        value = str(path).replace("/", "\\")
        return value.startswith("\\\\")

    @staticmethod
    def path_probes_for(request: UploadTaskRequest) -> tuple[PathProbe, ...]:
        """只构建需要后台 I/O 的路径探测项目，路径关系规则仍由同步校验处理。

        输入：上传请求。
        输出：源目录（只读）以及 SMB 目标/备份目录（需写入）的探测列表。
        风险点：FTP-only 不需要本地目标目录写入探测；错误探测会制造不必要的现场失败提示。
        """
        protocol = str(request.upload_protocol or "smb").lower()
        probes = [PathProbe("源文件夹", request.source)]
        if protocol in {"smb", "both"}:
            probes.append(PathProbe("目标文件夹", request.target, require_write=True))
        if request.enable_backup:
            probes.append(PathProbe("备份文件夹", request.backup, require_write=True))
        return tuple(probes)

    def probe_request_async(
        self,
        request: UploadTaskRequest,
        callback: Callable[[PathProbeResult], None],
        *,
        timeout: float = 2.0,
    ) -> int:
        """在调用方线程之外探测路径可用性，返回本次提交的探测数量。"""
        return self._path_probe_service.probe(
            self.path_probes_for(request), callback, timeout=timeout
        )

    def cancel_path_probes(self) -> int:
        """取消仍未完成的路径探测任务，并返回取消数量。"""
        return self._path_probe_service.cancel()

    def start(
        self,
        request: UploadTaskRequest,
        event_callback: UploadEventCallback,
    ) -> UploadCommandResult:
        """创建 Worker 会话并启动 QThread。

        用途：将所有长时间上传工作移出 GUI 线程。
        输入：上传请求与控制器事件回调。
        输出：成功时仅表示线程已启动；进度和结果由桥接事件异步返回。
        关键步骤：先校验和防重入、构造 Worker、创建线程/桥、连接所有信号、保存强引用、启动线程。
        风险点：Worker 只能移到 QThread 后执行；直接在 UI 调用 ``worker.start`` 会冻结界面。
        """
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
            # Worker 归属后台 QThread；桥接对象留在主线程，QueuedConnection 让 UI 更新
            # 始终回到 Qt 主事件循环处理。
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
            # Worker 结束后请求 QThread 退出；实际释放资源要等内部线程也结束后再进行。
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
        """请求当前 Worker 增加“手动暂停”原因；没有 Worker 时返回可展示错误。"""
        if self._worker is None:
            return UploadCommandResult(False, "上传任务未运行")
        try:
            self._worker.pause()
            return UploadCommandResult(True, "上传任务已暂停")
        except Exception as exc:
            return UploadCommandResult(False, str(exc))

    def resume(self) -> UploadCommandResult:
        """请求 Worker 移除“手动暂停”原因；网络/磁盘暂停原因由 Worker 自己保留。"""
        if self._worker is None:
            return UploadCommandResult(False, "上传任务未运行")
        try:
            self._worker.resume()
            return UploadCommandResult(True, "上传任务已恢复")
        except Exception as exc:
            return UploadCommandResult(False, str(exc))

    def stop(self) -> UploadCommandResult:
        """非阻塞请求停止上传，并安排稍后的资源释放检查。

        用途：供用户点击停止时快速返回界面。
        输入：无。
        输出：Worker 接受停止请求时返回成功，不保证所有内部线程已经退出。
        关键步骤：调用 Worker 停止、请求 QThread 退出、安排轮询式释放检查。
        风险点：不要在此处 ``wait``；网络路径操作或归档线程可能暂未返回。
        """
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
        """将 UI 的重复文件选择写入共享结果对象，并设置 Event 唤醒 Worker。

        用途：Worker 在 ask 策略下等待 UI 选择时，不直接操作 UI。
        输入：桥接转发的载荷、单次选择和是否应用到后续重复项。
        输出：合法载荷中的结果字典被更新，等待事件被设置。
        风险点：载荷来自异步事件，必须做类型检查；无效载荷只能安全忽略。
        """
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
        """尽力读取当前 FTP 客户端状态；无客户端或读取失败时返回空字典。"""
        try:
            client = getattr(self._worker, "ftp_client", None)
            return client.get_status() if client is not None else {}
        except Exception:
            return {}

    def archive_queue_size(self) -> int:
        """尽力读取归档队列大小；诊断读取失败不影响上传会话。"""
        try:
            queue = getattr(self._worker, "archive_queue", None)
            return int(queue.qsize()) if queue is not None else 0
        except Exception:
            return 0

    @property
    def worker_pause_reasons(self) -> frozenset[str]:
        """向控制器暴露 Worker 的有效暂停原因，用于避免错误显示“已恢复”。"""
        try:
            return frozenset(getattr(self._worker, "pause_reasons", ()))
        except Exception:
            return frozenset()

    def request_stop_all(self) -> UploadCommandResult:
        """非阻塞请求停止当前上传及其内部后台任务。"""
        return self.stop()

    @property
    def has_running_workers(self) -> bool:
        """检查会话的 QThread 和 Worker 内部线程；完全停止后才释放强引用。"""
        running = self._workers_running_raw()
        if not running and self._worker is not None:
            self._release_resources()
        return running

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """在应用整体退出时取消路径探测并有限等待上传线程。

        用途：比普通 ``stop`` 更严格地收尾，但仍避免无限等待造成退出假死。
        输入：允许等待 QThread 的最长毫秒数。
        输出：线程及时结束则释放资源；超时则保留引用并安排后续检查。
        关键步骤：先停止路径探测、请求 Worker 停止、请求线程退出、有限等待、检查内部任务。
        风险点：超时不能强制销毁对象；保留引用比 Qt 在运行中析构 QThread 更安全。
        """
        self._path_probe_service.shutdown()
        worker = self._worker
        thread = self._thread
        if worker is not None:
            try:
                try:
                    worker.stop(wait=False, timeout=max(timeout_ms, 0) / 1000.0)
                except TypeError:
                    # 测试替身和旧版 Worker 可能只暴露无参数的 stop()。
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
        """不产生副作用地检查 QThread 或 Worker 内部任务是否仍在运行。"""
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
        """在 Qt 主事件循环中轮询会话是否完全停止，停止后再释放引用。

        用途：处理“Worker 已发 finished，但归档/网络子线程仍在收尾”的短暂阶段。
        输入：无。
        输出：完全停止时调用 ``_release_resources``；未停止时 50ms 后继续检查。
        关键步骤：防止重复安排、确认存在 QApplication、使用 QTimer 递归排队检查。
        风险点：不能用忙等或阻塞 sleep，占用 UI 线程会延迟其它 Qt 事件。
        """
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
        """仅在没有任何 Worker 任务运行时清空会话强引用。"""
        if self._workers_running_raw():
            return
        self._worker = None
        self._thread = None
        self._bridge = None
        self._release_check_scheduled = False
