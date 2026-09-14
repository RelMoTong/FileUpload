"""Bounded, checksummed JSON persistence without a database.

State is written into a sibling temporary file, flushed to disk, and atomically
replaced.  The immediately previous valid version is retained as ``.bak`` so a
crash or corrupted write can recover without treating a partial file as state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional


ATOMIC_JSON_SCHEMA_VERSION = 1


class AtomicJsonStore:
    """Atomic JSON object storage with validation, backups, and corruption quarantine."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        max_records: int = 10_000,
        schema_version: int = ATOMIC_JSON_SCHEMA_VERSION,
        wrap_envelope: bool = True,
        replace_func: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(f"{self.path.name}.bak")
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.schema_version = schema_version
        self.wrap_envelope = wrap_envelope
        self._replace_func = replace_func
        self.last_error = ""

    def read(
        self,
        *,
        validator: Optional[Callable[[Any], bool]] = None,
        default: Any = None,
    ) -> Any:
        if not self.path.exists():
            self.last_error = ""
            return default
        result = self._read_path(self.path, validator)
        if result is not None:
            self.last_error = ""
            return result

        original_error = self.last_error
        backup = self._read_path(self.backup_path, validator)
        if backup is not None:
            self._quarantine(self.path)
            self.last_error = f"{original_error}; recovered_from_backup"
            return backup
        self.last_error = original_error
        return default

    def write(self, payload: Any) -> bool:
        temp_path: Path | None = None
        descriptor: int | None = None
        try:
            self._validate_payload(payload)
            encoded_payload = self._canonical_bytes(payload)
            if self.wrap_envelope:
                envelope = {
                    "schema_version": self.schema_version,
                    "checksum_sha256": hashlib.sha256(encoded_payload).hexdigest(),
                    "payload": payload,
                }
                encoded = self._canonical_bytes(envelope)
            else:
                encoded = encoded_payload
            if len(encoded) > self.max_bytes:
                raise ValueError(f"state exceeds {self.max_bytes} byte limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temp_path = Path(raw_path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = None
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            if self._replace_func is None:
                os.replace(temp_path, self.path)
            else:
                self._replace_func(temp_path, self.path)
            temp_path = None
            self._fsync_directory(self.path.parent)
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

    def _read_path(
        self, path: Path, validator: Optional[Callable[[Any], bool]]
    ) -> Any | None:
        if not path.exists():
            return None
        try:
            raw = path.read_bytes()
            if not raw:
                raise ValueError("zero-byte state file")
            if len(raw) > self.max_bytes:
                raise ValueError(f"state exceeds {self.max_bytes} byte limit")
            loaded = json.loads(raw.decode("utf-8"))
            payload = self._decode_envelope(loaded)
            self._validate_payload(payload)
            if validator is not None and not validator(payload):
                raise ValueError("state payload validation failed")
            return payload
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _decode_envelope(self, loaded: Any) -> Any:
        if not isinstance(loaded, Mapping):
            return loaded  # Legacy bare JSON is readable and upgraded on write.
        required = {"schema_version", "checksum_sha256", "payload"}
        if not required.issubset(loaded):
            return loaded
        if loaded["schema_version"] != self.schema_version:
            raise ValueError("unsupported state schema")
        payload = loaded["payload"]
        expected = loaded["checksum_sha256"]
        if not isinstance(expected, str):
            raise ValueError("missing state checksum")
        actual = hashlib.sha256(self._canonical_bytes(payload)).hexdigest()
        if actual != expected:
            raise ValueError("state checksum mismatch")
        return payload

    def _validate_payload(self, payload: Any) -> None:
        if not isinstance(payload, (dict, list)):
            raise ValueError("state payload must be a JSON object or list")
        record_count = self._record_count(payload)
        if record_count > self.max_records:
            raise ValueError(f"state exceeds {self.max_records} record limit")

    @staticmethod
    def _record_count(payload: Any) -> int:
        if isinstance(payload, dict):
            if len(payload) == 1 and isinstance(payload.get("items"), list):
                return len(payload["items"])
            return len(payload)
        return len(payload)

    @staticmethod
    def _canonical_bytes(payload: Any) -> bytes:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _quarantine(self, path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            if self._replace_func is None:
                os.replace(path, quarantine)
            else:
                self._replace_func(path, quarantine)
        except OSError:
            pass
