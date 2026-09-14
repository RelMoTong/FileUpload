"""Pure data models used by the MVC application layers."""

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
