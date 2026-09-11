"""Small conversion helpers for configuration-backed models."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Type, TypeVar


EnumT = TypeVar("EnumT", bound=Enum)


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def as_str_list(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [item for item in value if isinstance(item, str)]


def as_enum(enum_type: Type[EnumT], value: Any, default: EnumT) -> EnumT:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def unknown_fields(data: Mapping[str, Any], known_fields: Iterable[str]) -> Dict[str, Any]:
    known = set(known_fields)
    return {key: deepcopy(value) for key, value in data.items() if key not in known}

