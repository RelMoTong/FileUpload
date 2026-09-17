"""FTP 服务端与客户端配置模型。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import datetime
from typing import Any, Dict, Mapping

from ._conversion import as_bool, as_int, as_str, unknown_fields


@dataclass
class FTPServerSettings:
    """FTP 服务端的可持久化配置及未知字段容器。"""
    host: str = "0.0.0.0"
    port: int = 2121
    username: str = "upload_user"
    password: str = ""
    password_encrypted: str = ""
    shared_folder: str = ""
    enable_passive: bool = True
    passive_ports_start: int = 60000
    passive_ports_end: int = 65535
    enable_tls: bool = False
    cert_file: str = ""
    key_file: str = ""
    max_connections: int = 256
    max_connections_per_ip: int = 5
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    CONFIG_KEYS = (
        "host",
        "port",
        "username",
        "password",
        "password_encrypted",
        "shared_folder",
        "enable_passive",
        "passive_ports_start",
        "passive_ports_end",
        "enable_tls",
        "cert_file",
        "key_file",
        "max_connections",
        "max_connections_per_ip",
    )

    @classmethod
    def from_mapping(cls, data: Any) -> "FTPServerSettings":
        """从可能不完整的映射恢复服务端设置，并保留未知字段。"""
        values: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
        return cls(
            host=as_str(values.get("host"), "0.0.0.0"),
            port=as_int(values.get("port"), 2121),
            username=as_str(values.get("username"), "upload_user"),
            password=as_str(values.get("password")),
            password_encrypted=as_str(values.get("password_encrypted")),
            shared_folder=as_str(values.get("shared_folder")),
            enable_passive=as_bool(values.get("enable_passive"), True),
            passive_ports_start=as_int(values.get("passive_ports_start"), 60000),
            passive_ports_end=as_int(values.get("passive_ports_end"), 65535),
            enable_tls=as_bool(values.get("enable_tls"), False),
            cert_file=as_str(values.get("cert_file")),
            key_file=as_str(values.get("key_file")),
            max_connections=as_int(values.get("max_connections"), 256),
            max_connections_per_ip=as_int(values.get("max_connections_per_ip"), 5),
            extra=unknown_fields(values, cls.CONFIG_KEYS),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """导出服务端配置，确保未知字段不会在保存时丢失。"""
        result = deepcopy(self.extra)
        result.update(
            {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "password": self.password,
                "password_encrypted": self.password_encrypted,
                "shared_folder": self.shared_folder,
                "enable_passive": self.enable_passive,
                "passive_ports_start": self.passive_ports_start,
                "passive_ports_end": self.passive_ports_end,
                "enable_tls": self.enable_tls,
                "cert_file": self.cert_file,
                "key_file": self.key_file,
                "max_connections": self.max_connections,
                "max_connections_per_ip": self.max_connections_per_ip,
            }
        )
        return result


@dataclass
class FTPClientSettings:
    """FTP 客户端上传端点的可持久化配置及未知字段容器。"""
    host: str = ""
    port: int = 21
    username: str = ""
    password: str = ""
    password_encrypted: str = ""
    remote_path: str = "/upload"
    timeout: int = 30
    retry_count: int = 3
    passive_mode: bool = True
    enable_tls: bool = False
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    CONFIG_KEYS = (
        "host",
        "port",
        "username",
        "password",
        "password_encrypted",
        "remote_path",
        "timeout",
        "retry_count",
        "passive_mode",
        "enable_tls",
    )

    @classmethod
    def from_mapping(cls, data: Any) -> "FTPClientSettings":
        """从可能不完整的映射恢复客户端设置，并保留未知字段。"""
        values: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
        return cls(
            host=as_str(values.get("host")),
            port=as_int(values.get("port"), 21),
            username=as_str(values.get("username")),
            password=as_str(values.get("password")),
            password_encrypted=as_str(values.get("password_encrypted")),
            remote_path=as_str(values.get("remote_path"), "/upload"),
            timeout=as_int(values.get("timeout"), 30),
            retry_count=as_int(values.get("retry_count"), 3),
            passive_mode=as_bool(values.get("passive_mode"), True),
            enable_tls=as_bool(values.get("enable_tls"), False),
            extra=unknown_fields(values, cls.CONFIG_KEYS),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """导出客户端配置，确保未知字段不会在保存时丢失。"""
        result = deepcopy(self.extra)
        result.update(
            {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "password": self.password,
                "password_encrypted": self.password_encrypted,
                "remote_path": self.remote_path,
                "timeout": self.timeout,
                "retry_count": self.retry_count,
                "passive_mode": self.passive_mode,
                "enable_tls": self.enable_tls,
            }
        )
        return result


@dataclass
class FTPSettings:
    """组合 FTP 总开关、服务端和客户端设置。"""
    enable_server: bool = False
    server: FTPServerSettings = field(default_factory=FTPServerSettings)
    client: FTPClientSettings = field(default_factory=FTPClientSettings)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FTPSettings":
        """从应用级配置中构造 FTP 总设置。"""
        return cls(
            enable_server=as_bool(data.get("enable_ftp_server"), False),
            server=FTPServerSettings.from_mapping(data.get("ftp_server")),
            client=FTPClientSettings.from_mapping(data.get("ftp_client")),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """导出 FTP 总开关及嵌套的服务端、客户端配置。"""
        return {
            "enable_ftp_server": self.enable_server,
            "ftp_server": self.server.to_mapping(),
            "ftp_client": self.client.to_mapping(),
        }


@dataclass(frozen=True)
class FTPEvent:
    """用于日志和界面展示的单条 FTP 事件。"""
    event: str
    timestamp: str = ""
    client_ip: str = ""
    username: str = ""
    path: str = ""
    size: Any = ""
    message: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FTPEvent":
        """从事件映射恢复对象；缺少时间时记录当前时间。"""
        return cls(
            event=as_str(data.get("event")),
            timestamp=as_str(data.get("timestamp"))
            or datetime.datetime.now().isoformat(timespec="seconds"),
            client_ip=as_str(data.get("client_ip")),
            username=as_str(data.get("username")),
            path=as_str(data.get("path")),
            size=data.get("size", ""),
            message=as_str(data.get("message")),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """导出可 JSON 序列化的事件字段。"""
        return {
            "event": self.event,
            "timestamp": self.timestamp,
            "client_ip": self.client_ip,
            "username": self.username,
            "path": self.path,
            "size": self.size,
            "message": self.message,
        }


@dataclass(frozen=True)
class FTPValidationResult:
    """FTP 配置校验得到的错误和非阻塞警告。"""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        """没有阻塞错误时返回真；警告不会使配置无效。"""
        return not self.errors


@dataclass(frozen=True)
class FTPOperationResult:
    """FTP 启停或测试操作的统一结果。"""
    success: bool
    message: str = ""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    status: Dict[str, Any] = field(default_factory=dict)
