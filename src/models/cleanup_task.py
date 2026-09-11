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
class CleanupScanRequest:
    folders: Tuple[str, ...]
    formats: Tuple[str, ...]
    keep_days: int = 0


@dataclass(frozen=True)
class CleanupDeleteRequest:
    files: Tuple[CleanupFileItem, ...]
    use_trash: bool = True


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


@dataclass(frozen=True)
class CleanupIndexRecord:
    normalized_path: str
    path: str
    file_name: str
    created_at: float
    size_bytes: int
    root_path: str
    source: str = "scan"
    modified_at_ns: int = 0
    file_id: str = ""


@dataclass(frozen=True)
class CleanupIndexResult:
    status: str
    error: str = ""
    indexed_count: int = 0
    failed_count: int = 0
    scope_fingerprint: str = ""

    @property
    def success(self) -> bool:
        return self.status in {"已就绪", "建立完成"} and not self.error
