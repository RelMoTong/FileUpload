# -*- coding: utf-8 -*-
"""
通用工具函数模块

提供路径处理、资源访问等通用功能
"""
import base64
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

# 版本号单一来源
try:
    from src import __version__  # type: ignore  # 屏蔽类型检查在运行时动态导入
except Exception:
    __version__ = "0.0.0"


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
    # 开发环境，返回项目根目录
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
        str: 版本号，如 "3.2.0"
    """
    return __version__


def get_app_title() -> str:
    """获取应用程序标题
    
    Returns:
        str: 应用程序标题
    """
    return f"图片异步上传工具 v{get_app_version()}"


_DPAPI_PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _create_data_blob(data: bytes) -> tuple[_DATA_BLOB, object]:
    """将 bytes 转为 Windows DPAPI 需要的 DATA_BLOB。"""
    buffer = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def protect_secret(secret: str) -> str:
    """使用 Windows DPAPI 加密敏感信息。

    在非 Windows 平台上回退为原文，避免破坏兼容性。
    """
    if not secret:
        return ""
    if sys.platform != "win32":
        return secret

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, buffer_in = _create_data_blob(secret.encode("utf-8"))
    blob_out = _DATA_BLOB()

    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        raise ctypes.WinError()

    try:
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)


def unprotect_secret(secret: str) -> str:
    """解密由 protect_secret 生成的密文。"""
    if not secret:
        return ""
    if not secret.startswith(_DPAPI_PREFIX):
        return secret
    if sys.platform != "win32":
        return ""

    try:
        encrypted = base64.b64decode(secret[len(_DPAPI_PREFIX):])
    except Exception:
        return ""

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, buffer_in = _create_data_blob(encrypted)
    blob_out = _DATA_BLOB()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        return ""

    try:
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return decrypted.decode("utf-8")
    except Exception:
        return ""
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)
