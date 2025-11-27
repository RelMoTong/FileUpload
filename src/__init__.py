# -*- coding: utf-8 -*-
"""
图片异步上传工具 - 模块化架构

v3.0.1 - 模块化架构重构完成，布局优化

目录结构:
- src/
  - main.py: 程序主入口
  - config.py: 配置管理
  - core/: 核心功能模块
    - utils.py: 工具函数
    - permissions.py: 权限管理
  - ui/: 用户界面模块
    - widgets.py: 自定义控件
    - main_window.py: 主窗口
  - workers/: 后台工作线程
    - upload_worker.py: 上传工作线程
  - protocols/: 协议模块
    - ftp.py: FTP 协议实现
"""

__version__ = "3.0.1"
__author__ = "RelMoTong"
