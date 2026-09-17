"""
文件名：src/models/cleanup_settings.py
文件作用：纯数据模型与业务契约模块“cleanup_settings”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

磁盘清理配置模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping

from ._conversion import SHARED_RETIRED_CONFIG_KEYS, as_bool, as_int, as_str, as_str_list


def normalize_cleanup_folders(folders: Iterable[Any]) -> list[str]:
    """按原顺序返回非空且不重复的清理目录。

    用途：为手动和自动清理目录提供唯一的文本规范化边界。
    输入：任意可迭代目录值。
    输出：去除空白和重复项后的路径字符串列表。
    关键步骤：仅做内存中的字符串处理，不访问文件系统。
    风险点：不得在这里探测 UNC 路径，否则界面和模型层可能被网络 I/O 阻塞。
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

    # 自 v3.4.2 起清理策略固定为全局最旧优先，旧字段从未影响清理请求。
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
        """规范化清理配置，使运行时始终拿到范围内且互相一致的字段。

        用途：集中约束阈值、检查间隔、目录和扩展名配置。
        输入：数据类构造时传入的原始字段值。
        输出：原地修正后的 ``CleanupSettings`` 实例。
        关键步骤：裁剪数值范围、去重目录和扩展名，并兼容旧单目录字段。
        风险点：这里只校正文本与数值，不验证目录存在性或磁盘关系，避免构造模型时发生 I/O。
        """
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
        """从配置映射构建清理设置；每个字段均带类型容错默认值。"""
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
        """导出可 JSON 序列化的清理配置副本。"""
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
