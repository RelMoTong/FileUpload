"""Disk cleanup configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from ._conversion import as_bool, as_int, as_str, as_str_list


@dataclass
class CleanupSettings:
    enable_auto_delete: bool = False
    auto_delete_folder: str = ""
    auto_delete_folders: list[str] = field(default_factory=list)
    auto_delete_threshold: int = 80
    auto_delete_target_percent: int = 40
    auto_delete_keep_days: int = 10
    auto_delete_check_interval: int = 300

    CONFIG_KEYS = (
        "enable_auto_delete",
        "auto_delete_folder",
        "auto_delete_folders",
        "auto_delete_threshold",
        "auto_delete_target_percent",
        "auto_delete_keep_days",
        "auto_delete_check_interval",
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CleanupSettings":
        return cls(
            enable_auto_delete=as_bool(data.get("enable_auto_delete"), False),
            auto_delete_folder=as_str(data.get("auto_delete_folder")),
            auto_delete_folders=as_str_list(data.get("auto_delete_folders")),
            auto_delete_threshold=as_int(data.get("auto_delete_threshold"), 80),
            auto_delete_target_percent=as_int(data.get("auto_delete_target_percent"), 40),
            auto_delete_keep_days=as_int(data.get("auto_delete_keep_days"), 10),
            auto_delete_check_interval=as_int(data.get("auto_delete_check_interval"), 300),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "enable_auto_delete": self.enable_auto_delete,
            "auto_delete_folder": self.auto_delete_folder,
            "auto_delete_folders": list(self.auto_delete_folders),
            "auto_delete_threshold": self.auto_delete_threshold,
            "auto_delete_target_percent": self.auto_delete_target_percent,
            "auto_delete_keep_days": self.auto_delete_keep_days,
            "auto_delete_check_interval": self.auto_delete_check_interval,
        }

