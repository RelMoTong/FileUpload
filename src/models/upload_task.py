"""Runtime models for upload orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .app_state import NetworkStatus, UploadStatus


@dataclass(frozen=True)
class UploadTaskRequest:
    source: str
    target: str
    backup: str
    interval: int
    mode: str
    disk_threshold_percent: int
    retry_count: int
    filters: Tuple[str, ...]
    app_dir: Path
    enable_deduplication: bool = False
    hash_algorithm: str = "md5"
    duplicate_strategy: str = "ask"
    network_check_interval: int = 10
    network_auto_pause: bool = True
    network_auto_resume: bool = True
    enable_auto_delete: bool = False
    auto_delete_threshold: int = 80
    auto_delete_target_percent: int = 40
    upload_protocol: str = "smb"
    ftp_client_config: Optional[Dict[str, Any]] = None
    enable_backup: bool = True
    limit_upload_rate: bool = False
    max_upload_rate_mbps: float = 10.0
    file_upload_delay_seconds: float = 1.5


@dataclass(frozen=True)
class UploadValidationResult:
    errors: Tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class UploadCommandResult:
    success: bool
    message: str = ""
    errors: Tuple[str, ...] = ()


@dataclass
class UploadRuntimeState:
    status: UploadStatus = UploadStatus.STOPPED
    network_status: NetworkStatus = NetworkStatus.UNKNOWN
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0
    rate: str = "0 MB/s"
    progress_current: int = 0
    progress_total: int = 0
    current_filename: str = ""
    file_progress: int = 0
    start_time: Optional[float] = None
    last_error: str = ""
    archive_queue_size: int = 0

    @property
    def is_running(self) -> bool:
        return self.status in {UploadStatus.RUNNING, UploadStatus.PAUSED}

    @property
    def is_paused(self) -> bool:
        return self.status is UploadStatus.PAUSED

    def snapshot(self) -> "UploadRuntimeState":
        return replace(self)
