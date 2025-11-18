# -*- coding: utf-8 -*-
"""
服务层模块 - 业务逻辑与UI解耦

包含的服务类：
- UploadManager: 上传队列、重试、进度管理
- ProtocolClient: SMB/FTP/Both 协议封装
- CleanupManager: 磁盘清理、自动删除服务
- DedupService: 智能去重服务
"""

from .upload_manager import UploadManager, UploadTask, UploadResult
from .protocol_client import ProtocolClient, ProtocolType
from .cleanup_manager import CleanupManager
from .dedup_service import DedupService

__all__ = [
    'UploadManager',
    'UploadTask',
    'UploadResult',
    'ProtocolClient',
    'ProtocolType',
    'CleanupManager',
    'DedupService',
]
