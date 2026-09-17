"""
文件名：src/controllers/ftp_controller.py
文件作用：控制器层的“ftp_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

Controller for FTP validation, lifecycle and server events.
"""

from __future__ import annotations

import ntpath
import logging
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from src.models import FTPEvent, FTPOperationResult, FTPValidationResult

logger = logging.getLogger(__name__)


class FTPBusinessService(Protocol):
    available: bool
    tls_server_available: bool

    def validate_server(self, config: Mapping[str, Any]) -> FTPValidationResult:
        """协议占位：声明“validate_server”的最小调用约定，由实现方提供既有行为。"""
        ...
    def validate_client(self, config: Mapping[str, Any]) -> FTPValidationResult:
        """协议占位：声明“validate_client”的最小调用约定，由实现方提供既有行为。"""
        ...
    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: Mapping[str, Any],
        client_config: Mapping[str, Any],
    ) -> FTPValidationResult:
        """协议占位：声明“validate_configuration”的最小调用约定，由实现方提供既有行为。"""
        ...
    def test_server(self, config: Mapping[str, Any]) -> FTPOperationResult:
        """协议占位：声明“test_server”的最小调用约定，由实现方提供既有行为。"""
        ...
    def test_client(self, config: Mapping[str, Any]) -> FTPOperationResult:
        """协议占位：声明“test_client”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_client_test(
        self, config: Mapping[str, Any], event_callback: Callable[[dict], None]
    ) -> FTPOperationResult:
        """协议占位：声明“start_client_test”的最小调用约定，由实现方提供既有行为。"""
        ...
    def cancel_client_test(self) -> FTPOperationResult:
        """协议占位：声明“cancel_client_test”的最小调用约定，由实现方提供既有行为。"""
        ...
    @property
    def client_test_running(self) -> bool:
        """协议占位：声明“client_test_running”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_server(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult:
        """协议占位：声明“start_server”的最小调用约定，由实现方提供既有行为。"""
        ...
    def stop_server(self) -> FTPOperationResult:
        """协议占位：声明“stop_server”的最小调用约定，由实现方提供既有行为。"""
        ...
    def is_server_running(self) -> bool:
        """协议占位：声明“is_server_running”的最小调用约定，由实现方提供既有行为。"""
        ...
    def server_status(self) -> Dict[str, Any]:
        """协议占位：声明“server_status”的最小调用约定，由实现方提供既有行为。"""
        ...
    def stop_all(self) -> None:
        """协议占位：声明“stop_all”的最小调用约定，由实现方提供既有行为。"""
        ...
    def shutdown(self) -> None:
        """协议占位：声明“shutdown”的最小调用约定，由实现方提供既有行为。"""
        ...


class FTPEventWriter(Protocol):
    def write(self, event: FTPEvent) -> Any:
        """协议占位：声明“write”的最小调用约定，由实现方提供既有行为。"""
        ...


class FTPController:
    def __init__(
        self,
        service: FTPBusinessService,
        event_writer: Optional[FTPEventWriter] = None,
    ) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._service = service
        self._event_writer = event_writer
        self._event_listener: Optional[Callable[[dict], None]] = None
        self.server_started_independently = False
        self.server_started_by_upload = False

    @property
    def available(self) -> bool:
        """作用：执行“available”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.available

    @property
    def tls_server_available(self) -> bool:
        """作用：执行“tls_server_available”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.tls_server_available

    def set_event_listener(self, listener: Callable[[dict], None]) -> None:
        """作用：执行“set_event_listener”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._event_listener = listener

    def validate_server(self, config: Mapping[str, Any]) -> FTPValidationResult:
        """作用：执行“validate_server”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.validate_server(config)

    def validate_client(self, config: Mapping[str, Any]) -> FTPValidationResult:
        """作用：执行“validate_client”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.validate_client(config)

    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: Mapping[str, Any],
        client_config: Mapping[str, Any],
    ) -> FTPValidationResult:
        """作用：执行“validate_configuration”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        if protocol == "smb" and not enable_server:
            return FTPValidationResult()
        return self._service.validate_configuration(
            enable_server, protocol, server_config, client_config
        )

    def test_server(self, config: Mapping[str, Any]) -> FTPOperationResult:
        """作用：执行“test_server”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.test_server(config)

    def test_client(self, config: Mapping[str, Any]) -> FTPOperationResult:
        """作用：执行“test_client”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.test_client(config)

    def start_client_test(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult:
        """作用：执行“start_client_test”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.start_client_test(config, event_callback)

    def cancel_client_test(self) -> FTPOperationResult:
        """作用：执行“cancel_client_test”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.cancel_client_test()

    @property
    def client_test_running(self) -> bool:
        """作用：执行“client_test_running”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.client_test_running

    @property
    def has_running_workers(self) -> bool:
        """作用：执行“has_running_workers”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self.client_test_running

    def start_server(
        self,
        config: Mapping[str, Any],
        source: str,
    ) -> FTPOperationResult:
        """作用：执行“start_server”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        if self.is_server_running():
            return FTPOperationResult(True, "FTP服务器已在运行", status=self.server_status())
        result = self._service.start_server(config, self._handle_event)
        if result.success:
            self.server_started_independently = source == "independent"
            self.server_started_by_upload = source == "upload"
        return result

    def stop_server(self) -> FTPOperationResult:
        """作用：执行“stop_server”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        result = self._service.stop_server()
        if result.success:
            self.server_started_independently = False
            self.server_started_by_upload = False
        return result

    def stop_upload_server(self) -> FTPOperationResult:
        """作用：执行“stop_upload_server”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        if not self.server_started_by_upload or self.server_started_independently:
            return FTPOperationResult(True, "FTP服务器不由上传任务管理")
        return self.stop_server()

    def is_server_running(self) -> bool:
        """作用：执行“is_server_running”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.is_server_running()

    def server_status(self) -> Dict[str, Any]:
        """作用：执行“server_status”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.server_status()

    def shutdown(self) -> None:
        """作用：执行“shutdown”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        shutdown = getattr(self._service, "shutdown", None)
        if callable(shutdown):
            shutdown()
        else:
            self._service.stop_all()
        self.server_started_independently = False
        self.server_started_by_upload = False

    def _handle_event(self, payload: dict) -> None:
        """内部辅助：完成“_handle_event”对应的既有局部工作。"""
        event = FTPEvent.from_mapping(payload)
        if self._event_writer is not None:
            try:
                self._event_writer.write(event)
            except Exception as exc:
                logger.warning(
                    "FTP event audit write failed for %s: %s",
                    event.event,
                    exc,
                )
        if self._event_listener is not None:
            notification = event.to_mapping()
            notification["display_message"] = self.format_event(event)
            self._event_listener(notification)

    @staticmethod
    def format_event(event: FTPEvent) -> str:
        """作用：执行“format_event”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        client_ip = event.client_ip or "-"
        username = event.username or "-"
        name = ntpath.basename(event.path) if event.path else ""
        if event.event == "connect":
            return f"🔌 [FTP-SERVER] 客户端连接: {client_ip}"
        if event.event == "login_ok":
            return f"✅ [FTP-SERVER] 登录成功: {username}@{client_ip}"
        if event.event == "login_failed":
            return f"❌ [FTP-SERVER] 登录失败: {username}@{client_ip}"
        if event.event == "disconnect":
            return f"🔌 [FTP-SERVER] 客户端断开: {username}@{client_ip}"
        if event.event == "upload_ok":
            return f"✅ [FTP-SERVER] 上传成功: {name} ({event.size} 字节)"
        if event.event == "upload_incomplete":
            return f"⚠️ [FTP-SERVER] 上传未完成: {name} ({event.size} 字节)"
        if event.event == "error":
            return f"❌ [FTP-SERVER] {event.message or '发生错误'}"
        return ""
