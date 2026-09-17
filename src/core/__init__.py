# -*- coding: utf-8 -*-
"""
文件名：src/core/__init__.py
文件作用：通用基础设施与安全边界模块“__init__”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

__init__.py for src.core package

v3.0.2 新增：
- ResumeManager: 断点续传管理器
- I18n: 多语言国际化支持
"""
from .utils import (
    get_app_dir,
    get_resource_path,
    get_app_version,
    get_app_title,
    protect_secret,
    unprotect_secret,
)
from .resume_manager import ResumeManager, ResumableFileUploader
from .i18n import I18n, t, set_language, get_language, add_language_listener, LANG_ZH_CN, LANG_EN_US

__all__ = [
    'get_app_dir',
    'get_resource_path',
    'get_app_version',
    'get_app_title',
    'protect_secret',
    'unprotect_secret',
    # v3.0.2 断点续传
    'ResumeManager',
    'ResumableFileUploader',
    # v3.0.2 多语言
    'I18n',
    't',
    'set_language',
    'get_language',
    'add_language_listener',
    'LANG_ZH_CN',
    'LANG_EN_US',
]
