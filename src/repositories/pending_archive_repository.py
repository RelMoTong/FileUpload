"""
文件名：src/repositories/pending_archive_repository.py
文件作用：运行期持久化边界模块“pending_archive_repository”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Durable journal for uploaded source files awaiting archive/delete.
"""

from __future__ import annotations

from pathlib import Path
import threading
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.file_identity import FileIdentity, normalize_file_path
from src.core.atomic_json_store import AtomicJsonStore


ARCHIVE_RECORD_SCHEMA_VERSION = 2


class PendingArchiveRepository:
    def __init__(self, app_dir: Path) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self.path = Path(app_dir) / "data" / "pending_archives.json"
        self.failure_path = Path(app_dir) / "data" / "pending_archive_failures.json"
        self._store = AtomicJsonStore(
            self.path,
            max_bytes=4 * 1024 * 1024,
            max_records=10_000,
        )
        self._failure_store = AtomicJsonStore(
            self.failure_path, max_bytes=4 * 1024 * 1024, max_records=10_000
        )
        self._lock = threading.RLock()
        self.last_error = ""

    @staticmethod
    def normalize(path: str) -> str:
        """Use the same identity key for local and network-backed sources."""
        return normalize_file_path(path)

    def load(self) -> tuple[dict[str, Any], ...]:
        """作用：执行“load”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        with self._lock:
            records = self._read()
            if records is None:
                return ()
            return tuple(dict(value) for value in records.values())

    def add(
        self,
        source: str,
        destination: str,
        action: str,
        identity: FileIdentity,
        protocol_results: Mapping[str, bool] | None = None,
    ) -> bool:
        """Persist an archive request bound to one exact source generation."""
        with self._lock:
            if identity.normalized_path != self.normalize(source):
                self.last_error = "ValueError: archive identity path does not match source"
                return False
            records = self._read()
            if records is None:
                return False
            records[self.normalize(source)] = {
                "schema_version": ARCHIVE_RECORD_SCHEMA_VERSION,
                "source": source,
                "destination": destination,
                "action": action,
                "identity": identity.to_mapping(),
                "protocol_results": dict(protocol_results or {}),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "state": "pending",
            }
            return self._write(records)

    def mark_stale(self, source: str, reason: str) -> bool:
        """Retain an unsafe record as audit evidence without executing it."""
        with self._lock:
            records = self._read()
            if records is None:
                return False
            record = records.get(self.normalize(source))
            if record is None:
                self.last_error = "KeyError: pending archive record not found"
                return False
            record["state"] = "stale"
            record["stale_reason"] = reason
            record["stale_at"] = datetime.now(timezone.utc).isoformat()
            records[self.normalize(source)] = record
            return self._write(records)

    def remove(self, source: str) -> bool:
        """作用：执行“remove”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        with self._lock:
            records = self._read()
            if records is None:
                return False
            records.pop(self.normalize(source), None)
            return self._write(records)

    def load_failures(self) -> tuple[dict[str, Any], ...]:
        """作用：执行“load_failures”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        with self._lock:
            payload = self._failure_store.read(default={})
            if self._failure_store.last_error:
                self.last_error = self._failure_store.last_error
                return ()
            self.last_error = ""
            if not isinstance(payload, dict):
                return ()
            return tuple(dict(value) for value in payload.values() if isinstance(value, dict))

    def record_failure(self, record: Mapping[str, Any]) -> bool:
        """作用：执行“record_failure”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        source = self.normalize(str(record.get("source", "")))
        if not source:
            self._failure_store.last_error = "ValueError: missing source"
            return False
        with self._lock:
            payload = self._failure_store.read(default={})
            if self._failure_store.last_error:
                self.last_error = self._failure_store.last_error
                return False
            if not isinstance(payload, dict):
                return False
            payload[source] = dict(record)
            return self._failure_store.write(payload)

    def remove_failure(self, source: str) -> bool:
        """作用：执行“remove_failure”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        with self._lock:
            payload = self._failure_store.read(default={})
            if self._failure_store.last_error:
                self.last_error = self._failure_store.last_error
                return False
            if not isinstance(payload, dict):
                return False
            payload.pop(self.normalize(source), None)
            return self._failure_store.write(payload)

    def _read(self) -> dict[str, dict[str, Any]] | None:
        """作用：执行“_read”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
        if not self.path.exists():
            return {}
        try:
            payload = self._store.read(default=None)
            if payload is None:
                self.last_error = self._store.last_error
                return None
            if not isinstance(payload, dict):
                raise ValueError("pending archive journal must be an object")
            self.last_error = ""
            return {
                str(key): dict(value)
                for key, value in payload.items()
                if isinstance(value, dict)
            }
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            # Fail closed: never overwrite a corrupt journal with an empty one,
            # otherwise uploaded source files could silently lose archive state.
            return None

    def _write(self, records: dict[str, dict[str, Any]]) -> bool:
        """内部辅助：完成“_write”对应的既有局部工作。"""
        try:
            if not self._store.write(records):
                raise OSError(self._store.last_error)
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
