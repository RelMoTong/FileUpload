# -*- coding: utf-8 -*-
"""
__init__.py for src.core package

v3.0.2 新增：
- ResumeManager: 断点续传管理器
- I18n: 多语言国际化支持
"""
from .utils import get_app_dir, get_resource_path, get_app_version, get_app_title
from .permissions import PermissionManager
from .resume_manager import ResumeManager, ResumableFileUploader
from .i18n import I18n, t, set_language, get_language, add_language_listener, LANG_ZH_CN, LANG_EN_US

__all__ = [
    'get_app_dir', 
    'get_resource_path', 
    'get_app_version', 
    'get_app_title',
    'PermissionManager',
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
