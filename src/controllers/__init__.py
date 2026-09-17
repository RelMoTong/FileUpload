"""
文件名：src/controllers/__init__.py
文件作用：控制器层的“__init__”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

MVC controllers.
"""

from .auth_controller import AuthController
from .ftp_controller import FTPController
from .settings_controller import SettingsController
from .upload_controller import UploadController
from .cleanup_controller import CleanupController
from .runtime_controller import RuntimeController
from .lifecycle_controller import LifecycleController

__all__ = [
    "AuthController",
    "CleanupController",
    "FTPController",
    "LifecycleController",
    "RuntimeController",
    "SettingsController",
    "UploadController",
]
