"""
文件名：src/models/runtime.py
文件作用：纯数据模型与业务契约模块“runtime”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

运行基础设施初始化、命令和退出结果模型。
"""

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
