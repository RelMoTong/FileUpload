"""Application runtime state and shared MVC enums."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    GUEST = "guest"
    USER = "user"
    ADMIN = "admin"


class UploadStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class NetworkStatus(str, Enum):
    UNKNOWN = "unknown"
    GOOD = "good"
    UNSTABLE = "unstable"
    DISCONNECTED = "disconnected"


class UploadProtocol(str, Enum):
    SMB = "smb"
    FTP_CLIENT = "ftp_client"
    BOTH = "both"


class DuplicateStrategy(str, Enum):
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE = "overwrite"
    ASK = "ask"


@dataclass
class AppState:
    """Single controller-owned snapshot of user-visible runtime state."""

    role: UserRole = UserRole.GUEST
    upload_status: UploadStatus = UploadStatus.STOPPED
    network_status: NetworkStatus = NetworkStatus.UNKNOWN
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def is_running(self) -> bool:
        return self.upload_status in {UploadStatus.RUNNING, UploadStatus.PAUSED}

    @property
    def is_paused(self) -> bool:
        return self.upload_status is UploadStatus.PAUSED

