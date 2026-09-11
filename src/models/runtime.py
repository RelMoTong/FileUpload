"""Runtime infrastructure result models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class RuntimeCommandResult:
    success: bool
    enabled: bool = False
    messages: Tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class RuntimeInitializationResult:
    app_dir: Path
    log_available: bool
    error: str = ""


@dataclass(frozen=True)
class LifecycleShutdownResult:
    order: Tuple[str, ...]
    errors: Tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return not self.errors
