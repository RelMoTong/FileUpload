"""
文件名：src/controllers/lifecycle_controller.py
文件作用：控制器层的“lifecycle_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

退出文字流程图：
界面停止新请求 → 请求上传/清理/FTP 停止 → Qt 事件循环轮询 Worker →
确认退出事件 → 清理任务、上传任务、FTP、运行时参与者依次 shutdown → 释放窗口资源；
任一参与者失败只记录错误，不跳过后续释放。

Application shutdown orchestration with explicit dependency order.
"""

from __future__ import annotations

import threading
import logging
from typing import Any, Callable, List, Optional, Protocol

from PySide6 import QtCore, QtWidgets

from src.models import LifecycleShutdownResult


logger = logging.getLogger(__name__)


class ShutdownParticipant(Protocol):
    def shutdown(self) -> None:
        """协议占位：声明“shutdown”的最小调用约定，由实现方提供既有行为。"""
        ...


class LifecycleView(Protocol):
    def set_all_tasks_pending_stop(self) -> None:
        """协议占位：声明“set_all_tasks_pending_stop”的最小调用约定，由实现方提供既有行为。"""
        ...
    def abort_pending_exit(self, errors: tuple[str, ...]) -> None:
        """协议占位：声明“abort_pending_exit”的最小调用约定，由实现方提供既有行为。"""
        ...
    def prepare_for_shutdown(self) -> None:
        """协议占位：声明“prepare_for_shutdown”的最小调用约定，由实现方提供既有行为。"""
        ...
    def release_view_resources(self) -> None:
        """协议占位：声明“release_view_resources”的最小调用约定，由实现方提供既有行为。"""
        ...


