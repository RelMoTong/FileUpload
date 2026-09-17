"""供清理和归档共用的“失败即保留文件”删除策略。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from typing import Callable, Literal


DeletionMode = Literal["trash", "permanent"]
IdentityVerifier = Callable[[], tuple[bool, str]]


def trash_supported() -> bool:
    """判断当前环境是否具备回收站删除能力。"""
    try:
        from send2trash import send2trash  # noqa: F401
        return True
    except ImportError:
        return os.name == "nt"


def send_to_trash(path: str) -> None:
    """把一个路径移入回收站；失败时抛出异常，绝不偷偷改为永久删除。

    用途：为清理和归档提供可恢复的底层删除操作。
    输入：待移入回收站的文件或目录路径【path】。
    输出：成功时无返回；失败时抛出异常。
    关键步骤：优先使用跨平台库，Windows 缺少该库时调用系统 Shell API。
    风险点：不支持回收站时必须失败，不能静默退化为永久删除。
    """
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    # 优先使用跨平台库；Windows 未安装该库时才退回系统 Shell API。
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

    # FOF_ALLOWUNDO 保留撤销能力，FOF_NOCONFIRMATION/FOF_SILENT 避免后台任务阻塞弹窗。
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
    """执行经过身份复核的删除，任何失败都不自动降级为永久删除。"""

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
        """按固定安全顺序执行一次删除请求。

        用途：在实际删除前完成目录边界、授权和文件身份复核。
        输入：包含路径、允许目录、身份校验器和删除模式的【SafeDeletionRequest】。
        输出：不抛出底层删除异常的【SafeDeletionResult】。
        关键步骤：限定允许目录 → 校验删除模式/授权 → 复核文件身份 → 检查回收站 →
        调用删除操作。
        风险点：每一步失败都会立即返回，文件保持原状；自动任务绝不可降级为永久删除。
        """
        # 1. 路径必须位于本次已经验证过的目录内，阻止越界删除。
        if not self._is_allowed_file(request.path, request.allowed_roots):
            return SafeDeletionResult(False, "protected_path", "文件不在允许的清理目录内")
        # 2. 自动任务永远禁止永久删除；手动永久删除必须携带二次授权。
        if request.mode == "permanent":
            if request.automatic:
                return SafeDeletionResult(False, "automatic_permanent_forbidden", "自动任务禁止永久删除")
            if not request.permanent_authorized:
                return SafeDeletionResult(False, "permanent_not_authorized", "永久删除缺少显式授权")
        elif request.mode != "trash":
            return SafeDeletionResult(False, "invalid_mode", "未知删除模式")

        # 3. 删除前重新确认扫描快照仍然对应当前文件。
        try:
            identity_matches, reason = request.identity_verifier()
        except Exception as exc:
            return SafeDeletionResult(False, "identity_check_failed", f"无法确认文件身份: {type(exc).__name__}: {exc}")
        if not identity_matches:
            return SafeDeletionResult(False, "identity_changed", reason or "文件身份已变化")

        # 4. 回收站不可用时安全停止；只有明确授权的手动操作可走永久删除。
        if request.mode == "trash":
            if not self._is_trash_available():
                return SafeDeletionResult(False, "trash_unavailable", "回收站不可用，文件已保留")
            operation = self._move_to_trash
        else:
            operation = self._remove_file
        # 5. 真实文件操作失败时向调用方返回失败，供审计和界面展示。
        try:
            operation(request.path)
        except Exception as exc:
            return SafeDeletionResult(False, "delete_failed", f"删除失败: {type(exc).__name__}: {exc}")
        return SafeDeletionResult(True, "deleted", "已移入回收站" if request.mode == "trash" else "已永久删除")

    @staticmethod
    def _is_allowed_file(path: str, allowed_roots: tuple[str, ...]) -> bool:
        """规范化真实路径后，确认待删对象是允许目录下的普通文件。"""
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
