"""Runtime registry for one upload task per exact file generation.

The registry is deliberately in-memory.  Durable recovery records belong to
the pending-archive/resume stores; this component only prevents a scanner,
retry scheduler, and archive worker from concurrently claiming one generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Dict, Optional

from .file_identity import FileIdentity, normalize_file_path


class FileTaskState(str, Enum):
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
    identity: FileIdentity
    state: FileTaskState
    retry_count: int = 0
    next_retry_at: float = 0.0
    protocol_results: Dict[str, bool] | None = None
    last_reason: str = ""
    updated_at: float = 0.0


class FileTaskRegistry:
    """Thread-safe, generation-aware task registry used by UploadWorker."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: Dict[str, FileTask] = {}

    @staticmethod
    def _key(identity: FileIdentity) -> str:
        return f"{identity.normalized_path}|{identity.size}|{identity.mtime_ns}|{identity.sha256}"

    def _touch(self, task: FileTask, state: FileTaskState, reason: str = "") -> FileTask:
        task.state = state
        task.last_reason = reason
        task.updated_at = time.time()
        return task

    def discover(self, identity: FileIdentity) -> FileTask:
        """Register a generation and return its current task record."""
        with self._lock:
            key = self._key(identity)
            task = self._tasks.get(key)
            if task is None:
                task = FileTask(identity, FileTaskState.DISCOVERED, updated_at=time.time())
                self._tasks[key] = task
            return task

    def claim_for_upload(self, identity: FileIdentity) -> bool:
        """Atomically claim a discovered/waiting generation for one uploader."""
        with self._lock:
            task = self.discover(identity)
            if task.state not in {FileTaskState.DISCOVERED, FileTaskState.WAITING}:
                return False
            self._touch(task, FileTaskState.UPLOADING, "upload_claimed")
            return True

    def claim_due_retry(self, identity: FileIdentity, now: Optional[float] = None) -> bool:
        """Claim an expired retry slot; scanners can never claim it first."""
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
        """Return true when this exact generation already has active/terminal work."""
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
        with self._lock:
            task = self.discover(identity)
            task.retry_count = retry_count
            task.next_retry_at = next_retry_at
            if protocol_results is not None:
                task.protocol_results = dict(protocol_results)
            return self._touch(task, FileTaskState.RETRY_WAIT, reason)

    def mark_failed(self, identity: FileIdentity, reason: str) -> FileTask:
        return self.set_state(identity, FileTaskState.FAILED, reason=reason)

    def mark_stale(self, identity: FileIdentity, reason: str) -> FileTask:
        return self.set_state(identity, FileTaskState.STALE, reason=reason)

    def get(self, identity: FileIdentity) -> Optional[FileTask]:
        with self._lock:
            return self._tasks.get(self._key(identity))

    def for_path(self, path: str) -> tuple[FileTask, ...]:
        normalized = normalize_file_path(path)
        with self._lock:
            return tuple(
                task for task in self._tasks.values()
                if task.identity.normalized_path == normalized
            )

    def snapshot(self) -> tuple[FileTask, ...]:
        with self._lock:
            return tuple(self._tasks.values())

    def discard(self, identity: FileIdentity) -> None:
        with self._lock:
            self._tasks.pop(self._key(identity), None)

    def release_for_scan(self, identity: FileIdentity, reason: str = "") -> None:
        """Make a claimed generation discoverable after a non-upload skip."""
        with self._lock:
            task = self._tasks.get(self._key(identity))
            if task is not None:
                self._touch(task, FileTaskState.DISCOVERED, reason)
