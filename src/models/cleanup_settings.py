"""Disk cleanup configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping

from ._conversion import SHARED_RETIRED_CONFIG_KEYS, as_bool, as_int, as_str, as_str_list


def normalize_cleanup_folders(folders: Iterable[Any]) -> list[str]:
    """Return configured cleanup folders as ordered, non-empty unique paths.

    This is the single normalization boundary for cleanup-folder inputs. It is
    filesystem-free, so views and models share a rule without probing UNC paths.
    """
    normalized: list[str] = []
    for path in folders:
        if not isinstance(path, str):
            continue
        cleaned = path.strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


@dataclass
class CleanupSettings:
    enable_auto_delete: bool = False
    auto_delete_folder: str = ""
    auto_delete_folders: list[str] = field(default_factory=list)
    auto_delete_threshold: int = 80
    auto_delete_target_percent: int = 40
    auto_delete_check_interval: int = 300
    auto_delete_formats: list[str] = field(default_factory=list)
    auto_delete_use_trash: bool = True

    # The cleanup policy has been global-oldest-first since v3.4.2, so this
    # former setting never influenced a cleanup request.
    RETIRED_CONFIG_KEYS = SHARED_RETIRED_CONFIG_KEYS

    CONFIG_KEYS = (
        "enable_auto_delete",
        "auto_delete_folder",
        "auto_delete_folders",
        "auto_delete_threshold",
        "auto_delete_target_percent",
        "auto_delete_check_interval",
        "auto_delete_formats",
        "auto_delete_use_trash",
    )

    def __post_init__(self) -> None:
        self.auto_delete_threshold = min(99, max(1, int(self.auto_delete_threshold)))
        self.auto_delete_target_percent = min(
            self.auto_delete_threshold - 1,
            max(0, int(self.auto_delete_target_percent)),
        )
        self.auto_delete_check_interval = min(
            86400, max(1, int(self.auto_delete_check_interval))
        )
        self.auto_delete_folders = normalize_cleanup_folders(
            self.auto_delete_folders
        )
        configured_formats = normalize_cleanup_folders(self.auto_delete_formats)
        self.auto_delete_formats = []
        for extension in configured_formats:
            normalized_extension = extension.lower()
            if normalized_extension not in self.auto_delete_formats:
                self.auto_delete_formats.append(normalized_extension)
        if not self.auto_delete_folders and self.auto_delete_folder.strip():
            self.auto_delete_folders = [self.auto_delete_folder.strip()]
        if self.auto_delete_folders:
            self.auto_delete_folder = self.auto_delete_folders[0]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CleanupSettings":
        return cls(
            enable_auto_delete=as_bool(data.get("enable_auto_delete"), False),
            auto_delete_folder=as_str(data.get("auto_delete_folder")),
            auto_delete_folders=as_str_list(data.get("auto_delete_folders")),
            auto_delete_threshold=as_int(data.get("auto_delete_threshold"), 80),
            auto_delete_target_percent=as_int(data.get("auto_delete_target_percent"), 40),
            auto_delete_check_interval=as_int(data.get("auto_delete_check_interval"), 300),
            auto_delete_formats=as_str_list(data.get("auto_delete_formats")),
            auto_delete_use_trash=as_bool(data.get("auto_delete_use_trash"), True),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "enable_auto_delete": self.enable_auto_delete,
            "auto_delete_folder": self.auto_delete_folder,
            "auto_delete_folders": list(self.auto_delete_folders),
            "auto_delete_threshold": self.auto_delete_threshold,
            "auto_delete_target_percent": self.auto_delete_target_percent,
            "auto_delete_check_interval": self.auto_delete_check_interval,
            "auto_delete_formats": list(self.auto_delete_formats),
            "auto_delete_use_trash": self.auto_delete_use_trash,
        }
