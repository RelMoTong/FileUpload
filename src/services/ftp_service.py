"""
文件名：src/services/ftp_service.py
文件作用：业务服务层的“ftp_service”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。

FTP configuration validation and protocol lifecycle service.
"""

from __future__ import annotations

import os
import re
import ssl
import threading
from typing import Any, Callable, Dict, Mapping, Optional

from src.models import FTPOperationResult, FTPValidationResult

try:
    from src.protocols.ftp import (
        FTPClientUploader,
        FTPProtocolManager,
        FTPServerManager,
        TLS_FTPHandler,
    )
except ImportError:
    FTPClientUploader = None  # type: ignore[assignment,misc]
    FTPProtocolManager = None  # type: ignore[assignment,misc]
    FTPServerManager = None  # type: ignore[assignment,misc]
    TLS_FTPHandler = None  # type: ignore[assignment,misc]


class FTPService:
    """Hide concrete FTP protocol classes from controllers and views."""

    def __init__(
        self,
        manager_factory: Optional[Callable[[], Any]] = None,
        server_factory: Optional[Callable[[dict], Any]] = None,
        client_factory: Optional[Callable[[dict], Any]] = None,
    ) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._manager_factory = manager_factory or FTPProtocolManager
        self._server_factory = server_factory or FTPServerManager
        self._client_factory = client_factory or FTPClientUploader
        self._manager: Any = None
        self._test_lock = threading.Lock()
        self._test_cancel = threading.Event()
        self._test_thread: Optional[threading.Thread] = None
        self._test_client: Any = None
        self._test_running = False

    @property
    def available(self) -> bool:
        """作用：执行“available”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return all((self._manager_factory, self._server_factory, self._client_factory))

    @property
    def tls_server_available(self) -> bool:
        """作用：执行“tls_server_available”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return TLS_FTPHandler is not None

    @staticmethod
    def build_server_config(config: Mapping[str, Any]) -> Dict[str, Any]:
        """作用：执行“build_server_config”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        passive_ports = None
        if config.get("enable_passive", True):
            passive_ports = (
                config.get("passive_ports_start", 60000),
                config.get("passive_ports_end", 65535),
            )
        return {
            "host": config.get("host", "0.0.0.0"),
            "port": config.get("port", 2121),
            "username": config.get("username", "upload_user"),
            "password": config.get("password", "upload_pass"),
            "shared_folder": config.get("shared_folder", ""),
            "enable_tls": config.get("enable_tls", False),
            "cert_file": str(config.get("cert_file", "")).strip(),
            "key_file": str(config.get("key_file", "")).strip(),
            "passive_ports": passive_ports,
            "passive_ports_start": config.get("passive_ports_start", 60000),
            "passive_ports_end": config.get("passive_ports_end", 65535),
            "enable_passive": config.get("enable_passive", True),
            "max_cons": config.get("max_connections", 256),
            "max_cons_per_ip": config.get("max_connections_per_ip", 5),
        }

    @staticmethod
    def validate_server(config: Mapping[str, Any]) -> FTPValidationResult:
        """作用：执行“validate_server”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        errors: list[str] = []
        warnings: list[str] = []
        host = str(config.get("host", "")).strip()
        if not host:
            errors.append("FTP服务器主机地址为空")
        elif host not in {"0.0.0.0", "localhost", "127.0.0.1"}:
            if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
                errors.append(f"FTP服务器主机地址格式无效: {host}")

        port = config.get("port", 0)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(f"FTP服务器端口无效: {port}（范围：1-65535）")
        elif port < 1024 and port != 21:
            warnings.append(f"FTP服务器使用特权端口 {port}，可能需要管理员权限")

        username = str(config.get("username", "")).strip()
        if not username:
            errors.append("FTP服务器用户名为空")
        elif len(username) < 3:
            errors.append("FTP服务器用户名至少需要3个字符")

        password = str(config.get("password", "")).strip()
        if not password:
            errors.append("FTP服务器密码为空")
        elif len(password) < 6:
            errors.append("FTP服务器密码至少需要6个字符")

        shared_folder = str(config.get("shared_folder", "")).strip()
        if not shared_folder:
            errors.append("FTP服务器共享目录为空")
        elif not os.path.exists(shared_folder):
            errors.append(f"FTP服务器共享目录不存在: {shared_folder}")
        elif not os.path.isdir(shared_folder):
            errors.append(f"FTP服务器共享路径不是目录: {shared_folder}")

        if config.get("enable_passive", True):
            start_port = config.get("passive_ports_start", 0)
            end_port = config.get("passive_ports_end", 0)
            if not isinstance(start_port, int) or not isinstance(end_port, int):
                errors.append("FTP服务器被动端口范围无效")
            elif start_port > end_port:
                errors.append(f"FTP服务器被动端口范围无效: {start_port}-{end_port}")

        if config.get("enable_tls", False):
            if TLS_FTPHandler is None:
                errors.append(
                    "当前运行环境缺少 pyOpenSSL，不支持 FTPS 服务器"
                )
            cert_file = str(config.get("cert_file", "")).strip()
            key_file = str(config.get("key_file", "")).strip()
            if not cert_file:
                errors.append("FTPS 证书文件为空")
            elif not os.path.isfile(cert_file):
                errors.append(f"FTPS 证书文件不存在或不是文件: {cert_file}")
            if not key_file:
                errors.append("FTPS 私钥文件为空")
            elif not os.path.isfile(key_file):
                errors.append(f"FTPS 私钥文件不存在或不是文件: {key_file}")
            if cert_file and key_file and not errors:
                try:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
                except (OSError, ssl.SSLError, ValueError) as exc:
                    errors.append(
                        "FTPS 证书/私钥无效或不匹配: "
                        f"{type(exc).__name__}: {exc}"
                    )
        return FTPValidationResult(tuple(errors), tuple(warnings))

    @staticmethod
    def validate_client(config: Mapping[str, Any]) -> FTPValidationResult:
        """作用：执行“validate_client”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        errors: list[str] = []
        host = str(config.get("host", "")).strip()
        if not host:
            errors.append("FTP客户端主机地址为空")
        else:
            is_ip = re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host)
            is_domain = re.match(
                r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
                r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$",
                host,
            )
            if not is_ip and not is_domain:
                errors.append(f"FTP客户端主机地址格式无效: {host}")

        port = config.get("port", 0)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(f"FTP客户端端口无效: {port}（范围：1-65535）")
        if not str(config.get("username", "")).strip():
            errors.append("FTP客户端用户名为空")
        if not str(config.get("password", "")).strip():
            errors.append("FTP客户端密码为空")
        remote_path = str(config.get("remote_path", "")).strip()
        if not remote_path:
            errors.append("FTP客户端远程路径为空")
        elif not remote_path.startswith("/"):
            errors.append(f"FTP客户端远程路径应以 / 开头: {remote_path}")
        return FTPValidationResult(tuple(errors))

    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: Mapping[str, Any],
        client_config: Mapping[str, Any],
    ) -> FTPValidationResult:
        """作用：执行“validate_configuration”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        errors: list[str] = []
        warnings: list[str] = []
        if enable_server:
            server = self.validate_server(server_config)
            errors.extend(server.errors)
            warnings.extend(server.warnings)
        if protocol in {"ftp_client", "both"}:
            client = self.validate_client(client_config)
            errors.extend(client.errors)
            warnings.extend(client.warnings)
        return FTPValidationResult(tuple(errors), tuple(warnings))

    def _ensure_manager(self) -> Any:
        """内部辅助：完成“_ensure_manager”对应的既有局部工作。"""
        if not self.available:
            raise RuntimeError("FTP模块不可用")
        if self._manager is None:
            manager_factory = self._manager_factory
            if manager_factory is None:
                raise RuntimeError("FTP管理器不可用")
            self._manager = manager_factory()
        return self._manager

    def test_server(self, config: Mapping[str, Any]) -> FTPOperationResult:
        """作用：执行“test_server”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        validation = self.validate_server(config)
        if not validation.is_valid:
            return FTPOperationResult(False, errors=validation.errors, warnings=validation.warnings)
        if not self.available:
            return FTPOperationResult(False, "FTP模块不可用")
        server = None
        manager_config = self.build_server_config(config)
        try:
            server_factory = self._server_factory
            if server_factory is None:
                return FTPOperationResult(False, "FTP服务器模块不可用")
            server = server_factory(manager_config)
            if not server.start():
                return FTPOperationResult(False, "FTP服务器启动失败", warnings=validation.warnings)
            return FTPOperationResult(True, "FTP服务器测试成功", warnings=validation.warnings)
        except Exception as exc:
            return FTPOperationResult(False, str(exc), warnings=validation.warnings)
        finally:
            if server is not None:
                try:
                    server.stop()
                except Exception:
                    pass

    def test_client(
        self,
        config: Mapping[str, Any],
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> FTPOperationResult:
        """作用：执行“test_client”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        validation = self.validate_client(config)
        if not validation.is_valid:
            return FTPOperationResult(False, errors=validation.errors)
        if not self.available:
            return FTPOperationResult(False, "FTP模块不可用")
        client = None
        try:
            client_factory = self._client_factory
            if client_factory is None:
                return FTPOperationResult(False, "FTP客户端模块不可用")
            client = client_factory({"name": "test_client", **dict(config)})
            with self._test_lock:
                self._test_client = client
            try:
                connected = client.test_connection(
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )
            except TypeError:
                # 兼容旧协议实现和轻量测试替身。
                connected = client.test_connection()
            if connected:
                return FTPOperationResult(True, "FTP客户端连接测试成功")
            if cancel_event is not None and cancel_event.is_set():
                return FTPOperationResult(
                    False, "FTP客户端连接测试已取消", status={"cancelled": True}
                )
            return FTPOperationResult(False, "FTP客户端连接失败")
        except Exception as exc:
            return FTPOperationResult(False, str(exc))
        finally:
            with self._test_lock:
                if self._test_client is client:
                    self._test_client = None
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

    def start_client_test(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult:
        """作用：执行“start_client_test”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        validation = self.validate_client(config)
        if not validation.is_valid:
            return FTPOperationResult(False, errors=validation.errors)
        with self._test_lock:
            if self._test_running:
                return FTPOperationResult(False, "FTP客户端连接测试正在运行")
            self._test_running = True
            self._test_cancel.clear()

        def emit(payload: dict) -> None:
            """作用：执行“emit”的既有业务服务职责。

            参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
            执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
            风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
            """
            try:
                event_callback(payload)
            except Exception:
                pass

        def run() -> None:
            """作用：执行“run”的既有业务服务职责。

            参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
            执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
            风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
            """
            try:
                result = self.test_client(
                    config,
                    cancel_event=self._test_cancel,
                    progress_callback=lambda attempt, total: emit(
                        {"type": "progress", "attempt": attempt, "total": total}
                    ),
                )
                emit(
                    {
                        "type": "cancelled"
                        if result.status.get("cancelled")
                        else ("success" if result.success else "failure"),
                        "result": result,
                    }
                )
            finally:
                with self._test_lock:
                    self._test_running = False
                    self._test_thread = None

        try:
            thread = threading.Thread(
                target=run,
                daemon=True,
                name="FTPConnectionTest",
            )
            with self._test_lock:
                self._test_thread = thread
            thread.start()
            return FTPOperationResult(True, "FTP客户端连接测试已启动")
        except Exception as exc:
            with self._test_lock:
                self._test_running = False
            return FTPOperationResult(False, str(exc))

    def cancel_client_test(self) -> FTPOperationResult:
        """作用：执行“cancel_client_test”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        with self._test_lock:
            running = self._test_running
            client = self._test_client
        if not running:
            return FTPOperationResult(True, "FTP客户端连接测试未运行")
        self._test_cancel.set()
        if client is not None:
            cancel = getattr(client, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        return FTPOperationResult(True, "已请求取消FTP客户端连接测试")

    @property
    def client_test_running(self) -> bool:
        """作用：执行“client_test_running”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        with self._test_lock:
            return self._test_running

    def start_server(
        self,
        config: Mapping[str, Any],
        event_callback: Callable[[dict], None],
    ) -> FTPOperationResult:
        """作用：执行“start_server”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        validation = self.validate_server(config)
        if not validation.is_valid:
            return FTPOperationResult(False, errors=validation.errors, warnings=validation.warnings)
        try:
            manager = self._ensure_manager()
            manager_config = self.build_server_config(config)
            manager_config["event_callback"] = event_callback
            if not manager.start_server(manager_config):
                return FTPOperationResult(False, "FTP服务器启动失败", warnings=validation.warnings)
            return FTPOperationResult(
                True,
                "FTP服务器已启动",
                warnings=validation.warnings,
                status=manager.get_status().get("server") or {},
            )
        except Exception as exc:
            return FTPOperationResult(False, str(exc), warnings=validation.warnings)

    def stop_server(self) -> FTPOperationResult:
        """作用：执行“stop_server”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        if not self.is_server_running():
            return FTPOperationResult(True, "FTP服务器未运行")
        try:
            if self._manager.stop_server():
                return FTPOperationResult(True, "FTP服务器已停止")
            return FTPOperationResult(False, "FTP服务器停止失败")
        except Exception as exc:
            return FTPOperationResult(False, str(exc))

    def is_server_running(self) -> bool:
        """作用：执行“is_server_running”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        try:
            return bool(
                self._manager
                and self._manager.server
                and self._manager.server.is_running
            )
        except Exception:
            return False

    def server_status(self) -> Dict[str, Any]:
        """作用：执行“server_status”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        if not self._manager:
            return {}
        return self._manager.get_status().get("server") or {}

    def stop_all(self) -> None:
        """作用：执行“stop_all”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        self.cancel_client_test()
        if self._manager:
            self._manager.stop_all()

    def shutdown(self) -> None:
        """作用：执行“shutdown”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        self.stop_all()
