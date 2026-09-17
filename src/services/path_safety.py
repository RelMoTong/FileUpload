"""
文件名：src/services/path_safety.py
文件作用：业务服务层的“path_safety”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。

Canonical local-path relationship checks used before upload startup.
"""

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
    """内部辅助：完成“_looks_like_unc”对应的既有局部工作。"""
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
    """内部辅助：完成“_relationship”对应的既有局部工作。"""
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
    """作用：执行“describe_local_path_conflict”的既有业务服务职责。

    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
    """
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
