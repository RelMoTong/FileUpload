# -*- coding: utf-8 -*-
"""
图片异步上传工具 - 兼容层入口

v3.1.1 - 精简为仅调用模块化入口
- 保留此文件用于向后兼容和打包脚本
- 实际功能已迁移到 src/ 模块化架构

使用方式：
    python pyqt_app.py
    
或使用模块化入口：
    python -m src.main
"""
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 导入模块化入口
from src.main import main

# 导出常用组件（兼容旧代码引用）
from src.core import get_app_dir, get_resource_path, get_app_version, get_app_title
from src.config import ConfigManager
from src.ui.widgets import Toast, ChipWidget, CollapsibleBox, DiskCleanupDialog
from src.workers.upload_worker import UploadWorker
from src.ui import MainWindow

# v3.1.1: 安全导入 FTP 模块，缺少依赖时不崩溃
try:
    from src.protocols.ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader
    FTP_AVAILABLE = True
except ImportError as e:
    FTP_AVAILABLE = False
    FTPProtocolManager = None  # type: ignore
    FTPServerManager = None  # type: ignore
    FTPClientUploader = None  # type: ignore
    print(f"[警告] FTP 模块不可用: {e}")
    print("[提示] 如需 FTP 功能，请运行: pip install pyftpdlib")

# 版本信息（保持向后兼容）
APP_VERSION = get_app_version()
APP_TITLE = f"图片异步上传工具 v{APP_VERSION}"


if __name__ == '__main__':
    sys.exit(main())
