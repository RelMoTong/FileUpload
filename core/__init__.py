# -*- coding: utf-8 -*-
"""
核心业务逻辑模块
"""
from .config_manager import ConfigManager
# v2.2.0: 以下服务类暂未实现，预留接口
# from .upload_manager import UploadManager
# from .protocol_client import ProtocolClient
# from .cleanup_manager import CleanupManager
# from .dedup_service import DedupService
from .permission_manager import PermissionManager
from .single_instance import SingleInstanceManager
from .error_classifier import ErrorClassifier

__all__ = [
    'ConfigManager',
    # 'UploadManager',
    # 'ProtocolClient',
    # 'CleanupManager',
    # 'DedupService',
    'PermissionManager',
    'SingleInstanceManager',
    'ErrorClassifier',
]
