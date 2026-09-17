"""
文件名：src/ui/dialogs/__init__.py
文件作用：Qt 界面层的“__init__”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。

Standalone dialogs used by the main application view.
"""

from .change_password_dialog import ChangePasswordDialog
from .disk_cleanup_dialog import DiskCleanupDialog, calculate_dialog_responsive_metrics
from .login_dialog import LoginDialog

__all__ = [
    "ChangePasswordDialog",
    "DiskCleanupDialog",
    "LoginDialog",
    "calculate_dialog_responsive_metrics",
]
