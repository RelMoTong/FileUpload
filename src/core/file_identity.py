"""
文件名：src/core/file_identity.py
文件作用：通用基础设施与安全边界模块“file_identity”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

用于安全文件生命周期处理的不可变源文件身份标识。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping


def normalize_file_path(path: str | Path) -> str:
    """返回用于身份比较的规范路径；调用时不要求路径一定存在。

    用途：让大小写不同、相对路径或符号链接表示的同一文件得到相同的比较键。
    输入：字符串或 ``Path`` 路径。
    输出：绝对、解析真实路径并按平台规则标准化大小写后的字符串。
    风险点：它只规范路径表示，不证明当前文件身份；删除/归档仍必须调用 ``matches_path``。
    """
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


@dataclass(frozen=True)
class FileIdentity:
    """描述某一代源文件的可持久化身份。

    文件大小和纳秒级修改时间可快速识别多数变化；文件系统提供设备号或 inode 时
    一并保存。SHA-256 用于避免“大小相同且修改时间被还原”的替换文件误通过校验。
    """

    normalized_path: str
    size: int
    mtime_ns: int
    device: int | None
    inode: int | None
    sha256: str

    @classmethod
    def capture(cls, path: str | Path) -> "FileIdentity":
        """读取文件元数据和完整摘要，生成上传或归档前的安全快照。

        用途：为文件在扫描后、实际操作前是否被替换提供可复核依据。
        输入：待捕获的文件路径【path】。
        输出：包含规范路径、大小、修改时间、文件系统标识和 SHA-256 的不可变身份对象。
        关键步骤：先读取元数据，再按固定块大小计算完整摘要。
        风险点：捕获期间文件若被其他程序修改，后续【matches_path】会以新的完整快照拒绝操作。
        """
        source = Path(path)
        # 先读取元数据，再以固定块大小计算摘要，避免一次性把大文件读入内存。
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
        """从持久化身份数据恢复对象；字段异常时拒绝恢复而不是猜测默认值。

        用途：安全读取归档日志和断点恢复记录中的文件身份。
        输入：JSON 解析后的身份映射【value】。
        输出：通过全部字段校验的【FileIdentity】对象。
        关键步骤：校验路径、大小、纳秒时间、摘要长度和十六进制字符，再恢复可选文件系统标识。
        风险点：任何关键字段不可信都会抛出异常；不能用默认身份继续删除或归档文件。
        """
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
        """转换为可写入 JSON 日志或断点恢复记录的普通字典。

        用途：将不可变身份快照传给持久化归档日志，而不依赖 Python 对象序列化。
        输出：只包含下一次安全复核所需字段的 JSON 兼容字典。
        风险点：不能只保存路径；缺少大小、时间和 SHA-256 会使恢复归档无法确认文件代际。
        """
        return {
            "normalized_path": self.normalized_path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
            "sha256": self.sha256,
        }

    def matches_path(self, path: str | Path) -> bool:
        """重新捕获路径身份，仅当其仍是扫描时的同一代文件才返回真。

        用途：在删除或归档前防止对同名但已替换的文件执行操作。
        输入：当前要复核的路径【path】。
        输出：身份完全一致返回【True】，否则返回【False】或传播无法读取文件的异常。
        关键步骤：重新计算完整快照后与本对象比较。
        风险点：完整摘要读取会产生磁盘 I/O；必须由后台流程调用，不能阻塞主界面。
        """
        # 重新完整采样，确保删除或归档前不会操作扫描后已被替换的同名文件。
        actual = self.capture(path)
        return actual == self


def _optional_file_id(stat_result: os.stat_result, name: str) -> int | None:
    """提取文件系统可选标识；缺失或非正值统一视为不可用。"""
    value = getattr(stat_result, name, None)
    return value if isinstance(value, int) and value > 0 else None


def _optional_mapping_id(value: Mapping[str, Any], name: str) -> int | None:
    """校验从持久化数据读取的可选文件系统标识，拒绝布尔值和负数。"""
    candidate = value.get(name)
    if candidate is None:
        return None
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ValueError("invalid file identity")
    return candidate or None
