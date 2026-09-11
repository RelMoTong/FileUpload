"""Canonical local-path relationship checks used before upload startup."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable, Tuple


@dataclass(frozen=True)
class LocalPathConflict:
    left_label: str
    left_path: str
    right_label: str
    right_path: str
    relation: str


def _looks_like_unc(path: str) -> bool:
    value = path.replace("/", "\\")
    return value.startswith("\\\\")


def normalize_local_path(path: str) -> str:
    """Normalize a local/UNC path without probing a remote UNC endpoint.

    Existing local aliases are resolved so a symlink cannot bypass the
    relationship gate. UNC values are normalized lexically; reachability is a
    separate bounded I/O concern handled by the later path-probe task.
    """
    value = os.path.expandvars(os.path.expanduser(str(path).strip()))
    if not value:
        return ""
    if _looks_like_unc(value):
        normalized = os.path.normpath(value)
    else:
        normalized = os.path.realpath(os.path.abspath(value))
    return os.path.normcase(os.path.normpath(normalized))


def _relationship(left: str, right: str) -> str:
    if left == right:
        return "same"
    try:
        common = os.path.commonpath((left, right))
    except (OSError, ValueError):
        return "separate"
    if common == left:
        return "ancestor"
    if common == right:
        return "descendant"
    return "separate"


def find_local_path_conflicts(
    labeled_paths: Iterable[Tuple[str, str]],
) -> tuple[LocalPathConflict, ...]:
    """Return all equal or ancestor/descendant conflicts for named paths."""
    entries = [
        (label, path, normalize_local_path(path))
        for label, path in labeled_paths
        if str(path).strip()
    ]
    conflicts: list[LocalPathConflict] = []
    for index, (left_label, left_path, left_normalized) in enumerate(entries):
        for right_label, right_path, right_normalized in entries[index + 1 :]:
            relation = _relationship(left_normalized, right_normalized)
            if relation != "separate":
                conflicts.append(
                    LocalPathConflict(
                        left_label=left_label,
                        left_path=left_path,
                        right_label=right_label,
                        right_path=right_path,
                        relation=relation,
                    )
                )
    return tuple(conflicts)


def describe_local_path_conflict(conflict: LocalPathConflict) -> str:
    if conflict.relation == "same":
        return (
            f"{conflict.left_label}与{conflict.right_label}路径相同："
            f"{conflict.left_path}"
        )
    if conflict.relation == "ancestor":
        container_label, container_path = conflict.left_label, conflict.left_path
        nested_label, nested_path = conflict.right_label, conflict.right_path
    else:
        container_label, container_path = conflict.right_label, conflict.right_path
        nested_label, nested_path = conflict.left_label, conflict.left_path
    return (
        f"{conflict.left_label}与{conflict.right_label}目录嵌套："
        f"{container_label} {container_path} 包含 {nested_label} {nested_path}"
    )
