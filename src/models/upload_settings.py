"""
文件名：src/models/upload_settings.py
文件作用：纯数据模型与业务契约模块“upload_settings”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

上传流程和应用行为的配置模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from ._conversion import SHARED_RETIRED_CONFIG_KEYS, as_bool, as_enum, as_float, as_int, as_str
from .app_state import DuplicateStrategy, UploadProtocol


@dataclass
class UploadSettings:
    """上传相关持久化配置及其运行时规范化规则。

    用途：集中管理目录、重试、速率、网络和协议等上传参数。
    输入：配置映射转换后的字段值或代码中的显式构造参数。
    输出：字段类型及有效范围均已规范化的设置对象。
    关键步骤：裁剪数值范围，并把字符串协议、重名策略转为对应枚举。
    风险点：本模型不验证路径可达性和网络连通性，避免配置加载时产生阻塞 I/O。
    """
    source_folder: str = ""
    target_folder: str = ""
    backup_folder: str = ""
    enable_backup: bool = True
    upload_interval: int = 30
    file_upload_delay_seconds: float = 1.5
    disk_threshold_percent: int = 10
    retry_count: int = 3
    disk_check_interval: int = 5
    filter_jpg: bool = True
    filter_png: bool = True
    filter_bmp: bool = True
    filter_gif: bool = True
    filter_raw: bool = True
    auto_start_windows: bool = False
    auto_run_on_startup: bool = False
    show_notifications: bool = True
    limit_upload_rate: bool = False
    max_upload_rate_mbps: float = 10.0
    enable_deduplication: bool = False
    hash_algorithm: str = "md5"
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.ASK
    network_check_interval: int = 10
    network_auto_pause: bool = True
    network_auto_resume: bool = True
    upload_protocol: UploadProtocol = UploadProtocol.SMB
    current_protocol: UploadProtocol = UploadProtocol.SMB
    language: str = "zh_CN"

    def __post_init__(self) -> None:
        """规范化上传参数，确保控制器读取到稳定的数值和枚举类型。

        用途：集中消化历史 JSON 中的字符串、超范围数值和缺失枚举。
        输入：数据类初始化后的原始字段。
        输出：原地修正后的 ``UploadSettings`` 实例。
        关键步骤：限制数值边界，再逐项将协议和策略转换为枚举。
        风险点：不能在这里进行文件系统或网络验证，以免配置加载阻塞主界面。
        """
        self.upload_interval = max(1, int(self.upload_interval))
        self.file_upload_delay_seconds = max(0.0, float(self.file_upload_delay_seconds))
        self.disk_threshold_percent = min(95, max(1, int(self.disk_threshold_percent)))
        self.retry_count = min(20, max(0, int(self.retry_count)))
        self.disk_check_interval = min(3600, max(1, int(self.disk_check_interval)))
        self.max_upload_rate_mbps = max(0.1, float(self.max_upload_rate_mbps))
        self.network_check_interval = min(3600, max(1, int(self.network_check_interval)))
        if not isinstance(self.duplicate_strategy, DuplicateStrategy):
            self.duplicate_strategy = as_enum(
                DuplicateStrategy, self.duplicate_strategy, DuplicateStrategy.ASK
            )
        if not isinstance(self.upload_protocol, UploadProtocol):
            self.upload_protocol = as_enum(
                UploadProtocol, self.upload_protocol, UploadProtocol.SMB
            )
        if not isinstance(self.current_protocol, UploadProtocol):
            self.current_protocol = as_enum(
                UploadProtocol, self.current_protocol, self.upload_protocol
            )

    # 这些字段曾被写入配置但没有运行时开关；读取旧 JSON 时兼容，下一次成功保存时移除。
    RETIRED_CONFIG_KEYS = SHARED_RETIRED_CONFIG_KEYS

    CONFIG_KEYS = (
        "source_folder",
        "target_folder",
        "backup_folder",
        "enable_backup",
        "upload_interval",
        "file_upload_delay_seconds",
        "disk_threshold_percent",
        "retry_count",
        "disk_check_interval",
        "filter_jpg",
        "filter_png",
        "filter_bmp",
        "filter_gif",
        "filter_raw",
        "auto_start_windows",
        "auto_run_on_startup",
        "show_notifications",
        "limit_upload_rate",
        "max_upload_rate_mbps",
        "enable_deduplication",
        "hash_algorithm",
        "duplicate_strategy",
        "network_check_interval",
        "network_auto_pause",
        "network_auto_resume",
        "upload_protocol",
        "current_protocol",
        "language",
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "UploadSettings":
        """从原始配置映射构建上传设置，并给每个字段提供类型容错默认值。"""
        return cls(
            source_folder=as_str(data.get("source_folder")),
            target_folder=as_str(data.get("target_folder")),
            backup_folder=as_str(data.get("backup_folder")),
            enable_backup=as_bool(data.get("enable_backup"), True),
            upload_interval=as_int(data.get("upload_interval"), 30),
            file_upload_delay_seconds=max(
                0.0, as_float(data.get("file_upload_delay_seconds"), 1.5)
            ),
            disk_threshold_percent=as_int(data.get("disk_threshold_percent"), 10),
            retry_count=as_int(data.get("retry_count"), 3),
            disk_check_interval=as_int(data.get("disk_check_interval"), 5),
            filter_jpg=as_bool(data.get("filter_jpg"), True),
            filter_png=as_bool(data.get("filter_png"), True),
            filter_bmp=as_bool(data.get("filter_bmp"), True),
            filter_gif=as_bool(data.get("filter_gif"), True),
            filter_raw=as_bool(data.get("filter_raw"), True),
            auto_start_windows=as_bool(data.get("auto_start_windows"), False),
            auto_run_on_startup=as_bool(data.get("auto_run_on_startup"), False),
            show_notifications=as_bool(data.get("show_notifications"), True),
            limit_upload_rate=as_bool(data.get("limit_upload_rate"), False),
            max_upload_rate_mbps=as_float(data.get("max_upload_rate_mbps"), 10.0),
            enable_deduplication=as_bool(data.get("enable_deduplication"), False),
            hash_algorithm=as_str(data.get("hash_algorithm"), "md5"),
            duplicate_strategy=as_enum(
                DuplicateStrategy, data.get("duplicate_strategy"), DuplicateStrategy.ASK
            ),
            network_check_interval=as_int(data.get("network_check_interval"), 10),
            network_auto_pause=as_bool(data.get("network_auto_pause"), True),
            network_auto_resume=as_bool(data.get("network_auto_resume"), True),
            upload_protocol=as_enum(
                UploadProtocol, data.get("upload_protocol"), UploadProtocol.SMB
            ),
            current_protocol=as_enum(
                UploadProtocol,
                data.get("current_protocol", data.get("upload_protocol")),
                UploadProtocol.SMB,
            ),
            language=as_str(data.get("language"), "zh_CN"),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """导出可 JSON 序列化的上传配置，枚举转换为稳定字符串值。"""
        return {
            "source_folder": self.source_folder,
            "target_folder": self.target_folder,
            "backup_folder": self.backup_folder,
            "enable_backup": self.enable_backup,
            "upload_interval": self.upload_interval,
            "file_upload_delay_seconds": self.file_upload_delay_seconds,
            "disk_threshold_percent": self.disk_threshold_percent,
            "retry_count": self.retry_count,
            "disk_check_interval": self.disk_check_interval,
            "filter_jpg": self.filter_jpg,
            "filter_png": self.filter_png,
            "filter_bmp": self.filter_bmp,
            "filter_gif": self.filter_gif,
            "filter_raw": self.filter_raw,
            "auto_start_windows": self.auto_start_windows,
            "auto_run_on_startup": self.auto_run_on_startup,
            "show_notifications": self.show_notifications,
            "limit_upload_rate": self.limit_upload_rate,
            "max_upload_rate_mbps": self.max_upload_rate_mbps,
            "enable_deduplication": self.enable_deduplication,
            "hash_algorithm": self.hash_algorithm,
            "duplicate_strategy": self.duplicate_strategy.value,
            "network_check_interval": self.network_check_interval,
            "network_auto_pause": self.network_auto_pause,
            "network_auto_resume": self.network_auto_resume,
            "upload_protocol": self.upload_protocol.value,
            "current_protocol": self.current_protocol.value,
            "language": self.language,
        }
