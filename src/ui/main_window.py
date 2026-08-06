# -*- coding: utf-8 -*-
"""
Main window UI module.
"""
import os
import sys
import json
import copy
import time
import shutil
import threading
import datetime
import queue
import winreg
import hashlib
import logging
import ctypes
import re
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Iterable, List, Tuple, Optional, Any, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# 创建logger
logger = logging.getLogger(__name__)

try:
    from src.protocols.ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader
    FTP_AVAILABLE = True
except ImportError as _ftp_import_error:
    FTP_AVAILABLE = False
    FTPProtocolManager = FTPServerManager = FTPClientUploader = None  # type: ignore[misc, assignment]
    import logging
    logging.warning(f"FTP 模块不可用: {_ftp_import_error}. 如需 FTP 功能，请安装 pyftpdlib")

# 类型守卫（仅用于类型检查）
if not FTP_AVAILABLE:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        # 为类型检查器提供类型提示
        FTPProtocolManager = FTPServerManager = FTPClientUploader = Any  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
    from PySide6.QtNetwork import QLocalServer, QLocalSocket  # type: ignore[import-not-found]
    Signal = QtCore.Signal
else:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    Signal = QtCore.Signal

from src.core import (
    get_app_dir,
    get_resource_path,
    get_app_version,
    get_app_title,
    protect_secret,
    unprotect_secret,
)
from src.config import ConfigManager
from src.core.i18n import t, set_language, get_language, add_language_listener, SUPPORTED_LANGUAGES  # v3.0.2: 多语言支持
from src.ui.widgets import Toast, ChipWidget, CollapsibleBox, DiskCleanupDialog, trash_supported, send_to_trash
from src.workers.upload_worker import UploadWorker

APP_VERSION = get_app_version()
APP_TITLE = get_app_title()
DEFAULT_USER_PASSWORD_HASH = hashlib.sha256('123'.encode('utf-8')).hexdigest()
DEFAULT_ADMIN_PASSWORD_HASH = hashlib.sha256('Tops123'.encode('utf-8')).hexdigest()
STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ImageUploader"
AUTO_CLEANUP_FAILURE_LIMIT = 20


