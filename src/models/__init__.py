"""
文件名：src/models/__init__.py
文件作用：纯数据模型与业务契约模块“__init__”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

MVC 各层共用的纯数据模型导出入口。
"""

from .app_state import (
    AppState,
    DuplicateStrategy,
    NetworkStatus,
    UploadProtocol,
    UploadStatus,
    UserRole,
)
from .auth_model import (
    AuthModel,
    ControlPermissions,
    LoginResult,
    PasswordChangeResult,
    PermissionContext,
)
from .cleanup_settings import CleanupSettings, normalize_cleanup_folders
from .cleanup_task import (
    AutoCleanupRequest,
    AutoCleanupResult,
    CleanupCandidate,
    CleanupCommandResult,
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupScanRequest,
    CleanupValidationResult,
)
from .ftp_settings import (
    FTPClientSettings,
    FTPEvent,
    FTPOperationResult,
    FTPServerSettings,
    FTPSettings,
    FTPValidationResult,
)
from .settings import ApplicationSettings
from .runtime import LifecycleShutdownResult, RuntimeCommandResult, RuntimeInitializationResult
from .path_probe import PathProbe, PathProbeResult
from .upload_settings import UploadSettings
from .upload_task import (
    UploadCommandResult,
    UploadRuntimeState,
    UploadTaskRequest,
    UploadValidationResult,
)

__all__ = [
    "AppState",
    "ApplicationSettings",
    "AuthModel",
    "ControlPermissions",
    "CleanupSettings",
    "normalize_cleanup_folders",
    "AutoCleanupRequest",
    "AutoCleanupResult",
    "CleanupCandidate",
    "CleanupCommandResult",
    "CleanupDeleteRequest",
    "CleanupFileItem",
    "CleanupScanRequest",
    "CleanupValidationResult",
    "DuplicateStrategy",
    "FTPClientSettings",
    "FTPEvent",
    "FTPOperationResult",
    "FTPServerSettings",
    "FTPSettings",
    "FTPValidationResult",
    "NetworkStatus",
    "LoginResult",
    "LifecycleShutdownResult",
    "PasswordChangeResult",
    "PermissionContext",
    "PathProbe",
    "PathProbeResult",
    "RuntimeCommandResult",
    "RuntimeInitializationResult",
    "UploadProtocol",
    "UploadCommandResult",
    "UploadRuntimeState",
    "UploadSettings",
    "UploadStatus",
    "UploadTaskRequest",
    "UploadValidationResult",
    "UserRole",
]
