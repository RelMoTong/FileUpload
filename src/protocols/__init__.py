# -*- coding: utf-8 -*-
"""
文件名：src/protocols/__init__.py
文件作用：网络传输协议实现模块“__init__”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Protocols 模块 - 网络协议实现

包含：
- ftp.py: FTP/FTPS 协议管理器（服务器和客户端）
"""

from .ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader

__all__ = ['FTPProtocolManager', 'FTPServerManager', 'FTPClientUploader']
