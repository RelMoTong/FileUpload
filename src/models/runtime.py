"""运行基础设施初始化、命令和退出结果模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class RuntimeCommandResult:
    """表示单项运行时命令的执行结果和可展示信息。"""
    success: bool
    enabled: bool = False
    messages: Tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class RuntimeInitializationResult:
    """表示应用目录和日志设施初始化后的状态。"""
    app_dir: Path
    log_available: bool
    error: str = ""


@dataclass(frozen=True)
class LifecycleShutdownResult:
    """记录应用退出时已执行的顺序和未完成的错误。"""
    order: Tuple[str, ...]
    errors: Tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        """所有退出步骤均未记录错误时返回真。"""
        return not self.errors
