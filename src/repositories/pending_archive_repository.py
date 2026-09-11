"""Durable journal for uploaded source files awaiting archive/delete."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


class PendingArchiveRepository:
    def __init__(self, app_dir: Path) -> None:
        self.path = Path(app_dir) / "data" / "pending_archives.json"
        self._lock = threading.RLock()
        self.last_error = ""

    @staticmethod
    def normalize(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def load(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            records = self._read()
            if records is None:
                return ()
            return tuple(dict(value) for value in records.values())

    def add(self, source: str, destination: str, action: str) -> bool:
        with self._lock:
            records = self._read()
            if records is None:
                return False
            records[self.normalize(source)] = {
                "source": source,
                "destination": destination,
                "action": action,
            }
            return self._write(records)

    def remove(self, source: str) -> bool:
        with self._lock:
            records = self._read()
            if records is None:
                return False
            records.pop(self.normalize(source), None)
            return self._write(records)

    def _read(self) -> dict[str, dict[str, Any]] | None:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
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
        temp_path: Path | None = None
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temp_path = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                descriptor = None
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
