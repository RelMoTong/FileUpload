# -*- coding: utf-8 -*-
"""
通用工具函数模块

提供路径处理、资源访问等通用功能
"""
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """获取应用程序数据目录（用于配置和日志等可写文件）
    
    - 开发环境：返回项目根目录
    - 打包后：返回 exe 所在目录（用户可写）
    
    Returns:
        Path: 应用程序数据目录
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，返回 exe 所在目录
        return Path(sys.executable).parent
    # 开发环境，返回项目根目录（pyqt_app.py 所在目录）
    return Path(__file__).parent.parent.parent


def get_resource_path(relative_path: str) -> Path:
    """获取资源文件的绝对路径（支持打包）
    
    用于读取只读资源文件，如 Logo、默认配置等
    
    Args:
        relative_path: 相对于资源目录的路径，如 'assets/logo.png'
    
    Returns:
        Path: 资源文件的绝对路径
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # 打包后，资源文件在 _internal 目录（sys._MEIPASS）
        # 使用 getattr 避免类型检查错误（_MEIPASS 是运行时动态属性）
        base_path = Path(getattr(sys, '_MEIPASS'))
    else:
        # 开发环境，资源文件在项目根目录
        base_path = Path(__file__).parent.parent.parent
    return base_path / relative_path


def get_app_version() -> str:
    """获取应用程序版本号
    
    Returns:
        str: 版本号，如 "3.1.0"
    """
    return "3.1.0"


def get_app_title() -> str:
    """获取应用程序标题
    
    Returns:
        str: 应用程序标题
    """
    return f"图片异步上传工具 v{get_app_version()}"