class LifecycleController:
    """Stop application domains in data-safe order and only once."""

    def __init__(
        self,
        cleanup: ShutdownParticipant,
        upload: ShutdownParticipant,
        ftp: ShutdownParticipant,
        runtime: ShutdownParticipant,
        quit_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """作用：执行“__init__”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._cleanup = cleanup
        self._upload = upload
        self._ftp = ftp
        self._runtime = runtime
        self._participants = (
            ("cleanup", cleanup),
            ("upload", upload),
            ("ftp", ftp),
            ("runtime", runtime),
        )
        self._lock = threading.Lock()
        self._result: Optional[LifecycleShutdownResult] = None
        self._shutdown_in_progress = False
        self._quit_callback = quit_callback
        self._application_provider: Callable[[], Any] = QtWidgets.QApplication.instance
        self._quit_pending = False
        self._pending_result: Optional[LifecycleShutdownResult] = None
        self._pending_view: Optional[LifecycleView] = None
        self._pending_app: Any = None
        self._about_to_quit_handler: Optional[Callable[[], None]] = None
        self._quit_generation = 0
        self._quit_watchdog_ms = 1000

    def request_shutdown(
        self,
        view: LifecycleView,
        timeout_ms: int = 5000,
        poll_interval_ms: int = 100,
    ) -> LifecycleShutdownResult:
        """在 GUI 线程中协作式停止后台任务，成功后才退出 Qt。"""
        with self._lock:
            if self._result is not None:
                return self._result
            if self._quit_pending and self._pending_result is not None:
                return self._pending_result
            if self._shutdown_in_progress:
                return LifecycleShutdownResult(
                    ("shutdown_pending",), ("退出流程已在执行",)
                )
            self._shutdown_in_progress = True

        order: List[str] = []
        errors: List[str] = []
        try:
            try:
                view.set_all_tasks_pending_stop()
                order.append("view_pending_stop")
            except Exception as exc:
                errors.append(f"view_pending_stop: {type(exc).__name__}: {exc}")

            self._request_participant_stop(
                "upload_stop_requested",
                self._upload,
                ("request_stop_all", "stop"),
                order,
                errors,
            )
            self._request_participant_stop(
                "cleanup_cancel_requested",
                self._cleanup,
                ("cancel", "cancel_auto_cleanup"),
                order,
                errors,
            )
            self._request_participant_stop(
                "ftp_stop_requested",
                self._ftp,
                ("shutdown",),
                order,
                errors,
            )

            timed_out = False
            event_loop = QtCore.QEventLoop()
            poll_timer = QtCore.QTimer()
            poll_timer.setInterval(max(1, poll_interval_ms))

            def running_workers() -> tuple[str, ...]:
                """作用：执行“running_workers”的既有协调或状态处理职责。

                参数：沿用当前函数签名及已有类型、单位和状态约定。
                返回结果：沿用当前实现的返回值、回调或异常语义。
                执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
                风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
                """
                running: list[str] = []
                for name, participant in (
                    ("upload", self._upload),
                    ("cleanup", self._cleanup),
                    ("ftp", self._ftp),
                    ("runtime", self._runtime),
                ):
                    try:
                        value = getattr(participant, "has_running_workers", False)
                        if value() if callable(value) else bool(value):
                            running.append(name)
                    except Exception as exc:
                        running.append(name)
                        message = f"{name}_status: {type(exc).__name__}: {exc}"
                        if message not in errors:
                            errors.append(message)
                return tuple(running)

            def poll() -> None:
                """作用：执行“poll”的既有协调或状态处理职责。

                参数：沿用当前函数签名及已有类型、单位和状态约定。
                返回结果：沿用当前实现的返回值、回调或异常语义。
                执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
                风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
                """
                if not running_workers():
                    event_loop.quit()

            def timeout() -> None:
                """作用：执行“timeout”的既有协调或状态处理职责。

                参数：沿用当前函数签名及已有类型、单位和状态约定。
                返回结果：沿用当前实现的返回值、回调或异常语义。
                执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
                风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
                """
                nonlocal timed_out
                timed_out = True
                event_loop.quit()

            pending = running_workers()
            if pending:
                poll_timer.timeout.connect(poll)
                poll_timer.start()
                QtCore.QTimer.singleShot(max(0, timeout_ms), timeout)
                event_loop.exec()
                poll_timer.stop()
                pending = running_workers()

            if timed_out and pending:
                errors.append(
                    f"后台 Worker 在 {timeout_ms}ms 内未停止: {', '.join(pending)}"
                )
                result = LifecycleShutdownResult(tuple(order), tuple(errors))
                try:
                    view.abort_pending_exit(result.errors)
                except Exception as exc:
                    errors.append(f"view_abort_exit: {type(exc).__name__}: {exc}")
                    result = LifecycleShutdownResult(tuple(order), tuple(errors))
                return result

            result = LifecycleShutdownResult(tuple(order), tuple(errors))
            with self._lock:
                self._quit_pending = True
                self._pending_result = result
                self._pending_view = view
                self._quit_generation += 1
                generation = self._quit_generation

            schedule_error = self._schedule_application_quit(generation)
            if schedule_error:
                failed_errors = result.errors + (schedule_error,)
                self._abort_pending_application_quit(generation, failed_errors)
                return LifecycleShutdownResult(result.order, failed_errors)
            return result
        finally:
            with self._lock:
                self._shutdown_in_progress = False

    def _schedule_application_quit(self, generation: int) -> str:
        """在当前托盘回调返回后请求退出，并等待 Qt 明确认领退出事件。"""
        quit_callback = self._quit_callback
        if quit_callback is not None:
            def issue_test_quit() -> None:
                """作用：执行“issue_test_quit”的既有协调或状态处理职责。

                参数：沿用当前函数签名及已有类型、单位和状态约定。
                返回结果：沿用当前实现的返回值、回调或异常语义。
                执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
                风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
                """
                if not self._is_pending_generation(generation):
                    return
                try:
                    quit_callback()
                except Exception as exc:
                    with self._lock:
                        pending = self._pending_result
                    base_errors = pending.errors if pending is not None else ()
                    self._abort_pending_application_quit(
                        generation,
                        base_errors
                        + (f"application_quit: {type(exc).__name__}: {exc}",),
                    )
                    return
                self._confirm_application_quit(generation)

            QtCore.QTimer.singleShot(0, issue_test_quit)
            return ""

        app = self._application_provider()
        if app is None:
            return "application_quit: QApplication 实例不存在"

        handler = lambda: self._confirm_application_quit(generation)
        try:
            app.aboutToQuit.connect(handler)
        except Exception as exc:
            return f"application_quit_signal: {type(exc).__name__}: {exc}"

        with self._lock:
            if not self._quit_pending or generation != self._quit_generation:
                try:
                    app.aboutToQuit.disconnect(handler)
                except Exception:
                    pass
                return "application_quit: 退出请求状态已失效"
            self._pending_app = app
            self._about_to_quit_handler = handler

        QtCore.QTimer.singleShot(0, app.quit)
        QtCore.QTimer.singleShot(
            self._quit_watchdog_ms,
            lambda: self._verify_application_quit(generation, attempt=1),
        )
        return ""

    def _verify_application_quit(self, generation: int, attempt: int) -> None:
        """quit() 未生效时安全重试一次 exit(0)，仍失败则恢复界面。"""
        if not self._is_pending_generation(generation):
            return
        with self._lock:
            app = self._pending_app
            pending = self._pending_result
        if app is None:
            self._abort_pending_application_quit(
                generation,
                ("application_quit: QApplication 实例已丢失",),
            )
            return

        if attempt == 1:
            logger.warning("QApplication.quit() 未在超时内生效，安全重试 exit(0)")
            try:
                app.exit(0)
            except Exception as exc:
                base_errors = pending.errors if pending is not None else ()
                self._abort_pending_application_quit(
                    generation,
                    base_errors
                    + (f"application_exit: {type(exc).__name__}: {exc}",),
                )
                return
            QtCore.QTimer.singleShot(
                self._quit_watchdog_ms,
                lambda: self._verify_application_quit(generation, attempt=2),
            )
            return

        base_errors = pending.errors if pending is not None else ()
        self._abort_pending_application_quit(
            generation,
            base_errors + ("Qt 主事件循环未确认退出，已取消本次退出",),
        )

    def _confirm_application_quit(self, generation: int) -> None:
        """仅在 aboutToQuit 已发出后执行最终资源释放。"""
        with self._lock:
            if not self._quit_pending or generation != self._quit_generation:
                return
            view = self._pending_view
            self._quit_pending = False
        if view is None:
            return

        self._disconnect_about_to_quit_handler()
        result = self.shutdown(view)
        for error in result.errors:
            logger.error("最终关闭资源失败: %s", error)
        with self._lock:
            self._pending_result = None
            self._pending_view = None
            self._pending_app = None

    def _abort_pending_application_quit(
        self,
        generation: int,
        errors: tuple[str, ...],
    ) -> None:
        """Qt 未确认退出时保留应用并恢复 View，避免半退出状态。"""
        with self._lock:
            if not self._quit_pending or generation != self._quit_generation:
                return
            view = self._pending_view
            self._quit_pending = False
            self._pending_result = None
            self._pending_view = None
        self._disconnect_about_to_quit_handler()
        with self._lock:
            self._pending_app = None
        if view is not None:
            try:
                view.abort_pending_exit(errors)
            except Exception as exc:
                logger.error("恢复退出前界面失败: %s: %s", type(exc).__name__, exc)

    def _disconnect_about_to_quit_handler(self) -> None:
        """内部辅助：完成“_disconnect_about_to_quit_handler”对应的既有局部工作。"""
        with self._lock:
            app = self._pending_app
            handler = self._about_to_quit_handler
            self._about_to_quit_handler = None
        if app is not None and handler is not None:
            try:
                app.aboutToQuit.disconnect(handler)
            except Exception:
                pass

    def _is_pending_generation(self, generation: int) -> bool:
        """内部辅助：完成“_is_pending_generation”对应的既有局部工作。"""
        with self._lock:
            return self._quit_pending and generation == self._quit_generation

    @staticmethod
    def _request_participant_stop(
        label: str,
        participant: Any,
        method_names: tuple[str, ...],
        order: List[str],
        errors: List[str],
    ) -> None:
        """作用：执行“_request_participant_stop”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        try:
            for method_name in method_names:
                method = getattr(participant, method_name, None)
                if callable(method):
                    result = method()
                    if not bool(getattr(result, "success", True)):
                        errors.append(
                            f"{label}: {getattr(result, 'message', '') or '请求失败'}"
                        )
                    break
            else:
                errors.append(f"{label}: 参与者缺少停止接口")
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
        finally:
            order.append(label)

    def shutdown(self, view: LifecycleView) -> LifecycleShutdownResult:
        """作用：执行“shutdown”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        with self._lock:
            if self._result is not None:
                return self._result

            order: List[str] = []
            errors: List[str] = []
            try:
                view.prepare_for_shutdown()
                order.append("view_prepare")
            except Exception as exc:
                errors.append(f"view_prepare: {type(exc).__name__}: {exc}")

            for name, participant in self._participants:
                try:
                    participant.shutdown()
                except Exception as exc:
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
                finally:
                    order.append(name)

            try:
                view.release_view_resources()
                order.append("view_release")
            except Exception as exc:
                errors.append(f"view_release: {type(exc).__name__}: {exc}")

            self._result = LifecycleShutdownResult(tuple(order), tuple(errors))
            return self._result