def _parse_app_version(value: str) -> Optional[Tuple[int, ...]]:
    """从版本文本或打包文件名中提取可比较的数字版本。"""
    match = re.search(r"(?:^|[_-])v?(\d+(?:\.\d+){1,3})(?:\D|$)", value or "", re.IGNORECASE)
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def _extract_startup_target(command: str) -> str:
    """从 Run 注册表命令中提取首个 exe/python 路径，兼容旧的未加引号值。"""
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else ""
    match = re.match(r"(.+?\.(?:exe|com|bat|cmd|pyw?|py))(?=\s|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.split(maxsplit=1)[0]


def _extract_startup_script(command: str) -> str:
    """提取源码模式启动命令中的脚本路径。"""
    text = (command or "").strip()
    target = _extract_startup_target(text)
    if not target:
        return ""

    if text.startswith('"'):
        end = text.find('"', 1)
        remainder = text[end + 1:].strip() if end > 1 else ""
    else:
        remainder = text[len(target):].strip()
    if not remainder:
        return ""
    if remainder.startswith('"'):
        end = remainder.find('"', 1)
        return remainder[1:end] if end > 1 else ""
    return remainder.split(maxsplit=1)[0]


def get_qt_enum(enum_class, attr_name: str, fallback_value: int):
    """Safe Qt enum getter compatible with PySide6/PyQt5."""
    try:
        return getattr(enum_class, attr_name, fallback_value)
    except AttributeError:
        return fallback_value

__all__ = ['MainWindow']


class MainWindow(QtWidgets.QMainWindow):  # type: ignore[misc]
    # 内部信号用于线程安全的UI更新
    _disk_update_signal = Signal(str, float)  # disk_type, free_percent
    _async_log_signal = Signal(str)
    _ftp_server_event_signal = Signal(dict)
    _permission_changed_signal = Signal()  # 角色/运行状态变更
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self._init_responsive_metrics()
        self.setMinimumSize(self.window_min_width, self.window_min_height)
        self.resize(self.window_initial_width, self.window_initial_height)
        self.app_dir = get_app_dir()
        
        # 连接内部信号
        self._disk_update_signal.connect(self._on_disk_update)
        self._async_log_signal.connect(self._append_log)
        self._ftp_server_event_signal.connect(self._handle_ftp_server_event)
        # 权限系统
        self.current_role = 'guest'  # guest, user, admin
        # 默认密码（SHA256哈希）
        self.user_password = DEFAULT_USER_PASSWORD_HASH
        self.admin_password = DEFAULT_ADMIN_PASSWORD_HASH
        self.default_password_roles: List[str] = []
        # state
        self.source = ''
        self.target = ''
        self.backup = ''
        self.enable_backup = True  # v2.1.1 新增：是否启用备份
        self.interval = 30
        self.mode = 'periodic'
        self.disk_threshold_percent = 10
        self.retry_count = 3
        self.filters = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.raw']
        self.autoscroll = True
        self.auto_start_windows = False  # 开机自启动
        self.auto_run_on_startup = False  # 软件自动运行
        self.is_running = False
        self.is_paused = False
        self.start_time = None
        self.worker = None
        # v2.2.0 新增：保存统计数据（用于通知和显示）
        self.uploaded = 0
        self.failed = 0
        self.skipped = 0
        self.config_modified = False  # 配置是否被修改
        self._config_loading = False  # 配置加载期间守卫标志
        self.saved_config = {}  # 保存的配置（用于回退）
        self.last_config_save_error = ''
        self.disk_check_interval = 5  # 磁盘空间检查间隔（秒）
        self.disk_check_counter = 0  # 磁盘空间检查计数器
        
        # v1.9 新增：文件去重配置
        self.enable_deduplication = False  # 是否启用智能去重
        self.hash_algorithm = 'md5'  # 哈希算法：md5 或 sha256
        self.duplicate_strategy = 'ask'  # 去重策略：skip, rename, overwrite, ask
        
        # v1.9 新增：网络监控配置
        self.network_check_interval = 10  # 网络检测间隔（秒）
        self.network_auto_pause = True  # 网络断开自动暂停
        self.network_auto_resume = True  # 网络恢复自动继续
        self.network_status = 'unknown'  # 网络状态：good, unstable, disconnected, unknown
        
        # v1.9 新增：自动删除配置
        self.enable_auto_delete = False
        self.auto_delete_folder = ''
        self.auto_delete_folders = []
        self.auto_delete_threshold = 80  # 磁盘使用率达到此值时触发
        self.auto_delete_target_percent = 40  # 触发后回落到此值
        self.auto_delete_keep_days = 10  # 仅兼容旧配置，不参与自动清理筛选
        self.auto_delete_check_interval = 300  # 每5分钟检查一次
        self.auto_delete_formats: List[str] = []  # 自动清理文件格式过滤
        self.auto_delete_use_trash = True  # 自动清理删除模式（True=回收站）
        
        # v2.0 新增：FTP 协议配置
        self.current_protocol = 'smb'  # 上传协议：smb, ftp_client, both
        self.enable_ftp_server = False  # v3.1.0: FTP服务器独立开关
        self.ftp_server_config = {
            'host': '0.0.0.0',
            'port': 2121,
            'username': 'upload_user',
            'password': 'upload_pass',
            'shared_folder': '',
        }
        self.ftp_client_config = {
            'host': '',
            'port': 21,
            'username': '',
            'password': '',
            'remote_path': '/upload',
            'timeout': 30,
            'retry_count': 3,
        }
        
        # 日志文件路径（每天一个日志文件）
        self.log_file_path = None
        self._init_log_file()
        
        # 确保必要的目录存在
        self._ensure_directories()
        
        # 日志写入线程池（避免阻塞主线程）
        self._log_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LogWriter")
        self._disk_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="DiskCheck")
        self._cleanup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AutoCleanup")
        self._auto_cleanup_timer = QtCore.QTimer(self)
        self._auto_cleanup_timer.timeout.connect(self._auto_cleanup_tick)
        self._auto_cleanup_running = False
        self._auto_cleanup_lock = threading.Lock()
        self._auto_cleanup_cancel_event = threading.Event()
        self._is_closing = False
        self._auto_cleanup_last_warn = 0.0
        
        # v2.2.0 新增：系统托盘配置
        self.minimize_to_tray = True  # 最小化到托盘
        self.show_notifications = True  # 显示通知
        self.tray_icon = None  # 托盘图标对象
        
        # v2.3.0 新增：速率限制配置
        self.limit_upload_rate = False
        self.max_upload_rate_mbps = 10.0
        
        # v2.0 新增：FTP 协议管理器（延迟初始化，避免在UI创建前调用日志）
        self.ftp_manager = None
        self._ftp_server_started_independently = False
        self._ftp_server_started_by_upload = False
        
        # UI
        self._build_ui()
        self._load_config()
        self._update_auto_cleanup_schedule()
        self._apply_theme()
        self._update_ui_permissions()
        
        # v2.2.0 新增：初始化系统托盘
        self._init_tray_icon()
        
        # v2.0 新增：初始化 FTP 协议管理器（在UI创建后）
        if FTP_AVAILABLE:
            try:
                self.ftp_manager = FTPProtocolManager()  # type: ignore[misc]
                self._append_log("✓ FTP 协议管理器已初始化")
            except Exception as e:
                self._append_log(f"⚠ FTP 协议管理器初始化失败: {e}")
                self.ftp_manager = None
        
        # 自动运行检查
        if self.auto_run_on_startup:
            QtCore.QTimer.singleShot(1000, self._auto_start_upload)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @staticmethod
    def calculate_responsive_metrics(available_width: int, available_height: int) -> dict:
        """Return screen-aware UI sizing values for the main window."""
        width = max(int(available_width or 0), 800)
        height = max(int(available_height or 0), 600)
        compact = width <= 1180 or height <= 800
        scale = min(width / 1350, height / 880, 1.0)
        scale = max(0.78 if compact else 0.9, scale)

        min_width = 1200 if not compact else min(900, max(840, int(width * 0.86)))
        min_height = 750 if not compact else min(680, max(610, int(height * 0.82)))
        min_width = min(min_width, max(760, width - 48))
        min_height = min(min_height, max(560, height - 48))

        initial_width = min(1350, max(min_width, width - 24))
        initial_height = min(880, max(min_height, height - 40))
        status_columns = 2 if compact else 4

        return {
            'available_width': width,
            'available_height': height,
            'scale': scale,
            'compact': compact,
            'min_width': min_width,
            'min_height': min_height,
            'initial_width': initial_width,
            'initial_height': initial_height,
            'status_columns': status_columns,
            'content_min_width': 0,
        }

    def _init_responsive_metrics(self) -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            metrics = self.calculate_responsive_metrics(geometry.width(), geometry.height())
        else:
            metrics = self.calculate_responsive_metrics(1366, 768)

        self.responsive_metrics = metrics
        self.ui_scale = float(metrics['scale'])
        self.compact_mode = bool(metrics['compact'])
        self.window_min_width = int(metrics['min_width'])
        self.window_min_height = int(metrics['min_height'])
        self.window_initial_width = int(metrics['initial_width'])
        self.window_initial_height = int(metrics['initial_height'])
        self.status_grid_columns = int(metrics['status_columns'])

    def _scale_px(self, value: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
        scaled = max(minimum, int(round(value * self.ui_scale)))
        if maximum is not None:
            scaled = min(maximum, scaled)
        return scaled

    def _font_pt(self, value: int, minimum: int = 8) -> int:
        return max(minimum, int(round(value * self.ui_scale)))

    def _clamped_dialog_size(self, width: int, height: int) -> QtCore.QSize:
        screen_width = int(self.responsive_metrics.get('available_width', 1366))
        screen_height = int(self.responsive_metrics.get('available_height', 768))
        return QtCore.QSize(
            min(width, max(320, int(screen_width * 0.9))),
            min(height, max(220, int(screen_height * 0.9))),
        )

    def _init_log_file(self):
        """初始化日志文件（每天一个日志文件）"""
        try:
            logs_dir = self.app_dir / 'logs'
            logs_dir.mkdir(parents=True, exist_ok=True)
            
            # 使用当前日期作为文件名
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            self.log_file_path = logs_dir / f'upload_{today}.txt'
            
            # 如果是新文件，写入文件头
            if not self.log_file_path.exists():
                with open(self.log_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"{'='*60}\n")
                    f.write(f"  图片异步上传工具 - 运行日志\n")
                    f.write(f"  日期: {today}\n")
                    f.write(f"{'='*60}\n\n")
        except Exception as e:
            print(f"初始化日志文件失败: {e}")
            self.log_file_path = None

    def _ensure_directories(self):
        """确保必要的目录存在（logs 等）
        
        在打包后的程序中，需要在 exe 所在目录创建可写目录
        config.json 由 ConfigManager.load() 在首次加载时自动创建，无需在此重复处理
        """
        try:
            # 创建 logs 目录
            logs_dir = self.app_dir / 'logs'
            logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"创建目录失败: {e}")

    def _apply_theme(self):
        stylesheet = """
            QWidget{font-family:'Segoe UI', 'Microsoft YaHei UI'; font-size:11pt; color:#1F2937; background:#E3F2FD;}
            QMainWindow{background:#E3F2FD;}
            QFrame#Card{background:#FFFFFF; border:2px solid #64B5F6; border-radius:10px;}
            QLabel{color:#1F2937;}
            QLabel.Title{color:#1976D2; font-weight:700; font-size:14pt;}
            QPushButton{font-size:11pt;}
            QPushButton:disabled{background:#E5E7EB; color:#9CA3AF; border:1px solid #D1D5DB;}
            QPushButton.Primary{background:#1976D2; color:#FFFFFF; border:none; border-radius:8px; padding:8px 12px;}
            QPushButton.Primary:hover{background:#1E88E5;}
            QPushButton.Primary:disabled{background:#BDBDBD; color:#FFFFFF;}
            QPushButton.Secondary{background:#F1F5F9; color:#0F172A; border:1px solid #64B5F6; border-radius:8px; padding:6px 10px;}
            QPushButton.Secondary:hover{background:#E3F2FD;}
            QPushButton.Secondary:disabled{background:#E5E7EB; color:#9CA3AF;}
            QPushButton.Warning{background:#FEF3C7; color:#A16207; border:1px solid #FCD34D; border-radius:8px; padding:6px 10px;}
            QPushButton.Warning:hover{background:#FDE68A;}
            QPushButton.Warning:disabled{background:#E5E7EB; color:#9CA3AF;}
            QPushButton.Danger{background:#FEE2E2; color:#B91C1C; border:1px solid #FCA5A5; border-radius:8px; padding:6px 10px;}
            QPushButton.Danger:hover{background:#FECACA;}
            QPushButton.Danger:disabled{background:#E5E7EB; color:#9CA3AF;}
            QProgressBar{border:1px solid #64B5F6; border-radius:6px; background:#EEF2F5; text-align:center; color:#1F2937;}
            QProgressBar::chunk{border-radius:6px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4FACFE, stop:1 #00F2FE);} 
            QPlainTextEdit{background:#FFFFFF; border:1px solid #64B5F6; color:#1F2937; border-radius:4px;}
            QSpinBox{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px; padding-right:2px;}
            QSpinBox:disabled{background:#F3F4F6; color:#9CA3AF; border:1px solid #D1D5DB;}
            QSpinBox::up-button{background:#FFFFFF; border:1px solid #64B5F6; border-top-right-radius:3px; width:24px; height:14px;}
            QSpinBox::up-button:hover{background:#E3F2FD;}
            QSpinBox::up-button:pressed{background:#BBDEFB;}
            QSpinBox::up-button:disabled{background:#F3F4F6; border:1px solid #D1D5DB;}
            QSpinBox::down-button{background:#FFFFFF; border:1px solid #64B5F6; border-bottom-right-radius:3px; width:24px; height:14px;}
            QSpinBox::down-button:hover{background:#E3F2FD;}
            QSpinBox::down-button:pressed{background:#BBDEFB;}
            QSpinBox::down-button:disabled{background:#F3F4F6; border:1px solid #D1D5DB;}
            QSpinBox::up-arrow{width:18px; height:18px; image:url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAxOCAxOCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48dGV4dCB4PSI1MCUiIHk9IjU1JSIgZG9taW5hbnQtYmFzZWxpbmU9Im1pZGRsZSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IiMxOTc2RDIiPuKshjwvdGV4dD48L3N2Zz4=);}
            QSpinBox::up-arrow:disabled{image:url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAxOCAxOCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48dGV4dCB4PSI1MCUiIHk9IjU1JSIgZG9taW5hbnQtYmFzZWxpbmU9Im1pZGRsZSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IiNDQkQ1RTEiPuKshjwvdGV4dD48L3N2Zz4=);}
            QSpinBox::down-arrow{width:18px; height:18px; image:url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAxOCAxOCIgeG1zbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48dGV4dCB4PSI1MCUiIHk9IjU1JSIgZG9taW5hbnQtYmFzZWxpbmU9Im1pZGRsZSIgdGV4dC1hbmNob3I9Im1pZGRzZSIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IiMxOTc2RDIiPuKshzwvdGV4dD48L3N2Zz4=);}
            QSpinBox::down-arrow:disabled{image:url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHZpZXdCb3g9IjAgMCAxOCAxOCIgeG1zbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48dGV4dCB4PSI1MCUiIHk9IjU1JSIgZG9taW5hbnQtYmFzZWxpbmU9Im1pZGRsZSIgdGV4dC1hbmNob3I9Im1pZGRzZSIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IiNDQkQ1RTEiPuKshzwvdGV4dD48L3N2Zz4=);}
            QLineEdit{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
            QLineEdit:read-only{background:#F3F4F6; color:#6B7280; border:1px solid #D1D5DB;}
            QCheckBox{color:#1F2937; spacing:8px;}
            QCheckBox:disabled{color:#9CA3AF;}
            QCheckBox::indicator{width:22px; height:22px; background:#FFFFFF; border:2px solid #64B5F6; border-radius:4px;}
            QCheckBox::indicator:disabled{background:#F3F4F6; border:2px solid #D1D5DB;}
            QCheckBox::indicator:checked{background:qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1976D2, stop:1 #2196F3); border:2px solid #1976D2;}
            QCheckBox::indicator:checked:disabled{background:#E0E0E0; border:2px solid #D1D5DB;}
            QToolButton{color:#1F2937; background:#FFFFFF; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
            QToolButton:hover{background:#E3F2FD;}
            QToolButton::menu-indicator{image:none;}
            QMenu{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
            QMenu::item{padding:6px 20px; border-radius:3px;}
            QMenu::item:disabled{color:#9CA3AF; background:#FFFFFF;}
            QMenu::item:selected{background:#E3F2FD; color:#1976D2;}
            QMenu::item:selected:disabled{background:#F3F4F6; color:#9CA3AF;}
            QMenu::separator{height:1px; background:#E5EAF0; margin:4px 0px;}
            QDialog{background:#E3F2FD;}
            QComboBox{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
            QComboBox:disabled{background:#F3F4F6; color:#9CA3AF; border:1px solid #D1D5DB;}
            QComboBox::drop-down{border:none;}
            QComboBox::down-arrow{image:none; border-left:4px solid transparent; border-right:4px solid transparent; border-top:6px solid #1976D2; margin-right:8px;}
            QComboBox::down-arrow:disabled{border-top-color:#9CA3AF;}
            QComboBox QAbstractItemView{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; selection-background-color:#E3F2FD;}
            
            /* 滚动条样式 */
            QScrollBar:vertical{background:#E3F2FD; width:12px; border-radius:6px; margin:0px;}
            QScrollBar::handle:vertical{background:#90CAF9; border-radius:6px; min-height:30px;}
            QScrollBar::handle:vertical:hover{background:#64B5F6;}
            QScrollBar::handle:vertical:pressed{background:#42A5F5;}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{height:0px;}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical{background:transparent;}
            
            QScrollBar:horizontal{background:#E3F2FD; height:12px; border-radius:6px; margin:0px;}
            QScrollBar::handle:horizontal{background:#90CAF9; border-radius:6px; min-width:30px;}
            QScrollBar::handle:horizontal:hover{background:#64B5F6;}
            QScrollBar::handle:horizontal:pressed{background:#42A5F5;}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal{width:0px;}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal{background:transparent;}
            """
        stylesheet = stylesheet.replace("font-size:11pt", f"font-size:{self._font_pt(11)}pt")
        stylesheet = stylesheet.replace("font-size:14pt", f"font-size:{self._font_pt(14, 11)}pt")
        stylesheet = stylesheet.replace("font-size:10pt", f"font-size:{self._font_pt(10)}pt")
        stylesheet = stylesheet.replace("font-size:9pt", f"font-size:{self._font_pt(9)}pt")
        self.setStyleSheet(stylesheet)

    def _set_checkbox_mark(self, cb: QtWidgets.QCheckBox, checked: bool):
        """Fallback visual marker for checkboxes: prefix label with ✓ when checked.
        This ensures users see a clear marker even if stylesheet indicator image fails to render.
        """
        try:
            orig = cb.property('orig_text') or cb.text()
            if checked:
                # use fullwidth mark for clear appearance
                cb.setText(f"✓ {orig}")
            else:
                cb.setText(str(orig))
        except Exception:
            pass

    def _build_ui(self):
        # 创建滚动区域作为中央窗口
        scroll_area = QtWidgets.QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.main_scroll_area = scroll_area
        self.setCentralWidget(scroll_area)
        
        # 创建内容容器 - 优化宽度适配高分辨率
        central = QtWidgets.QWidget()
        central.setMinimumWidth(int(self.responsive_metrics.get('content_min_width', 0)))
        central.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.central_content = central
        scroll_area.setWidget(central)
        
        root = QtWidgets.QVBoxLayout(central)
        root.setSpacing(self._scale_px(12, 8))
        root.setContentsMargins(
            self._scale_px(12, 8),
            self._scale_px(12, 8),
            self._scale_px(12, 8),
            self._scale_px(12, 8),
        )

        # header
        header = QtWidgets.QHBoxLayout()
        
        # Logo - 使用资源路径函数确保打包后也能访问
        logo_path = get_resource_path("assets/logo.png")
        if logo_path.exists():
            logo_label = QtWidgets.QLabel()
            pixmap = QtGui.QPixmap(str(logo_path))
            if not pixmap.isNull():
                # 设置 Logo 大小，低分辨率下按比例缩小。
                scaled_pixmap = pixmap.scaledToHeight(self._scale_px(40, 28))
                logo_label.setPixmap(scaled_pixmap)
                logo_label.setStyleSheet("background: transparent;")
                header.addWidget(logo_label)
                header.addSpacing(self._scale_px(12, 8))
            else:
                logger.warning("⚠️ Logo 文件加载失败")
        else:
            logger.warning(f"⚠️ Logo 文件不存在: {logo_path}")
        
        self.header_title = QtWidgets.QLabel(t('header_title'))
        self.header_title.setObjectName("Title")
        ver = QtWidgets.QLabel(f"v{APP_VERSION} (PyQt)")
        header.addWidget(self.header_title)
        header.addWidget(ver)
        header.addStretch(1)
        self.role_label = QtWidgets.QLabel(t('role_guest'))
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        header.addWidget(self.role_label)
        root.addLayout(header)

        # center three columns - 优化列间距
        center = QtWidgets.QHBoxLayout()
        center.setSpacing(self._scale_px(15, 8))
        root.addLayout(center, 1)

        left = QtWidgets.QVBoxLayout()
        middle = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        left.setSpacing(self._scale_px(12, 8))
        middle.setSpacing(self._scale_px(12, 8))
        right.setSpacing(self._scale_px(12, 8))
        center.addLayout(left, 1)
        center.addLayout(middle, 1)
        center.addLayout(right, 1)

        # left cards - 使用 QSplitter 防止卡片互相影响大小
        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        left_splitter.setChildrenCollapsible(False)  # 防止子部件被完全折叠
        left_splitter.setHandleWidth(self._scale_px(8, 5))
        left_splitter.setStyleSheet("""
            QSplitter::handle {
                background: #E5EAF0;
                margin: 2px 0;
            }
            QSplitter::handle:hover {
                background: #1976D2;
            }
        """)
        
        folder_card = self._folder_card()
        settings_card = self._settings_card()
        
        left_splitter.addWidget(folder_card)
        left_splitter.addWidget(settings_card)
        
        # 设置初始比例：文件夹卡片较小，设置卡片较大
        left_splitter.setSizes([self._scale_px(200, 160), self._scale_px(500, 360)])
        
        left.addWidget(left_splitter)

        # middle cards - 同样使用 QSplitter
        middle_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        middle_splitter.setChildrenCollapsible(False)
        middle_splitter.setHandleWidth(self._scale_px(8, 5))
        middle_splitter.setStyleSheet("""
            QSplitter::handle {
                background: #E5EAF0;
                margin: 2px 0;
            }
            QSplitter::handle:hover {
                background: #1976D2;
            }
        """)
        
        middle_splitter.addWidget(self._control_card())
        middle_splitter.addWidget(self._status_card())
        middle_splitter.setSizes([self._scale_px(250, 190), self._scale_px(450, 330)])
        
        middle.addWidget(middle_splitter)

        # right - log card
        right.addWidget(self._log_card(), 1)

    def _card(self, title_text: str, title_key: str = '') -> Tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout, Optional[QtWidgets.QLabel]]:
        """创建卡片容器
        
        Args:
            title_text: 标题文本
            title_key: i18n 翻译键（用于动态切换语言）
            
        Returns:
            (card, layout, title_label) - title_label 用于后续更新文本
        """
        card = QtWidgets.QFrame()
        card.setObjectName("Card")
        v = QtWidgets.QVBoxLayout(card)
        margin = self._scale_px(14, 8)
        v.setContentsMargins(margin, margin, margin, margin)
        v.setSpacing(self._scale_px(10, 6))
        title_label = None
        if title_text:
            title_label = QtWidgets.QLabel(title_text)
            title_label.setProperty("class", "Title")
            if title_key:
                title_label.setProperty("i18n_key", title_key)
            v.addWidget(title_label)
            line = QtWidgets.QFrame()
            shape_enum = getattr(QtWidgets.QFrame, 'Shape', QtWidgets.QFrame)
            line.setFrameShape(getattr(shape_enum, 'HLine'))
            line.setStyleSheet("color:#E5EAF0")
            v.addWidget(line)
        return card, v, title_label

    def _folder_card(self) -> QtWidgets.QFrame:
        card, v, self.title_folder = self._card("📁 文件夹设置", "card_folder_settings")
        
        # source
        self.src_edit, self.btn_choose_src, self.lbl_src = self._path_row(v, "源文件夹", self._choose_source)
        # target
        self.tgt_edit, self.btn_choose_tgt, self.lbl_tgt = self._path_row(v, "目标文件夹", self._choose_target)
        # backup
        self.bak_edit, self.btn_choose_bak, self.lbl_bak = self._path_row(v, "备份文件夹", self._choose_backup)
        
        # v2.1.1 新增：启用备份复选框
        self.cb_enable_backup = QtWidgets.QCheckBox(" 启用备份功能")
        self.cb_enable_backup.setProperty('orig_text', " 启用备份功能")
        self.cb_enable_backup.setChecked(True)
        self.cb_enable_backup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_enable_backup, checked))
        self.cb_enable_backup.toggled.connect(self._on_backup_toggled)
        self._set_checkbox_mark(self.cb_enable_backup, self.cb_enable_backup.isChecked())
        v.addWidget(self.cb_enable_backup)
        
        # 添加说明文本
        self.backup_hint = QtWidgets.QLabel(t('backup_hint'))
        self.backup_hint.setWordWrap(True)
        self.backup_hint.setStyleSheet(
            f"color: #666; font-size: {self._font_pt(10, 8)}pt; padding: {self._scale_px(5, 3)}px 0;"
        )
        v.addWidget(self.backup_hint)
        
        card.setFixedHeight(self._scale_px(260, 210, 260))
        
        return card

    def _path_row(self, layout: QtWidgets.QVBoxLayout, label: str, chooser):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(self._scale_px(10, 6))
        lab = QtWidgets.QLabel(label + ":")
        lab.setMinimumWidth(self._scale_px(90, 68))
        edit = QtWidgets.QLineEdit()
        edit.setMinimumHeight(self._scale_px(32, 26))
        # v2.2.0 修复：设置路径输入框的文本对齐方式，避免长路径被截断显示
        edit.setCursorPosition(0)  # 默认显示路径开头
        btn = QtWidgets.QPushButton("浏览")
        btn.setProperty("class", "Secondary")
        btn.setMinimumWidth(self._scale_px(80, 58))
        btn.setMinimumHeight(self._scale_px(32, 26))
        btn.clicked.connect(chooser)
        row.addWidget(lab)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        layout.addLayout(row)
        # v2.2.0 修复：为输入框设置工具提示，显示完整路径
        edit.textChanged.connect(lambda text: edit.setToolTip(text) if text else None)
        # v3.3.0：路径手动编辑标记配置修改
        edit.textChanged.connect(lambda _: self._mark_config_modified())
        return edit, btn, lab  # v3.0.2: 返回标签引用用于多语言

    def _settings_card(self) -> QtWidgets.QFrame:
        card, v, self.title_settings = self._card("⚙️ 上传设置", "card_upload_settings")
        
        # v3.0.0 修复：将设置内容放入滚动区域，防止可折叠组件展开时影响其他卡片大小
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 关键：设置尺寸策略，防止滚动区域随内容扩展
        scroll_area.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        # 设置滚动区域的最小高度，防止被压缩得太小
        scroll_area.setMinimumHeight(self._scale_px(200, 150))
        
        # 创建滚动内容容器
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, self._scale_px(8, 5), 0)
        scroll_layout.setSpacing(self._scale_px(10, 6))
        
        # 将后续所有内容添加到 scroll_layout 而不是 v
        # ========== v2.0 新增：协议选择 ==========
        self.protocol_title_label = QtWidgets.QLabel(t('upload_protocol_title'))
        self.protocol_title_label.setStyleSheet("color:#1976D2; font-size:11px; font-weight:700;")
        scroll_layout.addWidget(self.protocol_title_label)
        
        # 协议选择下拉框
        protocol_row = QtWidgets.QHBoxLayout()
        self.protocol_type_label = QtWidgets.QLabel(t('protocol_type_label'))
        self.combo_protocol = QtWidgets.QComboBox()
        self.combo_protocol.addItems([
            t('protocol_option_smb'),
            t('protocol_option_ftp_client'),
            t('protocol_option_both')
        ])
        self.combo_protocol.currentIndexChanged.connect(self._on_protocol_changed)
        protocol_row.addWidget(self.protocol_type_label)
        protocol_row.addWidget(self.combo_protocol, 1)
        scroll_layout.addLayout(protocol_row)
        
        # 协议说明
        self.protocol_desc = QtWidgets.QLabel()
        self.protocol_desc.setWordWrap(True)
        self.protocol_desc.setStyleSheet("color: #6B7280; padding: 8px; background: #F3F4F6; border-radius: 6px; font-size: 10px;")
        scroll_layout.addWidget(self.protocol_desc)
        self._update_protocol_description(0)
        
        # v3.1.0 新增：FTP 服务器独立开关（默认SMB模式下禁用）
        ftp_server_switch_row = QtWidgets.QHBoxLayout()
        self.cb_enable_ftp_server = QtWidgets.QCheckBox(t('enable_ftp_server'))
        self.cb_enable_ftp_server.setChecked(False)
        self.cb_enable_ftp_server.setEnabled(False)  # 默认SMB模式下禁用
        self.cb_enable_ftp_server.toggled.connect(self._on_ftp_server_toggled)
        ftp_server_switch_row.addWidget(self.cb_enable_ftp_server)
        ftp_server_switch_row.addStretch()
        scroll_layout.addLayout(ftp_server_switch_row)
        
        # FTP 服务器提示
        self.ftp_server_hint = QtWidgets.QLabel(t('ftp_server_hint'))
        self.ftp_server_hint.setWordWrap(True)
        self.ftp_server_hint.setStyleSheet("color: #9CA3AF; font-size: 9px; padding-left: 20px;")
        self.ftp_server_hint.setVisible(False)
        scroll_layout.addWidget(self.ftp_server_hint)
        
        # FTP 配置容器（v3.1.0: 始终可见但根据模式启用/禁用，避免布局跳动）
        self.ftp_config_widget = QtWidgets.QWidget()
        self.ftp_config_widget.setVisible(True)  # 始终可见
        self.ftp_config_widget.setEnabled(False)  # 默认SMB模式下禁用
        ftp_layout = QtWidgets.QVBoxLayout(self.ftp_config_widget)
        ftp_layout.setContentsMargins(0, self._scale_px(8, 5), 0, 0)
        ftp_layout.setSpacing(self._scale_px(10, 6))
        
        # ========== FTP 服务器配置 - 可折叠 ==========
        self.ftp_server_collapsible = CollapsibleBox(t('ftp_server_config'), self)
        server_layout = QtWidgets.QFormLayout()
        server_layout.setSpacing(self._scale_px(8, 5))
        server_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ftp_server_host = QtWidgets.QLineEdit("0.0.0.0")
        self.ftp_server_host.setToolTip(t('listen_address_tooltip'))
        server_layout.addRow(t('listen_address'), self.ftp_server_host)
        
        self.ftp_server_port = QtWidgets.QSpinBox()
        self.ftp_server_port.setRange(1, 65535)
        self.ftp_server_port.setValue(2121)
        self.ftp_server_port.setToolTip(t('port_tooltip'))
        server_layout.addRow(t('port_label'), self.ftp_server_port)
        
        self.ftp_server_user = QtWidgets.QLineEdit("upload_user")
        self.ftp_server_user.setToolTip(t('username_tooltip'))
        server_layout.addRow(t('username_label'), self.ftp_server_user)
        
        # v3.1.0: 密码输入框带可见性切换按钮
        server_pass_row = QtWidgets.QHBoxLayout()
        self.ftp_server_pass = QtWidgets.QLineEdit("upload_pass")
        self.ftp_server_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ftp_server_pass.setToolTip(t('password_tooltip'))
        self.btn_toggle_server_pass = QtWidgets.QToolButton()
        self.btn_toggle_server_pass.setText("👁")
        self.btn_toggle_server_pass.setToolTip(t('show_password'))
        self.btn_toggle_server_pass.setCheckable(True)
        self.btn_toggle_server_pass.setStyleSheet(
            f"QToolButton {{ border: none; font-size: {self._font_pt(14, 10)}px; padding: {self._scale_px(2, 1)}px; }}"
        )
        self.btn_toggle_server_pass.toggled.connect(lambda checked: self._toggle_password_visibility(
            self.ftp_server_pass, self.btn_toggle_server_pass, checked))
        server_pass_row.addWidget(self.ftp_server_pass, 1)
        server_pass_row.addWidget(self.btn_toggle_server_pass)
        server_layout.addRow(t('password_label'), server_pass_row)
        
        # 共享目录选择
        share_row = QtWidgets.QHBoxLayout()
        self.ftp_server_share = QtWidgets.QLineEdit()
        self.ftp_server_share.setPlaceholderText(t('select_ftp_share'))
        self.ftp_server_share.setToolTip(t('share_dir_tooltip'))
        self.btn_choose_ftp_share = QtWidgets.QPushButton(t('browse'))
        self.btn_choose_ftp_share.setProperty("class", "Secondary")
        self.btn_choose_ftp_share.clicked.connect(self._choose_ftp_share)
        share_row.addWidget(self.ftp_server_share, 1)
        share_row.addWidget(self.btn_choose_ftp_share)
        server_layout.addRow(t('share_directory'), share_row)
        
        # v2.0 新增：高级选项 - 被动模式
        self.cb_server_passive = QtWidgets.QCheckBox(t('enable_passive'))
        self.cb_server_passive.setChecked(True)
        self.cb_server_passive.setToolTip(t('passive_mode_tooltip'))
        server_layout.addRow("", self.cb_server_passive)
        
        # 被动端口范围
        passive_row = QtWidgets.QHBoxLayout()
        self.ftp_server_passive_start = QtWidgets.QSpinBox()
        self.ftp_server_passive_start.setRange(1024, 65535)
        self.ftp_server_passive_start.setValue(60000)
        self.ftp_server_passive_start.setPrefix(t('port_start') + " ")
        passive_row.addWidget(self.ftp_server_passive_start)
        
        self.ftp_server_passive_end = QtWidgets.QSpinBox()
        self.ftp_server_passive_end.setRange(1024, 65535)
        self.ftp_server_passive_end.setValue(65535)
        self.ftp_server_passive_end.setPrefix(t('port_end') + " ")
        passive_row.addWidget(self.ftp_server_passive_end)
        passive_row.addStretch()
        server_layout.addRow("  " + t('port_range'), passive_row)
        
        # v2.0 新增：TLS/SSL选项
        self.cb_server_tls = QtWidgets.QCheckBox(t('enable_tls'))
        self.cb_server_tls.setChecked(False)
        self.cb_server_tls.setToolTip(t('enable_tls_tooltip'))
        server_layout.addRow("", self.cb_server_tls)
        
        # v2.0 新增：连接数限制
        conn_row = QtWidgets.QHBoxLayout()
        self.conn_label = QtWidgets.QLabel(t('max_connections'))
        self.ftp_server_max_conn = QtWidgets.QSpinBox()
        self.ftp_server_max_conn.setRange(1, 1000)
        self.ftp_server_max_conn.setValue(256)
        self.ftp_server_max_conn.setSuffix(" " + t('unit_connections'))
        conn_row.addWidget(self.conn_label)
        conn_row.addWidget(self.ftp_server_max_conn)
        
        self.ip_label = QtWidgets.QLabel("  " + t('per_ip_limit'))
        self.ftp_server_max_conn_per_ip = QtWidgets.QSpinBox()
        self.ftp_server_max_conn_per_ip.setRange(1, 100)
        self.ftp_server_max_conn_per_ip.setValue(5)
        self.ftp_server_max_conn_per_ip.setSuffix(" " + t('unit_connections'))
        conn_row.addWidget(self.ip_label)
        conn_row.addWidget(self.ftp_server_max_conn_per_ip)
        conn_row.addStretch()
        server_layout.addRow(t('connection_limit'), conn_row)
        
        # v2.0 新增：FTP服务器测试按钮
        self.btn_test_ftp_server = QtWidgets.QPushButton(t('test_config'))
        self.btn_test_ftp_server.setProperty("class", "Secondary")
        self.btn_test_ftp_server.clicked.connect(self._test_ftp_server_config)
        server_layout.addRow("", self.btn_test_ftp_server)

        self.btn_toggle_ftp_server = QtWidgets.QPushButton(t('start_ftp_server'))
        self.btn_toggle_ftp_server.setProperty("class", "Primary")
        self.btn_toggle_ftp_server.clicked.connect(self._toggle_ftp_server_only)
        server_layout.addRow("", self.btn_toggle_ftp_server)
        
        self.ftp_server_collapsible.setContentLayout(server_layout)
        ftp_layout.addWidget(self.ftp_server_collapsible)
        
        # ========== FTP 客户端配置 - 可折叠 ==========
        self.ftp_client_collapsible = CollapsibleBox(t('ftp_client_config'), self)
        client_layout = QtWidgets.QFormLayout()
        client_layout.setSpacing(self._scale_px(8, 5))
        client_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ftp_client_host = QtWidgets.QLineEdit()
        self.ftp_client_host.setPlaceholderText("ftp.example.com")
        self.ftp_client_host.setToolTip(t('server_address_tooltip'))
        client_layout.addRow(t('server_label'), self.ftp_client_host)
        
        self.ftp_client_port = QtWidgets.QSpinBox()
        self.ftp_client_port.setRange(1, 65535)
        self.ftp_client_port.setValue(21)
        self.ftp_client_port.setToolTip(t('client_port_tooltip'))
        client_layout.addRow(t('port_label'), self.ftp_client_port)
        
        self.ftp_client_user = QtWidgets.QLineEdit()
        self.ftp_client_user.setPlaceholderText(t('username_placeholder'))
        self.ftp_client_user.setToolTip(t('client_username_tooltip'))
        client_layout.addRow(t('username_label'), self.ftp_client_user)
        
        # v3.1.0: 密码输入框带可见性切换按钮
        client_pass_row = QtWidgets.QHBoxLayout()
        self.ftp_client_pass = QtWidgets.QLineEdit()
        self.ftp_client_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ftp_client_pass.setPlaceholderText(t('password_placeholder'))
        self.ftp_client_pass.setToolTip(t('client_password_tooltip'))
        self.btn_toggle_client_pass = QtWidgets.QToolButton()
        self.btn_toggle_client_pass.setText("👁")
        self.btn_toggle_client_pass.setToolTip(t('show_password'))
        self.btn_toggle_client_pass.setCheckable(True)
        self.btn_toggle_client_pass.setStyleSheet(
            f"QToolButton {{ border: none; font-size: {self._font_pt(14, 10)}px; padding: {self._scale_px(2, 1)}px; }}"
        )
        self.btn_toggle_client_pass.toggled.connect(lambda checked: self._toggle_password_visibility(
            self.ftp_client_pass, self.btn_toggle_client_pass, checked))
        client_pass_row.addWidget(self.ftp_client_pass, 1)
        client_pass_row.addWidget(self.btn_toggle_client_pass)
        client_layout.addRow(t('password_label'), client_pass_row)
        
        self.ftp_client_remote = QtWidgets.QLineEdit("/upload")
        self.ftp_client_remote.setToolTip(t('remote_path_tooltip'))
        client_layout.addRow(t('remote_path'), self.ftp_client_remote)
        
        # v2.0 新增：超时和重试配置
        timeout_row = QtWidgets.QHBoxLayout()
        self.ftp_client_timeout = QtWidgets.QSpinBox()
        self.ftp_client_timeout.setRange(10, 300)
        self.ftp_client_timeout.setValue(30)
        self.ftp_client_timeout.setSuffix(" " + t('seconds'))
        self.ftp_client_timeout.setToolTip(t('timeout_tooltip'))
        timeout_row.addWidget(self.ftp_client_timeout)
        timeout_row.addStretch()
        client_layout.addRow(t('timeout_label'), timeout_row)
        
        retry_row = QtWidgets.QHBoxLayout()
        self.ftp_client_retry = QtWidgets.QSpinBox()
        self.ftp_client_retry.setRange(0, 10)
        self.ftp_client_retry.setValue(3)
        self.ftp_client_retry.setSuffix(" " + t('unit_times'))
        self.ftp_client_retry.setToolTip(t('retry_tooltip'))
        retry_row.addWidget(self.ftp_client_retry)
        retry_row.addStretch()
        client_layout.addRow(t('ftp_retry_label'), retry_row)
        
        # v2.0 新增：高级选项 - 被动模式
        self.cb_client_passive = QtWidgets.QCheckBox(t('use_passive_mode'))
        self.cb_client_passive.setChecked(True)
        self.cb_client_passive.setToolTip(t('passive_mode_tooltip'))
        client_layout.addRow("", self.cb_client_passive)
        
        # v2.0 新增：TLS/SSL选项
        self.cb_client_tls = QtWidgets.QCheckBox(t('enable_tls'))
        self.cb_client_tls.setChecked(False)
        self.cb_client_tls.setToolTip(t('client_tls_tooltip'))
        client_layout.addRow("", self.cb_client_tls)
        
        # v2.0 新增：FTP客户端测试按钮
        self.btn_test_ftp_client = QtWidgets.QPushButton(t('test_connection'))
        self.btn_test_ftp_client.setProperty("class", "Secondary")
        self.btn_test_ftp_client.clicked.connect(self._test_ftp_client_connection)
        client_layout.addRow("", self.btn_test_ftp_client)
        
        self.ftp_client_collapsible.setContentLayout(client_layout)
        ftp_layout.addWidget(self.ftp_client_collapsible)
        
        scroll_layout.addWidget(self.ftp_config_widget)
        
        scroll_layout.addWidget(self._hline())
        # ========== v2.0 协议选择结束 ==========
        
        # interval - v3.0.2: 解包返回值保存标签引用用于多语言
        self.spin_interval, self.lbl_interval = self._spin_row(scroll_layout, t("interval_label"), 10, 3600, 30)
        self.spin_disk, self.lbl_disk = self._spin_row(scroll_layout, t("disk_threshold_label"), 5, 50, 10)
        self.spin_retry, self.lbl_retry = self._spin_row(scroll_layout, t("retry_label"), 0, 10, 3)
        self.spin_disk_check, self.lbl_disk_check = self._spin_row(scroll_layout, t("disk_check_label"), 1, 60, 5)
        # 绑定磁盘检查间隔变化事件
        self.spin_disk_check.valueChanged.connect(lambda val: setattr(self, 'disk_check_interval', val))
        # v3.3.0：spin 变更标记配置修改
        self.spin_interval.valueChanged.connect(lambda _: self._mark_config_modified())
        self.spin_disk.valueChanged.connect(lambda _: self._mark_config_modified())
        self.spin_retry.valueChanged.connect(lambda _: self._mark_config_modified())
        self.spin_disk_check.valueChanged.connect(lambda _: self._mark_config_modified())
        
        # ========== 文件类型限制 - 可折叠 ==========
        self.filter_collapsible = CollapsibleBox(t('file_filter_title'), self)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(10)
        self.cb_ext = {}
        exts = [
            ("JPG", ".jpg"), ("PNG", ".png"), ("BMP", ".bmp"), ("GIF", ".gif"), ("RAW", ".raw")
        ]
        for i, (name, ext) in enumerate(exts):
            cb = QtWidgets.QCheckBox(name)
            # store original text so we can add a visible ✓ fallback if styling fails
            cb.setProperty('orig_text', name)
            cb.setChecked(True)
            # connect toggled to update visible text marker (robust fallback)
            cb.toggled.connect(lambda checked, cb=cb: self._set_checkbox_mark(cb, checked))
            cb.toggled.connect(lambda _: self._mark_config_modified())
            # initialize text with marker if checked
            self._set_checkbox_mark(cb, cb.isChecked())
            self.cb_ext[ext] = cb
            grid.addWidget(cb, i//3, i%3)
        self.filter_collapsible.addLayout(grid)
        scroll_layout.addWidget(self.filter_collapsible)
        
        # ========== 高级选项 - 可折叠 ==========
        self.adv_collapsible = CollapsibleBox(t('advanced_options_title'), self)
        
        self.cb_auto_start_windows = QtWidgets.QCheckBox(t('auto_start_windows'))
        self.cb_auto_start_windows.setProperty('orig_text', t('auto_start_windows'))
        self.cb_auto_start_windows.setChecked(False)
        self.cb_auto_start_windows.toggled.connect(self._toggle_autostart)
        self.cb_auto_start_windows.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_start_windows, checked))
        self._set_checkbox_mark(self.cb_auto_start_windows, self.cb_auto_start_windows.isChecked())
        self.adv_collapsible.addWidget(self.cb_auto_start_windows)
        
        self.cb_auto_run_on_startup = QtWidgets.QCheckBox(t('auto_run_on_startup'))
        self.cb_auto_run_on_startup.setProperty('orig_text', t('auto_run_on_startup'))
        self.cb_auto_run_on_startup.setChecked(False)
        self.cb_auto_run_on_startup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_run_on_startup, checked))
        self._set_checkbox_mark(self.cb_auto_run_on_startup, self.cb_auto_run_on_startup.isChecked())
        self.adv_collapsible.addWidget(self.cb_auto_run_on_startup)
        
        # v2.2.0 新增：托盘通知开关
        self.cb_show_notifications = QtWidgets.QCheckBox(t('show_notifications'))
        self.cb_show_notifications.setProperty('orig_text', t('show_notifications'))
        self.cb_show_notifications.setChecked(True)
        self.cb_show_notifications.toggled.connect(lambda checked: setattr(self, 'show_notifications', checked))
        self.cb_show_notifications.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_show_notifications, checked))
        self._set_checkbox_mark(self.cb_show_notifications, self.cb_show_notifications.isChecked())
        self.adv_collapsible.addWidget(self.cb_show_notifications)
        
        # v2.3.0 新增：速率限制
        rate_row = QtWidgets.QHBoxLayout()
        self.cb_limit_rate = QtWidgets.QCheckBox(t('limit_upload_rate'))
        self.cb_limit_rate.setProperty('orig_text', t('limit_upload_rate'))
        self.cb_limit_rate.setToolTip(t('limit_rate_tooltip'))
        self.cb_limit_rate.setChecked(False)
        self.cb_limit_rate.toggled.connect(self._on_rate_limit_toggled)
        self.cb_limit_rate.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_limit_rate, checked))
        self._set_checkbox_mark(self.cb_limit_rate, self.cb_limit_rate.isChecked())
        
        self.spin_max_rate = QtWidgets.QDoubleSpinBox()
        self.spin_max_rate.setRange(0.1, 1000.0)
        self.spin_max_rate.setValue(10.0)
        self.spin_max_rate.setSuffix(" MB/s")
        self.spin_max_rate.setSingleStep(0.5)
        self.spin_max_rate.setEnabled(False)
        self.spin_max_rate.setToolTip(t('max_rate_tooltip'))
        self.spin_max_rate.valueChanged.connect(lambda: setattr(self, 'config_modified', True))
        
        rate_row.addWidget(self.cb_limit_rate)
        rate_row.addWidget(self.spin_max_rate)
        rate_row.addStretch()
        self.adv_collapsible.addLayout(rate_row)
        
        # 添加分隔线
        self.adv_collapsible.addWidget(self._hline())
        
        # 去重功能
        self.cb_dedup_enable = QtWidgets.QCheckBox(t('enable_dedup'))
        self.cb_dedup_enable.setProperty('orig_text', t('enable_dedup'))
        self.cb_dedup_enable.setChecked(False)
        self.cb_dedup_enable.toggled.connect(self._on_dedup_toggled)
        self.cb_dedup_enable.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_dedup_enable, checked))
        self._set_checkbox_mark(self.cb_dedup_enable, self.cb_dedup_enable.isChecked())
        self.adv_collapsible.addWidget(self.cb_dedup_enable)
        
        # 哈希算法选择
        hash_row = QtWidgets.QHBoxLayout()
        self.hash_lab = QtWidgets.QLabel(t('hash_algorithm') + ":")
        self.combo_hash = QtWidgets.QComboBox()
        self.combo_hash.addItems(["MD5", "SHA256"])
        self.combo_hash.setEnabled(False)
        hash_row.addWidget(self.hash_lab)
        hash_row.addWidget(self.combo_hash)
        self.adv_collapsible.addLayout(hash_row)
        
        # 去重策略选择
        strategy_row = QtWidgets.QHBoxLayout()
        self.strategy_lab = QtWidgets.QLabel(t('duplicate_strategy') + ":")
        self.combo_strategy = QtWidgets.QComboBox()
        self.combo_strategy.addItems([t('strategy_skip'), t('strategy_rename'), t('strategy_overwrite'), t('strategy_ask')])
        self.combo_strategy.setEnabled(False)
        strategy_row.addWidget(self.strategy_lab)
        strategy_row.addWidget(self.combo_strategy)
        self.adv_collapsible.addLayout(strategy_row)
        
        # 说明文本
        self.dedup_hint = QtWidgets.QLabel(t('dedup_hint'))
        self.dedup_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        self.dedup_hint.setWordWrap(True)
        self.adv_collapsible.addWidget(self.dedup_hint)
        
        # 添加分隔线
        self.adv_collapsible.addWidget(self._hline())
        
        # 网络监控选项
        self.network_sub_lab = QtWidgets.QLabel(t('network_monitor'))
        self.network_sub_lab.setStyleSheet("color:#666; font-size:10px; font-weight:700;")
        self.adv_collapsible.addWidget(self.network_sub_lab)
        
        # 网络检测间隔 - 压缩布局
        network_check_row = QtWidgets.QHBoxLayout()
        self.network_check_lab = QtWidgets.QLabel(t('check_interval_label'))
        self.spin_network_check = QtWidgets.QSpinBox()
        self.spin_network_check.setRange(5, 60)
        self.spin_network_check.setValue(10)
        self.spin_network_check.setSuffix(" " + t('seconds'))
        network_check_row.addWidget(self.network_check_lab)
        network_check_row.addWidget(self.spin_network_check)
        network_check_row.addStretch()
        self.adv_collapsible.addLayout(network_check_row)
        
        self.cb_network_auto_pause = QtWidgets.QCheckBox(t('auto_pause_on_disconnect'))
        self.cb_network_auto_pause.setProperty('orig_text', t('auto_pause_on_disconnect'))
        self.cb_network_auto_pause.setChecked(True)
        self.cb_network_auto_pause.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_pause, checked))
        self.cb_network_auto_pause.toggled.connect(lambda _: self._mark_config_modified())
        self._set_checkbox_mark(self.cb_network_auto_pause, self.cb_network_auto_pause.isChecked())
        self.adv_collapsible.addWidget(self.cb_network_auto_pause)
        
        self.cb_network_auto_resume = QtWidgets.QCheckBox(t('auto_resume_on_reconnect'))
        self.cb_network_auto_resume.setProperty('orig_text', t('auto_resume_on_reconnect'))
        self.cb_network_auto_resume.setChecked(True)
        self.cb_network_auto_resume.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_resume, checked))
        self.cb_network_auto_resume.toggled.connect(lambda _: self._mark_config_modified())
        self._set_checkbox_mark(self.cb_network_auto_resume, self.cb_network_auto_resume.isChecked())
        self.adv_collapsible.addWidget(self.cb_network_auto_resume)
        
        # 说明文本
        self.network_hint = QtWidgets.QLabel(t('network_hint'))
        self.network_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        self.network_hint.setWordWrap(True)
        self.adv_collapsible.addWidget(self.network_hint)
        
        scroll_layout.addWidget(self.adv_collapsible)
        
        # 添加弹性空间，使内容紧凑排列
        scroll_layout.addStretch()
        
        # 设置滚动区域
        scroll_area.setWidget(scroll_content)
        v.addWidget(scroll_area, 1)  # stretch=1 让滚动区域填满剩余空间
        
        return card

    def _spin_row(self, layout: QtWidgets.QVBoxLayout, label: str, low: int, high: int, val: int):
        """创建带标签的数值输入行，返回 (QSpinBox, QLabel) 用于多语言支持"""
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label + ":")
        sp = QtWidgets.QSpinBox()
        sp.setRange(low, high)
        sp.setValue(val)
        row.addWidget(lab)
        row.addWidget(sp)
        layout.addLayout(row)
        return sp, lab  # v3.0.2: 返回标签用于多语言

    def _control_card(self) -> QtWidgets.QFrame:
        card, v, self.title_control = self._card("🎮 操作控制", "card_control")
        
        # primary start - 优化按钮尺寸
        self.btn_start = QtWidgets.QPushButton("▶ 开始上传")
        self.btn_start.setProperty("class", "Primary")
        self.btn_start.setMinimumHeight(self._scale_px(35, 30))
        self.btn_start.clicked.connect(self._on_start)
        v.addWidget(self.btn_start)
        # secondary pause/stop
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(self._scale_px(12, 8))
        self.btn_pause = QtWidgets.QPushButton("⏸ 暂停上传")
        self.btn_pause.setProperty("class", "Warning")
        self.btn_pause.setMinimumHeight(self._scale_px(35, 30))
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_resume)
        self.btn_stop = QtWidgets.QPushButton("⏹ 停止上传")
        self.btn_stop.setProperty("class", "Danger")
        self.btn_stop.setMinimumHeight(self._scale_px(35, 30))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        row.addWidget(self.btn_pause)
        row.addWidget(self.btn_stop)
        v.addLayout(row)
        # separator
        v.addWidget(self._hline())
        # save + more
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(self._scale_px(12, 8))
        self.btn_save = QtWidgets.QPushButton("💾 保存配置")
        self.btn_save.setProperty("class", "Secondary")
        self.btn_save.setMinimumHeight(self._scale_px(30, 28))
        self.btn_save.clicked.connect(self._save_config)
        self.btn_more = QtWidgets.QToolButton()
        self.btn_more.setText("更多 ▾")
        self.btn_more.setMinimumHeight(self._scale_px(30, 28))
        popup_enum = getattr(QtWidgets.QToolButton, 'ToolButtonPopupMode', QtWidgets.QToolButton)
        self.btn_more.setPopupMode(getattr(popup_enum, 'InstantPopup'))
        menu = QtWidgets.QMenu(self)
        act_clear = menu.addAction("🗑️ 清空日志")
        act_clear.triggered.connect(self._clear_logs)
        menu.addSeparator()
        act_disk_cleanup = menu.addAction("💿 磁盘清理")
        act_disk_cleanup.triggered.connect(self._show_disk_cleanup)
        menu.addSeparator()
        
        # v3.0.2 新增：语言切换子菜单
        lang_menu = menu.addMenu("🌐 语言 / Language")
        self.act_lang_zh = lang_menu.addAction("简体中文")
        self.act_lang_zh.setCheckable(True)
        self.act_lang_zh.triggered.connect(lambda: self._switch_language('zh_CN'))
        self.act_lang_en = lang_menu.addAction("English")
        self.act_lang_en.setCheckable(True)
        self.act_lang_en.triggered.connect(lambda: self._switch_language('en_US'))
        # 默认选中中文
        self.act_lang_zh.setChecked(True)
        
        menu.addSeparator()
        act_login = menu.addAction("🔐 权限登录")
        act_login.triggered.connect(self._show_login)
        act_change_pwd = menu.addAction("🔑 修改密码")
        act_change_pwd.triggered.connect(self._show_change_password)
        menu.addSeparator()
        act_logout = menu.addAction("🚪 退出登录")
        act_logout.triggered.connect(self._logout)
        self.btn_more.setMenu(menu)
        
        # 保存菜单项引用用于多语言更新
        self.menu_items = {
            'clear_logs': act_clear,
            'disk_cleanup': act_disk_cleanup,
            'login': act_login,
            'change_password': act_change_pwd,
            'logout': act_logout,
            'lang_menu': lang_menu,
        }
        
        row2.addWidget(self.btn_save)
        row2.addWidget(self.btn_more)
        v.addLayout(row2)
        
        card.setFixedHeight(self._scale_px(260, 210, 260))
        
        return card

    def _switch_language(self, lang: str):
        """切换语言并刷新 UI"""
        try:
            from src.core.i18n import set_language, get_language, LANG_ZH_CN, LANG_EN_US
            
            if lang == get_language():
                return
            
            set_language(lang)
            
            # 更新菜单选中状态
            self.act_lang_zh.setChecked(lang == LANG_ZH_CN)
            self.act_lang_en.setChecked(lang == LANG_EN_US)
            
            # 刷新所有 UI 文本
            self._refresh_ui_texts()
            
            # 显示提示
            if lang == LANG_ZH_CN:
                self._toast('语言已切换为简体中文', 'success')
                self._append_log('🌐 语言已切换为简体中文')
            else:
                self._toast('Language changed to English', 'success')
                self._append_log('🌐 Language changed to English')
            
            # 保存语言设置到配置
            self.config_modified = True
            
        except Exception as e:
            self._append_log(f'⚠ 语言切换失败: {e}')

    def _refresh_ui_texts(self):
        """刷新所有 UI 文本（用于语言切换）"""
        try:
            from src.core.i18n import t
            
            # === 卡片标题 ===
            if hasattr(self, 'title_folder') and self.title_folder:
                self.title_folder.setText(t('card_folder_settings'))
            if hasattr(self, 'title_settings') and self.title_settings:
                self.title_settings.setText(t('card_upload_settings'))
            if hasattr(self, 'title_control') and self.title_control:
                self.title_control.setText(t('card_control'))
            if hasattr(self, 'title_status') and self.title_status:
                self.title_status.setText(t('card_status'))
            if hasattr(self, 'title_log') and self.title_log:
                self.title_log.setText(t('card_log'))
            
            # === 按钮 ===
            if not self.is_running:
                self.btn_start.setText(t('start_upload'))
            if self.is_paused:
                self.btn_pause.setText(t('resume_upload'))
            else:
                self.btn_pause.setText(t('pause_upload'))
            self.btn_stop.setText(t('stop_upload'))
            self.btn_save.setText(t('save_config'))
            self.btn_more.setText(t('more'))
            
            # === 浏览按钮 ===
            self.btn_choose_src.setText(t('browse'))
            self.btn_choose_tgt.setText(t('browse'))
            self.btn_choose_bak.setText(t('browse'))
            
            # === 复选框 ===
            # 备份
            checked = self.cb_enable_backup.isChecked()
            self.cb_enable_backup.setProperty('orig_text', t('enable_backup'))
            self._set_checkbox_mark(self.cb_enable_backup, checked)
            
            # 高级选项
            if hasattr(self, 'cb_auto_start_windows'):
                checked = self.cb_auto_start_windows.isChecked()
                self.cb_auto_start_windows.setProperty('orig_text', t('auto_start_windows'))
                self._set_checkbox_mark(self.cb_auto_start_windows, checked)
            if hasattr(self, 'cb_auto_run_on_startup'):
                checked = self.cb_auto_run_on_startup.isChecked()
                self.cb_auto_run_on_startup.setProperty('orig_text', t('auto_run_on_startup'))
                self._set_checkbox_mark(self.cb_auto_run_on_startup, checked)
            if hasattr(self, 'cb_show_notifications'):
                checked = self.cb_show_notifications.isChecked()
                self.cb_show_notifications.setProperty('orig_text', t('show_notifications'))
                self._set_checkbox_mark(self.cb_show_notifications, checked)
            if hasattr(self, 'cb_limit_rate'):
                checked = self.cb_limit_rate.isChecked()
                self.cb_limit_rate.setProperty('orig_text', t('limit_upload_rate'))
                self._set_checkbox_mark(self.cb_limit_rate, checked)
            if hasattr(self, 'cb_dedup_enable'):
                checked = self.cb_dedup_enable.isChecked()
                self.cb_dedup_enable.setProperty('orig_text', t('enable_dedup'))
                self._set_checkbox_mark(self.cb_dedup_enable, checked)
            if hasattr(self, 'cb_network_auto_pause'):
                checked = self.cb_network_auto_pause.isChecked()
                self.cb_network_auto_pause.setProperty('orig_text', t('auto_pause_on_disconnect'))
                self._set_checkbox_mark(self.cb_network_auto_pause, checked)
            if hasattr(self, 'cb_network_auto_resume'):
                checked = self.cb_network_auto_resume.isChecked()
                self.cb_network_auto_resume.setProperty('orig_text', t('auto_resume_on_reconnect'))
                self._set_checkbox_mark(self.cb_network_auto_resume, checked)
            if hasattr(self, 'cb_autoscroll'):
                checked = self.cb_autoscroll.isChecked()
                self.cb_autoscroll.setText("📜 " + t('autoscroll').strip())
            
            # === 状态标签 ===
            if not self.is_running:
                self.lbl_status.setText(t('status_stopped'))
            elif self.is_paused:
                self.lbl_status.setText(t('status_paused'))
            else:
                self.lbl_status.setText(t('status_running'))
            
            # === 状态芯片 ===
            self._update_chip_label(self.lbl_uploaded, t('uploaded'))
            self._update_chip_label(self.lbl_failed, t('failed'))
            self._update_chip_label(self.lbl_skipped, t('skipped'))
            self._update_chip_label(self.lbl_rate, t('rate'))
            self._update_chip_label(self.lbl_queue, t('archive_queue'))
            self._update_chip_label(self.lbl_time, t('runtime'))
            self._update_chip_label(self.lbl_target_disk, t('target_disk'))
            self._update_chip_label(self.lbl_backup_disk, t('backup_disk'))
            self._update_chip_label(self.lbl_network, t('network_status'))
            
            # === 菜单项 ===
            if hasattr(self, 'menu_items'):
                self.menu_items['clear_logs'].setText(t('clear_logs'))
                self.menu_items['disk_cleanup'].setText(t('disk_cleanup'))
                self.menu_items['login'].setText(t('login'))
                self.menu_items['change_password'].setText(t('change_password'))
                self.menu_items['logout'].setText(t('logout'))
                self.menu_items['lang_menu'].setTitle("🌐 " + t('menu_language'))
            
            # === 角色标签 ===
            if hasattr(self, 'role_label'):
                if self.current_role == 'guest':
                    self.role_label.setText(t('role_guest'))
                elif self.current_role == 'user':
                    self.role_label.setText(t('role_user'))
                else:
                    self.role_label.setText(t('role_admin'))
            
            # === 等待提示文本 ===
            if hasattr(self, 'lbl_current_file') and not self.is_running:
                self.lbl_current_file.setText(t('waiting'))
            if hasattr(self, 'pbar_file') and not self.is_running:
                self.pbar_file.setFormat(t('waiting'))
            if hasattr(self, 'lbl_progress') and not self.is_running:
                self.lbl_progress.setText(t('waiting'))
            
            # === FTP 测试按钮 ===
            if hasattr(self, 'btn_test_ftp_server'):
                self.btn_test_ftp_server.setText(t('test_config'))
            if hasattr(self, 'btn_toggle_ftp_server'):
                self.btn_toggle_ftp_server.setText(
                    t('stop_ftp_server') if self._is_ftp_server_running() else t('start_ftp_server')
                )
            if hasattr(self, 'btn_test_ftp_client'):
                self.btn_test_ftp_client.setText(t('test_connection'))
            
            # === 可折叠区块标题 ===
            if hasattr(self, 'ftp_server_collapsible'):
                self.ftp_server_collapsible.setTitle(t('ftp_server_config'))
            if hasattr(self, 'ftp_client_collapsible'):
                self.ftp_client_collapsible.setTitle(t('ftp_client_config'))
            
            # === 路径标签 ===
            if hasattr(self, 'lbl_src'):
                self.lbl_src.setText(t('source_folder_label') + ":")
            if hasattr(self, 'lbl_tgt'):
                self.lbl_tgt.setText(t('target_folder_label') + ":")
            if hasattr(self, 'lbl_bak'):
                self.lbl_bak.setText(t('backup_folder_label') + ":")
            
            # === 备份提示 ===
            if hasattr(self, 'backup_hint'):
                self.backup_hint.setText(t('backup_hint'))
            
            # === 标题栏 ===
            if hasattr(self, 'header_title'):
                self.header_title.setText(t('header_title'))
            
            # === 协议芯片 ===
            if hasattr(self, 'lbl_protocol'):
                self._update_chip_label(self.lbl_protocol, t('protocol_chip'))
            if hasattr(self, 'lbl_ftp_server'):
                self._update_chip_label(self.lbl_ftp_server, t('ftp_server_chip'))
                # 如果未启动，更新值标签
                if hasattr(self.lbl_ftp_server, 'value_label'):
                    current_val = self.lbl_ftp_server.value_label.text()
                    if current_val in ['未启动', 'Not Started']:
                        self.lbl_ftp_server.setValue(t('not_started'))
            if hasattr(self, 'lbl_ftp_client'):
                self._update_chip_label(self.lbl_ftp_client, t('ftp_client_chip'))
                # 如果未连接，更新值标签
                if hasattr(self.lbl_ftp_client, 'value_label'):
                    current_val = self.lbl_ftp_client.value_label.text()
                    if current_val in ['未连接', 'Not Connected']:
                        self.lbl_ftp_client.setValue(t('not_connected'))
            
            # === 网络状态芯片值 ===
            if hasattr(self, 'lbl_network') and hasattr(self.lbl_network, 'value_label'):
                current_val = self.lbl_network.value_label.text()
                if current_val in ['未知', 'Unknown']:
                    self.lbl_network.setValue(t('network_unknown'))
                elif current_val in ['已连接', 'Connected']:
                    self.lbl_network.setValue(t('network_connected'))
                elif current_val in ['已断开', 'Disconnected']:
                    self.lbl_network.setValue(t('network_disconnected'))
            
            # === 当前文件标签 ===
            if hasattr(self, 'current_file_label_widget'):
                self.current_file_label_widget.setText(t('current_file_label'))
            
            # === 协议相关标签 ===
            if hasattr(self, 'protocol_title_label'):
                self.protocol_title_label.setText(t('upload_protocol_title'))
            if hasattr(self, 'protocol_type_label'):
                self.protocol_type_label.setText(t('protocol_type_label'))
            
            # === 协议下拉框选项 ===
            if hasattr(self, 'combo_protocol'):
                current_idx = self.combo_protocol.currentIndex()
                self.combo_protocol.setItemText(0, t('protocol_option_smb'))
                if self.combo_protocol.count() > 1:
                    self.combo_protocol.setItemText(1, t('protocol_option_ftp_client'))
                if self.combo_protocol.count() > 2:
                    self.combo_protocol.setItemText(2, t('protocol_option_both'))
            
            # === FTP 复选框 ===
            if hasattr(self, 'cb_server_passive'):
                self.cb_server_passive.setText(t('enable_passive'))
            if hasattr(self, 'cb_server_tls'):
                self.cb_server_tls.setText(t('enable_tls'))
            if hasattr(self, 'cb_client_passive'):
                self.cb_client_passive.setText(t('enable_passive'))
            if hasattr(self, 'cb_client_tls'):
                self.cb_client_tls.setText(t('enable_tls'))
            
            # === 数值设置行标签 ===
            if hasattr(self, 'lbl_interval'):
                self.lbl_interval.setText(t('interval_label') + ":")
            if hasattr(self, 'lbl_disk'):
                self.lbl_disk.setText(t('disk_threshold_label') + ":")
            if hasattr(self, 'lbl_retry'):
                self.lbl_retry.setText(t('retry_label') + ":")
            if hasattr(self, 'lbl_disk_check'):
                self.lbl_disk_check.setText(t('disk_check_label') + ":")
            
            # === 可折叠区块标题 ===
            if hasattr(self, 'filter_collapsible'):
                self.filter_collapsible.setTitle(t('file_filter_title'))
            if hasattr(self, 'adv_collapsible'):
                self.adv_collapsible.setTitle(t('advanced_options_title'))
            
            # === 高级选项区域标签 ===
            if hasattr(self, 'hash_lab'):
                self.hash_lab.setText(t('hash_algorithm') + ":")
            if hasattr(self, 'strategy_lab'):
                self.strategy_lab.setText(t('duplicate_strategy') + ":")
            if hasattr(self, 'network_sub_lab'):
                self.network_sub_lab.setText(t('network_monitor'))
            if hasattr(self, 'network_check_lab'):
                self.network_check_lab.setText(t('check_interval_label'))
            if hasattr(self, 'dedup_hint'):
                self.dedup_hint.setText(t('dedup_hint'))
            if hasattr(self, 'network_hint'):
                self.network_hint.setText(t('network_hint'))
            
            # === 策略下拉框选项 ===
            if hasattr(self, 'combo_strategy'):
                self.combo_strategy.setItemText(0, t('strategy_skip'))
                self.combo_strategy.setItemText(1, t('strategy_rename'))
                self.combo_strategy.setItemText(2, t('strategy_overwrite'))
                self.combo_strategy.setItemText(3, t('strategy_ask'))
            
            # === 网络检查间隔后缀 ===
            if hasattr(self, 'spin_network_check'):
                self.spin_network_check.setSuffix(" " + t('seconds'))
            
        except Exception as e:
            self._append_log(f'⚠ UI刷新失败: {e}')

    def _update_chip_label(self, chip: QtWidgets.QWidget, new_label: str):
        """更新芯片控件的标签文本（保持值不变）"""
        try:
            # ChipWidget 有 title_label 和 value_label 两部分
            if hasattr(chip, 'title_label'):
                chip.title_label.setText(new_label)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _logout(self):
        """退出登录"""
        self.current_role = 'guest'
        self.role_label.setText(t('role_guest'))
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        self._update_ui_permissions()
        self._toast(t('logged_out'), 'info')

    def _compute_control_states(self, role: str, is_running: bool, enable_backup: bool) -> dict:
        """
        统一计算所有控件的启用/禁用状态
        
        规则：
        - guest: 只能查看和登录，不能改配置或控制上传
        - user/admin: 未运行时可改配置；运行中完全不可改
        - 备份路径: 仅当"已启用备份"时可编辑
        - 运行中: 所有配置类控件禁用，无论角色
        
        Returns:
            dict: 控件名称 -> 是否启用的映射
        """
        is_user_or_admin = role in ['user', 'admin']
        is_admin = role == 'admin'
        is_guest = role == 'guest'
        can_edit_config = is_user_or_admin and not is_running
        protocol_uses_ftp = getattr(self, 'current_protocol', 'smb') != 'smb'
        ftp_server_enabled = bool(getattr(self, 'enable_ftp_server', False))
        ftp_server_running = bool(getattr(self, '_is_ftp_server_running', lambda: False)())
        can_edit_ftp_server = is_admin and not is_running and not ftp_server_running
        can_toggle_ftp_server = is_admin and (ftp_server_running or (not is_running and ftp_server_enabled))
        dedup_enabled = bool(getattr(self, 'enable_deduplication', False))
        rate_limit_enabled = bool(getattr(self, 'limit_upload_rate', False))
        
        logger.debug(f"[计算细节] role={role}, is_running={is_running}, enable_backup={enable_backup}")
        logger.debug(f"[计算细节] is_user_or_admin={is_user_or_admin}, can_edit_config={can_edit_config}")
        
        return {
            # 路径浏览按钮
            'btn_choose_src': can_edit_config,
            'btn_choose_tgt': can_edit_config,
            'btn_choose_bak': can_edit_config and enable_backup,
            # 路径输入框 (ReadOnly相反逻辑)
            'src_edit_readonly': not can_edit_config,
            'tgt_edit_readonly': not can_edit_config,
            'bak_edit_readonly': not (can_edit_config and enable_backup),
            # 协议与备份开关
            'combo_protocol': can_edit_config,
            'cb_enable_backup': can_edit_config,
            # 保存按钮
            'btn_save': can_edit_config,
            # 上传设置（间隔、磁盘、重试）
            'upload_settings': can_edit_config,
            # 文件类型复选框
            'file_filters': can_edit_config,
            # 自启动设置
            'startup_settings': can_edit_config,
            # 通知设置
            'notification_settings': can_edit_config,
            # v2.3.0 速率限制控件
            'cb_limit_rate': can_edit_config,
            'spin_max_rate': can_edit_config and rate_limit_enabled,
            # 高级配置
            'cb_dedup_enable': can_edit_config,
            'combo_hash': can_edit_config and dedup_enabled,
            'combo_strategy': can_edit_config and dedup_enabled,
            'network_settings': can_edit_config,
            'filter_collapsible': can_edit_config,
            'adv_collapsible': can_edit_config,
            # FTP 配置
            'cb_enable_ftp_server': can_edit_ftp_server,
            'ftp_config_widget': True,
            'ftp_client_collapsible': can_edit_config and protocol_uses_ftp,
            'ftp_server_collapsible': True,
            'ftp_server_controls': can_edit_ftp_server and ftp_server_enabled,
            'btn_toggle_ftp_server': can_toggle_ftp_server,
            'ftp_client_controls': can_edit_config and protocol_uses_ftp,
            # 上传控制按钮（guest 不允许操作）
            'btn_start': is_user_or_admin and not is_running,
            'btn_pause': is_user_or_admin and is_running,
            'btn_stop': is_user_or_admin and is_running,
            # 更多菜单。按钮本体保持可用，避免隐藏登录入口。
            'btn_more': True,
            'menu_clear_logs': is_user_or_admin,
            'menu_disk_cleanup': is_admin,
            'menu_login': is_guest,
            'menu_change_password': is_admin,
            'menu_logout': is_user_or_admin,
            'menu_language': is_user_or_admin,
        }

    def _can_manage_disk_cleanup(self) -> bool:
        """当前角色是否允许执行磁盘清理相关操作。"""
        return self.current_role == 'admin'

    def _get_disk_cleanup_block_reason(self) -> str:
        """获取磁盘清理被禁止时的原因。"""
        if self.current_role == 'guest':
            return '请先登录后再使用磁盘清理功能'
        if self.current_role == 'user':
            return '普通用户无权限使用磁盘清理，请切换管理员登录'
        return ''

    def _update_ui_permissions(self):
        """根据当前角色更新UI控件的启用状态"""
        logger.debug(f"更新权限: 当前角色={self.current_role}, 运行状态={'运行中' if self.is_running else '已停止'}")
        
        # 计算统一控件状态
        states = self._compute_control_states(self.current_role, self.is_running, self.enable_backup)
        
        logger.debug(f"[计算状态] 源按钮={states['btn_choose_src']}, 目标按钮={states['btn_choose_tgt']}, 备份按钮={states['btn_choose_bak']}")
        logger.debug(f"[计算状态] 源只读={states['src_edit_readonly']}, 目标只读={states['tgt_edit_readonly']}, 备份只读={states['bak_edit_readonly']}")
        
        # 路径浏览按钮
        if hasattr(self, 'btn_choose_src'):
            self.btn_choose_src.setEnabled(states['btn_choose_src'])
        if hasattr(self, 'btn_choose_tgt'):
            self.btn_choose_tgt.setEnabled(states['btn_choose_tgt'])
        if hasattr(self, 'btn_choose_bak'):
            self.btn_choose_bak.setEnabled(states['btn_choose_bak'])
        
        # 路径输入框
        self.src_edit.setReadOnly(states['src_edit_readonly'])
        self.tgt_edit.setReadOnly(states['tgt_edit_readonly'])
        self.bak_edit.setReadOnly(states['bak_edit_readonly'])

        # 备份启用复选框
        if hasattr(self, 'cb_enable_backup'):
            self.cb_enable_backup.setEnabled(states['cb_enable_backup'])

        # 设置项（运行中也允许查看但实际由Worker读取启动时的值）
        self.spin_interval.setEnabled(states['upload_settings'])
        self.spin_disk.setEnabled(states['upload_settings'])
        self.spin_retry.setEnabled(states['upload_settings'])
        self.spin_disk_check.setEnabled(states['upload_settings'])
        
        # 文件类型复选框
        for cb in self.cb_ext.values():
            cb.setEnabled(states['file_filters'])
        
        # 开机自启和自动运行复选框
        self.cb_auto_start_windows.setEnabled(states['startup_settings'])
        self.cb_auto_run_on_startup.setEnabled(states['startup_settings'])
        # v2.2.0 新增：通知开关
        if hasattr(self, 'cb_show_notifications'):
            self.cb_show_notifications.setEnabled(states['notification_settings'])
        # v2.3.0 新增：速率限制控件权限
        if hasattr(self, 'cb_limit_rate'):
            self.cb_limit_rate.setEnabled(states['cb_limit_rate'])
            self.spin_max_rate.setEnabled(states['spin_max_rate'])

        # 智能去重和网络监控
        if hasattr(self, 'cb_dedup_enable'):
            self.cb_dedup_enable.setEnabled(states['cb_dedup_enable'])
        if hasattr(self, 'combo_hash'):
            self.combo_hash.setEnabled(states['combo_hash'])
        if hasattr(self, 'combo_strategy'):
            self.combo_strategy.setEnabled(states['combo_strategy'])
        if hasattr(self, 'spin_network_check'):
            self.spin_network_check.setEnabled(states['network_settings'])
        if hasattr(self, 'cb_network_auto_pause'):
            self.cb_network_auto_pause.setEnabled(states['network_settings'])
        if hasattr(self, 'cb_network_auto_resume'):
            self.cb_network_auto_resume.setEnabled(states['network_settings'])
        if hasattr(self, 'filter_collapsible'):
            self.filter_collapsible.setEnabled(states['filter_collapsible'])
        if hasattr(self, 'adv_collapsible'):
            self.adv_collapsible.setEnabled(states['adv_collapsible'])
        
        # 保存配置按钮
        self.btn_save.setEnabled(states['btn_save'])
        
        # 协议选择框
        if hasattr(self, 'combo_protocol'):
            self.combo_protocol.setEnabled(states['combo_protocol'])
        if hasattr(self, 'cb_enable_ftp_server'):
            self.cb_enable_ftp_server.setEnabled(states['cb_enable_ftp_server'])
        if hasattr(self, 'ftp_config_widget'):
            self.ftp_config_widget.setEnabled(states['ftp_config_widget'])
        if hasattr(self, 'ftp_client_collapsible'):
            self.ftp_client_collapsible.setEnabled(states['ftp_client_collapsible'])
        if hasattr(self, 'ftp_server_collapsible'):
            self.ftp_server_collapsible.setEnabled(states['ftp_server_collapsible'])
        for name in (
            'ftp_server_host', 'ftp_server_port', 'ftp_server_user', 'ftp_server_pass',
            'ftp_server_share', 'btn_choose_ftp_share', 'cb_server_passive',
            'ftp_server_passive_start', 'ftp_server_passive_end', 'cb_server_tls',
            'ftp_server_max_conn', 'ftp_server_max_conn_per_ip', 'btn_test_ftp_server',
            'btn_toggle_server_pass',
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(states['ftp_server_controls'])
        if hasattr(self, 'btn_toggle_ftp_server'):
            self.btn_toggle_ftp_server.setEnabled(states['btn_toggle_ftp_server'])
            self.btn_toggle_ftp_server.setText(
                t('stop_ftp_server') if self._is_ftp_server_running() else t('start_ftp_server')
            )
        for name in (
            'ftp_client_host', 'ftp_client_port', 'ftp_client_user', 'ftp_client_pass',
            'btn_toggle_client_pass', 'ftp_client_remote', 'ftp_client_timeout',
            'ftp_client_retry', 'cb_client_passive', 'cb_client_tls',
            'btn_test_ftp_client',
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(states['ftp_client_controls'])
        if hasattr(self, 'menu_items'):
            self.menu_items['clear_logs'].setEnabled(states['menu_clear_logs'])
            self.menu_items['disk_cleanup'].setEnabled(states['menu_disk_cleanup'])
            self.menu_items['login'].setEnabled(states['menu_login'])
            self.menu_items['change_password'].setEnabled(states['menu_change_password'])
            self.menu_items['logout'].setEnabled(states['menu_logout'])
            self.menu_items['lang_menu'].setEnabled(states['menu_language'])
            if hasattr(self, 'act_lang_zh'):
                self.act_lang_zh.setEnabled(states['menu_language'])
            if hasattr(self, 'act_lang_en'):
                self.act_lang_en.setEnabled(states['menu_language'])
        if hasattr(self, 'btn_more'):
            self.btn_more.setEnabled(states['btn_more'])
            if self.current_role == 'guest':
                self.btn_more.setToolTip("请先登录")
            else:
                self.btn_more.setToolTip("更多操作")
        
        # 上传控制按钮
        self.btn_start.setEnabled(states['btn_start'])
        self.btn_pause.setEnabled(states['btn_pause'])
        self.btn_stop.setEnabled(states['btn_stop'])
        
        actual_src = self.btn_choose_src.isEnabled() if hasattr(self, 'btn_choose_src') else None
        actual_tgt = self.btn_choose_tgt.isEnabled() if hasattr(self, 'btn_choose_tgt') else None
        actual_bak = self.btn_choose_bak.isEnabled() if hasattr(self, 'btn_choose_bak') else None
        logger.debug(f"[应用后实际] 源按钮={actual_src}, 目标按钮={actual_tgt}, 备份按钮={actual_bak}")
        logger.debug(f"[应用后实际] 源只读={self.src_edit.isReadOnly()}, 目标只读={self.tgt_edit.isReadOnly()}, 备份只读={self.bak_edit.isReadOnly()}")
        
        if actual_tgt is not None and actual_tgt != states['btn_choose_tgt']:
            logger.warning(f"目标按钮状态不一致！计算={states['btn_choose_tgt']}, 实际={actual_tgt}")
        if actual_src is not None and actual_src != states['btn_choose_src']:
            logger.warning(f"源按钮状态不一致！计算={states['btn_choose_src']}, 实际={actual_src}")

        # 通知已打开的子窗口更新权限状态
        self._permission_changed_signal.emit()

    def _clear_logs(self):
        try:
            self.log.clear()
            self._toast('已清空日志', 'info')
        except Exception:
            pass

    def _load_user_passwords(self, cfg: dict) -> None:
        """从配置中读取角色密码哈希，并标记默认弱口令。"""
        users = cfg.get('users', {})
        if not isinstance(users, dict):
            users = {}

        user_hash = users.get('user')
        admin_hash = users.get('admin')
        if isinstance(user_hash, str) and user_hash.strip():
            self.user_password = user_hash.strip()
        else:
            self.user_password = DEFAULT_USER_PASSWORD_HASH

        if isinstance(admin_hash, str) and admin_hash.strip():
            self.admin_password = admin_hash.strip()
        else:
            self.admin_password = DEFAULT_ADMIN_PASSWORD_HASH

        weak_roles: List[str] = []
        if self.user_password == DEFAULT_USER_PASSWORD_HASH:
            weak_roles.append('用户')
        if self.admin_password == DEFAULT_ADMIN_PASSWORD_HASH:
            weak_roles.append('管理员')
        self.default_password_roles = weak_roles

    def _warn_if_default_password_in_use(self, role: str) -> None:
        """登录成功后提醒默认弱口令风险。"""
        if role == 'user' and self.user_password == DEFAULT_USER_PASSWORD_HASH:
            self._append_log("⚠️ 用户角色仍在使用默认口令，请联系管理员尽快修改。")
            self._toast('当前仍在使用默认用户口令，请联系管理员修改', 'warning')
        elif role == 'admin' and self.admin_password == DEFAULT_ADMIN_PASSWORD_HASH:
            self._append_log("⚠️ 管理员仍在使用默认口令，请立即修改密码。")
            self._toast('管理员仍在使用默认口令，请立即修改密码', 'warning')

    def _validate_new_password(self, old_password: str, new_password: str) -> str:
        """校验新密码强度，返回错误信息；空字符串表示通过。"""
        if len(new_password) < 8:
            return '新密码至少需要 8 位'
        if new_password == old_password:
            return '新密码不能与原密码相同'
        if new_password in {'123', '123456', 'password', 'upload_pass', 'Tops123'}:
            return '新密码过于简单，请使用更安全的密码'

        categories = 0
        if any(ch.islower() for ch in new_password):
            categories += 1
        if any(ch.isupper() for ch in new_password):
            categories += 1
        if any(ch.isdigit() for ch in new_password):
            categories += 1
        if any(not ch.isalnum() for ch in new_password):
            categories += 1
        if categories < 2:
            return '新密码至少包含两种字符类型'
        return ''

    def _read_ftp_password(self, section: dict, default: str = '') -> str:
        """优先读取加密密码，兼容旧版明文配置。"""
        encrypted = str(section.get('password_encrypted', '') or '').strip()
        if encrypted:
            decrypted = unprotect_secret(encrypted)
            if decrypted:
                return decrypted
        return str(section.get('password', default) or '')

    def _encrypt_ftp_password(self, password: str, label: str) -> "tuple[str, str]":
        """返回 (明文字段, 加密字段)。Windows 下强制写入加密字段。"""
        password = password.strip()
        if not password:
            return '', ''
        encrypted = protect_secret(password)
        if sys.platform == 'win32':
            if not encrypted or encrypted == password:
                raise RuntimeError(f"{label}密码加密失败")
            return '', encrypted
        return '', encrypted

    def _write_config_payload(self, cfg: dict) -> bool:
        """将配置写回磁盘，并保存错误信息。"""
        path = self.app_dir / 'config.json'
        self.last_config_save_error = ''
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.last_config_save_error = str(e)
            return False

    def _emit_async_log(self, message: str) -> None:
        """从后台线程安全地投递日志到主线程。"""
        try:
            self._async_log_signal.emit(message)
        except Exception:
            pass
    
    def _show_disk_cleanup(self):
        """显示磁盘清理对话框"""
        reason = self._get_disk_cleanup_block_reason()
        if reason:
            self._append_log(f"⚠️ 磁盘清理已阻止: {reason}")
            self._toast(reason, 'warning')
            return
        try:
            dialog = DiskCleanupDialog(self)
            dialog.exec()
        except Exception as e:
            self._append_log(f"❌ 打开磁盘清理对话框失败: {e}")
            self._toast('打开磁盘清理失败', 'danger')

    def _show_login(self):
        """显示权限登录对话框"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("🔐 权限登录")
        dialog.setModal(True)
        dialog.resize(self._clamped_dialog_size(400, 200))
        
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(self._scale_px(15, 8))
        
        # 角色选择
        role_layout = QtWidgets.QHBoxLayout()
        role_label = QtWidgets.QLabel(t('login_role_label'))
        role_label.setMinimumWidth(self._scale_px(80, 64))
        role_combo = QtWidgets.QComboBox()
        role_combo.addItems([t('role_user_option'), t('role_admin_option')])
        role_layout.addWidget(role_label)
        role_layout.addWidget(role_combo)
        layout.addLayout(role_layout)
        
        # 密码
        pwd_layout = QtWidgets.QHBoxLayout()
        pwd_label = QtWidgets.QLabel(t('password_label'))
        pwd_label.setMinimumWidth(self._scale_px(80, 64))
        pwd_input = QtWidgets.QLineEdit()
        echo_enum = getattr(QtWidgets.QLineEdit, 'EchoMode', QtWidgets.QLineEdit)
        pwd_input.setEchoMode(getattr(echo_enum, 'Password'))
        pwd_input.setPlaceholderText(t('enter_password'))
        pwd_layout.addWidget(pwd_label)
        pwd_layout.addWidget(pwd_input)
        layout.addLayout(pwd_layout)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)
        btn_cancel = QtWidgets.QPushButton(t('cancel'))
        btn_cancel.setProperty("class", "Secondary")
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok = QtWidgets.QPushButton(t('login'))
        btn_ok.setProperty("class", "Primary")
        btn_ok.setDefault(True)  # 设置为默认按钮，支持回车触发
        
        def do_login():
            role_text = role_combo.currentText()
            password = pwd_input.text().strip()
            
            if not password:
                self._toast(t('please_enter_password'), 'warning')
                return
            
            # 哈希密码
            pwd_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
            
            # 验证密码
            if t('role_user_option') in role_text or "用户" in role_text:
                if pwd_hash == self.user_password:
                    self.current_role = 'user'
                    self.role_label.setText(t('role_user'))
                    self.role_label.setStyleSheet("background:#E3F2FD; color:#1976D2; padding:6px 12px; border-radius:6px; font-weight:700;")
                    self._append_log("=" * 50)
                    self._append_log(t('user_login_success'))
                    self._toast(t('user_login_success'), 'success')
                    self._update_ui_permissions()
                    self._warn_if_default_password_in_use('user')
                    dialog.accept()
                else:
                    self._toast(t('wrong_password'), 'danger')
            elif t('role_admin_option') in role_text or "管理员" in role_text:
                if pwd_hash == self.admin_password:
                    self.current_role = 'admin'
                    self.role_label.setText(t('role_admin'))
                    self.role_label.setStyleSheet("background:#DCFCE7; color:#166534; padding:6px 12px; border-radius:6px; font-weight:700;")
                    self._append_log("=" * 50)
                    self._append_log(t('admin_login_success'))
                    self._toast(t('admin_login_success'), 'success')
                    self._update_ui_permissions()
                    self._warn_if_default_password_in_use('admin')
                    dialog.accept()
                else:
                    self._toast(t('wrong_password'), 'danger')
        
        btn_ok.clicked.connect(do_login)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        
        dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()

    def _show_change_password(self):
        """显示修改密码对话框"""
        # 检查权限 - 用户无法修改密码
        if self.current_role == 'guest':
            self._toast('请先登录', 'warning')
            return
        if self.current_role == 'user':
            self._toast('用户无权限修改密码，仅管理员可修改', 'warning')
            return
        
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("🔑 修改密码")
        dialog.setModal(True)
        dialog.resize(self._clamped_dialog_size(400, 300))
        
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(self._scale_px(15, 8))
        
        # 管理员可以选择修改哪个密码
        target_combo = None
        if self.current_role == 'admin':
            target_layout = QtWidgets.QHBoxLayout()
            target_label = QtWidgets.QLabel("修改对象:")
            target_label.setMinimumWidth(self._scale_px(80, 64))
            target_combo = QtWidgets.QComboBox()
            target_combo.addItems(["👤 用户密码", "👑 管理员密码"])
            target_layout.addWidget(target_label)
            target_layout.addWidget(target_combo)
            layout.addLayout(target_layout)
        
        # 原密码
        old_layout = QtWidgets.QHBoxLayout()
        old_label = QtWidgets.QLabel("原密码:")
        old_label.setMinimumWidth(self._scale_px(80, 64))
        old_input = QtWidgets.QLineEdit()
        echo_enum = getattr(QtWidgets.QLineEdit, 'EchoMode', QtWidgets.QLineEdit)
        old_input.setEchoMode(getattr(echo_enum, 'Password'))
        old_input.setPlaceholderText("请输入原密码")
        old_layout.addWidget(old_label)
        old_layout.addWidget(old_input)
        layout.addLayout(old_layout)
        
        # 新密码
        new_layout = QtWidgets.QHBoxLayout()
        new_label = QtWidgets.QLabel("新密码:")
        new_label.setMinimumWidth(self._scale_px(80, 64))
        new_input = QtWidgets.QLineEdit()
        echo_enum = getattr(QtWidgets.QLineEdit, 'EchoMode', QtWidgets.QLineEdit)
        new_input.setEchoMode(getattr(echo_enum, 'Password'))
        new_input.setPlaceholderText("请输入新密码")
        new_layout.addWidget(new_label)
        new_layout.addWidget(new_input)
        layout.addLayout(new_layout)
        
        # 确认密码
        confirm_layout = QtWidgets.QHBoxLayout()
        confirm_label = QtWidgets.QLabel("确认密码:")
        confirm_label.setMinimumWidth(self._scale_px(80, 64))
        confirm_input = QtWidgets.QLineEdit()
        echo_enum = getattr(QtWidgets.QLineEdit, 'EchoMode', QtWidgets.QLineEdit)
        confirm_input.setEchoMode(getattr(echo_enum, 'Password'))
        confirm_input.setPlaceholderText("请再次输入新密码")
        confirm_layout.addWidget(confirm_label)
        confirm_layout.addWidget(confirm_input)
        layout.addLayout(confirm_layout)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setProperty("class", "Secondary")
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok = QtWidgets.QPushButton("确认修改")
        btn_ok.setProperty("class", "Primary")
        
        def do_change():
            old_pwd = old_input.text().strip()
            new_pwd = new_input.text().strip()
            confirm_pwd = confirm_input.text().strip()
            
            if not old_pwd or not new_pwd or not confirm_pwd:
                self._toast('请填写所有字段', 'warning')
                return
            if new_pwd != confirm_pwd:
                self._toast('两次输入的新密码不一致', 'warning')
                return
            password_error = self._validate_new_password(old_pwd, new_pwd)
            if password_error:
                self._toast(password_error, 'warning')
                return
            
            # 哈希密码
            old_hash = hashlib.sha256(old_pwd.encode('utf-8')).hexdigest()
            new_hash = hashlib.sha256(new_pwd.encode('utf-8')).hexdigest()
            
            # 管理员修改密码
            if self.current_role == 'admin' and target_combo:
                target_text = target_combo.currentText()
                if "用户密码" in target_text:
                    # 验证管理员密码
                    if old_hash != self.admin_password:
                        self._toast('管理员密码错误', 'danger')
                        return
                    self.user_password = new_hash
                    target_role = 'user'
                    self._toast('用户密码修改成功！', 'success')
                else:
                    # 修改管理员密码
                    if old_hash != self.admin_password:
                        self._toast('原密码错误', 'danger')
                        return
                    self.admin_password = new_hash
                    target_role = 'admin'
                    self._toast('管理员密码修改成功！', 'success')
                
                # 保存到配置文件
                try:
                    path = self.app_dir / 'config.json'
                    cfg = ConfigManager(path).load()
                    users = cfg.get('users', {})
                    if not isinstance(users, dict):
                        users = {}
                    users[target_role] = new_hash
                    cfg['users'] = users
                    if not self._write_config_payload(cfg):
                        raise RuntimeError(self.last_config_save_error or '写入配置文件失败')
                    
                    self._append_log(f"✓ 密码已保存: {target_role}")
                    self.default_password_roles = [
                        role_name for role_name in self.default_password_roles
                        if role_name != ('用户' if target_role == 'user' else '管理员')
                    ]
                except Exception as e:
                    self._toast(f'保存密码失败: {e}', 'danger')
                    return
            
            dialog.accept()
        
        btn_ok.clicked.connect(do_change)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        
        dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()

    # ========== 开机自启动功能 ==========
    
    def _on_dedup_toggled(self, checked: bool):
        """切换智能去重开关"""
        self.enable_deduplication = checked
        self._mark_config_modified()
        self._update_ui_permissions()
        
        if checked:
            self._append_log("🔍 已启用智能去重")
        else:
            self._append_log("⚪ 已禁用智能去重")
    
    def _on_rate_limit_toggled(self, checked: bool):
        """v2.3.0 切换速率限制开关"""
        self.limit_upload_rate = checked
        self._mark_config_modified()
        self._update_ui_permissions()
        
        if checked:
            rate = self.spin_max_rate.value()
            self._append_log(f"⚡ 已启用速率限制: {rate} MB/s")
        else:
            self._append_log("⚪ 已禁用速率限制")

    def _toggle_password_visibility(self, line_edit: QtWidgets.QLineEdit, 
                                     button: QtWidgets.QToolButton, show: bool):
        """v3.1.0 新增: 切换密码可见性
        
        Args:
            line_edit: 密码输入框
            button: 切换按钮
            show: 是否显示密码
        """
        if show:
            line_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal)
            button.setText("🙈")
            button.setToolTip(t('hide_password'))
        else:
            line_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            button.setText("👁")
            button.setToolTip(t('show_password'))

    def _choose_ftp_share(self):
        """选择 FTP 共享目录"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择 FTP 共享目录", self.ftp_server_share.text()
        )
        if folder:
            self.ftp_server_share.setText(folder)
            self.config_modified = True

    def _collect_ftp_server_config(self) -> dict:
        return {
            'host': self.ftp_server_host.text().strip(),
            'port': self.ftp_server_port.value(),
            'username': self.ftp_server_user.text().strip(),
            'password': self.ftp_server_pass.text().strip(),
            'shared_folder': self.ftp_server_share.text().strip(),
            'enable_passive': self.cb_server_passive.isChecked(),
            'passive_ports_start': self.ftp_server_passive_start.value(),
            'passive_ports_end': self.ftp_server_passive_end.value(),
            'enable_tls': self.cb_server_tls.isChecked(),
            'max_connections': self.ftp_server_max_conn.value(),
            'max_connections_per_ip': self.ftp_server_max_conn_per_ip.value(),
        }

    def _collect_ftp_client_config(self) -> dict:
        return {
            'host': self.ftp_client_host.text().strip(),
            'port': self.ftp_client_port.value(),
            'username': self.ftp_client_user.text().strip(),
            'password': self.ftp_client_pass.text().strip(),
            'remote_path': self.ftp_client_remote.text().strip(),
            'timeout': self.ftp_client_timeout.value(),
            'retry_count': self.ftp_client_retry.value(),
            'passive_mode': self.cb_client_passive.isChecked(),
            'enable_tls': self.cb_client_tls.isChecked(),
        }

    def _build_ftp_server_manager_config(self, server_cfg: dict) -> dict:
        passive_ports = None
        if server_cfg.get('enable_passive', True):
            passive_ports = (
                server_cfg.get('passive_ports_start', 60000),
                server_cfg.get('passive_ports_end', 65535)
            )
        return {
            'host': server_cfg.get('host', '0.0.0.0'),
            'port': server_cfg.get('port', 2121),
            'username': server_cfg.get('username', 'upload_user'),
            'password': server_cfg.get('password', 'upload_pass'),
            'shared_folder': server_cfg.get('shared_folder', ''),
            'enable_tls': server_cfg.get('enable_tls', False),
            'passive_ports': passive_ports,
            'passive_ports_start': server_cfg.get('passive_ports_start', 60000),
            'passive_ports_end': server_cfg.get('passive_ports_end', 65535),
            'enable_passive': server_cfg.get('enable_passive', True),
            'max_cons': server_cfg.get('max_connections', 256),
            'max_cons_per_ip': server_cfg.get('max_connections_per_ip', 5),
        }

    def _validate_ftp_server_config_only(self, server_cfg: dict) -> List[str]:
        """只验证内置 FTP 服务器配置，不检查客户端或上传路径。"""
        errors: List[str] = []

        host = server_cfg.get('host', '').strip()
        if not host:
            errors.append("FTP服务器主机地址为空")
        elif host not in ['0.0.0.0', 'localhost', '127.0.0.1']:
            import re
            if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', host):
                errors.append(f"FTP服务器主机地址格式无效: {host}")

        port = server_cfg.get('port', 0)
        if not isinstance(port, int) or port < 1 or port > 65535:
            errors.append(f"FTP服务器端口无效: {port}（范围：1-65535）")
        elif port < 1024 and port != 21:
            self._append_log(f"⚠️  FTP服务器使用特权端口 {port}，可能需要管理员权限")

        username = server_cfg.get('username', '').strip()
        if not username:
            errors.append("FTP服务器用户名为空")
        elif len(username) < 3:
            errors.append("FTP服务器用户名至少需要3个字符")

        password = server_cfg.get('password', '').strip()
        if not password:
            errors.append("FTP服务器密码为空")
        elif len(password) < 6:
            errors.append("FTP服务器密码至少需要6个字符")

        share_folder = server_cfg.get('shared_folder', '').strip()
        if not share_folder:
            errors.append("FTP服务器共享目录为空")
        elif not os.path.exists(share_folder):
            errors.append(f"FTP服务器共享目录不存在: {share_folder}")
        elif not os.path.isdir(share_folder):
            errors.append(f"FTP服务器共享路径不是目录: {share_folder}")
        else:
            self._append_log(f"✓ FTP服务器共享目录有效: {share_folder}")

        if server_cfg.get('enable_passive', True):
            start_port = server_cfg.get('passive_ports_start', 0)
            end_port = server_cfg.get('passive_ports_end', 0)
            if not isinstance(start_port, int) or not isinstance(end_port, int):
                errors.append("FTP服务器被动端口范围无效")
            elif start_port > end_port:
                errors.append(f"FTP服务器被动端口范围无效: {start_port}-{end_port}")

        return errors
    
    def _test_ftp_server_config(self):
        """测试FTP服务器配置"""
        self._append_log("🧪 开始测试FTP服务器配置...")
        
        # 收集当前配置
        server_cfg = self._collect_ftp_server_config()
        self.ftp_server_config = copy.deepcopy(server_cfg)
        config = self._build_ftp_server_manager_config(server_cfg)
        
        # 验证配置
        errors = []
        if not server_cfg['host']:
            errors.append("主机地址为空")
        if not server_cfg['username']:
            errors.append("用户名为空")
        if not server_cfg['password']:
            errors.append("密码为空")
        if not server_cfg['shared_folder']:
            errors.append("共享目录为空")
        elif not os.path.exists(server_cfg['shared_folder']):
            errors.append(f"共享目录不存在: {server_cfg['shared_folder']}")
        
        if errors:
            error_msg = "\n".join(errors)
            self._append_log(f"❌ 配置验证失败:\n{error_msg}")
            QtWidgets.QMessageBox.critical(self, "配置错误", f"FTP服务器配置有误：\n\n{error_msg}")
            return
        
        # 尝试启动测试服务器
        try:
            from src.protocols.ftp import FTPServerManager
            
            self._append_log(f"🔧 正在测试FTP服务器 {config['host']}:{config['port']}...")
            test_server = FTPServerManager(config)
            
            if test_server.start():
                self._append_log("✓ FTP服务器测试成功！")
                self._append_log(f"  地址: {config['host']}:{config['port']}")
                self._append_log(f"  用户: {config['username']}")
                self._append_log(f"  共享: {config['shared_folder']}")
                
                # 立即停止测试服务器
                test_server.stop()
                self._append_log("✓ 测试服务器已停止")
                
                QtWidgets.QMessageBox.information(
                    self, "测试成功", 
                    f"FTP服务器配置有效！\n\n"
                    f"地址: {config['host']}:{config['port']}\n"
                    f"用户: {config['username']}\n"
                    f"共享: {config['shared_folder']}"
                )
            else:
                self._append_log("❌ FTP服务器启动失败")
                QtWidgets.QMessageBox.critical(
                    self, "测试失败", 
                    f"FTP服务器无法启动！\n\n可能原因：\n"
                    f"1. 端口 {config['port']} 已被占用\n"
                    f"2. 没有管理员权限（端口<1024需要）\n"
                    f"3. 防火墙阻止"
                )
        except Exception as e:
            self._append_log(f"❌ 测试异常: {e}")
            QtWidgets.QMessageBox.critical(self, "测试错误", f"测试过程中发生错误：\n\n{str(e)}")

    def _is_ftp_server_running(self) -> bool:
        try:
            return bool(self.ftp_manager and self.ftp_manager.server and self.ftp_manager.server.is_running)
        except Exception:
            return False

    def _emit_ftp_server_event(self, event: dict) -> None:
        """FTP 服务器线程回调入口，转发到 UI 线程处理。"""
        try:
            self._ftp_server_event_signal.emit(event)
        except Exception as e:
            logger.debug(f"转发FTP事件失败: {type(e).__name__}: {e}")

    def _toggle_ftp_server_only(self):
        if self._is_ftp_server_running():
            self._stop_ftp_server_only(manual=True)
        else:
            self._start_ftp_server_only(manual=True)

    def _start_ftp_server_only(self, manual: bool = False) -> bool:
        """独立启动内置 FTP 服务器，不创建上传 worker。"""
        if manual and self.current_role != 'admin':
            self._append_log("❌ 仅管理员可启动FTP服务器")
            self._toast('仅管理员可启动FTP服务器', 'warning')
            return False
        if not FTP_AVAILABLE or FTPProtocolManager is None:
            self._append_log("❌ FTP模块不可用，无法启动FTP服务器")
            self._toast('FTP模块不可用', 'danger')
            return False
        if self._is_ftp_server_running():
            self._append_log("ℹ️ FTP服务器已在运行")
            self._update_ui_permissions()
            self._update_protocol_status()
            return True

        server_cfg = self._collect_ftp_server_config()
        self.ftp_server_config = copy.deepcopy(server_cfg)
        errors = self._validate_ftp_server_config_only(server_cfg)
        if errors:
            error_msg = "\n".join(errors)
            self._append_log(f"❌ FTP服务器配置验证失败:\n{error_msg}")
            if manual:
                QtWidgets.QMessageBox.critical(self, "FTP服务器配置错误", error_msg)
            return False

        try:
            if not self.ftp_manager:
                self.ftp_manager = FTPProtocolManager()  # type: ignore[misc]
            server_config = self._build_ftp_server_manager_config(server_cfg)
            server_config['event_callback'] = self._emit_ftp_server_event
            self._append_log("🔧 正在启动FTP服务器（独立模式）...")
            if not self.ftp_manager.start_server(server_config):
                raise RuntimeError("FTP服务器启动失败")
            self._ftp_server_started_independently = True
            self._ftp_server_started_by_upload = False
            self.enable_ftp_server = True
            self.cb_enable_ftp_server.blockSignals(True)
            self.cb_enable_ftp_server.setChecked(True)
            self.cb_enable_ftp_server.blockSignals(False)
            status = self.ftp_manager.get_status().get('server') if self.ftp_manager else None
            address = status.get('address') if status else f"{server_cfg.get('host')}:{server_cfg.get('port')}"
            self._append_log(f"✓ FTP服务器已启动（独立模式）: {address}")
            self._toast('FTP服务器已启动', 'success')
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        except Exception as e:
            self._append_log(f"❌ FTP服务器启动失败: {e}")
            self._toast(f'FTP服务器启动失败: {e}', 'danger')
            self._update_ui_permissions()
            self._update_protocol_status()
            return False

    def _stop_ftp_server_only(self, manual: bool = False) -> bool:
        """独立停止内置 FTP 服务器，不影响上传 worker。"""
        if manual and self.current_role != 'admin':
            self._append_log("❌ 仅管理员可停止FTP服务器")
            self._toast('仅管理员可停止FTP服务器', 'warning')
            return False
        if not self._is_ftp_server_running():
            self._append_log("ℹ️ FTP服务器未运行")
            self._ftp_server_started_independently = False
            self._ftp_server_started_by_upload = False
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        try:
            self._append_log("🔧 正在停止FTP服务器...")
            if self.ftp_manager:
                self.ftp_manager.stop_server()
            self._ftp_server_started_independently = False
            self._ftp_server_started_by_upload = False
            self._append_log("✓ FTP服务器已停止")
            self._toast('FTP服务器已停止', 'info')
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        except Exception as e:
            self._append_log(f"⚠️ 停止FTP服务器时出错: {e}")
            self._update_ui_permissions()
            self._update_protocol_status()
            return False

    def _handle_ftp_server_event(self, event: dict):
        self._write_ftp_server_log(event)
        message = self._format_ftp_server_event(event)
        if message:
            self._append_log(message)

    def _format_ftp_server_event(self, event: dict) -> str:
        event_type = event.get('event', '')
        client_ip = event.get('client_ip') or '-'
        username = event.get('username') or '-'
        path = event.get('path') or ''
        size = event.get('size')
        name = os.path.basename(path) if path else ''
        if event_type == 'connect':
            return f"🔌 [FTP-SERVER] 客户端连接: {client_ip}"
        if event_type == 'login_ok':
            return f"✅ [FTP-SERVER] 登录成功: {username}@{client_ip}"
        if event_type == 'login_failed':
            return f"❌ [FTP-SERVER] 登录失败: {username}@{client_ip}"
        if event_type == 'disconnect':
            return f"🔌 [FTP-SERVER] 客户端断开: {username}@{client_ip}"
        if event_type == 'upload_ok':
            return f"✅ [FTP-SERVER] 上传成功: {name} ({size} 字节)"
        if event_type == 'upload_incomplete':
            return f"⚠️ [FTP-SERVER] 上传未完成: {name} ({size} 字节)"
        if event_type == 'server_started':
            return ""
        if event_type == 'server_stopped':
            return ""
        if event_type == 'error':
            return f"❌ [FTP-SERVER] {event.get('message', '发生错误')}"
        return ""

    def _write_ftp_server_log(self, event: dict):
        """写入单独的 FTP 服务器日志文件。"""
        app_dir = self.app_dir

        def write_log():
            try:
                logs_dir = app_dir / 'logs'
                logs_dir.mkdir(parents=True, exist_ok=True)
                today = datetime.datetime.now().strftime('%Y-%m-%d')
                log_path = logs_dir / f'ftp_server_{today}.txt'
                if not log_path.exists():
                    with open(log_path, 'w', encoding='utf-8') as f:
                        f.write("time\tevent\tclient_ip\tusername\tpath\tsize\tresult\tmessage\n")

                timestamp = event.get('timestamp') or datetime.datetime.now().isoformat(timespec='seconds')
                event_type = str(event.get('event', ''))
                client_ip = str(event.get('client_ip', ''))
                username = str(event.get('username', ''))
                path = str(event.get('path', ''))
                size = str(event.get('size', ''))
                result = 'ok' if event_type in ('server_started', 'server_stopped', 'connect', 'login_ok', 'disconnect', 'upload_ok') else 'fail'
                message = str(event.get('message', '')).replace('\t', ' ').replace('\n', ' ')
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"{timestamp}\t{event_type}\t{client_ip}\t{username}\t{path}\t{size}\t{result}\t{message}\n")
            except Exception as e:
                print(f"写入FTP服务器日志失败: {e}")

        try:
            self._log_executor.submit(write_log)
        except Exception:
            write_log()
    
    def _test_ftp_client_connection(self):
        """测试FTP客户端连接"""
        self._append_log("🔌 开始测试FTP客户端连接...")
        
        # 收集当前配置
        client_cfg = self._collect_ftp_client_config()
        self.ftp_client_config = copy.deepcopy(client_cfg)
        config = {
            'name': 'test_client',
            **client_cfg
        }
        
        # 验证配置
        errors = []
        if not client_cfg['host']:
            errors.append("服务器地址为空")
        if not client_cfg['username']:
            errors.append("用户名为空")
        if not client_cfg['password']:
            errors.append("密码为空")
        if not client_cfg['remote_path']:
            errors.append("远程路径为空")
        
        if errors:
            error_msg = "\n".join(errors)
            self._append_log(f"❌ 配置验证失败:\n{error_msg}")
            QtWidgets.QMessageBox.critical(self, "配置错误", f"FTP客户端配置有误：\n\n{error_msg}")
            return
        
        # 尝试连接
        try:
            from src.protocols.ftp import FTPClientUploader
            
            self._append_log(f"🔗 正在连接FTP服务器 {config['host']}:{config['port']}...")
            test_client = FTPClientUploader(config)
            
            if test_client.test_connection():
                self._append_log("✓ FTP客户端连接测试成功！")
                self._append_log(f"  服务器: {config['host']}:{config['port']}")
                self._append_log(f"  用户: {config['username']}")
                self._append_log(f"  远程路径: {config['remote_path']}")
                
                # 断开连接
                test_client.disconnect()
                self._append_log("✓ 已断开连接")
                
                QtWidgets.QMessageBox.information(
                    self, "测试成功", 
                    f"FTP客户端连接成功！\n\n"
                    f"服务器: {config['host']}:{config['port']}\n"
                    f"用户: {config['username']}\n"
                    f"远程路径: {config['remote_path']}"
                )
            else:
                self._append_log("❌ FTP客户端连接失败")
                QtWidgets.QMessageBox.critical(
                    self, "测试失败", 
                    f"无法连接到FTP服务器！\n\n可能原因：\n"
                    f"1. 服务器地址或端口错误\n"
                    f"2. 用户名或密码错误\n"
                    f"3. 网络不通或防火墙阻止\n"
                    f"4. 服务器未运行"
                )
        except Exception as e:
            self._append_log(f"❌ 测试异常: {e}")
            QtWidgets.QMessageBox.critical(self, "测试错误", f"测试过程中发生错误：\n\n{str(e)}")
    
    def _on_protocol_changed(self, index: int):
        """协议选择变化 (v3.1.0 重构: 移除 ftp_server 枚举)"""
        protocols = ['smb', 'ftp_client', 'both']  # v3.1.0: ftp_server 已抽离为独立开关
        self.current_protocol = protocols[index]
        
        # 更新说明文字
        self._update_protocol_description(index)
        
        # v3.1.0: FTP服务器已抽离为独立服务，不再受协议模式限制
        if index == 0:  # SMB
            # 保持ftp_config_widget可见但禁用,避免布局跳动
            self.ftp_config_widget.setVisible(True)
        else:
            self.ftp_config_widget.setVisible(True)
            # 启用FTP客户端配置并自动展开
            self.ftp_client_collapsible.set_expanded(True)
            # FTP服务器配置由独立开关控制
            if self.enable_ftp_server:
                self.ftp_server_collapsible.set_expanded(True)
        
        self._mark_config_modified()
        mode_names = ['SMB', 'FTP客户端', 'SMB+FTP客户端']
        self._append_log(f"📡 切换上传协议：{mode_names[index]}")
        
        # v3.1.0: 显示模式切换toast
        toast_keys = ['toast_protocol_smb', 'toast_protocol_ftp_client', 'toast_protocol_both']
        self._toast(t(toast_keys[index]), 'info')
        
        # 更新协议状态显示和模式标签
        self._update_protocol_status()
        self._update_mode_chip(index)
        self._update_ui_permissions()
    
    def _update_protocol_description(self, index: int):
        """更新协议说明 (v3.1.0 重构: 更短更直观)"""
        descriptions = [
            f"📁 {t('protocol_desc_smb_short')}",
            f"📤 {t('protocol_desc_ftp_client_short')}",
            f"🔄 {t('protocol_desc_both_short')}"
        ]
        self.protocol_desc.setText(descriptions[index])
    
    def _update_mode_chip(self, index: int):
        """v3.1.0 新增: 更新协议模式芯片显示"""
        mode_configs = [
            (t('mode_smb'), '#E3F2FD', '#1565C0'),       # SMB: 蓝色
            (t('mode_ftp_client'), '#FFF3E0', '#E65100'), # FTP客户端: 橙色
            (t('mode_both'), '#E8F5E9', '#2E7D32'),       # 双写: 绿色
        ]
        text, bg_color, text_color = mode_configs[index]
        if hasattr(self, 'lbl_current_mode'):
            self.lbl_current_mode.setValue(text)
            self.lbl_current_mode.setStyleSheet(
                f"background:{bg_color}; color:{text_color}; padding:4px 8px; "
                f"border-radius:4px; font-size:9pt; font-weight:600;"
            )
    
    def _on_ftp_server_toggled(self, checked: bool):
        """v3.1.0 新增: FTP 服务器开关切换"""
        self.enable_ftp_server = checked
        
        # 启用/禁用 FTP 服务器配置
        self.ftp_server_hint.setVisible(checked)
        
        # 启用时自动展开，方便用户配置
        if checked:
            self.ftp_server_collapsible.set_expanded(True)
        
        self.config_modified = True
        self._update_ui_permissions()
        status = '启用' if checked else '禁用'
        self._append_log(f"🖥️ FTP服务器已{status}")
        
        # 更新协议状态显示
        self._update_protocol_status()
    
    def _toggle_autostart(self, checked: bool):
        """切换开机自启动状态"""
        if self.current_role not in ['user', 'admin']:
            self._toast('需要登录后才能设置开机自启动', 'warning')
            # 阻止勾选
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(not checked)
            self.cb_auto_start_windows.blockSignals(False)
            return
        
        try:
            if checked:
                if not self._add_to_startup(explicit=True):
                    raise RuntimeError("启动项未能写入或校验失败")
            else:
                self._remove_from_startup()
            self.auto_start_windows = checked
        except Exception as e:
            self._toast(f'设置开机自启动失败: {e}', 'danger')
            # 恢复状态
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(not checked)
            self.cb_auto_start_windows.blockSignals(False)

    @staticmethod
    def _quote_startup_arg(value: str) -> str:
        return '"' + value.replace('"', '\\"') + '"'

    def _current_startup_command(self) -> str:
        """构造当前版本的启动命令，始终为中文/空格路径加引号。"""
        if getattr(sys, 'frozen', False):
            return self._quote_startup_arg(os.path.abspath(sys.executable))
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        return f"{self._quote_startup_arg(os.path.abspath(sys.executable))} {self._quote_startup_arg(str(main_path))}"

    @staticmethod
    def _startup_target_exists(command: str) -> bool:
        target = _extract_startup_target(command)
        if not target or not os.path.isfile(target):
            return False

        executable_name = Path(target).name.lower()
        if executable_name in {'python', 'python.exe', 'pythonw', 'pythonw.exe', 'py', 'py.exe'}:
            script = _extract_startup_script(command)
            return bool(script and os.path.isfile(script))
        return True

    def _read_startup_command(self) -> str:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REGISTRY_PATH,
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
                return value if isinstance(value, str) else ""
        except FileNotFoundError:
            return ""

    def _write_startup_command(self, command: str) -> None:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REGISTRY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)

        written = self._read_startup_command()
        if written != command or not self._startup_target_exists(written):
            raise RuntimeError("注册表启动命令校验失败")

    def _reconcile_startup_registration(self, explicit: bool = False) -> bool:
        """修复失效/旧版启动项；有效高版本启动项不会被低版本覆盖。"""
        existing = self._read_startup_command()
        current = self._current_startup_command()
        current_target = _extract_startup_target(current)
        if not self._startup_target_exists(current):
            raise RuntimeError(f"当前启动程序不存在: {current_target}")

        if not existing:
            if explicit or self.auto_start_windows:
                self._write_startup_command(current)
                self._append_log(f"✓ 已写入开机自启动: {current}")
                return True
            return False

        existing_target = _extract_startup_target(existing)
        existing_valid = self._startup_target_exists(existing)
        current_version = _parse_app_version(APP_VERSION)
        existing_version = _parse_app_version(existing_target)

        should_update = not existing_valid
        update_reason = "旧启动路径已失效"
        if existing_valid and current_version and existing_version:
            if current_version > existing_version:
                should_update = True
                update_reason = "检测到更高版本"
            elif current_version < existing_version:
                self._append_log(
                    f"ℹ️ 已保留更高版本开机自启动: {existing_target}"
                )
                return True
            elif existing != current and os.path.normcase(existing_target) == os.path.normcase(current_target):
                should_update = True
                update_reason = "规范化启动命令引号"
            elif explicit and os.path.normcase(existing_target) != os.path.normcase(current_target):
                should_update = True
                update_reason = "用户重新启用同版本启动项"
        elif existing_valid and existing_version is None:
            self._append_log(f"⚠️ 启动项版本无法识别，保留现有路径: {existing_target}")
            return True

        if should_update:
            self._write_startup_command(current)
            self._append_log(f"✓ {update_reason}，开机自启动已更新为: {current}")
            return True

        return True

    def _add_to_startup(self, explicit: bool = False) -> bool:
        """添加或自愈 Windows 开机启动项。"""
        try:
            enabled = self._reconcile_startup_registration(explicit=explicit)
            if enabled:
                self._toast('已设置开机自启动', 'success')
            return enabled
        except Exception as e:
            raise Exception(f"添加启动项失败: {str(e)}")

    def _remove_from_startup(self):
        """从Windows启动项移除"""
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REGISTRY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass
            if self._read_startup_command():
                raise RuntimeError("注册表启动项删除校验失败")
            self._append_log("✓ 已从开机自启动移除")
            self._toast('已取消开机自启动', 'success')
        except Exception as e:
            raise Exception(f"移除启动项失败: {str(e)}")

    def _check_startup_status(self) -> bool:
        """检查当前是否在启动项中"""
        try:
            return self._reconcile_startup_registration(explicit=False)
        except Exception as exc:
            self._append_log(f"⚠️ 开机自启动检查失败: {exc}")
            return False

    def _auto_start_upload(self):
        """自动开始上传（启动时调用）"""
        if not self.auto_run_on_startup:
            return

        if self.enable_ftp_server:
            started = self._start_ftp_server_only(manual=False)
            if started and self._is_server_only_configuration():
                self._append_log("✓ 自动运行已启动FTP服务器（server-only）")
                return
        
        # 验证设置
        if not self.src_edit.text() or not self.tgt_edit.text() or not self.bak_edit.text():
            self._append_log("⚠ 自动运行失败：文件夹路径未设置")
            return
        
        self._append_log("🚀 自动运行已触发，1秒后开始上传...")
        self._on_start()

    def _status_card(self) -> QtWidgets.QFrame:
        card, v, self.title_status = self._card("📊 运行状态", "card_status")
        # status pill
        self.lbl_status = QtWidgets.QLabel(t('status_stopped'))
        self.lbl_status.setStyleSheet(
            f"background:#FEE2E2; color:#B91C1C; padding:{self._scale_px(6, 4)}px {self._scale_px(12, 8)}px; "
            f"font-weight:700; border-radius:{self._scale_px(12, 8)}px; font-size:{self._font_pt(10)}pt;"
        )
        v.addWidget(self.lbl_status)
        # chips - 低分辨率下减少列数，避免中间列横向撑宽。
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(self._scale_px(12, 6))
        self.lbl_uploaded = self._chip(t('uploaded'), "0", "#E3F2FD", "#1976D2")
        self.lbl_failed = self._chip(t('failed'), "0", "#FFEBEE", "#C62828")
        self.lbl_skipped = self._chip(t('skipped'), "0", "#FFF9C3", "#F57F17")
        self.lbl_rate = self._chip(t('rate'), "0 MB/s", "#E8F5E9", "#2E7D32")
        self.lbl_queue = self._chip(t('archive_queue'), "0", "#F3E5F5", "#6A1B9A")
        self.lbl_time = self._chip(t('runtime'), "00:00:00", "#FFF3E0", "#E65100")
        # 新增：磁盘空间芯片
        self.lbl_target_disk = self._chip(t('target_disk'), "--", "#E1F5FE", "#01579B")
        self.lbl_backup_disk = self._chip(t('backup_disk'), "--", "#F1F8E9", "#33691E")
        # v1.9 新增：网络状态芯片
        self.lbl_network = self._chip(t('network_status'), t('network_unknown'), "#ECEFF1", "#546E7A")
        # v2.0 新增：协议和FTP状态芯片
        self.lbl_protocol = self._chip(t('protocol_chip'), "SMB", "#E8EAF6", "#3F51B5")
        self.lbl_ftp_server = self._chip(t('ftp_server_chip'), t('not_started'), "#FCE4EC", "#C2185B")
        self.lbl_ftp_client = self._chip(t('ftp_client_chip'), t('not_connected'), "#FFF8E1", "#F57C00")
        # v3.1.0 新增：当前模式芯片（醒目显示）
        self.lbl_current_mode = self._chip(t('current_mode'), t('mode_smb'), "#E3F2FD", "#1565C0")
        
        self.status_grid = grid
        self.status_chip_widgets = [
            self.lbl_uploaded, self.lbl_failed, self.lbl_skipped,
            self.lbl_rate, self.lbl_queue, self.lbl_time,
            self.lbl_target_disk, self.lbl_backup_disk, self.lbl_network,
            self.lbl_protocol, self.lbl_ftp_server, self.lbl_ftp_client,
            self.lbl_current_mode,
        ]
        for i, w in enumerate(self.status_chip_widgets):
            grid.addWidget(w, i // self.status_grid_columns, i % self.status_grid_columns)
        v.addLayout(grid)
        
        # 分隔线
        v.addWidget(self._hline())
        
        # 新增：当前文件信息
        self.current_file_label_widget = QtWidgets.QLabel(t('current_file_label'))
        self.current_file_label_widget.setStyleSheet(
            f"font-weight:700; font-size:{self._font_pt(10)}pt; color:#424242; margin-top:{self._scale_px(4, 2)}px;"
        )
        v.addWidget(self.current_file_label_widget)
        
        self.lbl_current_file = QtWidgets.QLabel(t('waiting'))
        self.lbl_current_file.setStyleSheet(
            f"color:#616161; font-size:{self._font_pt(9)}pt; padding:{self._scale_px(4, 2)}px {self._scale_px(8, 5)}px;"
        )
        self.lbl_current_file.setWordWrap(True)
        v.addWidget(self.lbl_current_file)
        
        # 当前文件进度条
        self.pbar_file = QtWidgets.QProgressBar()
        self.pbar_file.setRange(0, 100)
        self.pbar_file.setValue(0)
        self.pbar_file.setTextVisible(True)
        self.pbar_file.setFormat(t('waiting'))
        self.pbar_file.setStyleSheet("""
            QProgressBar {
                border: 2px solid #BDBDBD;
                border-radius: 6px;
                text-align: center;
                min-height: 18px;
                background-color: #F5F5F5;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                                            stop:0 #4CAF50, stop:1 #81C784);
                border-radius: 4px;
            }
        """)
        v.addWidget(self.pbar_file)
        
        # 分隔线
        v.addWidget(self._hline())
        
        # progress
        self.lbl_progress = QtWidgets.QLabel(t('waiting'))
        v.addWidget(self.lbl_progress)
        self.pbar = QtWidgets.QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        v.addWidget(self.pbar)
        return card

    def _chip(self, title: str, val: str, bg: str, fg: str) -> ChipWidget:
        return ChipWidget(title, val, bg, fg, self)

    def _hline(self):
        line = QtWidgets.QFrame()
        shape_enum = getattr(QtWidgets.QFrame, 'Shape', QtWidgets.QFrame)
        line.setFrameShape(getattr(shape_enum, 'HLine'))
        line.setStyleSheet("color:#E5EAF0")
        return line

    def _log_card(self) -> QtWidgets.QFrame:
        card, v, self.title_log = self._card("📜 运行日志", "card_log")
        # toolbar
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addStretch(1)
        
        # 右侧：自动滚动
        self.cb_autoscroll = QtWidgets.QCheckBox("📜 自动滚动")
        self.cb_autoscroll.setChecked(True)
        toolbar.addWidget(self.cb_autoscroll)
        v.addLayout(toolbar)
        # log area - 压缩高度以节省空间
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(self._scale_px(300, 180, 300))
        v.addWidget(self.log)
        return card

    # actions
    def _choose_source(self):
        """选择源文件夹"""
        # 获取当前路径作为默认打开位置
        current = self.src_edit.text()
        start_dir = current if current and os.path.exists(current) else ""
        
        self._append_log("📂 正在选择源文件夹...")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择源文件夹", start_dir)
        if d:
            self._append_log(f"✓ 已选择源文件夹: {d}")
            self.src_edit.setText(d)
        else:
            self._append_log("✗ 取消选择源文件夹")

    def _choose_target(self):
        """选择目标文件夹"""
        # 获取当前路径作为默认打开位置
        current = self.tgt_edit.text()
        start_dir = current if current and os.path.exists(current) else ""
        
        self._append_log("📂 正在选择目标文件夹...")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择目标文件夹", start_dir)
        if d:
            self._append_log(f"✓ 已选择目标文件夹: {d}")
            self.tgt_edit.setText(d)
        else:
            self._append_log("✗ 取消选择目标文件夹")

    def _choose_backup(self):
        """选择备份文件夹"""
        # 获取当前路径作为默认打开位置
        current = self.bak_edit.text()
        start_dir = current if current and os.path.exists(current) else ""
        
        self._append_log("📂 正在选择备份文件夹...")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择备份文件夹", start_dir)
        if d:
            self._append_log(f"✓ 已选择备份文件夹: {d}")
            self.bak_edit.setText(d)
        else:
            self._append_log("✗ 取消选择备份文件夹")

    def _on_backup_toggled(self, checked: bool):
        """切换备份开关"""
        self.enable_backup = checked
        # 刷新UI权限（会自动更新备份路径输入框和浏览按钮的状态）
        self._update_ui_permissions()
        self._mark_config_modified()

    def _mark_config_modified(self):
        """标记配置已修改"""
        self.config_modified = True
        if not self._config_loading:
            self._append_log('⚠ 配置已修改，请点击"保存配置"按钮确认')

    def _validate_paths(self) -> tuple:
        """验证文件夹路径是否存在
        返回: (是否全部有效, 错误消息列表)
        """
        errors = []
        src = self.src_edit.text().strip()
        tgt = self.tgt_edit.text().strip()
        bak = self.bak_edit.text().strip()
        
        self._append_log("🔍 正在验证文件夹路径...")
        
        if not src:
            errors.append("源文件夹路径为空")
        elif not os.path.exists(src):
            errors.append(f"源文件夹不存在: {src}")
        else:
            self._append_log(f"✓ 源文件夹路径有效: {src}")
        
        if not tgt:
            errors.append("目标文件夹路径为空")
        elif not os.path.exists(tgt):
            errors.append(f"目标文件夹不存在: {tgt}")
        else:
            self._append_log(f"✓ 目标文件夹路径有效: {tgt}")
        
        # v2.1.1 修改：只有启用备份时才验证备份路径
        if self.enable_backup:
            if not bak:
                errors.append("备份文件夹路径为空")
            elif not os.path.exists(bak):
                errors.append(f"备份文件夹不存在: {bak}")
            else:
                self._append_log(f"✓ 备份文件夹路径有效: {bak}")
        
        # 额外校验：三个路径必须互不相同，避免用户误填相同路径导致循环或数据覆盖
        try:
            def _norm(p: str) -> str:
                return os.path.normcase(os.path.abspath(p)) if p else ''

            n_src = _norm(src)
            n_tgt = _norm(tgt)
            n_bak = _norm(bak)

            if n_src and n_tgt and n_src == n_tgt:
                errors.append("源文件夹与目标文件夹路径相同，请选择不同的路径")
            # v2.1.1 修改：只有启用备份时才检查备份路径相同性
            if self.enable_backup:
                if n_src and n_bak and n_src == n_bak:
                    errors.append("源文件夹与备份文件夹路径相同，请选择不同的路径")
                if n_tgt and n_bak and n_tgt == n_bak:
                    errors.append("目标文件夹与备份文件夹路径相同，请选择不同的路径")
        except Exception:
            # 如果路径规范化出错，不影响已有的存在性检查，继续返回其他错误信息
            pass
        
        if errors:
            self._append_log(f"❌ 路径验证失败，发现 {len(errors)} 个错误")
        else:
            self._append_log("✓ 所有路径验证通过")
        
        return len(errors) == 0, errors

    def _is_server_only_configuration(self) -> bool:
        """是否是只运行内置 FTP 服务器、不启动上传 worker 的配置。"""
        if not self.enable_ftp_server or self.current_protocol != 'smb':
            return False
        src = self.src_edit.text().strip()
        tgt = self.tgt_edit.text().strip()
        return not src and not tgt
    
    def _validate_ftp_config(self) -> tuple:
        """
        验证FTP配置的有效性
        
        Returns:
            tuple: (是否有效, 错误消息列表)
        """
        errors = []
        
        # 如果没有启用 FTP 服务器，也没有使用 FTP 客户端，跳过验证
        if self.current_protocol == 'smb' and not self.enable_ftp_server:
            return True, []
        
        self._append_log("🔍 正在验证FTP配置...")
        server_cfg = self._collect_ftp_server_config()
        client_cfg = self._collect_ftp_client_config()
        self.ftp_server_config = copy.deepcopy(server_cfg)
        self.ftp_client_config = copy.deepcopy(client_cfg)
        
        # 验证FTP服务器配置 (v3.1.0 重构：由独立开关控制)
        if self.enable_ftp_server:
            errors.extend(self._validate_ftp_server_config_only(server_cfg))
        
        # 验证FTP客户端配置
        if self.current_protocol in ['ftp_client', 'both']:
            # 主机地址验证
            host = client_cfg.get('host', '').strip()
            if not host:
                errors.append("FTP客户端主机地址为空")
            else:
                # 简单的域名或IP格式验证
                import re
                is_ip = re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', host)
                is_domain = re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$', host)
                if not is_ip and not is_domain:
                    errors.append(f"FTP客户端主机地址格式无效: {host}")
            
            # 端口验证
            port = client_cfg.get('port', 0)
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append(f"FTP客户端端口无效: {port}（范围：1-65535）")
            
            # 用户名验证
            username = client_cfg.get('username', '').strip()
            if not username:
                errors.append("FTP客户端用户名为空")
            
            # 密码验证
            password = client_cfg.get('password', '').strip()
            if not password:
                errors.append("FTP客户端密码为空")
            
            # 远程路径验证
            remote_path = client_cfg.get('remote_path', '').strip()
            if not remote_path:
                errors.append("FTP客户端远程路径为空")
            elif not remote_path.startswith('/'):
                errors.append(f"FTP客户端远程路径应以 / 开头: {remote_path}")
        
        if errors:
            self._append_log(f"❌ FTP配置验证失败，发现 {len(errors)} 个错误")
        else:
            self._append_log("✓ FTP配置验证通过")
        
        return len(errors) == 0, errors

    def _save_config(self) -> bool:
        """保存配置到文件"""
        self.last_config_save_error = ''

        # v2.2.0 权限检查：仅登录用户可保存配置
        if self.current_role == 'guest':
            self.last_config_save_error = '请先登录后再保存配置'
            self._append_log("❌ 未登录用户无权保存配置")
            self._toast('请先登录后再保存配置', 'warning')
            return False
        
        self._append_log("💾 正在保存配置...")
        
        # v2.2.0 新增：保存前验证路径；FTP server-only 不需要源/目标路径
        if not self._is_server_only_configuration():
            is_valid, errors = self._validate_paths()
            if not is_valid:
                error_msg = "\n".join(errors)
                self.last_config_save_error = error_msg
                self._append_log(f"❌ 路径验证失败，无法保存配置:\n{error_msg}")
                self._toast('路径验证失败，请检查配置', 'danger')
                return False
        
        # v2.2.0 新增：验证FTP配置（如果使用FTP协议或启用内置服务器）
        if self.current_protocol != 'smb' or self.enable_ftp_server:
            is_valid, errors = self._validate_ftp_config()
            if not is_valid:
                error_msg = "\n".join(errors)
                self.last_config_save_error = error_msg
                self._append_log(f"❌ FTP配置验证失败，无法保存配置:\n{error_msg}")
                self._toast('FTP配置验证失败，请检查配置', 'danger')
                return False
        
        # 保留现有用户密码
        path = self.app_dir / 'config.json'
        users = {}
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    old_cfg = json.load(f)
                    users = old_cfg.get('users', {})
            except Exception:
                pass

        try:
            ftp_server_password, ftp_server_password_encrypted = self._encrypt_ftp_password(
                self.ftp_server_pass.text(),
                'FTP服务器',
            )
            ftp_client_password, ftp_client_password_encrypted = self._encrypt_ftp_password(
                self.ftp_client_pass.text(),
                'FTP客户端',
            )
        except Exception as e:
            self.last_config_save_error = str(e)
            self._append_log(f"❌ FTP密码加密失败，无法保存配置: {e}")
            self._toast(f'保存失败: {e}', 'danger')
            return False
        
        # 策略映射
        strategy_map = {'跳过': 'skip', '重命名': 'rename', '覆盖': 'overwrite', '询问': 'ask'}
        
        cfg = {
            'source_folder': self.src_edit.text(),
            'target_folder': self.tgt_edit.text(),
            'backup_folder': self.bak_edit.text(),
            'enable_backup': self.cb_enable_backup.isChecked(),  # v2.1.1 新增
            'upload_interval': self.spin_interval.value(),
            'monitor_mode': 'periodic',
            'disk_threshold_percent': self.spin_disk.value(),
            'retry_count': self.spin_retry.value(),
            'disk_check_interval': self.spin_disk_check.value(),
            'filter_jpg': self.cb_ext['.jpg'].isChecked(),
            'filter_png': self.cb_ext['.png'].isChecked(),
            'filter_bmp': self.cb_ext['.bmp'].isChecked(),
            'filter_gif': self.cb_ext['.gif'].isChecked(),
            'filter_raw': self.cb_ext['.raw'].isChecked(),
            'auto_start_windows': self.cb_auto_start_windows.isChecked(),
            'auto_run_on_startup': self.cb_auto_run_on_startup.isChecked(),
            # v2.2.0 新增：托盘通知开关
            'show_notifications': self.cb_show_notifications.isChecked() if hasattr(self, 'cb_show_notifications') else True,
            # v2.3.0 新增：速率限制
            'limit_upload_rate': self.cb_limit_rate.isChecked() if hasattr(self, 'cb_limit_rate') else False,
            'max_upload_rate_mbps': self.spin_max_rate.value() if hasattr(self, 'spin_max_rate') else 10.0,
            # v1.9 新增：去重
            'enable_deduplication': self.cb_dedup_enable.isChecked(),
            'hash_algorithm': self.combo_hash.currentText().lower(),
            'duplicate_strategy': strategy_map.get(self.combo_strategy.currentText(), 'ask'),
            # v1.9 新增：网络监控
            'network_check_interval': self.spin_network_check.value(),
            'network_auto_pause': self.cb_network_auto_pause.isChecked(),
            'network_auto_resume': self.cb_network_auto_resume.isChecked(),
            # v1.9 新增：自动删除
            'enable_auto_delete': self.enable_auto_delete,
            'auto_delete_folder': self.auto_delete_folder,
            'auto_delete_folders': self.auto_delete_folders,
            'auto_delete_threshold': self.auto_delete_threshold,
            'auto_delete_target_percent': self.auto_delete_target_percent,
            'auto_delete_keep_days': self.auto_delete_keep_days,
            'auto_delete_check_interval': self.auto_delete_check_interval,
            'auto_delete_formats': self.auto_delete_formats,
            'auto_delete_use_trash': self.auto_delete_use_trash,
            # v2.0 新增：FTP 协议配置 (v3.1.0 重构)
            'upload_protocol': self.current_protocol,
            # v2.2.0 新增：保存当前使用的协议模式
            'current_protocol': self.current_protocol,
            # v3.1.0 新增：FTP 服务器独立开关
            'enable_ftp_server': self.enable_ftp_server,
            'ftp_server': {
                'host': self.ftp_server_host.text(),
                'port': self.ftp_server_port.value(),
                'username': self.ftp_server_user.text(),
                'password': ftp_server_password,
                'password_encrypted': ftp_server_password_encrypted,
                'shared_folder': self.ftp_server_share.text(),
                'enable_passive': self.cb_server_passive.isChecked(),
                'passive_ports_start': self.ftp_server_passive_start.value(),
                'passive_ports_end': self.ftp_server_passive_end.value(),
                'enable_tls': self.cb_server_tls.isChecked(),
                'max_connections': self.ftp_server_max_conn.value(),
                'max_connections_per_ip': self.ftp_server_max_conn_per_ip.value(),
            },
            'ftp_client': {
                'host': self.ftp_client_host.text(),
                'port': self.ftp_client_port.value(),
                'username': self.ftp_client_user.text(),
                'password': ftp_client_password,
                'password_encrypted': ftp_client_password_encrypted,
                'remote_path': self.ftp_client_remote.text(),
                'timeout': self.ftp_client_timeout.value(),
                'retry_count': self.ftp_client_retry.value(),
                'passive_mode': self.cb_client_passive.isChecked(),
                'enable_tls': self.cb_client_tls.isChecked(),
            },
            'users': users,
        }
        if self._write_config_payload(cfg):
            # 保存成功后清除修改标记并更新保存的配置
            self.config_modified = False
            self.saved_config = copy.deepcopy(cfg)
            
            self._append_log("✓ 配置已成功保存到文件")
            self._toast('配置已保存', 'success')
            self._update_auto_cleanup_schedule()
            return True

        self._append_log(f"❌ 配置保存失败: {self.last_config_save_error}")
        self._toast(f'保存失败: {self.last_config_save_error}', 'danger')
        return False

    def _save_auto_cleanup_config(self, cleanup_config: dict) -> bool:
        """独立保存自动清理配置，避免被主配置校验链连坐。"""
        self.last_config_save_error = ''
        reason = self._get_disk_cleanup_block_reason()
        if reason:
            self.last_config_save_error = reason
            self._append_log(f"❌ 自动清理配置保存已阻止: {reason}")
            self._toast(reason, 'warning')
            return False

        folders: List[str] = []
        for path in cleanup_config.get('auto_delete_folders', []):
            if not isinstance(path, str):
                continue
            cleaned = path.strip()
            if cleaned and cleaned not in folders:
                folders.append(cleaned)

        group_valid, group_error, _ = self._validate_cleanup_folder_group(folders)
        if folders and not group_valid:
            self.last_config_save_error = group_error
            self._append_log(f"❌ 自动清理配置保存失败: {group_error}")
            self._toast(group_error, 'warning')
            return False

        enabled = bool(cleanup_config.get('enable_auto_delete', False))
        threshold = int(cleanup_config.get('auto_delete_threshold', self.auto_delete_threshold))
        target = int(cleanup_config.get('auto_delete_target_percent', self.auto_delete_target_percent))
        interval = int(cleanup_config.get('auto_delete_check_interval', self.auto_delete_check_interval))
        formats = list(cleanup_config.get('auto_delete_formats', self.auto_delete_formats))
        use_trash = bool(cleanup_config.get('auto_delete_use_trash', self.auto_delete_use_trash))
        keep_days = int(cleanup_config.get('auto_delete_keep_days', self.auto_delete_keep_days))

        try:
            path = self.app_dir / 'config.json'
            cfg = ConfigManager(path).load()
            cfg['enable_auto_delete'] = enabled
            cfg['auto_delete_folders'] = folders
            cfg['auto_delete_folder'] = folders[0] if folders else ''
            cfg['auto_delete_threshold'] = threshold
            cfg['auto_delete_target_percent'] = target
            cfg['auto_delete_check_interval'] = interval
            cfg['auto_delete_formats'] = formats
            cfg['auto_delete_use_trash'] = use_trash
            cfg['auto_delete_keep_days'] = keep_days

            if not self._write_config_payload(cfg):
                self._append_log(f"❌ 自动清理配置保存失败: {self.last_config_save_error}")
                return False

            self.enable_auto_delete = enabled
            self.auto_delete_folders = list(folders)
            self.auto_delete_folder = folders[0] if folders else ''
            self.auto_delete_threshold = threshold
            self.auto_delete_target_percent = target
            self.auto_delete_check_interval = interval
            self.auto_delete_formats = list(formats)
            self.auto_delete_use_trash = use_trash
            self.auto_delete_keep_days = keep_days

            if not isinstance(self.saved_config, dict):
                self.saved_config = {}
            self.saved_config.update({
                'enable_auto_delete': enabled,
                'auto_delete_folders': list(folders),
                'auto_delete_folder': folders[0] if folders else '',
                'auto_delete_threshold': threshold,
                'auto_delete_target_percent': target,
                'auto_delete_check_interval': interval,
                'auto_delete_formats': list(formats),
                'auto_delete_use_trash': use_trash,
                'auto_delete_keep_days': keep_days,
            })
            self._append_log("✓ 自动清理配置已保存")
            self._update_auto_cleanup_schedule()
            return True
        except Exception as e:
            self.last_config_save_error = str(e)
            self._append_log(f"❌ 自动清理配置保存失败: {e}")
            return False

    def _load_config(self):
        """从配置文件加载设置"""
        self._config_loading = True
        self._append_log("📖 正在加载配置文件...")
        
        path = self.app_dir / 'config.json'
        if not path.exists():
            self._append_log("⚠ 配置文件不存在，已生成默认配置")
        try:
            cfg = ConfigManager(path).load()

            self._append_log("✓ 配置文件加载成功")
            self._load_user_passwords(cfg)

            self.src_edit.setText(cfg.get('source_folder', ''))
            self.tgt_edit.setText(cfg.get('target_folder', ''))
            self.bak_edit.setText(cfg.get('backup_folder', ''))
            
            # v2.1.1 新增：加载备份启用状态
            self.enable_backup = cfg.get('enable_backup', True)
            self.cb_enable_backup.blockSignals(True)
            self.cb_enable_backup.setChecked(self.enable_backup)
            self.cb_enable_backup.blockSignals(False)
            
            self.spin_interval.setValue(int(cfg.get('upload_interval', 30)))
            self.spin_disk.setValue(int(cfg.get('disk_threshold_percent', 10)))
            self.spin_retry.setValue(int(cfg.get('retry_count', 3)))
            self.spin_disk_check.setValue(int(cfg.get('disk_check_interval', 5)))
            self.disk_check_interval = int(cfg.get('disk_check_interval', 5))
            self.cb_ext['.jpg'].setChecked(cfg.get('filter_jpg', True))
            self.cb_ext['.png'].setChecked(cfg.get('filter_png', True))
            self.cb_ext['.bmp'].setChecked(cfg.get('filter_bmp', True))
            self.cb_ext['.gif'].setChecked(cfg.get('filter_gif', True))
            self.cb_ext['.raw'].setChecked(cfg.get('filter_raw', True))
            
            # 加载高级选项
            self.auto_start_windows = cfg.get('auto_start_windows', False)
            self.auto_run_on_startup = cfg.get('auto_run_on_startup', False)
            # 从注册表检查实际的开机自启状态
            actual_startup = self._check_startup_status()
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(actual_startup)
            self.cb_auto_start_windows.blockSignals(False)
            self.cb_auto_run_on_startup.setChecked(self.auto_run_on_startup)
            
            # v2.2.0 新增：加载托盘通知开关
            self.show_notifications = cfg.get('show_notifications', True)
            if hasattr(self, 'cb_show_notifications'):
                self.cb_show_notifications.blockSignals(True)
                self.cb_show_notifications.setChecked(self.show_notifications)
                self.cb_show_notifications.blockSignals(False)
                self._set_checkbox_mark(self.cb_show_notifications, self.show_notifications)
            
            # v2.3.0 新增：加载速率限制配置
            self.limit_upload_rate = cfg.get('limit_upload_rate', False)
            self.max_upload_rate_mbps = cfg.get('max_upload_rate_mbps', 10.0)
            if hasattr(self, 'cb_limit_rate'):
                self.cb_limit_rate.blockSignals(True)
                self.cb_limit_rate.setChecked(self.limit_upload_rate)
                self.cb_limit_rate.blockSignals(False)
                self._set_checkbox_mark(self.cb_limit_rate, self.limit_upload_rate)
                self.spin_max_rate.setValue(self.max_upload_rate_mbps)
            
            # v1.9 新增：加载去重配置
            self.enable_deduplication = cfg.get('enable_deduplication', False)
            self.hash_algorithm = cfg.get('hash_algorithm', 'md5')
            self.duplicate_strategy = cfg.get('duplicate_strategy', 'ask')
            
            self.cb_dedup_enable.blockSignals(True)
            self.cb_dedup_enable.setChecked(self.enable_deduplication)
            self.cb_dedup_enable.blockSignals(False)
            
            # 映射策略文本
            strategy_text_map = {'skip': '跳过', 'rename': '重命名', 'overwrite': '覆盖', 'ask': '询问'}
            hash_text = self.hash_algorithm.upper()
            strategy_text = strategy_text_map.get(self.duplicate_strategy, '询问')
            
            self.combo_hash.setCurrentText(hash_text)
            self.combo_strategy.setCurrentText(strategy_text)
            
            # v1.9 新增：加载网络监控配置
            self.network_check_interval = cfg.get('network_check_interval', 10)
            self.network_auto_pause = cfg.get('network_auto_pause', True)
            self.network_auto_resume = cfg.get('network_auto_resume', True)
            
            self.spin_network_check.setValue(self.network_check_interval)
            self.cb_network_auto_pause.setChecked(self.network_auto_pause)
            self.cb_network_auto_resume.setChecked(self.network_auto_resume)
            
            # v1.9 新增：加载自动删除配置
            self.enable_auto_delete = cfg.get('enable_auto_delete', False)
            self.auto_delete_folder = cfg.get('auto_delete_folder', '')
            self.auto_delete_folders = cfg.get('auto_delete_folders', [])
            if not isinstance(self.auto_delete_folders, list):
                self.auto_delete_folders = []
            if not self.auto_delete_folders and self.auto_delete_folder:
                self.auto_delete_folders = [self.auto_delete_folder]
            self.auto_delete_threshold = cfg.get('auto_delete_threshold', 80)
            self.auto_delete_target_percent = cfg.get('auto_delete_target_percent', 40)
            self.auto_delete_keep_days = cfg.get('auto_delete_keep_days', 10)
            self.auto_delete_check_interval = cfg.get('auto_delete_check_interval', 300)
            self.auto_delete_formats = cfg.get('auto_delete_formats', [])
            if not isinstance(self.auto_delete_formats, list):
                self.auto_delete_formats = []
            self.auto_delete_use_trash = cfg.get('auto_delete_use_trash', True)
            if self.auto_delete_target_percent >= self.auto_delete_threshold:
                self.auto_delete_target_percent = max(0, self.auto_delete_threshold - 10)
            
            # 这些控件在磁盘清理对话框中，主窗口可能没有（用 getattr 避免 Pylance 误报）
            _cb_auto = getattr(self, 'cb_enable_auto_delete', None)
            if _cb_auto is not None:
                _cb_auto.blockSignals(True)
                _cb_auto.setChecked(self.enable_auto_delete)
                _cb_auto.blockSignals(False)
            
            _edit_folder = getattr(self, 'auto_del_folder_edit', None)
            can_manage_auto_cleanup = self._can_manage_disk_cleanup()
            if _edit_folder is not None:
                _edit_folder.setText(self.auto_delete_folder)
                _edit_folder.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            _btn_choose = getattr(self, 'btn_choose_auto_del', None)
            if _btn_choose is not None:
                _btn_choose.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            _spin_threshold = getattr(self, 'spin_auto_del_threshold', None)
            if _spin_threshold is not None:
                _spin_threshold.setValue(self.auto_delete_threshold)
                _spin_threshold.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            _spin_target = getattr(self, 'spin_auto_del_target', None)
            if _spin_target is not None:
                _spin_target.setValue(self.auto_delete_target_percent)
                _spin_target.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            _spin_keep = getattr(self, 'spin_auto_del_keep_days', None)
            if _spin_keep is not None:
                _spin_keep.setValue(self.auto_delete_keep_days)
                _spin_keep.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            _spin_interval = getattr(self, 'spin_auto_del_interval', None)
            if _spin_interval is not None:
                _spin_interval.setValue(self.auto_delete_check_interval)
                _spin_interval.setEnabled(can_manage_auto_cleanup and self.enable_auto_delete)
            
            # v2.0 新增：加载协议配置 (v3.1.0 重构)
            protocol = cfg.get('upload_protocol', 'smb')
            saved_protocol = cfg.get('current_protocol', protocol)
            
            # v3.1.0: 迁移旧配置 - 如果协议是 ftp_server，转换为 enable_ftp_server=True
            if saved_protocol == 'ftp_server' or protocol == 'ftp_server':
                self.enable_ftp_server = True
                saved_protocol = 'smb'  # 降级为 SMB 协议
                protocol = 'smb'
                self._append_log("⚠️ 配置迁移: ftp_server 已转换为独立开关")
            else:
                self.enable_ftp_server = cfg.get('enable_ftp_server', False)
            
            # v3.1.0: 新的协议映射（不包含 ftp_server）
            protocol_map = {
                'smb': 0,
                'ftp_client': 1,
                'both': 2
            }
            self.combo_protocol.setCurrentIndex(protocol_map.get(protocol, 0))
            
            # 设置当前协议
            self.current_protocol = saved_protocol if saved_protocol in protocol_map else 'smb'
            self._append_log(f"✓ 已加载上次协议模式: {self.current_protocol}")
            
            # v3.1.0: 加载 FTP 服务器独立开关状态，不再受 SMB/FTP 客户端协议限制
            self.cb_enable_ftp_server.blockSignals(True)
            self.cb_enable_ftp_server.setChecked(self.enable_ftp_server)
            self.cb_enable_ftp_server.blockSignals(False)
            self.ftp_server_hint.setVisible(self.enable_ftp_server)
            self.ftp_config_widget.setVisible(True)
            if self.enable_ftp_server:
                self.ftp_server_collapsible.set_expanded(True)
                self._append_log(f"✓ FTP服务器已启用")
            
            # 更新协议状态显示
            self._update_protocol_status()
            
            # 加载 FTP 服务器配置
            ftp_server = cfg.get('ftp_server', {})
            self.ftp_server_host.setText(ftp_server.get('host', '0.0.0.0'))
            self.ftp_server_port.setValue(ftp_server.get('port', 2121))
            self.ftp_server_user.setText(ftp_server.get('username', 'upload_user'))
            self.ftp_server_pass.setText(self._read_ftp_password(ftp_server))
            self.ftp_server_share.setText(ftp_server.get('shared_folder', ''))
            # v2.0 新增：加载高级选项
            self.cb_server_passive.setChecked(ftp_server.get('enable_passive', True))
            self.ftp_server_passive_start.setValue(ftp_server.get('passive_ports_start', 60000))
            self.ftp_server_passive_end.setValue(ftp_server.get('passive_ports_end', 65535))
            self.cb_server_tls.setChecked(ftp_server.get('enable_tls', False))
            self.ftp_server_max_conn.setValue(ftp_server.get('max_connections', 256))
            self.ftp_server_max_conn_per_ip.setValue(ftp_server.get('max_connections_per_ip', 5))
            
            # 加载 FTP 客户端配置
            ftp_client = cfg.get('ftp_client', {})
            self.ftp_client_host.setText(ftp_client.get('host', ''))
            self.ftp_client_port.setValue(ftp_client.get('port', 21))
            self.ftp_client_user.setText(ftp_client.get('username', ''))
            self.ftp_client_pass.setText(self._read_ftp_password(ftp_client))
            self.ftp_client_remote.setText(ftp_client.get('remote_path', '/upload'))
            self.ftp_client_timeout.setValue(ftp_client.get('timeout', 30))
            self.ftp_client_retry.setValue(ftp_client.get('retry_count', 3))
            # v2.0 新增：加载高级选项
            self.cb_client_passive.setChecked(ftp_client.get('passive_mode', True))
            self.cb_client_tls.setChecked(ftp_client.get('enable_tls', False))

            self.ftp_server_config = copy.deepcopy(ftp_server)
            self.ftp_client_config = copy.deepcopy(ftp_client)
            
            # 保存已加载的配置（用于回退）
            self.saved_config = copy.deepcopy(cfg)
            self.config_modified = False
            
            self._append_log(f"✓ 已加载配置: 源={cfg.get('source_folder', '未设置')}")
            self._append_log(f"✓ 已加载配置: 目标={cfg.get('target_folder', '未设置')}")
            self._append_log(f"✓ 已加载配置: 备份={cfg.get('backup_folder', '未设置')}")
            if self.default_password_roles:
                self._append_log(f"⚠️ 检测到默认弱口令仍在使用: {'、'.join(self.default_password_roles)}")
        except Exception as e:
            self._append_log(f"❌ 加载配置失败: {e}")
        finally:
            self._config_loading = False
            try:
                self._update_ui_permissions()
            except Exception as e:
                logger.debug(f"加载配置后刷新权限失败: {e}")

    def _on_start(self):
        """开始上传"""
        self._append_log("=" * 50)
        self._append_log("🚀 准备开始上传任务...")
        
        # 1. 验证路径是否存在
        is_valid, errors = self._validate_paths()
        if not is_valid:
            error_msg = "\n".join(errors)
            self._append_log(f"❌ 路径验证失败:\n{error_msg}")
            
            # 弹窗显示错误
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
            msg_box.setWindowTitle("路径验证失败")
            msg_box.setText("文件夹路径配置有误，无法开始上传！")
            msg_box.setDetailedText(error_msg)
            msg_box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
            msg_box.exec() if hasattr(msg_box, 'exec') else msg_box.exec_()
            
            self._toast('路径验证失败，无法开始上传', 'danger')
            return
        
        # v2.0 新增：验证FTP配置（如果使用FTP协议或启用内置服务器）
        if self.current_protocol != 'smb' or self.enable_ftp_server:
            is_valid, errors = self._validate_ftp_config()
            if not is_valid:
                error_msg = "\n".join(errors)
                self._append_log(f"❌ FTP配置验证失败:\n{error_msg}")
                
                # 弹窗显示错误
                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
                msg_box.setWindowTitle("FTP配置验证失败")
                msg_box.setText("FTP配置有误，无法开始上传！")
                msg_box.setDetailedText(error_msg)
                msg_box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
                msg_box.exec() if hasattr(msg_box, 'exec') else msg_box.exec_()
                
                self._toast('FTP配置验证失败', 'danger')
                return
        
        # 2. 检查配置是否被修改但未保存
        if self.config_modified:
            self._append_log("⚠ 检测到配置已修改但未保存")
            
            # v2.2.0 权限检查：未登录用户无权保存配置，直接恢复已保存配置
            if self.current_role == 'guest':
                self._append_log("⚠ 未登录用户无权保存配置，自动恢复已保存的配置")
                if self.saved_config:
                    self.src_edit.setText(self.saved_config.get('source_folder', ''))
                    self.tgt_edit.setText(self.saved_config.get('target_folder', ''))
                    self.bak_edit.setText(self.saved_config.get('backup_folder', ''))
                    self.config_modified = False
                    self._append_log("✓ 配置已恢复到已保存状态")
                    
                    # 重新验证路径
                    is_valid, errors = self._validate_paths()
                    if not is_valid:
                        error_msg = "\n".join(errors)
                        self._append_log(f"❌ 已保存的配置路径验证失败:\n{error_msg}")
                        self._toast('配置路径无效，请联系管理员', 'danger')
                        return
                else:
                    self._append_log("❌ 未找到已保存的配置")
                    self._toast('无可用配置，请联系管理员', 'danger')
                    return
            else:
                # 登录用户：询问是否保存配置
                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setIcon(QtWidgets.QMessageBox.Icon.Question)
                msg_box.setWindowTitle("配置未保存")
                msg_box.setText("检测到路径配置已修改但未保存！")
                msg_box.setInformativeText('是否保存当前配置并使用新路径上传？\n\n选择"是"：保存配置并使用新路径\n选择"否"：放弃修改，使用已保存的路径')
                msg_box.setStandardButtons(
                    QtWidgets.QMessageBox.StandardButton.Yes | 
                    QtWidgets.QMessageBox.StandardButton.No |
                    QtWidgets.QMessageBox.StandardButton.Cancel
                )
                msg_box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Yes)
                
                result = msg_box.exec() if hasattr(msg_box, 'exec') else msg_box.exec_()
                
                if result == QtWidgets.QMessageBox.StandardButton.Yes:
                    # 保存配置
                    self._append_log("✓ 用户选择保存配置")
                    if not self._save_config():
                        self._append_log("✗ 配置保存失败，已取消开始上传")
                        return
                elif result == QtWidgets.QMessageBox.StandardButton.No:
                    # 回退到保存的配置
                    self._append_log("⚠ 用户选择放弃修改，恢复已保存的配置")
                    if self.saved_config:
                        self.src_edit.setText(self.saved_config.get('source_folder', ''))
                        self.tgt_edit.setText(self.saved_config.get('target_folder', ''))
                        self.bak_edit.setText(self.saved_config.get('backup_folder', ''))
                        self.config_modified = False
                        self._append_log("✓ 配置已恢复")
                        
                        # 重新验证路径
                        is_valid, errors = self._validate_paths()
                        if not is_valid:
                            error_msg = "\n".join(errors)
                            self._append_log(f"❌ 恢复的配置路径验证失败:\n{error_msg}")
                            self._toast('已保存的配置路径无效', 'danger')
                            return
                else:
                    # 取消
                    self._append_log("✗ 用户取消开始上传")
                    return
        
        self._append_log("✓ 配置验证通过，开始启动上传任务...")
        
        self.is_running = True
        self.is_paused = False
        self.start_time = time.time()
        self._update_status_pill()
        
        # v2.2.0 重构：使用统一权限系统更新所有控件状态
        self._update_ui_permissions()
        
        self._append_log(f"📋 上传配置:")
        self._append_log(f"  源文件夹: {self.src_edit.text()}")
        self._append_log(f"  目标文件夹: {self.tgt_edit.text()}")
        filters = [ext for ext, cb in self.cb_ext.items() if cb.isChecked()]
        
        # v2.1.1 修改：根据备份启用状态显示不同信息
        if self.enable_backup:
            self._append_log(f"  备份文件夹: {self.bak_edit.text()}")
        else:
            self._append_log(f"  备份功能: 已禁用（上传成功后将删除源文件）")
        self._append_log(f"  间隔时间: {self.spin_interval.value()}秒")
        self._append_log(f"  重试次数: {self.spin_retry.value()}次")
        self._append_log(f"  文件类型: {', '.join(filters)}")
        self._append_log(f"  上传协议: {self.current_protocol}")
        
        # v2.0 新增：启动FTP服务器（v3.1.0 重构：由独立开关控制）
        if self.enable_ftp_server:
            try:
                if not self.ftp_manager:
                    self.ftp_manager = FTPProtocolManager()  # type: ignore[misc]
                
                if self._is_ftp_server_running():
                    self._append_log("ℹ️ FTP服务器已在运行，上传任务不会重复启动")
                else:
                    self._append_log("🔧 正在启动FTP服务器...")
                    server_cfg = self._collect_ftp_server_config()
                    self.ftp_server_config = copy.deepcopy(server_cfg)
                    share_folder = server_cfg.get('shared_folder', '')
                    if not share_folder or not os.path.exists(share_folder):
                        raise ValueError(f"FTP共享文件夹无效: {share_folder}")
                    server_config = self._build_ftp_server_manager_config(server_cfg)
                    server_config['event_callback'] = self._emit_ftp_server_event

                    success = self.ftp_manager.start_server(server_config)
                    if not success:
                        raise RuntimeError("FTP服务器启动失败")
                    self._ftp_server_started_by_upload = True
                    self._ftp_server_started_independently = False

                    server_status = self.ftp_manager.get_status()
                    if server_status.get('server'):
                        srv = server_status['server']
                        self._append_log(f"✓ FTP服务器已启动:")
                        self._append_log(f"  地址: {srv['address']}")
                        self._append_log(f"  共享: {srv['shared_folder']}")
                    else:
                        self._append_log(f"✓ FTP服务器已启动")
                
                # v2.0 新增：更新FTP状态显示
                self._update_protocol_status()
            except ValueError as e:
                # v2.0 增强：配置错误详细日志
                self._append_log(f"❌ [FTP-CONFIG] 配置错误: {e}")
                self._toast(f'FTP配置错误: {e}', 'danger')
                # v2.2.0 修复：使用统一权限系统恢复UI
                self.is_running = False
                self._update_status_pill()
                self._update_ui_permissions()
                return
            except OSError as e:
                # v2.0 增强：端口冲突等系统错误详细日志
                error_msg = str(e)
                if 'already in use' in error_msg.lower() or 'address already in use' in error_msg.lower():
                    port = server_cfg.get('port', 2121)
                    self._append_log(f"❌ [FTP-PORT] 端口 {port} 已被占用，请更换端口")
                else:
                    self._append_log(f"❌ [FTP-OS] 系统错误: {e}")
                self._toast(f'FTP服务器启动失败: {e}', 'danger')
                # v2.2.0 修复：使用统一权限系统恢复UI
                self.is_running = False
                self._update_status_pill()
                self._update_ui_permissions()
                return
            except Exception as e:
                # v2.0 增强：其他错误详细日志
                error_type = type(e).__name__
                self._append_log(f"❌ [FTP-{error_type}] FTP服务器启动失败: {e}")
                self._toast(f'FTP服务器启动失败: {e}', 'danger')
                # v2.2.0 修复：使用统一权限系统恢复UI
                self.is_running = False
                self._update_status_pill()
                self._update_ui_permissions()
                return
        
        # 获取去重策略映射
        strategy_map = {'跳过': 'skip', '重命名': 'rename', '覆盖': 'overwrite', '询问': 'ask'}
        duplicate_strategy = strategy_map.get(self.combo_strategy.currentText(), 'ask')
        
        # v2.0 新增：更新FTP客户端配置
        if self.current_protocol in ['ftp_client', 'both']:
            self.ftp_client_config = self._collect_ftp_client_config()
            self._append_log(f"📡 FTP客户端配置: {self.ftp_client_config['host']}:{self.ftp_client_config['port']}")
            self._append_log(f"  超时时间: {self.ftp_client_config['timeout']}秒, 重试次数: {self.ftp_client_config['retry_count']}次")
        
        self.worker = UploadWorker(
            self.src_edit.text(), self.tgt_edit.text(), self.bak_edit.text(),
            self.spin_interval.value(), 'periodic', self.spin_disk.value(), self.spin_retry.value(), 
            filters, self.app_dir,
            self.cb_dedup_enable.isChecked(),
            self.combo_hash.currentText().lower(),
            duplicate_strategy,
            self.spin_network_check.value(),
            self.cb_network_auto_pause.isChecked(),
            self.cb_network_auto_resume.isChecked(),
            # v1.9 新增：自动删除参数
            self.enable_auto_delete,
            self.auto_delete_threshold,
            self.auto_delete_target_percent,
            # v2.0 新增：协议参数
            self.current_protocol,
            self.ftp_client_config if self.current_protocol in ['ftp_client', 'both'] else None,
            # v2.2.0 新增：备份启用状态
            self.enable_backup,
            # v2.3.0 新增：速率限制参数
            self.cb_limit_rate.isChecked(),
            self.spin_max_rate.value()
        )
        self.worker_thread = QtCore.QThread(self)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.start)
        # 使用 Qt.QueuedConnection 确保信号异步处理，不阻塞 Worker 线程
        _queued = QtCore.Qt.ConnectionType.QueuedConnection
        self.worker.log.connect(self._append_log, _queued)  # type: ignore[call-arg]
        self.worker.stats.connect(self._on_stats, _queued)  # type: ignore[call-arg]
        self.worker.progress.connect(self._on_progress, _queued)  # type: ignore[call-arg]
        self.worker.file_progress.connect(self._on_file_progress, _queued)  # type: ignore[call-arg]
        self.worker.network_status.connect(self._on_network_status, _queued)  # type: ignore[call-arg]
        self.worker.finished.connect(self._on_worker_finished, _queued)  # type: ignore[call-arg]
        self.worker.status.connect(self._on_worker_status, _queued)  # type: ignore[call-arg]
        self.worker.ask_user_duplicate.connect(self._on_ask_duplicate, _queued)  # type: ignore[call-arg]
        # v2.2.0 新增：连接错误通知信号
        self.worker.upload_error.connect(self._on_upload_error, _queued)  # type: ignore[call-arg]
        # v2.2.0 新增：连接磁盘空间警告信号
        self.worker.disk_warning.connect(self._on_disk_warning, _queued)  # type: ignore[call-arg]
        # v3.3.0 新增：Worker 磁盘不足时通知主窗口执行统一清理
        self.worker.disk_cleanup_needed.connect(self._on_worker_disk_cleanup_needed, _queued)  # type: ignore[call-arg]
        # 上传期间停止定时器清理，改由 Worker 信号触发
        self._auto_cleanup_timer.stop()
        self.worker_thread.start()
        self._toast('开始上传', 'success')
        self._append_log("✓ 上传任务已启动")
        
        # v2.2.0 新增：显示通知
        self._show_notification(
            "上传已开始",
            f"正在上传文件到: {self.tgt_edit.text()}"
        )

    def _on_pause_resume(self):
        if not self.worker:
            return
        if self.is_paused:
            # 恢复上传
            self.is_paused = False
            self.worker.resume()
            self.btn_pause.setText("⏸ 暂停上传")
            self._toast('已恢复', 'info')
            # v2.2.0 系统托盘通知
            self._show_notification(
                "上传已恢复",
                "继续上传任务..."
            )
        else:
            # 暂停上传
            self.is_paused = True
            self.worker.pause()
            self.btn_pause.setText("▶ 恢复上传")
            self._toast('已暂停', 'warning')
            # v2.2.0 系统托盘通知
            self._show_notification(
                "上传已暂停",
                f"已上传: {self.uploaded}个文件"
            )
        self._update_status_pill()

    def _on_stop(self):
        """停止上传"""
        self._append_log("🛑 正在停止上传任务...")
        
        # v2.2.0 关键修复：立即设置运行状态为False
        self.is_running = False
        self.is_paused = False
        
        # v2.0 新增：停止由上传任务启动的FTP服务器；独立启动的服务器不受上传停止影响
        if self.ftp_manager and self._ftp_server_started_by_upload and not self._ftp_server_started_independently:
            try:
                self._append_log("🔧 正在停止FTP服务...")
                self.ftp_manager.stop_server()
                self._ftp_server_started_by_upload = False
                self._append_log("✓ FTP服务已停止")
                
                # v2.0 新增：更新FTP状态显示
                self._update_protocol_status()
            except Exception as e:
                self._append_log(f"⚠️ 停止FTP服务时出错: {e}")
        
        if not self.worker:
            # 没有Worker，直接恢复UI
            self._restore_ui_after_stop()
            return
        
        self.worker.stop()
        # 立即恢复UI（不等待线程完全退出，提升响应速度）
        self._restore_ui_after_stop()

        # 异步清理后台线程，避免阻塞主线程
        def _cleanup_worker_async():
            try:
                if self.worker_thread:
                    self.worker_thread.quit()
                    if not self.worker_thread.wait(3000):
                        self._append_log("⚠️ Worker线程未在预期时间内退出，尝试强制终止")
                        try:
                            self.worker_thread.terminate()
                            self.worker_thread.wait(1000)
                        except Exception:
                            pass
            finally:
                self.worker = None
                self.worker_thread = None
        
        try:
            QtCore.QTimer.singleShot(0, _cleanup_worker_async)
        except Exception:
            # 如果计时器不可用，直接在当前线程做一次尽力清理（可能会阻塞片刻）
            _cleanup_worker_async()
    
    def _restore_ui_after_stop(self):
        """恢复停止后的UI状态"""
        states = self._compute_control_states(self.current_role, self.is_running, self.enable_backup)
        
        # 应用状态
        self.src_edit.setReadOnly(states['src_edit_readonly'])
        self.tgt_edit.setReadOnly(states['tgt_edit_readonly'])
        self.bak_edit.setReadOnly(states['bak_edit_readonly'])

        if hasattr(self, 'btn_choose_src'):
            self.btn_choose_src.setEnabled(states['btn_choose_src'])
        if hasattr(self, 'btn_choose_tgt'):
            self.btn_choose_tgt.setEnabled(states['btn_choose_tgt'])
        if hasattr(self, 'btn_choose_bak'):
            self.btn_choose_bak.setEnabled(states['btn_choose_bak'])

        # 关键：停止后“开始”立刻可点（不受角色限制）
        self.btn_start.setEnabled(states['btn_start'])
        self.btn_pause.setEnabled(states['btn_pause'])
        self.btn_pause.setText("⏸ 暂停上传")
        self.btn_stop.setEnabled(states['btn_stop'])
        
        # 重置进度显示
        self.pbar.setValue(0)
        self.pbar_file.setValue(0)
        self.pbar_file.setFormat("等待...")
        self.lbl_current_file.setText("等待开始...")
        self.lbl_progress.setText("已停止")
        self._update_status_pill()
        
        # 统一再走一遍权限更新逻辑，确保一致（会重复应用但保证同步）
        try:
            self._update_ui_permissions()
        except Exception:
            pass
        
        actual_tgt = self.btn_choose_tgt.isEnabled() if hasattr(self, 'btn_choose_tgt') else None
        actual_src = self.btn_choose_src.isEnabled() if hasattr(self, 'btn_choose_src') else None
        logger.debug(f"[停止后实际] 源按钮={actual_src}, 目标按钮={actual_tgt}")
        
        if actual_tgt is not None and actual_tgt != states['btn_choose_tgt']:
            logger.warning(f"停止后目标按钮状态不一致！计算={states['btn_choose_tgt']}, 实际={actual_tgt}")
        
        self._toast('已停止', 'danger')
        self._append_log("✓ 上传任务已停止")
        self._append_log("=" * 50)
        
        # v2.2.0 系统托盘通知
        self._show_notification(
            "上传已停止",
            f"已上传: {self.uploaded}个 | 失败: {self.failed}个 | 跳过: {self.skipped}个"
        )

    def _on_stats(self, uploaded: int, failed: int, skipped: int, rate: str):
        # v2.2.0 保存统计数据
        self.uploaded = uploaded
        self.failed = failed
        self.skipped = skipped
        
        self.lbl_uploaded.setValue(str(uploaded))
        self.lbl_failed.setValue(str(failed))
        self.lbl_skipped.setValue(str(skipped))
        
        # v2.0 增强：速率显示添加协议图标
        protocol_icons = {
            'smb': '📁',
            'ftp_server': '🖥️',
            'ftp_client': '📤',
            'both': '🔄'
        }
        icon = protocol_icons.get(self.current_protocol, '📁')
        self.lbl_rate.setValue(f"{icon} {rate}")

    def _on_progress(self, current: int, total: int, filename: str):
        self.pbar.setValue(0 if total <= 0 else int(100*current/max(1,total)))
        eta = "--:--"
        remaining_count = total - current
        if self.start_time and current>0 and total>0:
            elapsed = max(time.time()-self.start_time, 0.001)
            remain = int(elapsed * (total-current)/current)
            h, remainder = divmod(remain, 3600)
            m, s = divmod(remainder, 60)
            if h > 0:
                eta = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                eta = f"{m:02d}:{s:02d}"
        prefix = f"总进度 {self.pbar.value()}%"
        suffix = f"  剩余 {remaining_count} 个文件  预计 {eta}" if total>0 else ""
        self.lbl_progress.setText(prefix + suffix)
    
    def _on_file_progress(self, filename: str, progress: int):
        """更新当前文件的进度"""
        # 截断过长的文件名
        display_name = filename
        if len(filename) > 50:
            display_name = filename[:25] + "..." + filename[-22:]
        
        self.lbl_current_file.setText(f"{display_name}")
        self.pbar_file.setValue(progress)
        
        # 小幅度刷新速率显示：当有进度时给出“上传中...”提示，避免长时间保持旧速率
        try:
            if 0 < progress < 100:
                self.lbl_rate.setValue("上传中...")
        except Exception:
            pass
        
        if progress == 0:
            self.pbar_file.setFormat("准备上传...")
        elif progress == 100:
            self.pbar_file.setFormat("✓ 完成")
        else:
            self.pbar_file.setFormat(f"{progress}%")

    def _on_ask_duplicate(self, payload: dict):
        """在主线程弹窗询问重复文件处理策略。payload 结构:
        {'file': str, 'duplicate': str, 'event': threading.Event, 'result': dict}
        """
        try:
            src = payload.get('file', '')
            dup = payload.get('duplicate', '')
            evt = payload.get('event')
            result = payload.get('result')

            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("发现重复文件")
            dialog.setModal(True)
            dialog.resize(self._clamped_dialog_size(560, 300))

            # 提升选中可见性：为单选项添加显著的选中背景/边框和更大的指示器，并统一主按钮样式
            dialog.setStyleSheet(
                """
                QDialog{background:#FAFAFA;}
                QLabel{font-size:13px;}
                QRadioButton{
                    padding:8px 12px;
                    border-radius:8px;
                    margin:2px 0;
                }
                QRadioButton:hover{background:#F5F5F5;}
                QRadioButton:checked{
                    background:#E3F2FD;
                    border:2px solid #1976D2;
                    font-weight:600;
                }
                QRadioButton::indicator{
                    width:18px; height:18px; margin-right:8px;
                }
                QRadioButton::indicator:unchecked{
                    border:2px solid #90A4AE; border-radius:9px; background:transparent;
                }
                QRadioButton::indicator:checked{
                    border:6px solid #1976D2; border-radius:9px; background:#1976D2;
                }
                QCheckBox{margin-top:8px;}
                QPushButton[class="Primary"]{
                    background:#1976D2; color:white; padding:6px 14px; border:none; border-radius:6px;
                }
                QPushButton[class="Primary"]:hover{background:#1565C0;}
                QPushButton[class="Primary"]:pressed{background:#0D47A1;}
                """
            )

            v = QtWidgets.QVBoxLayout(dialog)
            lab = QtWidgets.QLabel("检测到重复文件，请选择处理方式：")
            lab.setWordWrap(True)
            v.addWidget(lab)

            def short(p: str) -> str:
                return p if len(p) <= 90 else (p[:42] + "..." + p[-42:])
            v.addWidget(QtWidgets.QLabel(f"源文件：{short(src)}"))
            v.addWidget(QtWidgets.QLabel(f"目标已有：{short(dup)}"))

            group = QtWidgets.QButtonGroup(dialog)
            rb_skip = QtWidgets.QRadioButton("⏭ 跳过（不上传，直接归档源文件）")
            rb_rename = QtWidgets.QRadioButton("📝 重命名后上传（保留两份）")
            rb_overwrite = QtWidgets.QRadioButton("⚠ 覆盖已有文件（谨慎）")
            rb_skip.setChecked(True)
            rb_skip.setFocus()
            for rb in (rb_skip, rb_rename, rb_overwrite):
                group.addButton(rb)
                v.addWidget(rb)

            cb_apply = QtWidgets.QCheckBox("对后续重复文件使用同一选择")
            v.addWidget(cb_apply)

            row = QtWidgets.QHBoxLayout()
            row.addStretch(1)
            btn_cancel = QtWidgets.QPushButton("取消")
            btn_cancel.setProperty("class", "Secondary")
            btn_cancel.clicked.connect(dialog.reject)
            btn_ok = QtWidgets.QPushButton("确定")
            btn_ok.setProperty("class", "Primary")
            btn_ok.setDefault(True)
            row.addWidget(btn_cancel)
            row.addWidget(btn_ok)
            v.addLayout(row)

            # 键盘导航顺序：单选项 -> 确定 -> 取消
            try:
                QtWidgets.QDialog.setTabOrder(rb_skip, rb_rename)
                QtWidgets.QDialog.setTabOrder(rb_rename, rb_overwrite)
                QtWidgets.QDialog.setTabOrder(rb_overwrite, btn_ok)
                QtWidgets.QDialog.setTabOrder(btn_ok, btn_cancel)
            except Exception:
                pass

            def done(ok: bool):
                try:
                    choice = 'skip'
                    if rb_rename.isChecked():
                        choice = 'rename'
                    elif rb_overwrite.isChecked():
                        choice = 'overwrite'
                    if isinstance(result, dict):
                        result['choice'] = choice
                        result['apply_all'] = cb_apply.isChecked()
                finally:
                    dialog.accept() if ok else dialog.reject()
                    try:
                        if evt:
                            evt.set()
                    except Exception:
                        pass

            btn_cancel.clicked.connect(lambda: done(False))
            btn_ok.clicked.connect(lambda: done(True))

            dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()
        except Exception:
            try:
                if isinstance(payload.get('result'), dict):
                    payload['result']['choice'] = 'skip'
                    payload['result']['apply_all'] = False
            finally:
                if payload.get('event'):
                    try:
                        payload['event'].set()
                    except Exception:
                        pass
    
    def _on_network_status(self, status: str):
        """更新网络状态显示"""
        if status == 'good':
            self.lbl_network.setValue("🟢 正常")
            # 更新芯片样式为绿色
            self.lbl_network.setStyleSheet("QFrame{background:#E8F5E9; border-radius:8px;} QLabel{color:#2E7D32;}")
            self.network_status = 'good'
        elif status == 'unstable':
            self.lbl_network.setValue("🟡 不稳定")
            # 更新芯片样式为黄色
            self.lbl_network.setStyleSheet("QFrame{background:#FFF9C4; border-radius:8px;} QLabel{color:#F57F17;}")
            self.network_status = 'unstable'
        elif status == 'disconnected':
            self.lbl_network.setValue("🔴 已断开")
            # 更新芯片样式为红色
            self.lbl_network.setStyleSheet("QFrame{background:#FFEBEE; border-radius:8px;} QLabel{color:#C62828;}")
            self.network_status = 'disconnected'

    def _on_worker_status(self, s: str):
        if s == 'running':
            self.is_running = True
            self.is_paused = False
        elif s == 'paused':
            self.is_paused = True
        elif s == 'stopped':
            self.is_running = False
            self.is_paused = False
        self._update_status_pill()

    def _on_worker_finished(self):
        # v2.2.0 系统托盘通知：上传任务完成
        if self.uploaded > 0 or self.failed > 0:
            self._show_notification(
                "上传任务完成",
                f"成功: {self.uploaded}个 | 失败: {self.failed}个 | 跳过: {self.skipped}个"
            )
        # keep thread objects for GC safety
        pass
        # v3.3.0：Worker 结束后恢复定时清理
        self._update_auto_cleanup_schedule()
    
    def _on_upload_error(self, filename: str, error_message: str):
        """v2.2.0 处理上传错误通知"""
        # 限制错误通知频率（每个文件只通知一次最新错误）
        if not hasattr(self, '_error_notified_files'):
            self._error_notified_files = set()
        
        if filename not in self._error_notified_files:
            self._error_notified_files.add(filename)
            # 截断过长的错误信息
            short_error = error_message[:50] + '...' if len(error_message) > 50 else error_message
            self._show_notification(
                "上传错误",
                f"{filename}: {short_error}",
                icon_type=get_qt_enum(QtWidgets.QSystemTrayIcon, 'Warning', 2)
            )
        
        # 定期清理已通知文件集合（避免内存泄漏）
        if len(self._error_notified_files) > 100:
            self._error_notified_files.clear()
    
    def _on_disk_warning(self, target_percent: float, backup_percent: float, threshold: int):
        """v2.2.0 处理磁盘空间警告通知"""
        self._show_notification(
            "磁盘空间不足",
            f"目标: {target_percent:.0f}% | 备份: {backup_percent:.0f}% | 阈值: {threshold}%",
            icon_type=get_qt_enum(QtWidgets.QSystemTrayIcon, 'Warning', 2)
        )
        self._maybe_trigger_auto_cleanup("磁盘空间不足")

    def _log_message(self, message: str):
        self._append_log(message)

    def _append_log(self, line: str): 
        # If autoscroll is disabled, preserve the current scrollbar position.
        try:
            vsb = self.log.verticalScrollBar()
            prev = vsb.value()
        except Exception:
            vsb = None
            prev = None

        # 添加时间戳
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {line}"
        
        # Append the new line to UI
        self.log.appendPlainText(log_line)
        
        # Write to log file
        self._write_log_to_file(line)

        # Decide scrolling behaviour
        if self.cb_autoscroll.isChecked():
            move_enum = getattr(QtGui.QTextCursor, 'MoveOperation', QtGui.QTextCursor)
            self.log.moveCursor(getattr(move_enum, 'End'))
            if vsb is not None:
                vsb.setValue(vsb.maximum())
        else:
            # restore previous scrollbar position if possible
            if vsb is not None and prev is not None:
                # keep the view where it was before appending
                vsb.setValue(prev)
    
    def _write_log_to_file(self, line: str):
        """将日志写入文件（异步，不阻塞主线程）"""
        if self.log_file_path is None:
            return
        
        # 保存当前日志文件路径（避免在线程中访问 self）
        current_log_path = self.log_file_path
        app_dir = self.app_dir
        
        def write_log():
            try:
                # 检查日期是否变更
                today = datetime.datetime.now().strftime('%Y-%m-%d')
                expected_filename = f'upload_{today}.txt'
                
                log_path = current_log_path
                if log_path.name != expected_filename:
                    # 创建新的日志文件
                    log_dir = app_dir / "logs"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    log_path = log_dir / expected_filename
                
                # 写入日志（带时间戳）
                timestamp = datetime.datetime.now().strftime('%H:%M:%S')
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"[{timestamp}] {line}\n")
            except Exception as e:
                # 静默失败，不影响程序运行
                print(f"写入日志文件失败: {e}")
        
        # 提交到线程池异步执行
        try:
            self._log_executor.submit(write_log)
        except Exception:
            # 线程池关闭或其他问题，静默失败
            pass

    def _update_status_pill(self):
        if self.is_paused:
            self.lbl_status.setText("🟡 已暂停")
            self.lbl_status.setStyleSheet("background:#FEF9C3; color:#A16207; padding:4px 10px; font-weight:700; border-radius:12px;")
        elif self.is_running:
            self.lbl_status.setText("🟢 运行中")
            self.lbl_status.setStyleSheet("background:#DCFCE7; color:#166534; padding:4px 10px; font-weight:700; border-radius:12px;")
        else:
            self.lbl_status.setText("🔴 已停止")
            self.lbl_status.setStyleSheet("background:#FEE2E2; color:#B91C1C; padding:4px 10px; font-weight:700; border-radius:12px;")
    
    def _update_protocol_status(self):
        """更新协议和FTP状态显示 (v3.1.0 重构)"""
        # 更新协议模式芯片
        protocol_names = {
            'smb': 'SMB',
            'ftp_client': 'FTP客户端',
            'both': 'SMB+FTP'
        }
        protocol_text = protocol_names.get(self.current_protocol, 'SMB')
        self.lbl_protocol.setValue(protocol_text)
        
        # v3.1.0: 更新当前模式芯片（醒目显示）
        protocol_index = {'smb': 0, 'ftp_client': 1, 'both': 2}.get(self.current_protocol, 0)
        self._update_mode_chip(protocol_index)
        
        # 更新FTP服务器状态（由独立开关控制，不依赖协议）
        if self.enable_ftp_server:
            if self.ftp_manager and self.ftp_manager.server:
                try:
                    # 直接从FTPServerManager获取状态
                    server_info = self.ftp_manager.server.get_status()
                    if server_info.get('running'):
                        connections = server_info.get('connections', 0)
                        # 显示连接数，如果有连接则用绿色高亮
                        if connections > 0:
                            self.lbl_ftp_server.setValue(f"🟢 运行中 ({connections}个连接)")
                        else:
                            self.lbl_ftp_server.setValue("🟢 运行中 (0)")
                        self.lbl_ftp_server.setStyleSheet(
                            "background:#DCFCE7; color:#166534; padding:4px 8px; border-radius:4px; font-size:9pt; font-weight:500;"
                        )
                    else:
                        self.lbl_ftp_server.setValue("🔴 已停止")
                        self.lbl_ftp_server.setStyleSheet(
                            "background:#FEE2E2; color:#B91C1C; padding:4px 8px; border-radius:4px; font-size:9pt;"
                        )
                except AttributeError:
                    # 预期异常：服务器对象可能已销毁
                    self.lbl_ftp_server.setValue("⚪ 未启动")
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
                except Exception as e:
                    # 意外异常：记录日志并显示状态异常
                    logger.error(f"FTP服务器状态获取异常: {type(e).__name__}: {e}")
                    self.lbl_ftp_server.setValue("⚠️ 状态异常")
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#FEE2E2; color:#B91C1C; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
            else:
                self.lbl_ftp_server.setValue("⚪ 未启动")
                self.lbl_ftp_server.setStyleSheet(
                    "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
                )
        else:
            self.lbl_ftp_server.setValue("⚫ --")
            self.lbl_ftp_server.setStyleSheet(
                "background:#F5F5F5; color:#9E9E9E; padding:4px 8px; border-radius:4px; font-size:9pt;"
            )
        if hasattr(self, 'btn_toggle_ftp_server'):
            self.btn_toggle_ftp_server.setText(
                t('stop_ftp_server') if self._is_ftp_server_running() else t('start_ftp_server')
            )
        
        # 更新FTP客户端状态（含图标指示器）
        if self.current_protocol in ['ftp_client', 'both']:
            if self.worker and hasattr(self.worker, 'ftp_client') and self.worker.ftp_client:
                try:
                    client_status = self.worker.ftp_client.get_status()
                    if client_status.get('connected'):
                        host = client_status.get('host', '')
                        self.lbl_ftp_client.setValue(f"🟢 已连接 ({host})")
                        self.lbl_ftp_client.setStyleSheet(
                            "background:#DCFCE7; color:#166534; padding:4px 8px; border-radius:4px; font-size:9pt; font-weight:500;"
                        )
                    else:
                        self.lbl_ftp_client.setValue("🟡 未连接")
                        self.lbl_ftp_client.setStyleSheet(
                            "background:#FEF9C3; color:#A16207; padding:4px 8px; border-radius:4px; font-size:9pt;"
                        )
                except AttributeError:
                    # 预期异常：客户端对象可能已销毁
                    self.lbl_ftp_client.setValue("⚪ 未连接")
                    self.lbl_ftp_client.setStyleSheet(
                        "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
                except Exception as e:
                    # 意外异常：记录日志并显示状态异常
                    logger.error(f"FTP客户端状态获取异常: {type(e).__name__}: {e}")
                    self.lbl_ftp_client.setValue("⚠️ 状态异常")
                    self.lbl_ftp_client.setStyleSheet(
                        "background:#FEE2E2; color:#B91C1C; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
            else:
                self.lbl_ftp_client.setValue("⚪ 未连接")
                self.lbl_ftp_client.setStyleSheet(
                    "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
                )
        else:
            self.lbl_ftp_client.setValue("⚫ --")
            self.lbl_ftp_client.setStyleSheet(
                "background:#F5F5F5; color:#9E9E9E; padding:4px 8px; border-radius:4px; font-size:9pt;"
            )

    def _toast(self, msg: str, kind: str = 'info'):
        t = Toast(self.window(), msg, kind)
        t.show()

    def _tick(self):
        # 运行时间更新
        if self.is_running and self.start_time:
            elapsed = int(time.time() - self.start_time)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            t = f"{h:02d}:{m:02d}:{s:02d}"
            self.lbl_time.setValue(t)
        
        # 归档队列大小刷新（近似值即可）
        try:
            if self.worker is not None and hasattr(self.worker, 'archive_queue'):
                qsize = self.worker.archive_queue.qsize()
                self.lbl_queue.setValue(str(qsize))
        except Exception:
            pass
        
        # 磁盘空间更新（根据配置的间隔）
        self.disk_check_counter += 1
        # 每0.5秒tick一次，所以需要 interval * 2 次tick
        if self.disk_check_counter >= self.disk_check_interval * 2:
            self.disk_check_counter = 0
            self._update_disk_space()
        
        # v2.0 新增：更新协议和FTP状态
        self._update_protocol_status()

    def _update_disk_space(self):
        """更新磁盘剩余空间显示（异步，不阻塞主线程）"""
        target_path = self.tgt_edit.text()
        backup_path = self.bak_edit.text()
        
        def _is_network_path(p: str) -> bool:
            try:
                if not p:
                    return False
                # UNC 路径（例如 \\server\share\folder ）直接视为网络路径
                if p.startswith('\\\\'):
                    return True
                # 驱动器盘符判断网络映射盘（使用 Win32 API GetDriveType）
                drive, _ = os.path.splitdrive(p)
                if not drive:
                    return False
                root = drive + '\\'
                try:
                    import ctypes
                    DRIVE_REMOTE = 4
                    GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW
                    GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
                    GetDriveTypeW.restype = ctypes.c_uint
                    dtype = GetDriveTypeW(root)
                    return dtype == DRIVE_REMOTE
                except Exception:
                    return False
            except Exception:
                return False
        
        def update_disk_async():
            try:
                # 更新目标磁盘
                if target_path:
                    # 网络路径仅在网络正常时尝试读取空间，否则显示"--"
                    if _is_network_path(target_path) and getattr(self, 'network_status', 'unknown') != 'good':
                        self._disk_update_signal.emit("target", -1.0)
                    else:
                        try:
                            if os.path.exists(target_path):
                                usage = shutil.disk_usage(target_path)
                                free_percent = (usage.free / usage.total) * 100 if usage.total > 0 else 0
                                self._disk_update_signal.emit("target", free_percent)
                            else:
                                self._disk_update_signal.emit("target", -1.0)
                        except Exception:
                            self._disk_update_signal.emit("target", -1.0)
                
                # 更新归档磁盘
                if self.enable_backup and backup_path:
                    if _is_network_path(backup_path) and getattr(self, 'network_status', 'unknown') != 'good':
                        self._disk_update_signal.emit("backup", -1.0)
                    else:
                        try:
                            if os.path.exists(backup_path):
                                usage = shutil.disk_usage(backup_path)
                                free_percent = (usage.free / usage.total) * 100 if usage.total > 0 else 0
                                self._disk_update_signal.emit("backup", free_percent)
                            else:
                                self._disk_update_signal.emit("backup", -1.0)
                        except Exception:
                            self._disk_update_signal.emit("backup", -1.0)
                else:
                    self._disk_update_signal.emit("backup", -1.0)
            except Exception as e:
                self._emit_async_log(f"磁盘空间检查失败: {e}")
        
        # 提交到线程池异步执行
        try:
            self._disk_executor.submit(update_disk_async)
        except Exception:
            pass

    @staticmethod
    def _cleanup_volume_identity(path: str) -> Tuple[str, str]:
        """返回用于同盘校验的卷标识和用户可读名称。"""
        normalized = os.path.abspath(path)
        drive, _ = os.path.splitdrive(normalized)
        fallback = os.path.normcase(drive or Path(normalized).anchor or normalized)
        display = drive or Path(normalized).anchor or normalized

        if os.name != 'nt':
            return fallback, display

        try:
            volume_path = ctypes.create_unicode_buffer(32768)
            get_volume_path = ctypes.windll.kernel32.GetVolumePathNameW
            get_volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_path.restype = wintypes.BOOL
            if not get_volume_path(normalized, volume_path, len(volume_path)):
                return fallback, display

            volume_root = volume_path.value
            volume_name = ctypes.create_unicode_buffer(32768)
            get_volume_name = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW
            get_volume_name.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_volume_name.restype = wintypes.BOOL
            if get_volume_name(volume_root, volume_name, len(volume_name)):
                return os.path.normcase(volume_name.value), volume_root
            return os.path.normcase(volume_root), volume_root
        except Exception:
            return fallback, display

    @classmethod
    def _validate_cleanup_folder_group(
        cls,
        folders: Iterable[str],
    ) -> Tuple[bool, str, List[Tuple[str, str]]]:
        details: List[Tuple[str, str]] = []
        identities = set()
        for folder in folders:
            identity, display = cls._cleanup_volume_identity(folder)
            identities.add(identity)
            details.append((folder, display))
        if len(identities) > 1:
            mapping = "；".join(f"{path} -> {volume}" for path, volume in details)
            return False, f"自动清理目录必须位于同一磁盘：{mapping}", details
        return True, "", details

    @staticmethod
    def _deduplicate_cleanup_roots(folders: Iterable[str]) -> List[str]:
        """去除重复和被父目录覆盖的监测根目录。"""
        candidates: List[Tuple[str, str]] = []
        seen = set()
        for folder in folders:
            absolute = os.path.abspath(folder)
            normalized = os.path.normcase(os.path.realpath(absolute))
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((absolute, normalized))

        candidates.sort(key=lambda item: (len(Path(item[1]).parts), item[1]))
        result: List[Tuple[str, str]] = []
        for absolute, normalized in candidates:
            covered = False
            for _, parent_normalized in result:
                try:
                    if os.path.commonpath([parent_normalized, normalized]) == parent_normalized:
                        covered = True
                        break
                except ValueError:
                    continue
            if not covered:
                result.append((absolute, normalized))
        return [absolute for absolute, _ in result]

    def _write_cleanup_audit(self, event: str, run_id: str, **fields: Any) -> bool:
        """顺序写入一条清理审计；调用方可据返回值停止危险操作。"""
        record = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "event": event,
            **fields,
        }
        app_dir = self.app_dir

        def write_record() -> None:
            logs_dir = app_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            log_path = logs_dir / f"cleanup_{today}.log"
            with open(log_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        try:
            future = self._log_executor.submit(write_record)
            future.result()
            return True
        except Exception as exc:
            message = f"清理审计日志写入失败，自动清理将停止: {type(exc).__name__}: {exc}"
            logger.error(message)
            try:
                self._emit_async_log(f"❌ {message}")
            except Exception:
                pass
            return False

    def _get_auto_cleanup_folders(self) -> List[str]:
        folders: List[str] = []
        if isinstance(self.auto_delete_folders, list):
            for path in self.auto_delete_folders:
                if isinstance(path, str):
                    path = path.strip()
                    if path:
                        folders.append(path)
        if not folders and self.auto_delete_folder:
            path = self.auto_delete_folder.strip()
            if path:
                folders.append(path)
        seen = set()
        result = []
        for path in folders:
            if path and path not in seen:
                seen.add(path)
                result.append(path)
        return result

    @staticmethod
    def _sort_cleanup_candidates(
        file_infos: Iterable[Tuple[float, int, str]],
    ) -> List[Tuple[float, int, str]]:
        normalized = [
            (float(mtime), max(0, int(size)), path)
            for mtime, size, path in file_infos
        ]
        normalized.sort(key=lambda item: (item[0], os.path.normcase(os.path.abspath(item[2]))))
        return normalized

    @staticmethod
    def _select_cleanup_candidates(
        file_infos: Iterable[Tuple[float, int, str]],
        bytes_to_free: int,
    ) -> Tuple[List[Tuple[float, int, str]], int]:
        """兼容旧调用：从全局有序列表中选出预计足够释放空间的前缀。"""
        if bytes_to_free <= 0:
            return [], 0

        ordered = MainWindow._sort_cleanup_candidates(file_infos)
        retained_size = 0
        candidates: List[Tuple[float, int, str]] = []
        for item in ordered:
            candidates.append(item)
            retained_size += item[1]
            if retained_size >= bytes_to_free:
                break
        return candidates, len(ordered)

    def _record_cleanup_blocked(
        self,
        status: str,
        trigger_source: str,
        folders: List[str],
        error: str,
    ) -> None:
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self._write_cleanup_audit(
            "START",
            run_id,
            trigger_source=trigger_source,
            folders=folders,
            disk=None,
            used_percent=None,
            trigger_percent=self.auto_delete_threshold,
            target_percent=self.auto_delete_target_percent,
            delete_mode="回收站" if getattr(self, 'auto_delete_use_trash', True) else "永久删除",
            time_basis="st_mtime",
        )
        self._write_cleanup_audit(
            "END",
            run_id,
            status=status,
            error=error,
            scanned_count=0,
            deleted_count=0,
            failed_count=0,
            start_used_percent=None,
            final_used_percent=None,
            actual_released_bytes=0,
        )

    def _on_worker_disk_cleanup_needed(self) -> None:
        """Worker 检测到磁盘不足时触发主窗口统一清理引擎。"""
        self._maybe_trigger_auto_cleanup("Worker检测到磁盘空间不足", trigger_source="worker")

    def _submit_auto_cleanup(self, trigger_source: str) -> bool:
        """原子地标记并提交清理任务；提交失败时恢复运行状态。"""
        if getattr(self, '_is_closing', False):
            return False
        with self._auto_cleanup_lock:
            if self._auto_cleanup_running:
                return False
            self._auto_cleanup_running = True
            cancel_event = getattr(self, '_auto_cleanup_cancel_event', None)
            if cancel_event is not None:
                cancel_event.clear()
        try:
            self._cleanup_executor.submit(self._auto_cleanup_task, trigger_source)
            return True
        except Exception as exc:
            with self._auto_cleanup_lock:
                self._auto_cleanup_running = False
            folders = self._get_auto_cleanup_folders()
            error = f"自动清理任务提交失败: {type(exc).__name__}: {exc}"
            self._append_log(f"❌ {error}")
            self._record_cleanup_blocked("任务异常", trigger_source, folders, error)
            return False

    def _maybe_trigger_auto_cleanup(self, reason: str = "", trigger_source: str = "disk_warning") -> None:
        if not self.enable_auto_delete:
            return
        folders = self._get_auto_cleanup_folders()
        if not folders:
            return
        if getattr(self, 'auto_delete_use_trash', True) and not trash_supported():
            return
        invalid_folders = [path for path in folders if not os.path.isdir(path)]
        if invalid_folders:
            error = f"自动清理路径不可用: {'; '.join(invalid_folders)}"
            self._append_log(f"⚠️ {error}")
            self._record_cleanup_blocked("路径不可用", trigger_source, folders, error)
            return
        group_valid, group_error, _ = self._validate_cleanup_folder_group(folders)
        if not group_valid:
            self._append_log(f"⚠️ {group_error}，自动清理已停止")
            self._record_cleanup_blocked("跨盘配置无效", trigger_source, folders, group_error)
            return
        try:
            usage = shutil.disk_usage(folders[0])
        except Exception as exc:
            error = f"无法获取自动清理磁盘信息: {exc}"
            self._append_log(f"⚠️ {error}")
            self._record_cleanup_blocked("路径不可用", trigger_source, folders, error)
            return
        used_percent = ((usage.total - usage.free) / usage.total) * 100 if usage.total > 0 else 0.0
        if used_percent < self.auto_delete_threshold:
            return
        if not self._submit_auto_cleanup(trigger_source):
            return
        if reason:
            self._append_log(f"⚠️ {reason}，触发自动清理")
        else:
            self._append_log("⚠️ 磁盘空间不足，触发自动清理")

    def _update_auto_cleanup_schedule(self) -> None:
        if not hasattr(self, "_auto_cleanup_timer"):
            return
        if not self.enable_auto_delete:
            self._auto_cleanup_timer.stop()
            return
        folders = self._get_auto_cleanup_folders()
        if not folders:
            self._append_log("⚠️ 已启用自动清理但未设置清理路径")
            self._auto_cleanup_timer.stop()
            return
        valid_folders = [path for path in folders if os.path.isdir(path)]
        if len(valid_folders) != len(folders):
            invalid = [path for path in folders if path not in valid_folders]
            self._append_log(f"⚠️ 自动清理路径不可用: {'; '.join(invalid)}")
            self._auto_cleanup_timer.stop()
            return
        group_valid, group_error, _ = self._validate_cleanup_folder_group(valid_folders)
        if not group_valid:
            self._append_log(f"⚠️ {group_error}，自动清理定时任务已停止")
            self._auto_cleanup_timer.stop()
            return
        if getattr(self, 'auto_delete_use_trash', True) and not trash_supported():
            self._append_log("⚠️ 回收站不可用，自动清理无法启用")
            self._auto_cleanup_timer.stop()
            return
        interval = max(60, int(self.auto_delete_check_interval))
        self._auto_cleanup_timer.start(interval * 1000)
        self._append_log(f"ℹ️ 自动清理已启用，每 {interval} 秒检查一次")

    def _auto_cleanup_tick(self) -> None:
        if not self.enable_auto_delete or not self._get_auto_cleanup_folders():
            return
        self._submit_auto_cleanup("timer")

    def _auto_cleanup_task(self, trigger_source: str = "timer") -> None:
        def log(msg: str) -> None:
            self._emit_async_log(msg)

        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        audit_started = False
        audit_finished = False
        folders: List[str] = []
        start_usage = None
        final_usage = None
        scanned_count = 0
        deleted_count = 0
        failed_count = 0
        attempted_delete_bytes = 0
        audit_failure = False
        cancel_event = getattr(self, '_auto_cleanup_cancel_event', None)

        def calculate_used_percent(usage: Any) -> Optional[float]:
            if usage is None or usage.total <= 0:
                return None
            return ((usage.total - usage.free) / usage.total) * 100

        def finish(status: str, error: str = "") -> None:
            nonlocal audit_finished
            if audit_finished or not audit_started:
                return
            audit_finished = True
            actual_released = 0
            if start_usage is not None and final_usage is not None:
                actual_released = max(0, int(final_usage.free - start_usage.free))
            self._write_cleanup_audit(
                "END",
                run_id,
                status=status,
                error=error,
                scanned_count=scanned_count,
                deleted_count=deleted_count,
                failed_count=failed_count,
                start_used_percent=calculate_used_percent(start_usage),
                final_used_percent=calculate_used_percent(final_usage),
                actual_released_bytes=actual_released,
                attempted_delete_bytes=attempted_delete_bytes,
            )
            final_percent = calculate_used_percent(final_usage)
            final_text = f"{final_percent:.1f}%" if final_percent is not None else "未知"
            log(
                f"✅ 自动清理结束：状态={status}，扫描={scanned_count}，删除={deleted_count}，"
                f"失败={failed_count}，实际占用率={final_text}"
            )

        try:
            if cancel_event is not None and cancel_event.is_set():
                return
            folders = self._get_auto_cleanup_folders()
            if not folders:
                return

            trigger_percent = int(self.auto_delete_threshold)
            target_percent = int(self.auto_delete_target_percent)
            raw_formats = list(getattr(self, 'auto_delete_formats', []) or [])
            use_trash = bool(getattr(self, 'auto_delete_use_trash', True))

            invalid_folders = [path for path in folders if not os.path.isdir(path)]
            if invalid_folders:
                error = f"自动清理路径不可用: {'; '.join(invalid_folders)}"
                self._record_cleanup_blocked("路径不可用", trigger_source, folders, error)
                log(f"⚠️ {error}")
                return

            group_valid, group_error, volume_details = self._validate_cleanup_folder_group(folders)
            if not group_valid:
                self._record_cleanup_blocked("跨盘配置无效", trigger_source, folders, group_error)
                log(f"⚠️ {group_error}，自动清理已停止")
                return

            format_filter: set = set()
            if isinstance(raw_formats, list):
                format_filter = {ext.lower() for ext in raw_formats if isinstance(ext, str) and ext}

            if use_trash and not trash_supported():
                error = "回收站不可用，自动清理已暂停（避免永久删除）"
                self._record_cleanup_blocked("路径不可用", trigger_source, folders, error)
                log(f"⚠️ {error}")
                return
            if target_percent >= trigger_percent:
                log("⚠️ 自动清理阈值配置无效（目标阈值必须小于触发阈值），已跳过")
                return

            roots = self._deduplicate_cleanup_roots(folders)
            start_usage = shutil.disk_usage(roots[0])
            start_used_percent = calculate_used_percent(start_usage)
            if start_used_percent is None or start_used_percent < trigger_percent:
                return

            if not self._write_cleanup_audit(
                "START",
                run_id,
                trigger_source=trigger_source,
                folders=folders,
                effective_roots=roots,
                disk=volume_details[0][1] if volume_details else "",
                used_percent=start_used_percent,
                trigger_percent=trigger_percent,
                target_percent=target_percent,
                delete_mode="回收站" if use_trash else "永久删除",
                time_basis="st_mtime",
                format_filter=sorted(format_filter),
            ):
                log("❌ 自动清理已取消：无法写入 START 审计记录")
                return
            audit_started = True
            log(
                f"⚠️ 磁盘使用率 {start_used_percent:.1f}% 达到触发阈值 {trigger_percent}%"
                f"，将按修改时间全局最旧优先清理至 {target_percent}%"
            )

            file_infos: List[Tuple[float, int, str]] = []
            seen_files = set()
            stop_scan = False

            def record_scan_failure(path: str, exc: BaseException) -> None:
                nonlocal failed_count, stop_scan, audit_failure
                failed_count += 1
                if not self._write_cleanup_audit(
                    "SCAN_FAIL",
                    run_id,
                    path=path,
                    file_name=os.path.basename(path),
                    error_type=type(exc).__name__,
                    error=str(exc),
                    failed_count=failed_count,
                ):
                    audit_failure = True
                if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT or audit_failure:
                    stop_scan = True

            for monitor_root in roots:
                def on_walk_error(exc: OSError) -> None:
                    record_scan_failure(getattr(exc, 'filename', None) or monitor_root, exc)

                for root, _, files in os.walk(monitor_root, onerror=on_walk_error):
                    if cancel_event is not None and cancel_event.is_set():
                        stop_scan = True
                    if stop_scan:
                        break
                    for name in files:
                        if cancel_event is not None and cancel_event.is_set():
                            stop_scan = True
                        if stop_scan:
                            break
                        path = os.path.join(root, name)
                        canonical = os.path.normcase(os.path.realpath(os.path.abspath(path)))
                        if canonical in seen_files:
                            continue
                        seen_files.add(canonical)
                        try:
                            if format_filter:
                                _, ext = os.path.splitext(name)
                                if ext.lower() not in format_filter:
                                    continue
                            stat = os.stat(path)
                            file_infos.append((stat.st_mtime, stat.st_size, path))
                            scanned_count += 1
                            if scanned_count % 5000 == 0:
                                log(f"ℹ️ 自动清理扫描中：已遍历 {scanned_count} 个候选文件")
                        except Exception as exc:
                            record_scan_failure(path, exc)
                if stop_scan:
                    break

            if cancel_event is not None and cancel_event.is_set():
                finish("任务异常", "应用正在退出，自动清理已取消")
                return

            if audit_failure:
                finish("任务异常", "清理审计日志写入失败")
                return

            if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT:
                final_usage = shutil.disk_usage(roots[0])
                finish("失败达到20次")
                return

            candidates = self._sort_cleanup_candidates(file_infos)
            if not candidates:
                final_usage = shutil.disk_usage(roots[0])
                finish("无候选文件")
                return

            log(f"ℹ️ 自动清理扫描完成：候选 {len(candidates)} 个，开始按全局时间顺序处理")
            final_status = "无候选文件"
            final_error = ""
            for mtime, size, path in candidates:
                if cancel_event is not None and cancel_event.is_set():
                    final_status = "任务异常"
                    final_error = "应用正在退出，自动清理已取消"
                    break
                if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT:
                    final_status = "失败达到20次"
                    break
                before_usage = final_usage or start_usage
                try:
                    if use_trash:
                        send_to_trash(path)
                    else:
                        os.remove(path)
                    deleted_count += 1
                    attempted_delete_bytes += size
                except Exception as exc:
                    failed_count += 1
                    if not self._write_cleanup_audit(
                        "DELETE_FAIL",
                        run_id,
                        path=path,
                        file_name=os.path.basename(path),
                        size_bytes=size,
                        modified_time=datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                        error_type=type(exc).__name__,
                        error=str(exc),
                        failed_count=failed_count,
                    ):
                        audit_failure = True
                        final_status = "任务异常"
                        final_error = "清理审计日志写入失败"
                        break
                    if failed_count >= AUTO_CLEANUP_FAILURE_LIMIT:
                        final_status = "失败达到20次"
                        break
                    continue

                usage_error = ""
                usage_exception: Optional[BaseException] = None
                try:
                    final_usage = shutil.disk_usage(roots[0])
                except Exception as exc:
                    final_usage = None
                    usage_exception = exc
                    usage_error = f"{type(exc).__name__}: {exc}"

                current_percent = calculate_used_percent(final_usage)
                if not self._write_cleanup_audit(
                    "DELETE_OK",
                    run_id,
                    path=path,
                    file_name=os.path.basename(path),
                    size_bytes=size,
                    modified_time=datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                    delete_mode="回收站" if use_trash else "永久删除",
                    disk_used_percent=current_percent,
                    disk_usage_error=usage_error,
                ):
                    audit_failure = True
                    final_status = "任务异常"
                    final_error = "清理审计日志写入失败"
                    break

                if usage_error:
                    record_scan_failure(roots[0], usage_exception or RuntimeError(usage_error))
                    final_status = "任务异常" if audit_failure else "路径不可用"
                    final_error = "清理审计日志写入失败" if audit_failure else usage_error
                    break

                if use_trash and final_usage.free <= before_usage.free:
                    final_status = "回收站未释放空间"
                    log("⚠️ 文件移入回收站后磁盘空间未增加，已停止自动清理；请清空回收站或改用永久删除")
                    break
                if current_percent is not None and current_percent <= target_percent:
                    final_status = "达到目标"
                    break
            finish(final_status, final_error)
        except Exception as exc:
            if not audit_started:
                self._record_cleanup_blocked("任务异常", trigger_source, folders, str(exc))
            else:
                finish("任务异常", str(exc))
            log(f"⚠️ 自动清理任务异常: {exc}")
        finally:
            if audit_started and not audit_finished:
                finish("任务异常", "任务未正常结束")
            with self._auto_cleanup_lock:
                self._auto_cleanup_running = False
    
    def _on_disk_update(self, disk_type: str, free_percent: float):
        """处理磁盘更新信号（在主线程中执行）"""
        if disk_type == "target":
            if free_percent < 0:
                # 网络路径或不可达
                self.lbl_target_disk.setValue("--")
            else:
                self.lbl_target_disk.setValue(f"{free_percent:.1f}%")
                if free_percent < 10:
                    self.lbl_target_disk.setStyleSheet("QFrame{background:#FFEBEE; border-radius:8px;} QLabel{color:#C62828;}")
                elif free_percent < 20:
                    self.lbl_target_disk.setStyleSheet("QFrame{background:#FFF9C3; border-radius:8px;} QLabel{color:#F57F17;}")
                else:
                    self.lbl_target_disk.setStyleSheet("QFrame{background:#E1F5FE; border-radius:8px;} QLabel{color:#01579B;}")
        elif disk_type == "backup":
            if free_percent < 0:
                self.lbl_backup_disk.setValue("--")
            else:
                self.lbl_backup_disk.setValue(f"{free_percent:.1f}%")
                if free_percent < 10:
                    self.lbl_backup_disk.setStyleSheet("QFrame{background:#FFEBEE; border-radius:8px;} QLabel{color:#C62828;}")
                elif free_percent < 20:
                    self.lbl_backup_disk.setStyleSheet("QFrame{background:#FFF9C3; border-radius:8px;} QLabel{color:#F57F17;}")
                else:
                    self.lbl_backup_disk.setStyleSheet("QFrame{background:#F1F8E9; border-radius:8px;} QLabel{color:#33691E;}")
    
    # ========== v2.2.0 新增：系统托盘功能 ==========
    
    def _init_tray_icon(self):
        """初始化系统托盘图标和菜单"""
        # 创建托盘图标
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        
        # 设置托盘图标（使用应用图标或默认图标）
        icon = self.windowIcon()
        if icon.isNull():
            # 如果没有窗口图标，创建一个简单的图标
            pixmap = QtGui.QPixmap(64, 64)
            pixmap.fill(QtGui.QColor("#4CAF50"))
            painter = QtGui.QPainter(pixmap)
            painter.setPen(QtGui.QColor("white"))
            font = QtGui.QFont("Arial", 24)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), get_qt_enum(QtCore.Qt, 'AlignCenter', 0x0084), "图")
            painter.end()
            icon = QtGui.QIcon(pixmap)
        
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip(APP_TITLE)
        
        # 创建托盘菜单
        tray_menu = QtWidgets.QMenu()
        
        # 显示/隐藏主窗口
        show_action = tray_menu.addAction("📱 显示主窗口")
        show_action.triggered.connect(self._show_window)
        
        tray_menu.addSeparator()
        
        # 上传控制
        self.tray_start_action = tray_menu.addAction("▶️ 开始上传")
        self.tray_start_action.triggered.connect(self._on_start)
        
        self.tray_pause_action = tray_menu.addAction("⏸️ 暂停上传")
        self.tray_pause_action.triggered.connect(self._on_pause_resume)
        self.tray_pause_action.setEnabled(False)
        
        self.tray_stop_action = tray_menu.addAction("⏹️ 停止上传")
        self.tray_stop_action.triggered.connect(self._on_stop)
        self.tray_stop_action.setEnabled(False)
        
        tray_menu.addSeparator()
        
        # 统计信息
        stats_action = tray_menu.addAction("📊 查看统计")
        stats_action.triggered.connect(self._show_stats)
        
        tray_menu.addSeparator()
        
        # 退出程序
        quit_action = tray_menu.addAction("❌ 退出程序")
        quit_action.triggered.connect(self._quit_application)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # 双击托盘图标显示主窗口
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        # 显示托盘图标
        self.tray_icon.show()
        
        self._append_log("✓ 系统托盘已初始化")
    
    def _on_tray_activated(self, reason):
        """托盘图标激活事件"""
        if reason == get_qt_enum(QtWidgets.QSystemTrayIcon, 'DoubleClick', 2):
            self._show_window()
    
    def _show_window(self):
        """显示主窗口"""
        self.show()
        # WindowMinimized=0x00000001, WindowActive=0x00000004
        window_minimized = get_qt_enum(QtCore.Qt, 'WindowMinimized', 0x00000001)
        window_active = get_qt_enum(QtCore.Qt, 'WindowActive', 0x00000004)
        # Qt 枚举位运算在 PySide6 中不支持直接 int() 转换
        try:
            new_state = self.windowState() & ~window_minimized | window_active  # type: ignore[operator]
        except TypeError:
            new_state = QtCore.Qt.WindowState.WindowActive  # type: ignore[assignment]
        self.setWindowState(new_state)  # type: ignore[arg-type]
        self.activateWindow()
        self.raise_()
    
    def _show_stats(self):
        """显示统计信息对话框"""
        stats_text = f"""
📊 上传统计信息

运行状态: {'🟢 运行中' if self.is_running else '⚪ 已停止'}
已上传: {self.uploaded} 个文件
失败: {self.failed} 个文件
跳过: {self.skipped} 个文件

网络状态: {self._get_network_status_text()}
协议模式: {self.current_protocol.upper()}
"""
        if self.is_running and self.start_time:
            elapsed = time.time() - self.start_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            stats_text += f"运行时间: {hours:02d}:{minutes:02d}:{seconds:02d}\n"
        
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("统计信息")
        msg_box.setText(stats_text)
        msg_box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        msg_box.exec()
    
    def _get_network_status_text(self):
        """获取网络状态文本"""
        status_map = {
            'good': '🟢 正常',
            'unstable': '🟡 不稳定',
            'disconnected': '🔴 已断开',
            'unknown': '⚪ 未知'
        }
        return status_map.get(self.network_status, '⚪ 未知')
    
    def _quit_application(self):
        """退出应用程序"""
        reply = QtWidgets.QMessageBox.question(
            self,
            '确认退出',
            '确定要退出程序吗？\n\n如果有上传任务正在运行，将会被中止。',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self.tray_icon:
                self.tray_icon.hide()
            QtWidgets.QApplication.quit()
    
    def _show_notification(self, title: str, message: str, icon_type: Optional[Any] = None):
        """显示系统通知
        
        Note: PySide6 6.x 的 showMessage API 有两种签名，我们使用 type: ignore[call-overload] 来忽略类型检查
        """
        if self.show_notifications and self.tray_icon and self.tray_icon.isVisible():
            if icon_type is None:
                icon_type = QtWidgets.QSystemTrayIcon.MessageIcon.Information
            self.tray_icon.showMessage(title, message, icon_type, 3000)  # type: ignore[call-overload]
    
    def changeEvent(self, event):
        """窗口状态改变事件"""
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            if self.minimize_to_tray and self.isMinimized():
                # 最小化时隐藏到托盘
                event.ignore()
                self.hide()
                if self.show_notifications:
                    self._show_notification(
                        "已最小化到托盘",
                        "程序仍在后台运行\n双击托盘图标可恢复窗口"
                    )
                return
        super().changeEvent(event)

    @staticmethod
    def _shutdown_executor(executor: Any, name: str) -> None:
        """兼容 Python 3.8，并等待已运行任务完成。"""
        try:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=True)
        except Exception as exc:
            logger.error("关闭%s线程池失败: %s: %s", name, type(exc).__name__, exc)

    def _shutdown_background_executors(self) -> None:
        """先停止清理，再刷新日志，避免退出期间出现无审计删除。"""
        self._is_closing = True
        if hasattr(self, '_auto_cleanup_timer'):
            self._auto_cleanup_timer.stop()
        cancel_event = getattr(self, '_auto_cleanup_cancel_event', None)
        if cancel_event is not None:
            cancel_event.set()

        self._shutdown_executor(self._cleanup_executor, "自动清理")
        self._shutdown_executor(self._disk_executor, "磁盘检测")
        self._shutdown_executor(self._log_executor, "日志")
    
    def closeEvent(self, event):
        """窗口关闭事件，清理资源"""
        # 如果启用托盘且不是真正退出，则隐藏到托盘
        if self.minimize_to_tray and self.tray_icon and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            if self.show_notifications:
                self._show_notification(
                    "程序已隐藏",
                    "程序仍在后台运行\n右键托盘图标可选择退出"
                )
            return
        
        # 真正退出时清理资源
        # 停止上传任务
        if self.worker:
            self.worker.stop()

        # 停止内置 FTP 服务器
        if self._is_ftp_server_running():
            try:
                self._stop_ftp_server_only(manual=False)
            except Exception as e:
                logger.debug(f"关闭窗口时停止FTP服务器失败: {type(e).__name__}: {e}")
        
        self._shutdown_background_executors()
        
        # 接受关闭事件
        event.accept()
    
    def _setup_single_instance_server(self):
        """设置单例唤醒服务器
        
        监听来自新实例的唤醒请求，收到后将窗口置顶激活
        """
        self.local_server = QLocalServer(self)
        server_name = "ImageUploadTool_SingleInstance_Server"
        
        # 先移除可能残留的服务器（程序异常退出时可能遗留）
        QLocalServer.removeServer(server_name)
        
        # 启动服务器
        if not self.local_server.listen(server_name):
            # 服务器启动失败，记录日志但不影响程序运行
            self._log_message(f"警告: 单例服务器启动失败 - {self.local_server.errorString()}")
            return
        
        # 连接新连接信号
        self.local_server.newConnection.connect(self._handle_wakeup_request)
        self._log_message("单例服务器已启动，可接收唤醒请求")
    
    def _handle_wakeup_request(self):
        """处理来自新实例的唤醒请求"""
        # 获取新连接
        client_socket = self.local_server.nextPendingConnection()
        if not client_socket:
            return
        
        # 等待数据到达
        if client_socket.waitForReadyRead(1000):  # 等待最多1秒
            data = client_socket.readAll()
            # 使用 Qt 的方法转换为 Python 字符串
            message = bytes(data).decode('utf-8', errors='ignore')  # type: ignore[arg-type]
            
            if message == "WAKEUP":
                # 收到唤醒请求，激活窗口
                self._activate_window()
                self._log_message("收到唤醒请求，已激活窗口")
        
        # 关闭连接
        client_socket.disconnectFromServer()
    
    def _activate_window(self):
        """激活并置顶窗口"""
        # 如果窗口被隐藏，先显示
        if self.isHidden():
            self.show()
        
        # 如果窗口被最小化，恢复正常状态
        if self.isMinimized():
            self.showNormal()
        
        # 激活窗口（置顶并获得焦点）
        self.activateWindow()
        self.raise_()  # 确保窗口在最前面
        
        # 在 Windows 上，可能需要额外的操作来确保窗口真正置顶
        # 设置窗口标志强制置顶，然后立即恢复
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.show()
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.show()


