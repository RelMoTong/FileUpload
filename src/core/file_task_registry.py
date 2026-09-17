"""确保同一代文件只被一个上传任务处理的运行时登记簿。

该登记簿只保存在内存中；断电后的恢复数据由待归档日志和续传记录负责。它的职责
是阻止扫描器、重试调度器和归档 Worker 在同一时刻重复领取同一代文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Dict, Optional

from .file_identity import FileIdentity, normalize_file_path


class FileTaskState(str, Enum):
    """一代文件在上传会话中的状态集合。

    状态顺序不是简单线性：``RETRY_WAIT`` 会重新领取到 ``UPLOADING``，``STALE`` 表示
    同路径文件已经换代，旧任务不能再继续；状态变化由 ``FileTaskRegistry`` 的锁保护。
    """
    DISCOVERED = "discovered"
    WAITING = "waiting"
    UPLOADING = "uploading"
    RETRY_WAIT = "retry_wait"
    UPLOADED = "uploaded"
    ARCHIVE_PERSIST_FAILED = "archive_persist_failed"
    ARCHIVE_PENDING = "archive_pending"
    ARCHIVED = "archived"
    FAILED = "failed"
    STALE = "stale"


@dataclass
class FileTask:
    """单个精确文件代际的内存任务记录。

    ``identity`` 是不可变安全快照；其余字段记录本次会话中的状态、退避时间和双协议结果。
    该对象不落盘，进程重启后的恢复交给续传记录和待归档日志。
    """
    identity: FileIdentity
    state: FileTaskState
    retry_count: int = 0
    next_retry_at: float = 0.0
    protocol_results: Dict[str, bool] | None = None
    last_reason: str = ""
    updated_at: float = 0.0


class FileTaskRegistry:
    """供 UploadWorker 使用的线程安全、按文件代际区分的任务登记簿。

    用途：协调扫描器、重试调度器和归档 Worker，确保同一代文件只被一个流程领取。
    输入：由【FileIdentity】描述的精确文件代际及其状态变化。
    输出：可查询的内存任务记录或原子领取结果。
    关键步骤：以可重入锁保护所有读写，并用路径、大小、时间和摘要组成代际键。
    风险点：登记簿不落盘；进程重启后的恢复必须依赖续传与待归档持久化记录。
    """

    def __init__(self) -> None:
        """创建可重入锁和空任务表；可重入锁允许内部方法在同一线程嵌套调用。"""
        self._lock = threading.RLock()
        self._tasks: Dict[str, FileTask] = {}

    @staticmethod
    def _key(identity: FileIdentity) -> str:
        """组合影响文件代际的字段，避免同路径新文件被旧任务误识别。"""
        return f"{identity.normalized_path}|{identity.size}|{identity.mtime_ns}|{identity.sha256}"

    def _touch(self, task: FileTask, state: FileTaskState, reason: str = "") -> FileTask:
        """统一更新状态、原因和时间戳，保证状态转换记录完整。"""
        task.state = state
        task.last_reason = reason
        task.updated_at = time.time()
        return task

    def discover(self, identity: FileIdentity) -> FileTask:
        """登记新发现的文件代际，或返回已存在的任务记录。"""
        with self._lock:
            key = self._key(identity)
            # 同一代际再次被扫描到时必须复用原记录，不能重置其重试或归档状态。
            task = self._tasks.get(key)
            if task is None:
                task = FileTask(identity, FileTaskState.DISCOVERED, updated_at=time.time())
                self._tasks[key] = task
            return task

    def claim_for_upload(self, identity: FileIdentity) -> bool:
        """原子领取“已发现/等待中”的文件代际，供一个上传者处理。

        用途：阻止多个扫描周期或 Worker 同时上传同一代文件。
        输入：本次扫描捕获的精确文件身份【identity】。
        输出：领取成功返回【True】，状态不允许领取时返回【False】。
        关键步骤：在锁内创建或读取任务，再仅从允许状态转换到【UPLOADING】。
        风险点：不能在锁外先检查再修改，否则并发扫描会产生重复上传。
        """
        with self._lock:
            task = self.discover(identity)
            # 已上传、正在上传或等待重试的任务都不可被扫描器重复领取。
            if task.state not in {FileTaskState.DISCOVERED, FileTaskState.WAITING}:
                return False
            self._touch(task, FileTaskState.UPLOADING, "upload_claimed")
            return True

    def claim_due_retry(self, identity: FileIdentity, now: Optional[float] = None) -> bool:
        """领取已到期的重试任务；普通扫描流程不能抢在调度器之前领取它。

        用途：按预定时间重试失败文件，并避免重复任务绕过退避等待。
        输入：精确文件身份【identity】和可选的当前时间【now】。
        输出：到期且领取成功返回【True】，其他情况返回【False】。
        关键步骤：在锁内确认任务状态为【RETRY_WAIT】且重试时间已到，再改为【UPLOADING】。
        风险点：传入的测试时间必须和记录中的时间基准一致，否则可能提前或延后重试。
        """
        current = time.time() if now is None else now
        with self._lock:
            task = self._tasks.get(self._key(identity))
            if (
                task is None
                or task.state is not FileTaskState.RETRY_WAIT
                or task.next_retry_at > current
            ):
                return False
            self._touch(task, FileTaskState.UPLOADING, "retry_claimed")
            return True

    def should_skip_scan(self, identity: FileIdentity) -> bool:
        """同一代际已有记录时返回真，扫描器应跳过它。

        用途：避免上传、等待重试、待归档或已完成的同一代文件被下一轮扫描重复处理。
        风险点：这是会话内门禁，不负责重启恢复；归档日志门禁会覆盖跨进程情况。
        """
        with self._lock:
            task = self._tasks.get(self._key(identity))
            return task is not None

    def set_state(
        self,
        identity: FileIdentity,
        state: FileTaskState,
        *,
        reason: str = "",
        protocol_results: Optional[Dict[str, bool]] = None,
    ) -> FileTask:
        """更新任务状态，并可选地保存各上传协议的执行结果。

        用途：统一记录上传、归档和异常分支的状态变化。
        风险点：调用方必须传入当前精确身份，不能通过路径查找后修改另一个代际。
        """
        with self._lock:
            task = self.discover(identity)
            if protocol_results is not None:
                task.protocol_results = dict(protocol_results)
            return self._touch(task, state, reason)

    def schedule_retry(
        self,
        identity: FileIdentity,
        retry_count: int,
        next_retry_at: float,
        *,
        protocol_results: Optional[Dict[str, bool]] = None,
        reason: str = "upload_failed",
    ) -> FileTask:
        """记录失败后的下一次重试时间，保持同一代际的协议结果可追溯。

        用途：把失败任务交回重试调度器，而不是让扫描器立即重复处理。
        输入：文件身份、重试次数、下一次重试时间以及可选协议结果和失败原因。
        输出：已更新为【RETRY_WAIT】状态的任务记录。
        关键步骤：在锁内写入计数、调度时间和协议结果，再统一更新状态时间戳。
        风险点：调度时间错误会改变退避策略；必须按同一时钟基准传入。
        """
        with self._lock:
            task = self.discover(identity)
            task.retry_count = retry_count
            task.next_retry_at = next_retry_at
            if protocol_results is not None:
                task.protocol_results = dict(protocol_results)
            return self._touch(task, FileTaskState.RETRY_WAIT, reason)

    def mark_failed(self, identity: FileIdentity, reason: str) -> FileTask:
        """把任务标为不可继续处理的失败状态。"""
        return self.set_state(identity, FileTaskState.FAILED, reason=reason)

    def mark_stale(self, identity: FileIdentity, reason: str) -> FileTask:
        """把扫描后已变化的文件代际标为过期，禁止继续使用旧快照。"""
        return self.set_state(identity, FileTaskState.STALE, reason=reason)

    def get(self, identity: FileIdentity) -> Optional[FileTask]:
        """按精确文件代际读取任务。"""
        with self._lock:
            return self._tasks.get(self._key(identity))

    def for_path(self, path: str) -> tuple[FileTask, ...]:
        """查询同一路径下的所有历史代际，便于诊断同名文件替换。"""
        normalized = normalize_file_path(path)
        with self._lock:
            return tuple(
                task for task in self._tasks.values()
                if task.identity.normalized_path == normalized
            )

    def snapshot(self) -> tuple[FileTask, ...]:
        """返回当前任务快照；返回元组避免调用方直接修改内部容器。"""
        with self._lock:
            return tuple(self._tasks.values())

    def discard(self, identity: FileIdentity) -> None:
        """移除一个精确代际的内存记录，仅用于确认不再需要跟踪的任务。"""
        with self._lock:
            self._tasks.pop(self._key(identity), None)

    def release_for_scan(self, identity: FileIdentity, reason: str = "") -> None:
        """非上传原因跳过后，把已领取文件重新开放给后续扫描。"""
        with self._lock:
            task = self._tasks.get(self._key(identity))
            if task is not None:
                self._touch(task, FileTaskState.DISCOVERED, reason)
