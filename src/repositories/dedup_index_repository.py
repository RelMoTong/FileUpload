"""SQLite metadata/hash index used by SMB duplicate detection."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Iterable


class DedupIndexRepository:
    def __init__(self, app_dir: Path) -> None:
        self.path = Path(app_dir) / "data" / "dedup_hash_index.db"
        self._lock = threading.RLock()
        self.last_error = ""

    @staticmethod
    def normalize(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_files (
                target_root TEXT NOT NULL,
                path TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                digest TEXT NOT NULL DEFAULT '',
                scan_generation INTEGER NOT NULL,
                PRIMARY KEY (target_root, path, algorithm)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_lookup
            ON dedup_files(target_root, algorithm, size, digest)
            """
        )
        return connection

    @contextmanager
    def _connection(self):
        """Commit or roll back an operation and always release its file handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def begin_scan(self) -> int:
        return time.time_ns()

    def upsert_metadata_batch(
        self,
        target_root: str,
        algorithm: str,
        generation: int,
        records: Iterable[tuple[str, int, int]],
    ) -> bool:
        root = self.normalize(target_root)
        rows = [
            (root, self.normalize(path), algorithm, size, mtime_ns, generation)
            for path, size, mtime_ns in records
        ]
        if not rows:
            return True
        try:
            with self._lock, self._connection() as connection:
                connection.executemany(
                    """
                    INSERT INTO dedup_files(
                        target_root, path, algorithm, size, mtime_ns,
                        scan_generation
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(target_root, path, algorithm) DO UPDATE SET
                        digest = CASE
                            WHEN dedup_files.size = excluded.size
                             AND dedup_files.mtime_ns = excluded.mtime_ns
                            THEN dedup_files.digest ELSE '' END,
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        scan_generation = excluded.scan_generation
                    """,
                    rows,
                )
            self.last_error = ""
            return True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def finish_scan(
        self, target_root: str, algorithm: str, generation: int
    ) -> bool:
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM dedup_files
                    WHERE target_root = ? AND algorithm = ?
                      AND scan_generation <> ?
                    """,
                    (self.normalize(target_root), algorithm, generation),
                )
            self.last_error = ""
            return True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def candidates(
        self, target_root: str, algorithm: str, size: int
    ) -> tuple[tuple[str, int, str], ...]:
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT path, mtime_ns, digest FROM dedup_files
                    WHERE target_root = ? AND algorithm = ? AND size = ?
                    """,
                    (self.normalize(target_root), algorithm, size),
                ).fetchall()
            self.last_error = ""
            return tuple((str(path), int(mtime_ns), str(digest)) for path, mtime_ns, digest in rows)
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ()

    def update_file(
        self,
        target_root: str,
        algorithm: str,
        path: str,
        size: int,
        mtime_ns: int,
        digest: str,
        generation: int,
    ) -> bool:
        if not self.upsert_metadata_batch(
            target_root, algorithm, generation, [(path, size, mtime_ns)]
        ):
            return False
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    """
                    UPDATE dedup_files SET digest = ?
                    WHERE target_root = ? AND path = ? AND algorithm = ?
                    """,
                    (
                        digest,
                        self.normalize(target_root),
                        self.normalize(path),
                        algorithm,
                    ),
                )
            self.last_error = ""
            return True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def remove(self, target_root: str, algorithm: str, path: str) -> bool:
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM dedup_files
                    WHERE target_root = ? AND path = ? AND algorithm = ?
                    """,
                    (
                        self.normalize(target_root),
                        self.normalize(path),
                        algorithm,
                    ),
                )
            return True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
