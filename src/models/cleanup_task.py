"""Runtime models for manual and automatic disk cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Tuple


@dataclass
class CleanupFileItem:
    path: str
    size: int
    mtime: float
    checked: bool = True
    mtime_ns: int = 0
    file_id: str = ""

    @property
    def name(self) -> str:
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
