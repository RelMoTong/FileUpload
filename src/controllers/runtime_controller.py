"""
文件名：src/controllers/runtime_controller.py
文件作用：控制器层的“runtime_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

Controller for runtime logging, disk snapshots, and startup registration.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import threading
from typing import Any, Callable, Protocol

from src.models.runtime import RuntimeCommandResult, RuntimeInitializationResult


class _DaemonTaskExecutor:
    """Small bounded executor whose stuck tasks cannot hold the process open."""

    def __init__(self, max_workers: int = 2) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._slots = threading.BoundedSemaphore(max_workers)
        self._lock = threading.Lock()
        self._closing = False

    def submit(self, function: Callable[..., Any], *args: Any) -> Future[Any]:
        """作用：执行“submit”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        future: Future[Any] = Future()
        with self._lock:
            closing = self._closing
        if closing or not self._slots.acquire(blocking=False):
            future.cancel()
            return future

        def run() -> None:
            """作用：执行“run”的既有协调或状态处理职责。

            参数：沿用当前函数签名及已有类型、单位和状态约定。
            返回结果：沿用当前实现的返回值、回调或异常语义。
            执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
            风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
            """
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
        """作用：执行“shutdown”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        del wait, cancel_futures
        with self._lock:
            self._closing = True


class RuntimeBusinessService(Protocol):
    app_dir: Path

    def initialize(self) -> RuntimeInitializationResult:
        """协议占位：声明“initialize”的最小调用约定，由实现方提供既有行为。"""
        ...
    def append_log(self, line: str) -> bool:
        """协议占位：声明“append_log”的最小调用约定，由实现方提供既有行为。"""
        ...
    def disk_free_percent(self, path: str, network_available: bool = True) -> float:
        """协议占位：声明“disk_free_percent”的最小调用约定，由实现方提供既有行为。"""
        ...
    def reconcile_startup(self, auto_enabled: bool, explicit: bool = False) -> RuntimeCommandResult:
        """协议占位：声明“reconcile_startup”的最小调用约定，由实现方提供既有行为。"""
        ...
    def disable_startup(self) -> RuntimeCommandResult:
        """协议占位：声明“disable_startup”的最小调用约定，由实现方提供既有行为。"""
        ...


class RuntimeController:
    def __init__(self, service: RuntimeBusinessService, executor: Any = None) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._service = service
        self._executor = executor or _DaemonTaskExecutor(max_workers=2)
        self._closing = False
        self._future_lock = threading.Lock()
        self._futures: set[Any] = set()

    @property
    def app_dir(self) -> Path:
        """作用：执行“app_dir”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.app_dir

    def initialize(self) -> RuntimeInitializationResult:
        """作用：执行“initialize”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.initialize()

    def append_log(self, line: str) -> None:
        """作用：执行“append_log”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
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
        """作用：执行“request_disk_space”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        if self._closing:
            return

        def run() -> None:
            """作用：执行“run”的既有协调或状态处理职责。

            参数：沿用当前函数签名及已有类型、单位和状态约定。
            返回结果：沿用当前实现的返回值、回调或异常语义。
            执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
            风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
            """
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
        """作用：执行“reconcile_startup”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.reconcile_startup(auto_enabled, explicit)

    def disable_startup(self) -> RuntimeCommandResult:
        """作用：执行“disable_startup”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.disable_startup()

    @property
    def has_running_workers(self) -> bool:
        """作用：执行“has_running_workers”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        with self._future_lock:
            return any(not future.done() for future in self._futures)

    def _track_future(self, future: Any) -> None:
        """内部辅助：完成“_track_future”对应的既有局部工作。"""
        if not hasattr(future, "done") or not hasattr(future, "add_done_callback"):
            return
        with self._future_lock:
            self._futures.add(future)

        def release(completed: Any) -> None:
            """作用：执行“release”的既有协调或状态处理职责。

            参数：沿用当前函数签名及已有类型、单位和状态约定。
            返回结果：沿用当前实现的返回值、回调或异常语义。
            执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
            风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
            """
            with self._future_lock:
                self._futures.discard(completed)

        future.add_done_callback(release)

    def shutdown(self) -> None:
        """作用：执行“shutdown”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._closing = True
        with self._future_lock:
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)
