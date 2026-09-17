"""
文件名：src/services/__init__.py
文件作用：业务服务层的“__init__”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。

Business services used by MVC controllers.
"""

from .auth_service import AuthService
from .ftp_service import FTPService
from .upload_service import UploadService
from .cleanup_service import CleanupService
from .runtime_service import RuntimeService
from .path_probe_service import PathProbe, PathProbeResult, PathProbeService

__all__ = ["AuthService", "CleanupService", "FTPService", "RuntimeService", "UploadService", "PathProbe", "PathProbeResult", "PathProbeService"]
