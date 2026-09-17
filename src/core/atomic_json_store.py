"""不依赖数据库的有界、带校验和 JSON 持久化工具。

写入时先在目标文件同目录创建临时文件，确保内容已刷新到磁盘后再原子替换。
替换前保留上一份有效内容为 ``.bak``，因此断电或写入损坏时不会把半成品当作
运行状态使用。
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
    """提供校验、备份和损坏文件隔离能力的原子 JSON 存储。

    ``payload`` 只允许为字典或列表，并同时限制字节数与记录数，防止运行状态文件
    无限增长。``wrap_envelope`` 为真时会额外写入模式版本和 SHA-256 校验和。
    """

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
        """配置存储位置、大小/记录上限、封装校验方式和可替换原子替换函数。

        用途：让配置、续传状态和归档日志可以共享同一套断电安全写入机制。
        风险点：临时文件必须和目标文件同目录，跨卷移动不能保证 ``os.replace`` 的原子性。
        """
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
        """读取并校验状态；主文件失效时尝试【.bak】，两者都不可用则返回默认值。

        用途：安全恢复无数据库运行状态，避免把截断或损坏 JSON 交给业务层。
        输入：可选业务校验器【validator】和读取失败时的默认值【default】。
        输出：通过全部校验的载荷、有效备份载荷或默认值。
        关键步骤：优先读取主文件；失败后读取备份；备份成功时隔离损坏主文件。
        风险点：读取失败不能重写主文件，否则会覆盖仍可人工恢复的现场数据。
        """
        if not self.path.exists():
            self.last_error = ""
            return default
        # 第一步：优先读取最新主文件，成功后不触碰备份文件。
        result = self._read_path(self.path, validator)
        if result is not None:
            self.last_error = ""
            return result

        # 第二步：主文件校验失败时，仅使用已经存在的上一份有效备份恢复。
        original_error = self.last_error
        backup = self._read_path(self.backup_path, validator)
        if backup is not None:
            self._quarantine(self.path)
            self.last_error = f"{original_error}; recovered_from_backup"
            return backup
        self.last_error = original_error
        return default

    def write(self, payload: Any) -> bool:
        """以“临时文件 → 刷盘 → 备份 → 原子替换”的顺序保存状态。

        用途：把运行状态写入磁盘，同时确保断电或异常不暴露半成品文件。
        输入：字典或列表类型的待保存载荷【payload】。
        输出：写入完成返回【True】，失败返回【False】并记录【last_error】。
        关键步骤：校验边界、编码校验和、同目录临时写入和刷盘、备份、原子替换。
        风险点：任一操作失败都会删除临时文件并保留原状态文件；调用方必须检查返回值。
        """
        temp_path: Path | None = None
        descriptor: int | None = None
        try:
            # 1. 先校验业务数据，避免无效内容覆盖上一份可恢复状态。
            self._validate_payload(payload)
            encoded_payload = self._canonical_bytes(payload)
            # 2. 按需封装版本号和校验和，读取时可识别截断或篡改。
            if self.wrap_envelope:
                envelope = {
                    "schema_version": self.schema_version,
                    "checksum_sha256": hashlib.sha256(encoded_payload).hexdigest(),
                    "payload": payload,
                }
                encoded = self._canonical_bytes(envelope)
            else:
                encoded = encoded_payload
            # 3. 在创建文件前执行容量限制，避免异常大状态文件占满磁盘。
            if len(encoded) > self.max_bytes:
                raise ValueError(f"state exceeds {self.max_bytes} byte limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temp_path = Path(raw_path)
            # 4. 临时文件必须位于目标目录，才能保证后续 os.replace 为同盘原子替换。
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = None
            # 5. 仅在新内容已经写实后备份旧版本，失败时主文件仍保持不变。
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            if self._replace_func is None:
                os.replace(temp_path, self.path)
            else:
                self._replace_func(temp_path, self.path)
            temp_path = None
            # 6. POSIX 平台继续刷新目录项；Windows 的目录 fsync 不受支持。
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
        """读取单个候选文件，并把所有解析/校验错误收敛到 ``last_error``。

        用途：为主文件与备份文件复用完全相同的大小、JSON、校验和和业务校验流程。
        风险点：返回 ``None`` 只表示本候选不可用；上层 ``read`` 还会决定是否使用备份。
        """
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
        """兼容旧版裸 JSON，并校验新版封装中的模式版本和校验和。

        旧版载荷可读取以实现平滑升级；一旦发现完整封装字段，则版本和校验和必须全部通过。
        """
        if not isinstance(loaded, Mapping):
            return loaded  # 旧版裸 JSON 可读取，下次成功保存时会升级为封装格式。
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
        """确认状态结构和记录数均在本存储组件允许的边界内。"""
        if not isinstance(payload, (dict, list)):
            raise ValueError("state payload must be a JSON object or list")
        record_count = self._record_count(payload)
        if record_count > self.max_records:
            raise ValueError(f"state exceeds {self.max_records} record limit")

    @staticmethod
    def _record_count(payload: Any) -> int:
        """按对象顶层条目或 ``items`` 列表计算记录数，用于有界存储保护。"""
        if isinstance(payload, dict):
            if len(payload) == 1 and isinstance(payload.get("items"), list):
                return len(payload["items"])
            return len(payload)
        return len(payload)

    @staticmethod
    def _canonical_bytes(payload: Any) -> bytes:
        """以固定键顺序和紧凑格式编码 JSON，保证写入与校验和计算结果稳定。"""
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _fsync_file(path: Path) -> None:
        """刷新单个文件内容到磁盘；保留为基础工具供需要额外持久化保证的调用方使用。"""
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """在支持的系统上刷新目录元数据，确保原子替换已落盘。"""
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _quarantine(self, path: Path) -> None:
        """把损坏主文件改名隔离，避免下次启动再次误用该文件。

        隔离失败也不能覆盖原文件；调用方仍会返回有效备份或默认状态，并保留 ``last_error``。
        """
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
