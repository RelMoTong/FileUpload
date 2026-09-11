"""Immutable source-file identities for safety-critical file lifecycle work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping


def normalize_file_path(path: str | Path) -> str:
    """Return a canonical comparison path without requiring that it exists."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


@dataclass(frozen=True)
class FileIdentity:
    """A durable identity for one exact source-file generation.

    Size and nanosecond mtime make the common comparison inexpensive; available
    device/inode values help on filesystems that expose them.  The SHA-256
    digest closes the same-size, restored-timestamp replacement hole.
    """

    normalized_path: str
    size: int
    mtime_ns: int
    device: int | None
    inode: int | None
    sha256: str

    @classmethod
    def capture(cls, path: str | Path) -> "FileIdentity":
        source = Path(path)
        stat_result = source.stat()
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(
            normalized_path=normalize_file_path(source),
            size=int(stat_result.st_size),
            mtime_ns=int(stat_result.st_mtime_ns),
            device=_optional_file_id(stat_result, "st_dev"),
            inode=_optional_file_id(stat_result, "st_ino"),
            sha256=digest.hexdigest(),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FileIdentity":
        normalized_path = value.get("normalized_path")
        size = value.get("size")
        mtime_ns = value.get("mtime_ns")
        sha256 = value.get("sha256")
        if (
            not isinstance(normalized_path, str)
            or not normalized_path
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or isinstance(mtime_ns, bool)
            or not isinstance(mtime_ns, int)
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256.lower())
        ):
            raise ValueError("invalid file identity")
        return cls(
            normalized_path=normalized_path,
            size=size,
            mtime_ns=mtime_ns,
            device=_optional_mapping_id(value, "device"),
            inode=_optional_mapping_id(value, "inode"),
            sha256=sha256.lower(),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "normalized_path": self.normalized_path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
            "sha256": self.sha256,
        }

    def matches_path(self, path: str | Path) -> bool:
        """Return true only when *path* is still this exact file generation."""
        actual = self.capture(path)
        return actual == self


def _optional_file_id(stat_result: os.stat_result, name: str) -> int | None:
    value = getattr(stat_result, name, None)
    return value if isinstance(value, int) and value > 0 else None


def _optional_mapping_id(value: Mapping[str, Any], name: str) -> int | None:
    candidate = value.get(name)
    if candidate is None:
        return None
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ValueError("invalid file identity")
    return candidate or None
