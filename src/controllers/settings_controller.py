"""
文件名：src/controllers/settings_controller.py
文件作用：控制器层的“settings_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

Controller for loading and saving application settings.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol
import sys

from src.core import protect_secret, unprotect_secret
from src.models import ApplicationSettings


class SettingsRepository(Protocol):
    last_error: str

    def load(self) -> ApplicationSettings:
        """作用：执行“load”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...

    def load_raw(self) -> Dict[str, Any]:
        """作用：执行“load_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        """作用：执行“save”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        """作用：执行“save_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...


class SettingsController:
    """Coordinate configuration models and their persistence boundary."""

    def __init__(self, repository: SettingsRepository):
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._repository = repository
        self.last_error = ""

    @property
    def config_exists(self) -> bool:
        """作用：执行“config_exists”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        config_path = getattr(self._repository, "config_path", None)
        return bool(config_path and config_path.exists())

    def load(self) -> ApplicationSettings:
        """作用：执行“load”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        settings = self._repository.load()
        self.last_error = self._repository.last_error
        return settings

    def load_settings(self) -> ApplicationSettings:
        """Typed alias used by views; raw payloads stay at the repository edge."""
        return self.load()

    def load_raw(self) -> Dict[str, Any]:
        """作用：执行“load_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        config = self._repository.load_raw()
        self.last_error = self._repository.last_error
        return config

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        """作用：执行“save”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        success = self._repository.save(settings, preserve_users=preserve_users)
        self.last_error = self._repository.last_error
        return success

    @staticmethod
    def decode_ftp_password(section: Dict[str, Any], default: str = "") -> str:
        """Decode protected credentials while retaining legacy plaintext support."""
        encrypted = str(section.get("password_encrypted", "") or "").strip()
        if encrypted:
            decrypted = unprotect_secret(encrypted)
            if decrypted:
                return decrypted
        return str(section.get("password", default) or "")

    @staticmethod
    def encode_ftp_password(password: str, label: str) -> tuple[str, str]:
        """Return legacy plaintext/protected fields for persisted FTP settings."""
        password = password.strip()
        if not password:
            return "", ""
        encrypted = protect_secret(password)
        if sys.platform == "win32" and (not encrypted or encrypted == password):
            raise RuntimeError(f"{label}密码加密失败")
        return "", encrypted

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        """作用：执行“save_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        settings = ApplicationSettings.from_config(config)
        success = self._repository.save(settings, preserve_users=preserve_users)
        self.last_error = self._repository.last_error
        return success
