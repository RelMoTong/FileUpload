"""
文件名：src/repositories/__init__.py
文件作用：运行期持久化边界模块“__init__”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Persistence adapters for MVC model data.
"""

from .config_repository import ConfigRepository
from .ftp_event_log_repository import FTPEventLogRepository
from .cleanup_audit_repository import CleanupAuditRepository
from .runtime_repository import DailyLogRepository, WindowsStartupRepository
from .pending_archive_repository import PendingArchiveRepository

__all__ = [
    "CleanupAuditRepository",
    "ConfigRepository",
    "DailyLogRepository",
    "FTPEventLogRepository",
    "WindowsStartupRepository",
    "PendingArchiveRepository",
]
