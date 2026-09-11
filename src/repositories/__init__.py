"""Persistence adapters for MVC model data."""

from .config_repository import ConfigRepository
from .ftp_event_log_repository import FTPEventLogRepository
from .cleanup_audit_repository import CleanupAuditRepository
from .cleanup_index_repository import CleanupIndexRepository
from .runtime_repository import DailyLogRepository, WindowsStartupRepository
from .pending_archive_repository import PendingArchiveRepository
from .dedup_index_repository import DedupIndexRepository

__all__ = [
    "CleanupAuditRepository",
    "CleanupIndexRepository",
    "ConfigRepository",
    "DailyLogRepository",
    "FTPEventLogRepository",
    "WindowsStartupRepository",
    "PendingArchiveRepository",
    "DedupIndexRepository",
]
