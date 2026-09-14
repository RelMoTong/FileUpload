"""One fail-closed deletion policy shared by cleanup and archive workflows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from typing import Callable, Literal


DeletionMode = Literal["trash", "permanent"]
IdentityVerifier = Callable[[], tuple[bool, str]]


def trash_supported() -> bool:
    try:
        from send2trash import send2trash  # noqa: F401
        return True
    except ImportError:
        return os.name == "nt"


def send_to_trash(path: str) -> None:
    """Move one path to the recycle bin, or raise without deleting it."""
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    if send2trash is not None:
        send2trash(path)
        return
    if os.name != "nt":
        raise RuntimeError("Trash not supported without send2trash")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    operation = SHFILEOPSTRUCTW(
        0, 3, path + "\0\0", None, 0x40 | 0x10 | 0x4, False, None, None
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(result, "Send to Recycle Bin failed", path)


@dataclass(frozen=True)
class SafeDeletionRequest:
    path: str
    allowed_roots: tuple[str, ...]
    identity_verifier: IdentityVerifier
    mode: DeletionMode = "trash"
    permanent_authorized: bool = False
    automatic: bool = False


@dataclass(frozen=True)
class SafeDeletionResult:
    success: bool
    status: str
    message: str


class SafeDeletionPolicy:
    """Perform a verified deletion with no implicit permanent-delete fallback."""

    def __init__(
        self,
        is_trash_available: Callable[[], bool] | None = None,
        move_to_trash: Callable[[str], None] | None = None,
        remove_file: Callable[[str], None] | None = None,
    ) -> None:
        self._is_trash_available = is_trash_available or trash_supported
        self._move_to_trash = move_to_trash or send_to_trash
        self._remove_file = remove_file or os.remove

    def delete(self, request: SafeDeletionRequest) -> SafeDeletionResult:
        if not self._is_allowed_file(request.path, request.allowed_roots):
            return SafeDeletionResult(False, "protected_path", "文件不在允许的清理目录内")
        if request.mode == "permanent":
            if request.automatic:
                return SafeDeletionResult(False, "automatic_permanent_forbidden", "自动任务禁止永久删除")
            if not request.permanent_authorized:
                return SafeDeletionResult(False, "permanent_not_authorized", "永久删除缺少显式授权")
        elif request.mode != "trash":
            return SafeDeletionResult(False, "invalid_mode", "未知删除模式")

        try:
            identity_matches, reason = request.identity_verifier()
        except Exception as exc:
            return SafeDeletionResult(False, "identity_check_failed", f"无法确认文件身份: {type(exc).__name__}: {exc}")
        if not identity_matches:
            return SafeDeletionResult(False, "identity_changed", reason or "文件身份已变化")

        if request.mode == "trash":
            if not self._is_trash_available():
                return SafeDeletionResult(False, "trash_unavailable", "回收站不可用，文件已保留")
            operation = self._move_to_trash
        else:
            operation = self._remove_file
        try:
            operation(request.path)
        except Exception as exc:
            return SafeDeletionResult(False, "delete_failed", f"删除失败: {type(exc).__name__}: {exc}")
        return SafeDeletionResult(True, "deleted", "已移入回收站" if request.mode == "trash" else "已永久删除")

    @staticmethod
    def _is_allowed_file(path: str, allowed_roots: tuple[str, ...]) -> bool:
        if not allowed_roots or not path or not os.path.isfile(path):
            return False
        candidate = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        for root in allowed_roots:
            if not root:
                continue
            normalized_root = os.path.normcase(os.path.realpath(os.path.abspath(root)))
            try:
                if os.path.commonpath((candidate, normalized_root)) == normalized_root:
                    return candidate != normalized_root
            except ValueError:
                continue
        return False
