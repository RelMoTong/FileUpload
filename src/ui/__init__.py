# -*- coding: utf-8 -*-
"""
文件名：src/ui/__init__.py
文件作用：Qt 界面层的“__init__”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。

UI 模块 - 用户界面组件

包含通用控件、独立对话框和主窗口。
"""

from .dialogs import DiskCleanupDialog
from .panels import UploadFoldersPanel, UploadLogPanel, UploadSettingsPanel, UploadStatusPanel
from .widgets import Toast, ChipWidget, CollapsibleBox
from .main_window import MainWindow

__all__ = [
    'Toast',
    'ChipWidget',
    'CollapsibleBox',
    'DiskCleanupDialog',
    'MainWindow',
    'UploadFoldersPanel',
    'UploadLogPanel',
    'UploadSettingsPanel',
    'UploadStatusPanel',
]
