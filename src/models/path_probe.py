"""
文件名：src/models/path_probe.py
文件作用：纯数据模型与业务契约模块“path_probe”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

异步路径探测边界共用的请求与结果模型。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PathProbe:
    """描述一次需要后台验证的路径及其写入要求。"""
    label: str
    path: str
    require_write: bool = False


@dataclass(frozen=True)
class PathProbeResult:
    """描述同一代路径探测的错误、超时或取消结果。"""
    generation: int
    errors: tuple[str, ...] = ()
    timed_out: bool = False
    cancelled: bool = False

    @property
    def is_valid(self) -> bool:
        """仅当没有错误且未取消时，路径探测结果才可用于后续操作。"""
        return not self.errors and not self.cancelled
