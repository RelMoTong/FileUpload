"""Controller for FTP validation, lifecycle and server events."""

from __future__ import annotations

import ntpath
import logging
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from src.models import FTPEvent, FTPOperationResult, FTPValidationResult

logger = logging.getLogger(__name__)


class FTPBusinessService(Protocol):
    available: bool
    tls_server_available: bool

    def validate_server(self, config: Mapping[str, Any]) -> FTPValidationResult: ...
    def validate_client(self, config: Mapping[str, Any]) -> FTPValidationResult: ...
    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: Mapping[str, Any],
        client_config: Mapping[str, Any],
    ) -> FTPValidationResult: ...
    def test_server(self, config: Mapping[str, Any]) -> FTPOperationResult: ...
    def test_client(self, config: Mapping[str, Any]) -> FTPOperationResult: ...
    def start_client_test(
        self, config: Mapping[str, Any], event_callback: Callable[[dict], None]
    ) -> FTPOperationResult: ...
    def cancel_client_test(self) -> FTPOperationResult: ...
    @property
    def client_test_running(self) -> bool: ...
    def start_server(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult: ...
    def stop_server(self) -> FTPOperationResult: ...
    def is_server_running(self) -> bool: ...
    def server_status(self) -> Dict[str, Any]: ...
    def stop_all(self) -> None: ...
    def shutdown(self) -> None: ...


class FTPEventWriter(Protocol):
    def write(self, event: FTPEvent) -> Any: ...


class FTPController:
    def __init__(
        self,
        service: FTPBusinessService,
        event_writer: Optional[FTPEventWriter] = None,
    ) -> None:
        self._service = service
        self._event_writer = event_writer
        self._event_listener: Optional[Callable[[dict], None]] = None
        self.server_started_independently = False
        self.server_started_by_upload = False

    @property
    def available(self) -> bool:
        return self._service.available

    @property
    def tls_server_available(self) -> bool:
        return self._service.tls_server_available

    def set_event_listener(self, listener: Callable[[dict], None]) -> None:
        self._event_listener = listener

    def validate_server(self, config: Mapping[str, Any]) -> FTPValidationResult:
        return self._service.validate_server(config)

    def validate_client(self, config: Mapping[str, Any]) -> FTPValidationResult:
        return self._service.validate_client(config)

    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: Mapping[str, Any],
        client_config: Mapping[str, Any],
    ) -> FTPValidationResult:
        if protocol == "smb" and not enable_server:
            return FTPValidationResult()
        return self._service.validate_configuration(
            enable_server, protocol, server_config, client_config
        )

    def test_server(self, config: Mapping[str, Any]) -> FTPOperationResult:
        return self._service.test_server(config)

    def test_client(self, config: Mapping[str, Any]) -> FTPOperationResult:
        return self._service.test_client(config)

    def start_client_test(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult:
        return self._service.start_client_test(config, event_callback)

    def cancel_client_test(self) -> FTPOperationResult:
        return self._service.cancel_client_test()

    @property
    def client_test_running(self) -> bool:
        return self._service.client_test_running

    @property
    def has_running_workers(self) -> bool:
        return self.client_test_running

    def start_server(
        self,
        config: Mapping[str, Any],
        source: str,
    ) -> FTPOperationResult:
        if self.is_server_running():
            return FTPOperationResult(True, "FTP服务器已在运行", status=self.server_status())
        result = self._service.start_server(config, self._handle_event)
        if result.success:
            self.server_started_independently = source == "independent"
            self.server_started_by_upload = source == "upload"
        return result

    def stop_server(self) -> FTPOperationResult:
        result = self._service.stop_server()
        if result.success:
            self.server_started_independently = False
            self.server_started_by_upload = False
        return result

    def stop_upload_server(self) -> FTPOperationResult:
        if not self.server_started_by_upload or self.server_started_independently:
            return FTPOperationResult(True, "FTP服务器不由上传任务管理")
        return self.stop_server()

    def is_server_running(self) -> bool:
        return self._service.is_server_running()

    def server_status(self) -> Dict[str, Any]:
        return self._service.server_status()

    def shutdown(self) -> None:
        shutdown = getattr(self._service, "shutdown", None)
        if callable(shutdown):
            shutdown()
        else:
            self._service.stop_all()
        self.server_started_independently = False
        self.server_started_by_upload = False

    def _handle_event(self, payload: dict) -> None:
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
