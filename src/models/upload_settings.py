"""Upload and application behavior configuration model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from ._conversion import as_bool, as_enum, as_float, as_int, as_str
from .app_state import DuplicateStrategy, UploadProtocol


@dataclass
class UploadSettings:
    source_folder: str = ""
    target_folder: str = ""
    backup_folder: str = ""
    enable_backup: bool = True
    upload_interval: int = 30
    file_upload_delay_seconds: float = 1.5
    monitor_mode: str = "periodic"
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
    enable_resume: bool = True

    RETIRED_CONFIG_KEYS = ("resume_min_size_mb",)

    CONFIG_KEYS = (
        "source_folder",
        "target_folder",
        "backup_folder",
        "enable_backup",
        "upload_interval",
        "file_upload_delay_seconds",
        "monitor_mode",
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
        "enable_resume",
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "UploadSettings":
        return cls(
            source_folder=as_str(data.get("source_folder")),
            target_folder=as_str(data.get("target_folder")),
            backup_folder=as_str(data.get("backup_folder")),
            enable_backup=as_bool(data.get("enable_backup"), True),
            upload_interval=as_int(data.get("upload_interval"), 30),
            file_upload_delay_seconds=max(
                0.0, as_float(data.get("file_upload_delay_seconds"), 1.5)
            ),
            monitor_mode=as_str(data.get("monitor_mode"), "periodic"),
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
            enable_resume=as_bool(data.get("enable_resume"), True),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "source_folder": self.source_folder,
            "target_folder": self.target_folder,
            "backup_folder": self.backup_folder,
            "enable_backup": self.enable_backup,
            "upload_interval": self.upload_interval,
            "file_upload_delay_seconds": self.file_upload_delay_seconds,
            "monitor_mode": self.monitor_mode,
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
            "enable_resume": self.enable_resume,
        }
