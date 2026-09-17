"""异步路径探测边界共用的请求与结果模型。"""

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
