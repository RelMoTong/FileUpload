# -*- coding: utf-8 -*-
"""
文件名：src/workers/__init__.py
文件作用：后台上传执行模块“__init__”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Workers 模块 - 后台工作线程

包含：
- upload_worker.py: 上传工作线程（UploadWorker）
"""

from .upload_worker import UploadWorker

__all__ = ['UploadWorker']
