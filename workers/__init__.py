# -*- coding: utf-8 -*-
"""
Workers模块 - 纯IO操作层

这个模块包含所有Worker类，它们只负责：
1. 文件IO操作
2. 网络操作
3. 哈希计算
4. 发送信号给UI

不包含业务逻辑，所有业务逻辑在services层。
"""

from .upload_worker import UploadWorker

__all__ = ['UploadWorker']
