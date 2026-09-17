"""手动清理与自动清理的文件系统规则、后台 Worker 和生命周期管理。
文件名：src/services/cleanup_service.py
文件作用：业务服务层的“cleanup_service”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。


这里是清理功能真正接触文件系统的地方：扫描、候选排序、文件身份复核、回收站
删除和审计记录都在本模块完成。界面层只展示结果，控制器只协调调用，因此不能把
文件删除规则复制到 UI 或控制器中。
"""

from __future__ import annotations

import datetime
import ctypes
from ctypes import wintypes
from dataclasses import replace
import heapq
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Protocol, Tuple, cast
import uuid

from PySide6 import QtCore

from src.models import (
    AutoCleanupRequest,
    AutoCleanupResult,
    CleanupCandidate,
    CleanupCommandResult,
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupScanRequest,
    CleanupValidationResult,
)
from src.core.safe_deletion import (
    SafeDeletionPolicy,
    SafeDeletionRequest,
    send_to_trash,
    trash_supported,
)


AUTO_CLEANUP_FAILURE_LIMIT = 20
CLEANUP_RECORD_READ_LIMIT = 100
# 扫描结果必须持续推送到界面，但不能每个文件都跨线程发一次信号，否则大量排队
# 信号会让取消操作看起来像卡死。以下两个边界只控制“传递批次”，不会限制显示
# 的总文件数；字节总量始终保留为 Python 整数，不经过 Qt 32 位整数。
SCAN_PROGRESS_INTERVAL_SECONDS = 0.2
SCAN_RESULT_BATCH_INTERVAL_SECONDS = 0.1
SCAN_RESULT_BATCH_SIZE = 128
CleanupEventCallback = Callable[[str, Dict[str, Any]], None]
logger = logging.getLogger(__name__)


def _stat_mtime_ns(stat_result: Any) -> int:
    """取得可稳定比较的“纳秒级修改时间”。

    用途：为扫描快照保存整数时间，删除前可判断同路径文件是否已被替换或修改。
    输入：``os.stat`` 或 ``DirEntry.stat`` 返回的对象。
    输出：Python ``int``，优先使用文件系统提供的 ``st_mtime_ns``。
    关键步骤：旧文件系统没有纳秒字段时，用秒级时间换算为纳秒整数。
    风险点：不能使用格式化后的时间文本做比较，否则精度丢失会误删后来变化的文件。
    """
    value = getattr(stat_result, "st_mtime_ns", None)
    if value is not None:
        return int(value)
    return int(round(float(stat_result.st_mtime) * 1_000_000_000))


def _stat_file_id(stat_result: Any) -> str:
    """尽力取得文件系统标识，用于补强路径、大小和时间的身份校验。

    用途：同一路径被删除后又创建新文件时，尽可能识别这是不同的文件对象。
    输入：文件的 ``stat`` 结果。
    输出：有 inode 时返回 ``设备号:inode``，不支持时返回空字符串。
    关键步骤：先确认 inode 大于零，再拼接设备号，避免把无效的 ``0`` 当作真实身份。
    风险点：某些网络文件系统不稳定或不提供 inode，所以调用方必须允许空字符串。
    """
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    if inode <= 0:
        return ""
    device = int(getattr(stat_result, "st_dev", 0) or 0)
    return f"{device}:{inode}"


def _file_identity_changes(item: CleanupFileItem, stat_result: Any) -> tuple[str, ...]:
    """把界面候选转换为统一快照后，复用候选身份比较逻辑。"""
    return _candidate_identity_changes(
        CleanupCandidate(
            path=item.path,
            root_path="",
            size=item.size,
            mtime=item.mtime,
            mtime_ns=item.mtime_ns,
            file_id=item.file_id,
        ),
        stat_result,
    )


def _candidate_identity_changes(candidate: CleanupCandidate, stat_result: Any) -> tuple[str, ...]:
    """比较扫描快照和当前文件，但不执行删除。

    用途：删除前的最后一道“同一文件”确认。
    输入：扫描时保存的候选信息，以及删除前刚读取到的 ``stat`` 信息。
    输出：发生变化的字段名元组；空元组表示本次比较通过。
    关键步骤：依次比较大小、纳秒修改时间和可用的文件系统标识。
    风险点：只按路径删除存在 TOCTOU 风险；本函数的结果必须被删除策略严格处理。
    """
    changes: list[str] = []
    if int(stat_result.st_size) != int(candidate.size):
        changes.append("文件大小")
    if candidate.mtime_ns and _stat_mtime_ns(stat_result) != int(candidate.mtime_ns):
        changes.append("修改时间")
    if candidate.file_id and _stat_file_id(stat_result) != candidate.file_id:
        changes.append("文件标识")
    return tuple(changes)


