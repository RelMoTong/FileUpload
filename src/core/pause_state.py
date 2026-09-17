"""供长时间运行 Worker 使用的线程安全、可组合暂停状态。"""

from __future__ import annotations

import threading
from typing import FrozenSet


class PauseState:
    """分别记录暂停原因，避免一个原因恢复时误清除其他暂停原因。"""

    VALID_REASONS = frozenset({"manual", "network", "disk", "stopping"})

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reasons: set[str] = set()

    def set(self, reason: str, active: bool) -> bool:
        """启用或解除一个暂停原因，并返回整体暂停状态是否发生变化。

        用途：让手动、网络、磁盘和停止等暂停来源可以叠加存在。
        输入：受支持的暂停原因【reason】及其启用状态【active】。
        输出：所有原因合并后的暂停状态是否发生切换。
        关键步骤：在锁内保存修改前状态，再增删指定原因并比较修改后状态。
        风险点：未知原因必须立即拒绝，避免拼写错误导致 Worker 无法恢复。
        """
        if reason not in self.VALID_REASONS:
            raise ValueError(f"unknown pause reason: {reason}")
        with self._lock:
            # 先保存聚合状态，调用方据此决定是否需要刷新界面或通知 Worker。
            before = bool(self._reasons)
            if active:
                self._reasons.add(reason)
            else:
                self._reasons.discard(reason)
            return before != bool(self._reasons)

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return bool(self._reasons)

    @property
    def reasons(self) -> FrozenSet[str]:
        with self._lock:
            return frozenset(self._reasons)

    def clear(self) -> None:
        """仅在启动新会话或彻底停止时清空所有暂停原因。"""
        with self._lock:
            self._reasons.clear()
