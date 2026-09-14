"""Thread-safe composable pause state for long-running workers."""

from __future__ import annotations

import threading
from typing import FrozenSet


class PauseState:
    """Track independent pause reasons without allowing one to clear another."""

    VALID_REASONS = frozenset({"manual", "network", "disk", "stopping"})

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reasons: set[str] = set()

    def set(self, reason: str, active: bool) -> bool:
        if reason not in self.VALID_REASONS:
            raise ValueError(f"unknown pause reason: {reason}")
        with self._lock:
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
        with self._lock:
            self._reasons.clear()
