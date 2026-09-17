"""
文件名：src/models/upload_task.py
文件作用：纯数据模型与业务契约模块“upload_task”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

上传编排流程使用的运行时模型。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .app_state import NetworkStatus, UploadStatus


@dataclass(frozen=True)
class UploadTaskRequest:
    """启动上传任务所需的不可变参数快照。"""
    source: str
    target: str
    backup: str
    interval: int
    mode: str
    disk_threshold_percent: int
    retry_count: int
    filters: Tuple[str, ...]
    app_dir: Path
    enable_deduplication: bool = False
    hash_algorithm: str = "md5"
    duplicate_strategy: str = "ask"
    network_check_interval: int = 10
    network_auto_pause: bool = True
    network_auto_resume: bool = True
    enable_auto_delete: bool = False
    auto_delete_threshold: int = 80
    auto_delete_target_percent: int = 40
    upload_protocol: str = "smb"
    ftp_client_config: Optional[Dict[str, Any]] = None
    enable_backup: bool = True
    limit_upload_rate: bool = False
    max_upload_rate_mbps: float = 10.0
    file_upload_delay_seconds: float = 1.5


@dataclass(frozen=True)
class UploadValidationResult:
    """上传参数校验结果；错误为空时才允许启动任务。"""
    errors: Tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        """返回上传请求是否通过全部前置校验。"""
        return not self.errors


@dataclass(frozen=True)
class UploadCommandResult:
    """上传控制命令的统一返回结果。"""
    success: bool
    message: str = ""
    errors: Tuple[str, ...] = ()


@dataclass
class UploadRuntimeState:
    """上传会话在运行期间不断更新的状态快照。"""
    status: UploadStatus = UploadStatus.STOPPED
    network_status: NetworkStatus = NetworkStatus.UNKNOWN
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0
    rate: str = "0 MB/s"
    progress_current: int = 0
    progress_total: int = 0
    current_filename: str = ""
    file_progress: int = 0
    start_time: Optional[float] = None
    last_error: str = ""
    archive_queue_size: int = 0

    @property
    def is_running(self) -> bool:
        """返回上传会话是否仍处于运行或暂停状态。"""
        return self.status in {UploadStatus.RUNNING, UploadStatus.PAUSED}

    @property
    def is_paused(self) -> bool:
        """返回上传会话是否处于暂停状态。"""
        return self.status is UploadStatus.PAUSED

    def snapshot(self) -> "UploadRuntimeState":
        """复制当前可变状态，避免界面读写同一个实例。"""
        return replace(self)
