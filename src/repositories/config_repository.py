"""
文件名：src/repositories/config_repository.py
文件作用：运行期持久化边界模块“config_repository”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Configuration persistence adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.config import ConfigManager
from src.models import ApplicationSettings


class ConfigRepository:
    """Load and save typed settings through the existing ConfigManager."""

    def __init__(self, config_path: Path):
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._manager = ConfigManager(config_path)
        self.last_error = ""

    @property
    def config_path(self) -> Path:
        """作用：执行“config_path”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        return self._manager.config_path

    def load(self) -> ApplicationSettings:
        """作用：执行“load”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        raw = self.load_raw()
        return ApplicationSettings.from_config(raw)

    def load_raw(self) -> Dict[str, Any]:
        """作用：执行“load_raw”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        raw = self._manager.load()
        self.last_error = self._manager.last_error
        return raw

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        """作用：执行“save”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        return self.save_raw(settings.to_config(), preserve_users=preserve_users)

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        """作用：执行“save_raw”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        success = self._manager.save(config, preserve_users=preserve_users)
        self.last_error = self._manager.last_error
        return success
