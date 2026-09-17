"""应用运行状态与 MVC 共用枚举。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    GUEST = "guest"
    USER = "user"
    ADMIN = "admin"


class UploadStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class NetworkStatus(str, Enum):
    UNKNOWN = "unknown"
    GOOD = "good"
    UNSTABLE = "unstable"
    DISCONNECTED = "disconnected"


class UploadProtocol(str, Enum):
    SMB = "smb"
    FTP_CLIENT = "ftp_client"
    BOTH = "both"


class DuplicateStrategy(str, Enum):
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE = "overwrite"
    ASK = "ask"


@dataclass
class AppState:
    """由控制器独占维护的、可展示给界面的运行状态快照。

    用途：集中描述当前角色、上传状态、网络状态及统计计数。
    输入：控制器在状态变化时写入对应字段。
    输出：界面渲染和权限计算可直接读取的稳定数据对象。
    关键步骤：通过只读属性把上传状态转换为常用布尔判断。
    风险点：该模型不承担并发同步；跨线程更新必须先回到控制器边界。
    """

    role: UserRole = UserRole.GUEST
    upload_status: UploadStatus = UploadStatus.STOPPED
    network_status: NetworkStatus = NetworkStatus.UNKNOWN
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def is_running(self) -> bool:
        """返回上传是否处于运行或暂停这一类活动会话状态。"""
        return self.upload_status in {UploadStatus.RUNNING, UploadStatus.PAUSED}

    @property
    def is_paused(self) -> bool:
        """返回上传会话是否明确处于暂停状态。"""
        return self.upload_status is UploadStatus.PAUSED
