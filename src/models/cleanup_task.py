"""
文件名：src/models/cleanup_task.py
文件作用：纯数据模型与业务契约模块“cleanup_task”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

手动与自动磁盘清理共用的运行时模型。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Tuple


@dataclass
class CleanupFileItem:
    """手动清理列表中的单个文件及其勾选状态。"""
    path: str
    size: int
    mtime: float
    checked: bool = True
    mtime_ns: int = 0
    file_id: str = ""

    @property
    def name(self) -> str:
        """返回用于列表展示的文件名，不改变完整路径。"""
        return os.path.basename(self.path)


@dataclass(frozen=True)
class CleanupCandidate:
    """统一的清理候选快照；手动预览和自动清理使用同一身份字段。"""

    path: str
    root_path: str
    size: int
    mtime: float
    mtime_ns: int = 0
    file_id: str = ""
    created_at: float = 0.0

    def as_file_item(self) -> CleanupFileItem:
        """转换为手动列表项目，同时保留扫描时捕获的身份字段。"""
        return CleanupFileItem(
            path=self.path,
            size=self.size,
            mtime=self.mtime,
            mtime_ns=self.mtime_ns,
            file_id=self.file_id,
        )


@dataclass(frozen=True)
class CleanupScanRequest:
    folders: Tuple[str, ...]
    formats: Tuple[str, ...]
    keep_days: int = 0


@dataclass(frozen=True)
class CleanupDeleteRequest:
    files: Tuple[CleanupFileItem, ...]
    use_trash: bool = True
    permanent_authorized: bool = False
    allowed_roots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoCleanupRequest:
    enabled: bool
    folders: Tuple[str, ...]
    trigger_percent: int
    target_percent: int
    formats: Tuple[str, ...] = ()
    use_trash: bool = True
    trigger_source: str = "timer"


@dataclass(frozen=True)
class CleanupValidationResult:
    valid_folders: Tuple[str, ...] = ()
    invalid_reasons: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    volume_details: Tuple[Tuple[str, str], ...] = ()

    @property
    def is_valid(self) -> bool:
        """作用：执行“is_valid”的既有职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、处理与结果交付。
        风险或注意事项：本说明不改变既有持久化、线程、路径或公开接口约定。
        """
        return not self.errors and bool(self.valid_folders)


@dataclass(frozen=True)
class CleanupCommandResult:
    success: bool
    message: str = ""
    errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoCleanupResult:
    status: str
    error: str = ""
    scanned_count: int = 0
    deleted_count: int = 0
    failed_count: int = 0
    attempted_delete_bytes: int = 0
    actual_released_bytes: int = 0
    skipped_changed_count: int = 0
