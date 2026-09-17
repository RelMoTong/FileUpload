# -*- coding: utf-8 -*-
"""
文件名：src/__init__.py
文件作用：应用入口配套模块“__init__”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

图片异步上传工具 - 模块化架构

目录结构:
- src/
  - main.py: 程序主入口
  - config.py: 配置管理
  - core/: 核心功能模块
    - utils.py: 工具函数
    - resume_manager.py: 断点续传
    - i18n.py: 多语言支持
  - ui/: 用户界面模块
    - widgets.py: 自定义控件
    - main_window.py: 主窗口
  - workers/: 后台工作线程
    - upload_worker.py: 上传工作线程
  - protocols/: 协议模块
    - ftp.py: FTP 协议实现
"""

__version__ = "3.5.2"
__author__ = "RelMoTong"
