"""SQLite persistence for the automatic-cleanup file index."""

from __future__ import annotations

import datetime
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Iterable, Optional, Sequence

from src.models import CleanupIndexRecord


SCHEMA_VERSION = "2"


class CleanupIndexRepository:
    """Thread-safe, connection-per-operation SQLite cleanup index."""

    def __init__(self, app_dir: Path) -> None:
        self._data_dir = Path(app_dir) / "data"
        self.database_path = self._data_dir / "cleanup_index.sqlite3"
        self.marker_path = self._data_dir / "cleanup_index_ready.txt"
        self._write_lock = threading.RLock()
        self.last_error = ""

    @staticmethod
    def normalize_path(path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    @classmethod
    def scope_fingerprint(
        cls, folders: Iterable[str], formats: Iterable[str]
    ) -> str:
        payload = {
            "folders": sorted({cls.normalize_path(path) for path in folders if path}),
            "formats": sorted({str(ext).strip().lower() for ext in formats if str(ext).strip()}),
            "schema": SCHEMA_VERSION,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cleanup_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(cleanup_files)"
                ).fetchall()
            }
            required_columns = {"modified_at_ns", "file_id"}
            if existing_columns and not required_columns.issubset(existing_columns):
                connection.execute("DROP TABLE cleanup_files")
                self._set_meta(connection, "schema_version", "")
                self._set_meta(connection, "scope_fingerprint", "")
                self._set_meta(connection, "baseline_complete", "0")
                self._remove_marker()
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cleanup_files (
                    normalized_path TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    modified_at_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cleanup_files_modified_oldest
                ON cleanup_files(modified_at_ns, normalized_path)
                """
            )
            return connection
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO cleanup_index_meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    @staticmethod
    def _get_meta(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute(
            "SELECT value FROM cleanup_index_meta WHERE key=?", (key,)
        ).fetchone()
        return str(row["value"]) if row is not None else ""

    def ensure_schema(self) -> bool:
        try:
            with self._connection() as connection:
                self._set_meta(connection, "schema_version", SCHEMA_VERSION)
            self.last_error = ""
            return True
        except sqlite3.DatabaseError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self.recover_corrupt_database()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def prepare_scope(self, scope_fingerprint: str) -> bool:
        try:
            with self._write_lock:
                self._remove_marker()
                with self._connection() as connection:
                    connection.execute("DELETE FROM cleanup_files")
                    self._set_meta(connection, "schema_version", SCHEMA_VERSION)
                    self._set_meta(connection, "scope_fingerprint", scope_fingerprint)
                    self._set_meta(connection, "baseline_complete", "0")
            self.last_error = ""
            return True
        except sqlite3.DatabaseError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.recover_corrupt_database()
            return False
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def is_scope_current(self, scope_fingerprint: str) -> bool:
        if not self.database_path.exists():
            return False
        try:
            with self._connection() as connection:
                return (
                    self._get_meta(connection, "schema_version") == SCHEMA_VERSION
                    and self._get_meta(connection, "scope_fingerprint") == scope_fingerprint
                )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def is_ready(self, scope_fingerprint: str) -> bool:
        if not self.marker_path.is_file() or not self.database_path.is_file():
            if self.marker_path.exists():
                self._remove_marker()
            return False
        try:
            with self._connection() as connection:
                integrity = connection.execute("PRAGMA quick_check(1)").fetchone()
                healthy = bool(integrity and str(integrity[0]).lower() == "ok")
                ready = (
                    healthy
                    and self._get_meta(connection, "schema_version") == SCHEMA_VERSION
                    and self._get_meta(connection, "scope_fingerprint") == scope_fingerprint
                    and self._get_meta(connection, "baseline_complete") == "1"
                )
            if not ready:
                self.mark_dirty()
            self.last_error = ""
            return ready
        except sqlite3.DatabaseError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.recover_corrupt_database()
            return False
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.mark_dirty()
            return False

    def upsert_many(
        self, records: Sequence[CleanupIndexRecord], scope_fingerprint: str
    ) -> bool:
        if not records:
            return True
        values = [
            (
                item.normalized_path,
                item.path,
                item.file_name,
                float(item.created_at),
                max(
                    0,
                    int(
                        item.modified_at_ns
                        or round(float(item.created_at) * 1_000_000_000)
                    ),
                ),
                max(0, int(item.size_bytes)),
                str(item.file_id or ""),
                item.root_path,
                item.source,
                time.time(),
            )
            for item in records
        ]
        try:
            with self._write_lock, self._connection() as connection:
                if self._get_meta(connection, "scope_fingerprint") != scope_fingerprint:
                    self.last_error = "清理索引范围已变更"
                    return False
                connection.executemany(
                    """
                    INSERT INTO cleanup_files(
                        normalized_path, path, file_name, created_at, modified_at_ns,
                        size_bytes, file_id, root_path, source, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(normalized_path) DO UPDATE SET
                        path=excluded.path,
                        file_name=excluded.file_name,
                        created_at=excluded.created_at,
                        modified_at_ns=excluded.modified_at_ns,
                        size_bytes=excluded.size_bytes,
                        file_id=excluded.file_id,
                        root_path=excluded.root_path,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    values,
                )
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def oldest(
        self, scope_fingerprint: str, limit: int = 100
    ) -> tuple[CleanupIndexRecord, ...]:
        try:
            with self._connection() as connection:
                if self._get_meta(connection, "scope_fingerprint") != scope_fingerprint:
                    self.last_error = "清理索引范围已变更"
                    return ()
                rows = connection.execute(
                    """
                    SELECT normalized_path, path, file_name, created_at,
                           modified_at_ns, size_bytes, file_id, root_path, source
                    FROM cleanup_files
                    ORDER BY modified_at_ns ASC, normalized_path ASC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            self.last_error = ""
            return tuple(
                CleanupIndexRecord(
                    normalized_path=str(row["normalized_path"]),
                    path=str(row["path"]),
                    file_name=str(row["file_name"]),
                    created_at=float(row["created_at"]),
                    modified_at_ns=int(row["modified_at_ns"]),
                    size_bytes=int(row["size_bytes"]),
                    file_id=str(row["file_id"]),
                    root_path=str(row["root_path"]),
                    source=str(row["source"]),
                )
                for row in rows
            )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ()

    def remove(self, normalized_path: str, scope_fingerprint: str) -> bool:
        try:
            with self._write_lock, self._connection() as connection:
                if self._get_meta(connection, "scope_fingerprint") != scope_fingerprint:
                    self.last_error = "清理索引范围已变更"
                    return False
                connection.execute(
                    "DELETE FROM cleanup_files WHERE normalized_path=?",
                    (normalized_path,),
                )
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def count(self, scope_fingerprint: str) -> int:
        try:
            with self._connection() as connection:
                if self._get_meta(connection, "scope_fingerprint") != scope_fingerprint:
                    return 0
                row = connection.execute("SELECT COUNT(*) FROM cleanup_files").fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return 0

    def mark_ready(self, scope_fingerprint: str) -> bool:
        try:
            with self._write_lock, self._connection() as connection:
                if self._get_meta(connection, "scope_fingerprint") != scope_fingerprint:
                    self.last_error = "清理索引范围已变更"
                    return False
                self._set_meta(connection, "baseline_complete", "1")
                self._set_meta(connection, "schema_version", SCHEMA_VERSION)
            self._data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.marker_path.with_suffix(".tmp")
            temporary.write_bytes(b"")
            os.replace(temporary, self.marker_path)
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._remove_marker()
            return False

    def mark_dirty(self, clear_records: bool = False) -> bool:
        try:
            with self._write_lock:
                self._remove_marker()
                if self.database_path.exists():
                    with self._connection() as connection:
                        self._set_meta(connection, "baseline_complete", "0")
                        if clear_records:
                            connection.execute("DELETE FROM cleanup_files")
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def recover_corrupt_database(self) -> bool:
        """Preserve a corrupt index and recreate an empty schema."""
        try:
            with self._write_lock:
                self._remove_marker()
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                for path in (
                    self.database_path,
                    Path(str(self.database_path) + "-wal"),
                    Path(str(self.database_path) + "-shm"),
                ):
                    if not path.exists():
                        continue
                    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
                    os.replace(path, backup)
                with self._connection() as connection:
                    self._set_meta(connection, "schema_version", SCHEMA_VERSION)
                    self._set_meta(connection, "baseline_complete", "0")
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def _remove_marker(self) -> None:
        try:
            self.marker_path.unlink(missing_ok=True)
        except TypeError:
            if self.marker_path.exists():
                self.marker_path.unlink()