def iter_cleanup_candidates(
    roots: Iterable[str],
    formats: Iterable[str],
    *,
    keep_days: int = 0,
    cancel_event: Any = None,
    on_error: Optional[Callable[[str, BaseException], None]] = None,
) -> Iterable[CleanupCandidate]:
    """以深度优先方式流式产生清理候选，不建立整盘文件索引。

    用途：让手动预览和自动清理共用同一套候选定义，避免两种模式扫出不同文件。
    输入：清理根目录、允许的扩展名、可选保留天数、协作取消事件和访问错误回调。
    输出：逐个 ``yield`` 已完成 ``stat`` 采样的 ``CleanupCandidate``。
    关键步骤：去重根目录、使用 ``os.scandir`` 遍历、过滤格式/时间、保存身份快照。
    风险点：SMB 的 ``scandir`` 或 ``stat`` 属于系统 I/O，Python 不能中途强制取消；
    因此每次 I/O 返回后都必须检查 ``cancel_event``，并始终关闭已打开的迭代器。
    """
    normalized_formats = {
        str(ext).strip().lower() for ext in formats if str(ext).strip()
    }
    cutoff = time.time() - keep_days * 86400 if keep_days > 0 else 0
    cancelled = cancel_event or _NeverCancelled()
    # 先消除重复目录和父子嵌套目录，避免同一文件因多个根目录被重复扫描。
    for monitor_root in CleanupService.deduplicate_cleanup_roots(roots):
        stack: list[Any] = []
        try:
            # 栈保存当前深度优先路径上的 scandir 迭代器；不使用 os.walk，
            # 是为了能在每一层及时检查取消状态和精确关闭句柄。
            stack.append(os.scandir(monitor_root))
            while stack and not cancelled.is_set():
                try:
                    entry = next(stack[-1])
                except StopIteration:
                    stack.pop().close()
                    continue
                except OSError as exc:
                    if on_error:
                        on_error(getattr(exc, "filename", None) or monitor_root, exc)
                    stack.pop().close()
                    continue
                try:
                    # 先判断目录，再判断文件；符号链接不跟随，防止循环目录或越界访问。
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(os.scandir(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if normalized_formats and Path(entry.name).suffix.lower() not in normalized_formats:
                            continue
                        stat_result = os.stat(entry.path, follow_symlinks=False)
                        if cutoff and stat_result.st_mtime > cutoff:
                            continue
                        yield CleanupCandidate(
                            path=entry.path,
                            root_path=monitor_root,
                            size=int(stat_result.st_size),
                            mtime=float(stat_result.st_mtime),
                            mtime_ns=_stat_mtime_ns(stat_result),
                            file_id=_stat_file_id(stat_result),
                            created_at=CleanupService.file_created_at(stat_result),
                        )
                except OSError as exc:
                    if on_error:
                        on_error(getattr(exc, "filename", None) or entry.path, exc)
        except OSError as exc:
            if on_error:
                on_error(getattr(exc, "filename", None) or monitor_root, exc)
        finally:
            # 无论取消、访问错误还是正常完成，都必须关闭尚未遍历结束的目录句柄。
            while stack:
                stack.pop().close()


class _NewestCandidate:
    """反转堆比较顺序，使固定容量堆始终保留修改时间最早的候选。"""

    def __init__(self, candidate: CleanupCandidate) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self.candidate = candidate
        self.key = (
            int(candidate.mtime_ns),
            os.path.normcase(os.path.abspath(candidate.path)),
        )

    def __lt__(self, other: "_NewestCandidate") -> bool:
        """内部辅助：完成“__lt__”对应的既有局部工作。"""
        return self.key > other.key


def oldest_cleanup_candidates(
    roots: Iterable[str], formats: Iterable[str], *, limit: int,
    cancel_event: Any = None, on_error: Optional[Callable[[str, BaseException], None]] = None,
) -> tuple[tuple[CleanupCandidate, ...], int]:
    """扫描一次，只在内存中保留“最旧”的有限批候选。

    用途：自动清理需要按最旧优先删除，但不能把超大目录的全部文件放进内存。
    输入：根目录、格式、保留上限以及取消/错误回调。
    输出：按修改时间从旧到新排序的有限候选元组，以及本轮扫描到的文件总数。
    关键步骤：以反向堆保存当前最旧的 ``limit`` 个文件，较新的文件会立即被丢弃。
    风险点：此函数只用于自动清理；手动清理需要给用户展示所有候选，不能用它截断结果。
    """
    capacity = max(1, int(limit))
    heap: list[_NewestCandidate] = []
    scanned = 0
    for candidate in iter_cleanup_candidates(
        roots, formats, cancel_event=cancel_event, on_error=on_error
    ):
        scanned += 1
        entry = _NewestCandidate(candidate)
        if len(heap) < capacity:
            heapq.heappush(heap, entry)
        elif entry.key < heap[0].key:
            heapq.heapreplace(heap, entry)
    return tuple(
        entry.candidate for entry in sorted(heap, key=lambda item: item.key)
    ), scanned


def sort_cleanup_file_items(files: Iterable[CleanupFileItem]) -> list[CleanupFileItem]:
    """为需要全量排序的旧调用方提供稳定的“最旧优先”排序。"""
    return sorted(
        files,
        key=lambda item: (
            int(item.mtime_ns or round(item.mtime * 1_000_000_000)),
            os.path.normcase(os.path.abspath(item.path)),
        ),
    )


class _NeverCancelled:
    """为未传入取消事件的同步调用提供始终为假的兼容对象。"""

    def is_set(self) -> bool:
        """作用：执行“is_set”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return False


class CleanupAuditWriter(Protocol):
    """自动/永久删除写入审计记录所需的最小接口。"""
    last_error: str

    def write(self, event: str, run_id: str, **fields: Any) -> bool:
        """协议占位：声明“write”的最小调用约定，由实现方提供既有行为。"""
        ...


class _ScanWorker(QtCore.QObject):
    """运行在 QThread 中的手动扫描 Worker。

    用途：把目录遍历放到后台线程，并把结果与进度通过 Qt 队列信号交给主线程。
    输入：构造时接收的 ``CleanupScanRequest``。
    输出：日志、进度、小批结果和最终汇总事件。
    关键步骤：候选流式生成、按时间/数量批量发送、取消后发送已完成的最后小批次。
    风险点：不可在这里操作 Qt 窗口；网络 I/O 不可强杀，只能协作取消。
    """
    worker_event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(self, request: CleanupScanRequest) -> None:
        """保存不可变扫描请求，并创建仅属于本 Worker 的协作取消事件。"""
        super().__init__()
        self.request = request
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """设置取消标记；当前 ``stat/scandir`` 返回后循环会尽快结束。"""
        self._cancel_event.set()

    def is_set(self) -> bool:
        """实现候选迭代器需要的最小 Event 接口。"""
        return self._cancel_event.is_set()

    @QtCore.Slot()
    def run(self) -> None:
        """在后台流式扫描，并把已发现文件分批交给界面。

        用途：让用户在扫描尚未结束时就看到候选文件，同时保持取消操作可响应。
        输入：构造时保存的目录、格式和保留天数请求。
        输出：持续发送【scan_items】和【scan_progress】事件，最后发送扫描汇总。
        关键步骤：逐个产生候选、累积小批次、定时发送进度、收到取消后尽快结束。
        风险点：网络盘的系统 I/O 不能被 Python 强制中断，因此取消只能在当前 I/O 返回后生效。
        """
        pending_items: list[CleanupFileItem] = []
        total_size_bytes = 0
        file_count = 0
        last_progress_at = 0.0
        last_result_at = 0.0
        self.worker_event.emit("log", {"message": "开始扫描文件..."})
        for folder in CleanupService.deduplicate_cleanup_roots(self.request.folders):
            self.worker_event.emit("log", {"message": f"扫描目录: {folder}"})

        def on_error(path: str, exc: BaseException) -> None:
            # 扫描单个文件失败不应终止整次扫描；交给 UI 日志供现场定位权限/网络问题。
            """作用：执行“on_error”的既有业务服务职责。

            参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
            执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
            风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
            """
            self.worker_event.emit("log", {"message": f"无法访问文件 {path}: {exc}"})

        def flush_items() -> None:
            """把当前小批次移交给界面，随后立即释放 Worker 对该列表的引用。"""
            nonlocal pending_items
            if not pending_items:
                return
            # Qt 跨线程信号需要复制/排队；传出元组后立即换新列表，避免 Worker 持有
            # 全量候选，也避免每个文件一条信号淹没主线程事件队列。
            self.worker_event.emit("scan_items", {"files": tuple(pending_items)})
            pending_items = []

        for candidate in iter_cleanup_candidates(
            self.request.folders,
            self.request.formats,
            keep_days=self.request.keep_days,
            cancel_event=self,
            on_error=on_error,
        ):
            if self.is_set():
                break
            pending_items.append(candidate.as_file_item())
            file_count += 1
            # 必须保持 Python 任意精度整数，现场总容量可超过 100 TB。
            total_size_bytes += int(candidate.size)
            now = time.monotonic()
            if (
                file_count == 1
                or len(pending_items) >= SCAN_RESULT_BATCH_SIZE
                or now - last_result_at >= SCAN_RESULT_BATCH_INTERVAL_SECONDS
            ):
                flush_items()
                last_result_at = now
            if file_count == 1 or now - last_progress_at >= SCAN_PROGRESS_INTERVAL_SECONDS:
                last_progress_at = now
                self.worker_event.emit(
                    "scan_progress",
                    {
                        "current_dir": os.path.dirname(candidate.path),
                        "file_count": file_count,
                        "total_size_bytes": total_size_bytes,
                    },
                )
        # 即使用户取消，也把已经完成 stat 的最后一小批结果交给界面；关闭后的对话框
        # 已解除监听，不会再接收这些事件。
        flush_items()
        if self.is_set():
            self.worker_event.emit("log", {"message": "扫描已取消"})
        self.finished.emit(
            {
                "file_count": file_count,
                "total_size_bytes": total_size_bytes,
                "cancelled": self.is_set(),
            }
        )


class _DeleteWorker(QtCore.QObject):
    """运行在 QThread 中的手动删除 Worker，执行逐文件身份复核和审计。

    用途：让回收站/永久删除不阻塞界面，并在每个文件之间允许协作取消。
    输入：用户二次确认后的 ``CleanupDeleteRequest`` 和可选审计写入器。
    输出：删除进度、单文件失败日志与最终删除汇总。
    关键步骤：删除前复核身份；永久删除先写意图审计；调用统一安全删除策略；记录结果。
    风险点：扫描后文件可能被替换，绝不能仅凭旧路径删除；永久删除缺少审计必须拒绝执行。
    """
    worker_event = QtCore.Signal(str, object)
    finished = QtCore.Signal(object)

    def __init__(
        self,
        request: CleanupDeleteRequest,
        audit_writer: Optional[CleanupAuditWriter] = None,
    ) -> None:
        """保存已经确认的删除请求；请求对象内含允许根目录和永久删除授权。"""
        super().__init__()
        self.request = request
        self._audit_writer = audit_writer
        self._cancelled = False

    def cancel(self) -> None:
        """请求在当前文件处理完成后停止后续删除。"""
        self._cancelled = True

    @QtCore.Slot()
    def run(self) -> None:
        """逐个执行安全删除，并把剩余文件交回 UI 刷新列表。

        用途：保证任何删除都经过路径范围、身份、模式和审计检查。
        输入：构造时保存的文件列表与删除模式。
        输出：进度事件及 ``deleted_count``、``failed_count``、``remaining_files`` 汇总。
        关键步骤：取消检查、删除前身份比较、永久删除意图审计、调用策略、结果审计。
        风险点：不要把异常当成成功；身份不一致必须跳过而非重试删除当前路径。
        """
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        skipped_changed_count = 0
        policy = SafeDeletionPolicy(trash_supported, send_to_trash, os.remove)
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        total = len(self.request.files)
        for index, item in enumerate(self.request.files, start=1):
            if self._cancelled:
                self.worker_event.emit("log", {"message": "删除任务已取消"})
                break
            try:
                def verify_identity() -> tuple[bool, str]:
                    # SafeDeletionPolicy 在真正执行删除前再次调用该闭包，缩小扫描到删除
                    # 之间的竞态窗口。这里不捕获异常，让策略将其视为安全失败。
                    """作用：执行“verify_identity”的既有业务服务职责。

                    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
                    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
                    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
                    """
                    stat_result = os.stat(item.path, follow_symlinks=False)
                    changes = _file_identity_changes(item, stat_result)
                    return (
                        not changes,
                        "、".join(changes) if changes else "",
                    )

                # 永久删除不可恢复，因此比回收站模式多一道“意图审计”防线。
                if not self.request.use_trash:
                    identity_matches, identity_reason = verify_identity()
                    if not identity_matches:
                        skipped_changed_count += 1
                        self.worker_event.emit(
                            "log",
                            {"message": f"已跳过扫描后发生变化的文件 {item.path}: {identity_reason}"},
                        )
                        continue
                    if self._audit_writer is None or not self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_INTENT",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                        allowed_roots=list(self.request.allowed_roots),
                        authorization="ui_second_confirmation",
                    ):
                        failed_count += 1
                        self.worker_event.emit(
                            "log",
                            {"message": f"删除失败 {item.path}: 永久删除审计日志写入失败，文件已保留"},
                        )
                        continue

                # 所有模式都必须进入同一安全策略，集中校验允许目录、身份和授权状态。
                result = policy.delete(
                    SafeDeletionRequest(
                        path=item.path,
                        allowed_roots=self.request.allowed_roots,
                        identity_verifier=verify_identity,
                        mode="trash" if self.request.use_trash else "permanent",
                        permanent_authorized=self.request.permanent_authorized,
                    )
                )
                if not result.success:
                    if not self.request.use_trash and self._audit_writer is not None:
                        self._audit_writer.write(
                            "MANUAL_PERMANENT_DELETE_FAIL",
                            run_id,
                            path=item.path,
                            size_bytes=int(item.size),
                            status=result.status,
                            error=result.message,
                        )
                    if result.status == "identity_changed":
                        skipped_changed_count += 1
                        message = f"已跳过扫描后发生变化的文件 {item.path}: {result.message}"
                    else:
                        failed_count += 1
                        message = f"删除失败 {item.path}: {result.message}"
                    self.worker_event.emit("log", {"message": message})
                    continue
                deleted_count += 1
                deleted_size += item.size
                if not self.request.use_trash and self._audit_writer is not None:
                    if not self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_OK",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                    ):
                        self.worker_event.emit(
                            "log",
                            {
                                "message": (
                                    f"审计告警 {item.path}: 文件已永久删除，"
                                    "但结果审计写入失败，请立即保留当前日志"
                                )
                            },
                        )
            except Exception as exc:
                failed_count += 1
                if not self.request.use_trash and self._audit_writer is not None:
                    self._audit_writer.write(
                        "MANUAL_PERMANENT_DELETE_FAIL",
                        run_id,
                        path=item.path,
                        size_bytes=int(item.size),
                        status="exception",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self.worker_event.emit("log", {"message": f"删除失败 {item.path}: {exc}"})
            finally:
                self.worker_event.emit("delete_progress", {"current": index, "total": total})
        # 通过文件是否仍存在刷新界面候选；不把失败文件从用户视图静默移除。
        remaining = tuple(item for item in self.request.files if os.path.exists(item.path))
        self.finished.emit(
            {
                "deleted_count": deleted_count,
                "deleted_size": deleted_size,
                "failed_count": failed_count,
                "skipped_changed_count": skipped_changed_count,
                "remaining_files": remaining,
            }
        )


class _ManualEventBridge(QtCore.QObject):
    """把 Worker 的 Qt 信号转换为控制器能够处理的普通 Python 回调。"""
    def __init__(self, callback: CleanupEventCallback) -> None:
        """保存控制器回调；桥接对象会随对应 QThread 生命周期被服务层持有。"""
        super().__init__()
        self._callback = callback

    @QtCore.Slot(str, object)
    def on_event(self, kind: str, payload: object) -> None:
        """转发中间过程事件，异常数据类型降级为空字典而不破坏 UI 线程。"""
        self._callback(kind, dict(payload) if isinstance(payload, dict) else {})

    @QtCore.Slot(object)
    def on_scan_finished(self, result: object) -> None:
        """把新扫描汇总或旧版完整列表统一转换为控制器事件。"""
        if isinstance(result, dict):
            self._callback("scan_finished", dict(result))
            return
        # 兼容仍返回完整列表的旧 Worker，便于渐进升级或旧版扩展接入。
        self._callback(
            "scan_finished",
            {"files": list(cast(Iterable[CleanupFileItem], result))},
        )

    @QtCore.Slot(object)
    def on_delete_finished(self, result: object) -> None:
        """转发删除汇总；Worker 异常返回非字典时安全降级为空结果。"""
        self._callback("delete_finished", dict(result) if isinstance(result, dict) else {})


class CleanupService:
    """清理业务服务：管理手动 QThread，并执行受边界保护的自动清理。

    用途：集中存放文件系统规则，避免 UI 或控制器直接创建 Worker、删除文件。
    输入：扫描/删除/自动清理请求以及可选审计写入器。
    输出：同步命令结果和异步事件；自动清理返回结构化结果。
    关键步骤：校验输入、创建 Worker+QThread+事件桥、释放资源、按策略扫描/删除。
    风险点：线程引用必须保留到 ``thread.finished``；若提前清空，Qt 可能销毁运行中的线程。
    """
    def __init__(
        self,
        audit_writer: Optional[CleanupAuditWriter] = None,
        thread_factory: Callable[[], Any] = QtCore.QThread,
    ) -> None:
        """初始化两套互斥的手动 Worker 引用；扫描与删除不能各自重复启动。"""
        self._audit_writer = audit_writer
        self._thread_factory = thread_factory
        self._scan_worker: Optional[_ScanWorker] = None
        self._scan_thread: Any = None
        self._scan_bridge: Optional[_ManualEventBridge] = None
        self._delete_worker: Optional[_DeleteWorker] = None
        self._delete_thread: Any = None
        self._delete_bridge: Optional[_ManualEventBridge] = None
        self.last_shutdown_errors: Tuple[str, ...] = ()
        self.manual_shutdown_status = "idle"

    @property
    def trash_available(self) -> bool:
        """返回当前平台能否安全使用回收站；自动清理必须依赖此能力。"""
        return trash_supported()

    @staticmethod
    def validate_folder_access(path: str, require_write: bool = False) -> str:
        """验证一个目录能否被安全扫描，必要时验证创建/删除权限。

        用途：在启动扫描或保存自动清理配置前提前报告路径问题。
        输入：目录路径；``require_write`` 为真时额外验证可写、可删除。
        输出：空字符串表示通过；其他字符串是可直接展示给用户的失败原因。
        关键步骤：检查存在性/目录类型、尝试 ``scandir``、可写时创建并删除临时探针文件。
        风险点：探针文件只用于权限验证，必须在 ``finally`` 清理，避免污染用户目录。
        """
        if not os.path.exists(path):
            return "路径不存在"
        if not os.path.isdir(path):
            return "不是文件夹"
        try:
            with os.scandir(path) as iterator:
                next(iterator, None)
        except PermissionError:
            return "无读取权限"
        except OSError as exc:
            return f"无法读取目录: {exc}"
        if require_write:
            probe = ""
            try:
                # 仅尝试在目标目录创建一个短生命周期文件，避免真的修改用户数据。
                fd, probe = tempfile.mkstemp(prefix=".cleanup_probe_", dir=path)
                os.close(fd)
                os.remove(probe)
            except PermissionError:
                return "无删除/写入权限"
            except OSError as exc:
                return f"无删除/写入权限: {exc}"
            finally:
                if probe and os.path.exists(probe):
                    try:
                        os.remove(probe)
                    except OSError:
                        pass
        return ""

    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult:
        """校验手动扫描请求，并返回可扫描目录与不可用目录的完整分类。

        用途：允许“部分路径不可用但其余路径仍可扫描”，而不是一处故障中止全部任务。
        输入：用户选中的目录、扩展名和保留天数。
        输出：包含有效目录、无效原因和全局错误的 ``CleanupValidationResult``。
        关键步骤：先校验必填项，再逐目录调用访问校验，最后判断是否仍至少有一个有效目录。
        风险点：不能把无效目录悄悄忽略；UI 需要收到原因以便现场人员修复权限或网络。
        """
        if not request.folders:
            return CleanupValidationResult(errors=("请至少选择一个文件夹进行扫描",))
        if not request.formats:
            return CleanupValidationResult(errors=("请至少选择一种文件格式进行扫描",))
        valid: list[str] = []
        invalid: list[str] = []
        for path in request.folders:
            reason = self.validate_folder_access(path, require_write=False)
            if reason:
                invalid.append(f"{path}：{reason}")
            else:
                valid.append(path)
        errors = () if valid else ("没有可用的扫描路径，请检查路径与权限",)
        return CleanupValidationResult(tuple(valid), tuple(invalid), errors)

    def start_scan(
        self, request: CleanupScanRequest, callback: CleanupEventCallback
    ) -> CleanupCommandResult:
        """创建手动扫描 Worker、QThread 和事件桥，然后异步启动。

        用途：把可能缓慢的目录遍历从 GUI 线程移走。
        输入：扫描请求与控制器提供的事件回调。
        输出：仅表示“是否成功启动”的命令结果；实际结果稍后以事件返回。
        关键步骤：先校验、创建三类 Qt 对象、连接队列信号、保存强引用、最后启动线程。
        风险点：桥接对象和线程不能是局部临时变量；若被垃圾回收，跨线程事件会丢失或崩溃。
        """
        validation = self.validate_scan_request(request)
        if not validation.is_valid:
            return CleanupCommandResult(False, errors=validation.errors)
        if self._scan_worker is not None:
            return CleanupCommandResult(False, "扫描任务已在运行")
        effective = CleanupScanRequest(
            validation.valid_folders, request.formats, request.keep_days
        )
        try:
            worker = _ScanWorker(effective)
            thread = self._thread_factory()
            bridge = _ManualEventBridge(callback)
            # Worker 只在自己的 QThread 执行；桥接对象留在创建它的主线程，
            # QueuedConnection 会自动把事件排回 GUI 所在的事件循环。
            worker.moveToThread(thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.worker_event.connect(bridge.on_event, queued)
            worker.finished.connect(bridge.on_scan_finished, queued)
            thread.started.connect(worker.run)
            # Worker 完成后先退出线程，再在 thread.finished 中统一清空强引用。
            worker.finished.connect(thread.quit)
            thread.finished.connect(self._release_scan)
            self._scan_worker, self._scan_thread, self._scan_bridge = worker, thread, bridge
            thread.start()
            return CleanupCommandResult(True, "扫描任务已启动")
        except Exception as exc:
            self._release_scan()
            return CleanupCommandResult(False, str(exc))

    def cancel_scan(self) -> None:
        """向当前扫描 Worker 发出协作取消请求；没有 Worker 时安全忽略。"""
        if self._scan_worker is not None:
            self._scan_worker.cancel()

    def cancel(self) -> None:
        """非阻塞请求取消扫描与删除 Worker。

        用途：供关闭窗口和应用退出路径共用。
        输入：无。
        输出：两个 Worker 的取消标记被设置；方法立即返回。
        关键步骤：扫描、删除分别设置自己的协作取消状态。
        风险点：不能在这里调用 ``wait``；网络文件系统的 I/O 可能尚未返回。
        """
        self.cancel_scan()
        cancel_delete = getattr(self._delete_worker, "cancel", None)
        if callable(cancel_delete):
            cancel_delete()

    def start_delete(
        self, request: CleanupDeleteRequest, callback: CleanupEventCallback
    ) -> CleanupCommandResult:
        """校验删除安全前提后，在独立 QThread 中启动手动删除。

        用途：确保删除不会冻结界面，并拒绝不具备授权/审计条件的危险请求。
        输入：已勾选文件、允许目录、删除模式和二次授权组成的请求，以及事件回调。
        输出：启动结果；每个文件的最终状态通过异步事件报告。
        关键步骤：检查文件/根目录/永久授权/审计器，再创建并保存 Worker、线程和事件桥。
        风险点：永久删除没有审计器时必须拒绝；同一时刻不能启动第二个删除 Worker。
        """
        if not request.files:
            return CleanupCommandResult(False, "没有选中任何文件")
        if not request.allowed_roots:
            return CleanupCommandResult(False, "删除请求缺少已验证的清理目录")
        if not request.use_trash and not request.permanent_authorized:
            return CleanupCommandResult(False, "永久删除需要显式二次授权")
        if not request.use_trash and self._audit_writer is None:
            return CleanupCommandResult(False, "永久删除需要可用的审计日志")
        if self._delete_worker is not None:
            return CleanupCommandResult(False, "删除任务已在运行")
        try:
            worker = _DeleteWorker(request, self._audit_writer)
            thread = self._thread_factory()
            bridge = _ManualEventBridge(callback)
            # 与扫描一致：文件操作只在 Worker 所在线程执行，UI 只处理排队后的事件。
            worker.moveToThread(thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.worker_event.connect(bridge.on_event, queued)
            worker.finished.connect(bridge.on_delete_finished, queued)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            thread.finished.connect(self._release_delete)
            self._delete_worker, self._delete_thread, self._delete_bridge = worker, thread, bridge
            thread.start()
            return CleanupCommandResult(True, "删除任务已启动")
        except Exception as exc:
            self._release_delete()
            return CleanupCommandResult(False, str(exc))

    @property
    def is_scanning(self) -> bool:
        """作用：执行“is_scanning”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return self._scan_worker is not None

    @property
    def is_deleting(self) -> bool:
        """作用：执行“is_deleting”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return self._delete_worker is not None

    @property
    def has_running_workers(self) -> bool:
        """检查服务层保留的 QThread 是否仍在运行。

        用途：供应用退出逻辑决定是否仍有手动清理后台工作。
        输入：无。
        输出：任一扫描/删除线程运行时返回 ``True``。
        关键步骤：优先询问 QThread；测试替身不支持时退回 Worker 是否仍存在。
        风险点：不能依赖单个布尔变量，因为线程结束和引用释放之间存在短暂时序差。
        """
        for thread, worker in (
            (self._scan_thread, self._scan_worker),
            (self._delete_thread, self._delete_worker),
        ):
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    return True
            except Exception:
                if worker is not None:
                    return True
        return False

    def shutdown_manual(self, timeout_ms: int = 10000) -> Tuple[str, ...]:
        """在应用整体退出阶段取消并有限等待手动 QThread。

        用途：让最终进程退出有机会有序回收后台线程，并记录无法停止的线程。
        输入：所有手动 Worker 共用的最长等待毫秒数。
        输出：无法在期限内停止的错误文本元组，并更新内部关闭状态。
        关键步骤：先协作取消、按剩余时间等待每个线程、成功后释放引用、失败后保留错误记录。
        风险点：此方法会等待，不能用于普通对话框关闭；网络 I/O 卡住时最多等待给定期限。
        """
        self.cancel()
        scan_thread = self._scan_thread
        delete_thread = self._delete_thread
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
        errors: list[str] = []
        # 计算一个共享截止时间，而非每条线程都等待完整 timeout，避免总等待时间翻倍。
        for name, thread, release in (
            ("扫描", scan_thread, self._release_scan),
            ("删除", delete_thread, self._release_delete),
        ):
            if thread is None:
                continue
            thread.quit()
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            try:
                stopped = thread.wait(remaining_ms)
            except TypeError as exc:
                error = (
                    f"{name} QThread 不支持带超时的 wait({remaining_ms})，"
                    "任务已标记为失败挂起"
                )
                logger.error("%s: %s", error, exc)
                errors.append(error)
                thread.quit()
                continue
            if stopped is False:
                error = f"{name} QThread 在 {timeout_ms}ms 内未停止，任务已标记为失败挂起"
                logger.error(error)
                errors.append(error)
                thread.quit()
                continue
            release()
        self.last_shutdown_errors = tuple(errors)
        self.manual_shutdown_status = "failed_hung" if errors else "stopped"
        return self.last_shutdown_errors

    def _release_scan(self) -> None:
        """仅在线程结束后释放扫描相关强引用，允许 Qt 安全销毁对象。"""
        self._scan_worker = None
        self._scan_thread = None
        self._scan_bridge = None

    def _release_delete(self) -> None:
        """仅在线程结束后释放删除相关强引用，允许 Qt 安全销毁对象。"""
        self._delete_worker = None
        self._delete_thread = None
        self._delete_bridge = None

    @staticmethod
    def cleanup_volume_identity(path: str) -> Tuple[str, str]:
        """取得目录所在卷的稳定身份与供用户显示的卷路径。

        用途：自动清理只允许一个磁盘卷，确保容量阈值和删除结果指向同一块磁盘。
        输入：任意有效或可能失效的目录路径。
        输出：用于比较的规范化卷身份、用于错误提示的显示文本。
        关键步骤：Windows 优先调用卷 GUID API；失败或非 Windows 时退回盘符/根路径。
        风险点：盘符可重新映射，卷 GUID 更可靠；API 异常时必须安全退回而非让配置保存崩溃。
        """
        normalized = os.path.abspath(path)
        drive, _ = os.path.splitdrive(normalized)
        fallback = os.path.normcase(drive or Path(normalized).anchor or normalized)
        display = drive or Path(normalized).anchor or normalized
        if os.name != "nt":
            return fallback, display
        try:
            volume_path = ctypes.create_unicode_buffer(32768)
            get_volume_path = ctypes.windll.kernel32.GetVolumePathNameW
            get_volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_path.restype = wintypes.BOOL
            if not get_volume_path(normalized, volume_path, len(volume_path)):
                return fallback, display
            root = volume_path.value
            volume_name = ctypes.create_unicode_buffer(32768)
            get_volume_name = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW
            get_volume_name.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_name.restype = wintypes.BOOL
            if get_volume_name(root, volume_name, len(volume_name)):
                return os.path.normcase(volume_name.value), root
            return os.path.normcase(root), root
        except Exception:
            return fallback, display

    @classmethod
    def validate_cleanup_folder_group(
        cls, folders: Iterable[str]
    ) -> Tuple[bool, str, Tuple[Tuple[str, str], ...]]:
        """确认自动清理目录全部属于同一个磁盘卷。

        用途：避免按 A 盘阈值判断、却删除 B 盘文件的危险跨盘配置。
        输入：配置中的所有自动清理目录。
        输出：是否通过、可展示错误文本、每个目录对应的卷信息。
        关键步骤：逐目录取得卷身份，集合中出现多个身份即拒绝。
        风险点：不能只比较字符串盘符；挂载点和网络映射路径需要卷 API 的补强判断。
        """
        details: list[Tuple[str, str]] = []
        identities = set()
        for folder in folders:
            identity, display = cls.cleanup_volume_identity(folder)
            identities.add(identity)
            details.append((folder, display))
        if len(identities) > 1:
            mapping = "；".join(f"{path} -> {volume}" for path, volume in details)
            return False, f"自动清理目录必须位于同一磁盘：{mapping}", tuple(details)
        return True, "", tuple(details)

    @staticmethod
    def deduplicate_cleanup_roots(folders: Iterable[str]) -> list[str]:
        """删除重复根目录及嵌套子目录，保证一次扫描中每个路径最多访问一次。

        用途：用户同时选择父目录和子目录时，防止候选重复、进度重复和重复删除尝试。
        输入：任意数量的目录路径。
        输出：按层级从浅到深保留的非嵌套绝对路径列表。
        关键步骤：标准化绝对真实路径、去重、按路径层级排序、排除已被父目录覆盖的子目录。
        风险点：不同 Windows 盘符无法计算公共路径，必须单独忽略该比较异常而非拒绝所有目录。
        """
        candidates: list[Tuple[str, str]] = []
        seen = set()
        for folder in folders:
            absolute = os.path.abspath(folder)
            normalized = os.path.normcase(os.path.realpath(absolute))
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((absolute, normalized))
        candidates.sort(key=lambda item: (len(Path(item[1]).parts), item[1]))
        result: list[Tuple[str, str]] = []
        for absolute, normalized in candidates:
            is_nested = False
            for _, parent in result:
                try:
                    if os.path.commonpath([parent, normalized]) == parent:
                        is_nested = True
                        break
                except ValueError:
                    # 不同 Windows 磁盘没有公共路径，此时它们不可能互为父子目录。
                    continue
            if is_nested:
                continue
            result.append((absolute, normalized))
        return [absolute for absolute, _ in result]

    @staticmethod
    def file_created_at(stat_result: Any) -> float:
        """作用：执行“file_created_at”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        birth_time = getattr(stat_result, "st_birthtime", None)
        if birth_time is not None:
            return float(birth_time)
        return float(stat_result.st_ctime)

    @staticmethod
    def file_modified_at_ns(stat_result: Any) -> int:
        """作用：执行“file_modified_at_ns”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return _stat_mtime_ns(stat_result)

    @staticmethod
    def file_identity(stat_result: Any) -> str:
        """作用：执行“file_identity”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return _stat_file_id(stat_result)

    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult:
        """校验自动清理的安全前提，自动模式只允许使用回收站。

        用途：在任务开始和设置保存前拒绝高风险或不完整的自动清理配置。
        输入：启用状态、目录、阈值、格式及删除模式组成的请求。
        输出：含错误和卷信息的校验结果。
        关键步骤：依次检查启用/目录、同卷、回收站、阈值关系。
        风险点：自动模式不得降级为永久删除；回收站不可用时必须停止而不是“尽力删除”。
        """
        if not request.enabled:
            return CleanupValidationResult(errors=("自动清理未启用",))
        if not request.folders:
            return CleanupValidationResult(errors=("已启用自动清理但未设置清理路径",))
        invalid = tuple(path for path in request.folders if not os.path.isdir(path))
        if invalid:
            return CleanupValidationResult(
                invalid_reasons=invalid,
                errors=(f"自动清理路径不可用: {'; '.join(invalid)}",),
            )
        valid, error, details = self.validate_cleanup_folder_group(request.folders)
        if not valid:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=(error,),
                volume_details=details,
            )
        if not request.use_trash:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("自动清理仅允许回收站模式",),
                volume_details=details,
            )
        if request.use_trash and not trash_supported():
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("回收站不可用，自动清理无法启用",),
                volume_details=details,
            )
        if request.target_percent >= request.trigger_percent:
            return CleanupValidationResult(
                valid_folders=request.folders,
                errors=("自动清理目标阈值必须小于触发阈值",),
                volume_details=details,
            )
        return CleanupValidationResult(
            valid_folders=request.folders, volume_details=details
        )

    def should_trigger(self, request: AutoCleanupRequest) -> Tuple[bool, str]:
        """读取目标磁盘使用率，判断是否达到自动清理触发阈值。

        用途：让控制器可以在不扫描文件的情况下轻量判断是否要提交后台任务。
        输入：已经配置的自动清理请求。
        输出：是否触发，以及校验或磁盘读取失败原因。
        关键步骤：先复用完整配置校验，再调用 ``shutil.disk_usage`` 计算已用百分比。
        风险点：磁盘容量只用于百分比，不能转换为 32 位数值，现场超大卷也必须安全处理。
        """
        validation = self.validate_auto_request(request)
        if not validation.is_valid:
            return False, "；".join(validation.errors)
        try:
            disk = shutil.disk_usage(request.folders[0])
        except Exception as exc:
            return False, f"无法获取自动清理磁盘信息: {exc}"
        used = ((disk.total - disk.free) / disk.total) * 100 if disk.total > 0 else 0.0
        return used >= request.trigger_percent, ""

    def record_blocked(self, request: AutoCleanupRequest, status: str, error: str) -> None:
        """为“未实际开始”的自动清理写入成对 START/END 审计记录。

        用途：路径或配置阻止任务时，仍让现场人员能够从审计日志追溯原因。
        输入：原始请求、终止状态和错误原因。
        输出：尽力写入审计；审计器缺失时由 ``_write_audit`` 返回假值但不抛异常。
        关键步骤：生成唯一运行编号，然后写入零扫描、零删除的开始和结束记录。
        风险点：不要把“未执行”伪装成“执行成功”，否则现场证据无法判断是否真的删除过文件。
        """
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self._write_audit(
            "START",
            run_id,
            trigger_source=request.trigger_source,
            folders=list(request.folders),
            disk=None,
            used_percent=None,
            trigger_percent=request.trigger_percent,
            target_percent=request.target_percent,
            delete_mode="回收站" if request.use_trash else "永久删除",
            time_basis="file_modification_time",
        )
        self._write_audit(
            "END",
            run_id,
            status=status,
            error=error,
            scanned_count=0,
            deleted_count=0,
            failed_count=0,
            start_used_percent=None,
            final_used_percent=None,
            actual_released_bytes=0,
        )

    def _write_audit(self, event: str, run_id: str, **fields: Any) -> bool:
        """统一调用可选审计器，并把缺失审计器视为失败而不是异常。"""
        return bool(self._audit_writer and self._audit_writer.write(event, run_id, **fields))

    def run_auto_cleanup(
        self,
        request: AutoCleanupRequest,
        cancel_event: Any,
        log: Callable[[str], None],
        delete_mode_provider: Optional[Callable[[], bool]] = None,
    ) -> AutoCleanupResult:
        """按“全局修改时间最旧优先”执行有界流式自动清理。

        用途：在磁盘达到阈值时逐批找出最旧文件、移入回收站，并在达到目标后停止。
        输入：自动清理请求、协作取消事件、日志回调，以及可选的实时删除模式读取函数。
        输出：包含状态、扫描数、删除数、失败数和实际释放空间的 ``AutoCleanupResult``。
        关键步骤：校验配置和磁盘阈值、写 START 审计、循环有限扫描、删除前复核身份、
        每次删除后重新读取磁盘用量、最后写 END 审计。
        风险点：绝不建立全盘索引；自动模式仅允许回收站；回收站未释放空间或身份变化时安全停止。
        """
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        roots = self.deduplicate_cleanup_roots(request.folders)
        scanned_count = deleted_count = failed_count = attempted_bytes = skipped_changed_count = 0
        start_usage = final_usage = None
        audit_started = False

        def used_percent(usage: Any) -> Optional[float]:
            # disk_usage 的数值是 Python 整数；这里只在最后一步换算为百分比供阈值判断和显示。
            """作用：执行“used_percent”的既有业务服务职责。

            参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
            执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
            风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
            """
            return None if usage is None or usage.total <= 0 else ((usage.total - usage.free) / usage.total) * 100

        def finish(status: str, error: str = "") -> AutoCleanupResult:
            # 所有返回路径都走这里，保证已开始的审计任务一定尝试写 END 记录并输出最终日志。
            """作用：执行“finish”的既有业务服务职责。

            参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
            执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
            风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
            """
            released = max(0, int(final_usage.free - start_usage.free)) if start_usage is not None and final_usage is not None else 0
            if audit_started:
                self._write_audit("END", run_id, status=status, error=error, scanned_count=scanned_count, deleted_count=deleted_count, failed_count=failed_count, skipped_changed_count=skipped_changed_count, start_used_percent=used_percent(start_usage), final_used_percent=used_percent(final_usage), actual_released_bytes=released, attempted_delete_bytes=attempted_bytes)
            final = used_percent(final_usage)
            log(f"✅ 自动清理结束：状态={status}，扫描={scanned_count}，删除={deleted_count}，失败={failed_count}，实际占用率={final:.1f}%" if final is not None else f"✅ 自动清理结束：状态={status}，扫描={scanned_count}，删除={deleted_count}，失败={failed_count}，实际占用率=未知")
            return AutoCleanupResult(status, error, scanned_count, deleted_count, failed_count, attempted_bytes, released, skipped_changed_count)

        try:
            if cancel_event.is_set():
                return AutoCleanupResult("任务异常", "应用正在退出，自动清理已取消")
            # 每次运行开始时读取当前模式；自动清理只允许回收站，后面每次删除前还会再次复核。
            use_trash = bool(delete_mode_provider() if delete_mode_provider else request.use_trash)
            effective_request = replace(request, use_trash=use_trash)
            validation = self.validate_auto_request(effective_request)
            if not validation.is_valid:
                error = "；".join(validation.errors)
                self.record_blocked(effective_request, "路径不可用", error)
                return AutoCleanupResult("路径不可用", error)
            start_usage = final_usage = shutil.disk_usage(roots[0])
            if (start_percent := used_percent(start_usage)) is None or start_percent < request.trigger_percent:
                return AutoCleanupResult("未达到阈值")
            if not self._write_audit("START", run_id, trigger_source=request.trigger_source, folders=list(request.folders), effective_roots=roots, disk=validation.volume_details[0][1] if validation.volume_details else "", used_percent=start_percent, trigger_percent=request.trigger_percent, target_percent=request.target_percent, delete_mode="回收站", time_basis="file_modification_time", format_filter=sorted(set(request.formats)), ordering="bounded_stream_oldest_modified_first", candidate_batch_limit=CLEANUP_RECORD_READ_LIMIT):
                return AutoCleanupResult("任务异常", "清理审计日志写入失败")
            audit_started = True
            log(f"⚠️ 磁盘使用率 {start_percent:.1f}% 达到触发阈值 {request.trigger_percent}%，按全局修改时间最旧优先流式清理至 {request.target_percent}%")

            # 一次只保留有限个“最旧候选”，删完后重新扫描。这以更多 I/O 换取有界内存。
            while not cancel_event.is_set():
                scan_failures = 0
                def scan_error(path: str, exc: BaseException) -> None:
                    """作用：执行“scan_error”的既有业务服务职责。

                    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
                    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
                    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
                    """
                    nonlocal scan_failures
                    scan_failures += 1
                    log(f"⚠️ 清理候选无法访问: {path}: {type(exc).__name__}: {exc}")
                candidates, scanned = oldest_cleanup_candidates(roots, request.formats, limit=CLEANUP_RECORD_READ_LIMIT, cancel_event=cancel_event, on_error=scan_error)
                scanned_count += scanned
                if cancel_event.is_set():
                    return finish("任务异常", "应用正在退出，自动清理已取消")
                if scan_failures >= AUTO_CLEANUP_FAILURE_LIMIT:
                    return finish("失败达到20次", "候选扫描连续失败")
                if not candidates:
                    final_usage = shutil.disk_usage(roots[0])
                    final_percent = used_percent(final_usage)
                    return finish("达到目标" if final_percent is not None and final_percent <= request.target_percent else "候选已耗尽", "候选文件已耗尽，但磁盘仍未达到目标阈值")

                for candidate in candidates:
                    if cancel_event.is_set(): return finish("任务异常", "应用正在退出，自动清理已取消")
                    if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT: return finish("失败达到20次")
                    try:
                        stat_result = os.stat(candidate.path, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except Exception as exc:
                        failed_count += 1
                        self._write_audit("DELETE_FAIL", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), error_type=type(exc).__name__, error=str(exc), failed_count=failed_count)
                        continue
                    # 扫描和删除之间任何身份变化都要停止本轮，避免删除到后来生成的新文件。
                    changes = list(_candidate_identity_changes(candidate, stat_result))
                    created_at = self.file_created_at(stat_result)
                    if abs(created_at - candidate.created_at) > 0.001: changes.append("创建时间")
                    if changes:
                        skipped_changed_count += 1
                        self._write_audit("DELETE_SKIP_CHANGED", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), changes=changes, scanned_size_bytes=candidate.size, current_size_bytes=int(stat_result.st_size), scanned_modified_at_ns=candidate.mtime_ns, current_modified_at_ns=self.file_modified_at_ns(stat_result))
                        log(f"⚠️ 已跳过扫描后发生变化的文件 {candidate.path}：{', '.join(changes)}")
                        return finish("候选已刷新", "检测到同路径文件已被替换或修改，已跳过本次删除")
                    if not bool(delete_mode_provider() if delete_mode_provider else request.use_trash):
                        return finish("路径不可用", "自动清理仅允许回收站模式，已停止")
                    if not trash_supported(): return finish("路径不可用", "回收站不可用，自动清理已停止（避免意外永久删除）")
                    def verify_identity() -> tuple[bool, str]:
                        # SafeDeletionPolicy 内部在最后一刻调用本闭包，进一步缩小 TOCTOU 窗口。
                        """作用：执行“verify_identity”的既有业务服务职责。

                        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
                        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
                        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
                        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
                        """
                        current = os.stat(candidate.path, follow_symlinks=False)
                        current_changes = list(_candidate_identity_changes(candidate, current))
                        if abs(self.file_created_at(current) - candidate.created_at) > 0.001: current_changes.append("创建时间")
                        return not current_changes, "、".join(current_changes)
                    try:
                        # 自动模式固定使用回收站；策略仍会再次确认目录范围和文件身份。
                        result = SafeDeletionPolicy(trash_supported, send_to_trash, os.remove).delete(SafeDeletionRequest(path=candidate.path, allowed_roots=tuple(roots), identity_verifier=verify_identity, mode="trash", automatic=True))
                        if not result.success: raise OSError(result.message)
                        deleted_count += 1
                        attempted_bytes += int(stat_result.st_size)
                    except Exception as exc:
                        failed_count += 1
                        if not self._write_audit("DELETE_FAIL", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), size_bytes=int(stat_result.st_size), created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"), modified_at_ns=self.file_modified_at_ns(stat_result), delete_mode="回收站", error_type=type(exc).__name__, error=str(exc), failed_count=failed_count): return finish("任务异常", "清理审计日志写入失败")
                        continue
                    try:
                        # 删除后立即复测同一卷空间，避免仅凭“尝试删除的文件大小”误报空间已释放。
                        final_usage = shutil.disk_usage(roots[0])
                    except Exception as exc:
                        final_usage = None
                        usage_error = f"{type(exc).__name__}: {exc}"
                    else: usage_error = ""
                    current_percent = used_percent(final_usage)
                    if not self._write_audit("DELETE_OK", run_id, path=candidate.path, file_name=os.path.basename(candidate.path), size_bytes=int(stat_result.st_size), created_at=datetime.datetime.fromtimestamp(created_at).isoformat(timespec="seconds"), modified_at_ns=self.file_modified_at_ns(stat_result), delete_mode="回收站", disk_used_percent=current_percent, disk_usage_error=usage_error, candidate_source="stream_scan"): return finish("任务异常", "清理审计日志写入失败")
                    if usage_error: return finish("路径不可用", usage_error)
                    if final_usage is not None and start_usage is not None and final_usage.free <= start_usage.free:
                        return finish("回收站未释放空间")
                    if current_percent is not None and current_percent <= request.target_percent: return finish("达到目标")
            return finish("任务异常", "应用正在退出，自动清理已取消")
        except Exception as exc:
            if not audit_started: self.record_blocked(request, "任务异常", str(exc))
            log(f"⚠️ 自动清理任务异常: {exc}")
            return finish("任务异常", str(exc))
