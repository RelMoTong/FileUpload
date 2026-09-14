"""Models shared by the asynchronous path-probe boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PathProbe:
    label: str
    path: str
    require_write: bool = False


@dataclass(frozen=True)
class PathProbeResult:
    generation: int
    errors: tuple[str, ...] = ()
    timed_out: bool = False
    cancelled: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.cancelled
