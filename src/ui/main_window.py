# -*- coding: utf-8 -*-
"""
Main window UI module extracted from pyqt_app.py.
"""
import os
import sys
import json
import time
import shutil
import threading
import datetime
import queue
import winreg
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

try:
    from src.protocols.ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    FTPProtocolManager = FTPServerManager = FTPClientUploader = None  # type: ignore[misc, assignment]

# 类型守卫（仅用于类型检查）
if not FTP_AVAILABLE:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        # 为类型检查器提供类型提示
        FTPProtocolManager = FTPServerManager = FTPClientUploader = Any  # type: ignore[misc, assignment]

from qt_types import MessageBoxIcon, MessageBoxButton, TrayIconType, EventType

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    Signal = QtCore.Signal  # PySide6 signal
except ImportError:
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
    from PyQt5.QtNetwork import QLocalServer, QLocalSocket  # type: ignore[import-not-found]
    Signal = QtCore.pyqtSignal  # type: ignore[attr-defined]

from src.core import get_app_dir, get_resource_path, get_app_version, get_app_title
from src.ui.widgets import Toast, ChipWidget, CollapsibleBox, DiskCleanupDialog
from src.workers.upload_worker import UploadWorker

APP_VERSION = get_app_version()
APP_TITLE = get_app_title()


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
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        # 使用可折叠组件后，优化窗口大小
        self.resize(1350, 880)  # 稍微减小高度
        self.setMinimumSize(1200, 750)  # 减小最小尺寸
        self.app_dir = get_app_dir()
        
        # 连接内部信号
        self._disk_update_signal.connect(self._on_disk_update)
        # 权限系统
        self.current_role = 'guest'  # guest, user, admin
        # 默认密码（SHA256哈希）
        self.user_password = hashlib.sha256('123'.encode('utf-8')).hexdigest()
        self.admin_password = hashlib.sha256('Tops123'.encode('utf-8')).hexdigest()
        # state
        self.source = ''
        self.target = ''
        self.backup = ''
        self.enable_backup = True  # v2.1.1 新增：是否启用备份
        self.interval = 30
        self.mode = 'periodic'
        self.disk_threshold_percent = 10
        self.retry_count = 3
        self.filters = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif', '.raw']
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
        self.saved_config = {}  # 保存的配置（用于回退）
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
        self.auto_delete_threshold = 80  # 磁盘使用率达到80%时触发
        self.auto_delete_keep_days = 10  # 保留最近10天的文件
        self.auto_delete_check_interval = 300  # 每5分钟检查一次
        
        # v2.0 新增：FTP 协议配置
        self.current_protocol = 'smb'  # 上传协议：smb, ftp_server, ftp_client, both
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
        
        # v2.2.0 新增：系统托盘配置
        self.minimize_to_tray = True  # 最小化到托盘
        self.show_notifications = True  # 显示通知
        self.tray_icon = None  # 托盘图标对象
        
        # v2.3.0 新增：速率限制配置
        self.limit_upload_rate = False
        self.max_upload_rate_mbps = 10.0
        
        # v2.0 新增：FTP 协议管理器（延迟初始化，避免在UI创建前调用日志）
        self.ftp_manager = None
        
        # UI
        self._build_ui()
        self._load_config()
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
        """
        try:
            # 创建 logs 目录
            logs_dir = self.app_dir / 'logs'
            logs_dir.mkdir(parents=True, exist_ok=True)
            
            # 如果不存在 config.json，从资源目录复制默认配置
            config_path = self.app_dir / 'config.json'
            if not config_path.exists():
                # 尝试从资源目录复制
                resource_config = get_resource_path('config.json')
                if resource_config.exists():
                    import shutil
                    shutil.copy2(resource_config, config_path)
        except Exception as e:
            print(f"创建目录失败: {e}")

    def _apply_theme(self):
        self.setStyleSheet(
            """
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
            QMenu::item:selected{background:#E3F2FD; color:#1976D2;}
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
        )

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
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll_area)
        
        # 创建内容容器 - 优化宽度适配高分辨率
        central = QtWidgets.QWidget()
        central.setMinimumWidth(1250)  # 减小最小宽度
        scroll_area.setWidget(central)
        
        root = QtWidgets.QVBoxLayout(central)
        root.setSpacing(12)  # 减小间距，节省空间
        root.setContentsMargins(12, 12, 12, 12)  # 减小边距

        # header
        header = QtWidgets.QHBoxLayout()
        
        # Logo - 使用资源路径函数确保打包后也能访问
        logo_path = get_resource_path("assets/logo.png")
        if logo_path.exists():
            logo_label = QtWidgets.QLabel()
            pixmap = QtGui.QPixmap(str(logo_path))
            if not pixmap.isNull():
                # 设置 Logo 大小（高度40px，宽度按比例）
                scaled_pixmap = pixmap.scaledToHeight(40)
                logo_label.setPixmap(scaled_pixmap)
                logo_label.setStyleSheet("background: transparent;")
                header.addWidget(logo_label)
                header.addSpacing(12)  # Logo 和标题之间的间距
            else:
                self._append_log("⚠️ Logo 文件加载失败")
        else:
            self._append_log(f"⚠️ Logo 文件不存在: {logo_path}")
        
        title = QtWidgets.QLabel("图片异步上传工具")
        title.setObjectName("Title")
        ver = QtWidgets.QLabel(f"v{APP_VERSION} (PyQt)")
        header.addWidget(title)
        header.addWidget(ver)
        header.addStretch(1)
        self.role_label = QtWidgets.QLabel("🔒 未登录")
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        header.addWidget(self.role_label)
        root.addLayout(header)

        # center three columns - 优化列间距
        center = QtWidgets.QHBoxLayout()
        center.setSpacing(15)  # 减小列间距，节省空间
        root.addLayout(center, 1)

        left = QtWidgets.QVBoxLayout()
        middle = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        left.setSpacing(12)  # 减小卡片间距
        middle.setSpacing(12)
        right.setSpacing(12)
        center.addLayout(left, 1)
        center.addLayout(middle, 1)
        center.addLayout(right, 1)

        # left cards - 使用 QSplitter 防止卡片互相影响大小
        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        left_splitter.setChildrenCollapsible(False)  # 防止子部件被完全折叠
        left_splitter.setHandleWidth(8)  # 分隔条宽度
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
        left_splitter.setSizes([200, 500])
        
        left.addWidget(left_splitter)

        # middle cards - 同样使用 QSplitter
        middle_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        middle_splitter.setChildrenCollapsible(False)
        middle_splitter.setHandleWidth(8)
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
        middle_splitter.setSizes([250, 450])
        
        middle.addWidget(middle_splitter)

        # right - log card
        right.addWidget(self._log_card(), 1)

    def _card(self, title_text: str) -> Tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setObjectName("Card")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(14, 14, 14, 14)  # 减小内边距，节省空间
        v.setSpacing(10)  # 减小元素间距
        if title_text:
            t = QtWidgets.QLabel(title_text)
            t.setProperty("class", "Title")
            v.addWidget(t)
            line = QtWidgets.QFrame()
            shape_enum = getattr(QtWidgets.QFrame, 'Shape', QtWidgets.QFrame)
            line.setFrameShape(getattr(shape_enum, 'HLine'))
            line.setStyleSheet("color:#E5EAF0")
            v.addWidget(line)
        return card, v

    def _folder_card(self) -> QtWidgets.QFrame:
        card, v = self._card("📁 文件夹设置")
        
        # source
        self.src_edit, self.btn_choose_src = self._path_row(v, "源文件夹", self._choose_source)
        # target
        self.tgt_edit, self.btn_choose_tgt = self._path_row(v, "目标文件夹", self._choose_target)
        # backup
        self.bak_edit, self.btn_choose_bak = self._path_row(v, "备份文件夹", self._choose_backup)
        
        # v2.1.1 新增：启用备份复选框
        self.cb_enable_backup = QtWidgets.QCheckBox(" 启用备份功能")
        self.cb_enable_backup.setProperty('orig_text', " 启用备份功能")
        self.cb_enable_backup.setChecked(True)
        self.cb_enable_backup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_enable_backup, checked))
        self.cb_enable_backup.toggled.connect(self._on_backup_toggled)
        self._set_checkbox_mark(self.cb_enable_backup, self.cb_enable_backup.isChecked())
        v.addWidget(self.cb_enable_backup)
        
        # 添加说明文本
        backup_hint = QtWidgets.QLabel("💡 启用后，上传成功的文件会移动到备份文件夹保存；禁用后文件上传成功会直接删除")
        backup_hint.setWordWrap(True)
        backup_hint.setStyleSheet("color: #666; font-size: 11px; padding: 5px 0;")
        v.addWidget(backup_hint)
        
        # v3.0.0 修复：设置固定高度，防止被其他卡片挤压
        card.setFixedHeight(280)
        
        return card

    def _path_row(self, layout: QtWidgets.QVBoxLayout, label: str, chooser):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)  # 增加元素间距
        lab = QtWidgets.QLabel(label + ":")
        lab.setMinimumWidth(90)  # 设置标签最小宽度，对齐更整齐
        edit = QtWidgets.QLineEdit()
        edit.setMinimumHeight(32)  # 增加输入框高度
        # v2.2.0 修复：设置路径输入框的文本对齐方式，避免长路径被截断显示
        edit.setCursorPosition(0)  # 默认显示路径开头
        btn = QtWidgets.QPushButton("浏览")
        btn.setProperty("class", "Secondary")
        btn.setMinimumWidth(80)  # 设置按钮最小宽度
        btn.setMinimumHeight(32)
        btn.clicked.connect(chooser)
        row.addWidget(lab)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        layout.addLayout(row)
        # v2.2.0 修复：为输入框设置工具提示，显示完整路径
        edit.textChanged.connect(lambda text: edit.setToolTip(text) if text else None)
        return edit, btn

    def _settings_card(self) -> QtWidgets.QFrame:
        card, v = self._card("⚙️ 上传设置")
        
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
        scroll_area.setMinimumHeight(200)
        
        # 创建滚动内容容器
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 8, 0)  # 右边留出滚动条空间
        scroll_layout.setSpacing(10)
        
        # 将后续所有内容添加到 scroll_layout 而不是 v
        # ========== v2.0 新增：协议选择 ==========
        protocol_lab = QtWidgets.QLabel("📡 上传协议 (v2.0)")
        protocol_lab.setStyleSheet("color:#1976D2; font-size:11px; font-weight:700;")
        scroll_layout.addWidget(protocol_lab)
        
        # 协议选择下拉框
        protocol_row = QtWidgets.QHBoxLayout()
        protocol_label = QtWidgets.QLabel("协议类型:")
        self.combo_protocol = QtWidgets.QComboBox()
        self.combo_protocol.addItems([
            "SMB (网络共享)",
            "FTP 服务器模式",
            "FTP 客户端模式",
            "混合模式 (Server + Client)"
        ])
        self.combo_protocol.currentIndexChanged.connect(self._on_protocol_changed)
        protocol_row.addWidget(protocol_label)
        protocol_row.addWidget(self.combo_protocol, 1)
        scroll_layout.addLayout(protocol_row)
        
        # 协议说明
        self.protocol_desc = QtWidgets.QLabel()
        self.protocol_desc.setWordWrap(True)
        self.protocol_desc.setStyleSheet("color: #6B7280; padding: 8px; background: #F3F4F6; border-radius: 6px; font-size: 10px;")
        scroll_layout.addWidget(self.protocol_desc)
        self._update_protocol_description(0)
        
        # FTP 配置容器（可折叠）
        self.ftp_config_widget = QtWidgets.QWidget()
        self.ftp_config_widget.setVisible(False)
        ftp_layout = QtWidgets.QVBoxLayout(self.ftp_config_widget)
        ftp_layout.setContentsMargins(0, 8, 0, 0)
        ftp_layout.setSpacing(10)
        
        # ========== FTP 服务器配置 - 可折叠 ==========
        self.ftp_server_collapsible = MainWindow.CollapsibleBox("🖥️ FTP 服务器配置", self)
        server_layout = QtWidgets.QFormLayout()
        server_layout.setSpacing(8)
        server_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ftp_server_host = QtWidgets.QLineEdit("0.0.0.0")
        self.ftp_server_host.setToolTip("0.0.0.0 表示监听所有网卡，127.0.0.1 仅本机可访问")
        server_layout.addRow("监听地址:", self.ftp_server_host)
        
        self.ftp_server_port = QtWidgets.QSpinBox()
        self.ftp_server_port.setRange(1, 65535)
        self.ftp_server_port.setValue(2121)
        self.ftp_server_port.setToolTip("默认FTP端口为21，建议使用2121避免权限问题")
        server_layout.addRow("端口:", self.ftp_server_port)
        
        self.ftp_server_user = QtWidgets.QLineEdit("upload_user")
        self.ftp_server_user.setToolTip("FTP登录用户名")
        server_layout.addRow("用户名:", self.ftp_server_user)
        
        self.ftp_server_pass = QtWidgets.QLineEdit("upload_pass")
        self.ftp_server_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ftp_server_pass.setToolTip("FTP登录密码，建议使用强密码")
        server_layout.addRow("密码:", self.ftp_server_pass)
        
        # 共享目录选择
        share_row = QtWidgets.QHBoxLayout()
        self.ftp_server_share = QtWidgets.QLineEdit()
        self.ftp_server_share.setPlaceholderText("选择FTP共享目录")
        self.ftp_server_share.setToolTip("FTP服务器的根目录，客户端连接后可访问此目录")
        btn_choose_share = QtWidgets.QPushButton("浏览")
        btn_choose_share.setProperty("class", "Secondary")
        btn_choose_share.clicked.connect(self._choose_ftp_share)
        share_row.addWidget(self.ftp_server_share, 1)
        share_row.addWidget(btn_choose_share)
        server_layout.addRow("共享目录:", share_row)
        
        # v2.0 新增：高级选项 - 被动模式
        self.cb_server_passive = QtWidgets.QCheckBox("启用被动模式")
        self.cb_server_passive.setChecked(True)
        self.cb_server_passive.setToolTip("被动模式适用于NAT/防火墙环境，建议启用")
        server_layout.addRow("", self.cb_server_passive)
        
        # 被动端口范围
        passive_row = QtWidgets.QHBoxLayout()
        self.ftp_server_passive_start = QtWidgets.QSpinBox()
        self.ftp_server_passive_start.setRange(1024, 65535)
        self.ftp_server_passive_start.setValue(60000)
        self.ftp_server_passive_start.setPrefix("起始: ")
        passive_row.addWidget(self.ftp_server_passive_start)
        
        self.ftp_server_passive_end = QtWidgets.QSpinBox()
        self.ftp_server_passive_end.setRange(1024, 65535)
        self.ftp_server_passive_end.setValue(65535)
        self.ftp_server_passive_end.setPrefix("结束: ")
        passive_row.addWidget(self.ftp_server_passive_end)
        passive_row.addStretch()
        server_layout.addRow("  端口范围:", passive_row)
        
        # v2.0 新增：TLS/SSL选项
        self.cb_server_tls = QtWidgets.QCheckBox("启用 TLS/SSL (FTPS)")
        self.cb_server_tls.setChecked(False)
        self.cb_server_tls.setToolTip("启用加密连接，需要证书文件")
        server_layout.addRow("", self.cb_server_tls)
        
        # v2.0 新增：连接数限制
        conn_row = QtWidgets.QHBoxLayout()
        conn_label = QtWidgets.QLabel("最大连接:")
        self.ftp_server_max_conn = QtWidgets.QSpinBox()
        self.ftp_server_max_conn.setRange(1, 1000)
        self.ftp_server_max_conn.setValue(256)
        self.ftp_server_max_conn.setSuffix(" 个")
        conn_row.addWidget(conn_label)
        conn_row.addWidget(self.ftp_server_max_conn)
        
        ip_label = QtWidgets.QLabel("  单IP限制:")
        self.ftp_server_max_conn_per_ip = QtWidgets.QSpinBox()
        self.ftp_server_max_conn_per_ip.setRange(1, 100)
        self.ftp_server_max_conn_per_ip.setValue(5)
        self.ftp_server_max_conn_per_ip.setSuffix(" 个")
        conn_row.addWidget(ip_label)
        conn_row.addWidget(self.ftp_server_max_conn_per_ip)
        conn_row.addStretch()
        server_layout.addRow("连接限制:", conn_row)
        
        # v2.0 新增：FTP服务器测试按钮
        self.btn_test_ftp_server = QtWidgets.QPushButton("🧪 测试配置")
        self.btn_test_ftp_server.setProperty("class", "Secondary")
        self.btn_test_ftp_server.clicked.connect(self._test_ftp_server_config)
        server_layout.addRow("", self.btn_test_ftp_server)
        
        self.ftp_server_collapsible.setContentLayout(server_layout)
        ftp_layout.addWidget(self.ftp_server_collapsible)
        
        # ========== FTP 客户端配置 - 可折叠 ==========
        self.ftp_client_collapsible = MainWindow.CollapsibleBox("💻 FTP 客户端配置", self)
        client_layout = QtWidgets.QFormLayout()
        client_layout.setSpacing(8)
        client_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ftp_client_host = QtWidgets.QLineEdit()
        self.ftp_client_host.setPlaceholderText("ftp.example.com")
        self.ftp_client_host.setToolTip("FTP服务器地址，可以是域名或IP地址")
        client_layout.addRow("服务器:", self.ftp_client_host)
        
        self.ftp_client_port = QtWidgets.QSpinBox()
        self.ftp_client_port.setRange(1, 65535)
        self.ftp_client_port.setValue(21)
        self.ftp_client_port.setToolTip("FTP服务器端口，标准端口为21")
        client_layout.addRow("端口:", self.ftp_client_port)
        
        self.ftp_client_user = QtWidgets.QLineEdit()
        self.ftp_client_user.setPlaceholderText("用户名")
        self.ftp_client_user.setToolTip("FTP服务器登录用户名")
        client_layout.addRow("用户名:", self.ftp_client_user)
        
        self.ftp_client_pass = QtWidgets.QLineEdit()
        self.ftp_client_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ftp_client_pass.setPlaceholderText("密码")
        self.ftp_client_pass.setToolTip("FTP服务器登录密码")
        client_layout.addRow("密码:", self.ftp_client_pass)
        
        self.ftp_client_remote = QtWidgets.QLineEdit("/upload")
        self.ftp_client_remote.setToolTip("文件上传到服务器的目标路径")
        client_layout.addRow("远程路径:", self.ftp_client_remote)
        
        # v2.0 新增：超时和重试配置
        timeout_row = QtWidgets.QHBoxLayout()
        self.ftp_client_timeout = QtWidgets.QSpinBox()
        self.ftp_client_timeout.setRange(10, 300)
        self.ftp_client_timeout.setValue(30)
        self.ftp_client_timeout.setSuffix(" 秒")
        self.ftp_client_timeout.setToolTip("连接和传输超时时间，网络慢时可适当增加")
        timeout_row.addWidget(self.ftp_client_timeout)
        timeout_row.addStretch()
        client_layout.addRow("超时时间:", timeout_row)
        
        retry_row = QtWidgets.QHBoxLayout()
        self.ftp_client_retry = QtWidgets.QSpinBox()
        self.ftp_client_retry.setRange(0, 10)
        self.ftp_client_retry.setValue(3)
        self.ftp_client_retry.setSuffix(" 次")
        self.ftp_client_retry.setToolTip("连接失败时的重试次数，0表示不重试")
        retry_row.addWidget(self.ftp_client_retry)
        retry_row.addStretch()
        client_layout.addRow("重试次数:", retry_row)
        
        # v2.0 新增：高级选项 - 被动模式
        self.cb_client_passive = QtWidgets.QCheckBox("使用被动模式")
        self.cb_client_passive.setChecked(True)
        self.cb_client_passive.setToolTip("被动模式适用于NAT/防火墙环境，建议启用")
        client_layout.addRow("", self.cb_client_passive)
        
        # v2.0 新增：TLS/SSL选项
        self.cb_client_tls = QtWidgets.QCheckBox("启用 TLS/SSL (FTPS)")
        self.cb_client_tls.setChecked(False)
        self.cb_client_tls.setToolTip("连接到FTPS服务器时启用")
        client_layout.addRow("", self.cb_client_tls)
        
        # v2.0 新增：FTP客户端测试按钮
        self.btn_test_ftp_client = QtWidgets.QPushButton("🔌 测试连接")
        self.btn_test_ftp_client.setProperty("class", "Secondary")
        self.btn_test_ftp_client.clicked.connect(self._test_ftp_client_connection)
        client_layout.addRow("", self.btn_test_ftp_client)
        
        self.ftp_client_collapsible.setContentLayout(client_layout)
        ftp_layout.addWidget(self.ftp_client_collapsible)
        
        scroll_layout.addWidget(self.ftp_config_widget)
        
        scroll_layout.addWidget(self._hline())
        # ========== v2.0 协议选择结束 ==========
        
        # interval
        self.spin_interval = self._spin_row(scroll_layout, "间隔时间(秒)", 10, 3600, 30)
        self.spin_disk = self._spin_row(scroll_layout, "磁盘阈值(%)", 5, 50, 10)
        self.spin_retry = self._spin_row(scroll_layout, "失败重试次数", 0, 10, 3)
        self.spin_disk_check = self._spin_row(scroll_layout, "磁盘检查间隔(秒)", 1, 60, 5)
        # 绑定磁盘检查间隔变化事件
        self.spin_disk_check.valueChanged.connect(lambda val: setattr(self, 'disk_check_interval', val))
        
        # ========== 文件类型限制 - 可折叠 ==========
        filter_collapsible = MainWindow.CollapsibleBox("📋 文件类型限制", self)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(10)
        self.cb_ext = {}
        exts = [
            ("JPG", ".jpg"), ("PNG", ".png"), ("BMP", ".bmp"), ("TIFF", ".tiff"), ("GIF", ".gif"), ("RAW", ".raw")
        ]
        for i, (name, ext) in enumerate(exts):
            cb = QtWidgets.QCheckBox(name)
            # store original text so we can add a visible ✓ fallback if styling fails
            cb.setProperty('orig_text', name)
            cb.setChecked(True)
            # connect toggled to update visible text marker (robust fallback)
            cb.toggled.connect(lambda checked, cb=cb: self._set_checkbox_mark(cb, checked))
            # initialize text with marker if checked
            self._set_checkbox_mark(cb, cb.isChecked())
            self.cb_ext[ext] = cb
            grid.addWidget(cb, i//3, i%3)
        filter_collapsible.addLayout(grid)
        scroll_layout.addWidget(filter_collapsible)
        
        # ========== 高级选项 - 可折叠 ==========
        adv_collapsible = MainWindow.CollapsibleBox("⚡ 高级选项", self)
        
        self.cb_auto_start_windows = QtWidgets.QCheckBox("🚀 开机自启动")
        self.cb_auto_start_windows.setProperty('orig_text', "🚀 开机自启动")
        self.cb_auto_start_windows.setChecked(False)
        self.cb_auto_start_windows.toggled.connect(self._toggle_autostart)
        self.cb_auto_start_windows.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_start_windows, checked))
        self._set_checkbox_mark(self.cb_auto_start_windows, self.cb_auto_start_windows.isChecked())
        adv_collapsible.addWidget(self.cb_auto_start_windows)
        
        self.cb_auto_run_on_startup = QtWidgets.QCheckBox("▶ 启动时自动运行")
        self.cb_auto_run_on_startup.setProperty('orig_text', "▶ 启动时自动运行")
        self.cb_auto_run_on_startup.setChecked(False)
        self.cb_auto_run_on_startup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_run_on_startup, checked))
        self._set_checkbox_mark(self.cb_auto_run_on_startup, self.cb_auto_run_on_startup.isChecked())
        adv_collapsible.addWidget(self.cb_auto_run_on_startup)
        
        # v2.2.0 新增：托盘通知开关
        self.cb_show_notifications = QtWidgets.QCheckBox("🔔 显示托盘通知")
        self.cb_show_notifications.setProperty('orig_text', "🔔 显示托盘通知")
        self.cb_show_notifications.setChecked(True)
        self.cb_show_notifications.toggled.connect(lambda checked: setattr(self, 'show_notifications', checked))
        self.cb_show_notifications.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_show_notifications, checked))
        self._set_checkbox_mark(self.cb_show_notifications, self.cb_show_notifications.isChecked())
        adv_collapsible.addWidget(self.cb_show_notifications)
        
        # v2.3.0 新增：速率限制
        rate_row = QtWidgets.QHBoxLayout()
        self.cb_limit_rate = QtWidgets.QCheckBox("⚡ 限制上传速率")
        self.cb_limit_rate.setProperty('orig_text', "⚡ 限制上传速率")
        self.cb_limit_rate.setToolTip("启用后将限制最大上传速度，避免占用过多带宽")
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
        self.spin_max_rate.setToolTip("设置最大上传速率（单位：MB/秒）")
        self.spin_max_rate.valueChanged.connect(lambda: setattr(self, 'config_modified', True))
        
        rate_row.addWidget(self.cb_limit_rate)
        rate_row.addWidget(self.spin_max_rate)
        rate_row.addStretch()
        adv_collapsible.addLayout(rate_row)
        
        # 添加分隔线
        adv_collapsible.addWidget(self._hline())
        
        # 去重功能
        self.cb_dedup_enable = QtWidgets.QCheckBox("🔍 启用文件去重 (v1.8)")
        self.cb_dedup_enable.setProperty('orig_text', "🔍 启用文件去重 (v1.8)")
        self.cb_dedup_enable.setChecked(False)
        self.cb_dedup_enable.toggled.connect(self._on_dedup_toggled)
        self.cb_dedup_enable.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_dedup_enable, checked))
        self._set_checkbox_mark(self.cb_dedup_enable, self.cb_dedup_enable.isChecked())
        adv_collapsible.addWidget(self.cb_dedup_enable)
        
        # 哈希算法选择
        hash_row = QtWidgets.QHBoxLayout()
        hash_lab = QtWidgets.QLabel("哈希算法:")
        self.combo_hash = QtWidgets.QComboBox()
        self.combo_hash.addItems(["MD5", "SHA256"])
        self.combo_hash.setEnabled(False)
        hash_row.addWidget(hash_lab)
        hash_row.addWidget(self.combo_hash)
        adv_collapsible.addLayout(hash_row)
        
        # 去重策略选择
        strategy_row = QtWidgets.QHBoxLayout()
        strategy_lab = QtWidgets.QLabel("重复策略:")
        self.combo_strategy = QtWidgets.QComboBox()
        self.combo_strategy.addItems(["跳过", "重命名", "覆盖", "询问"])
        self.combo_strategy.setEnabled(False)
        strategy_row.addWidget(strategy_lab)
        strategy_row.addWidget(self.combo_strategy)
        adv_collapsible.addLayout(strategy_row)
        
        # 说明文本
        dedup_hint = QtWidgets.QLabel("💡 通过文件哈希检测重复，避免上传相同内容的文件")
        dedup_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        dedup_hint.setWordWrap(True)
        adv_collapsible.addWidget(dedup_hint)
        
        # 添加分隔线
        adv_collapsible.addWidget(self._hline())
        
        # 网络监控选项
        network_sub_lab = QtWidgets.QLabel("🌐 网络监控")
        network_sub_lab.setStyleSheet("color:#666; font-size:10px; font-weight:700;")
        adv_collapsible.addWidget(network_sub_lab)
        
        # 网络检测间隔 - 压缩布局
        network_check_row = QtWidgets.QHBoxLayout()
        network_check_lab = QtWidgets.QLabel("检测间隔:")
        self.spin_network_check = QtWidgets.QSpinBox()
        self.spin_network_check.setRange(5, 60)
        self.spin_network_check.setValue(10)
        self.spin_network_check.setSuffix(" 秒")
        network_check_row.addWidget(network_check_lab)
        network_check_row.addWidget(self.spin_network_check)
        network_check_row.addStretch()
        adv_collapsible.addLayout(network_check_row)
        
        self.cb_network_auto_pause = QtWidgets.QCheckBox("⏸️ 断网时自动暂停")
        self.cb_network_auto_pause.setProperty('orig_text', "⏸️ 断网时自动暂停")
        self.cb_network_auto_pause.setChecked(True)
        self.cb_network_auto_pause.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_pause, checked))
        self._set_checkbox_mark(self.cb_network_auto_pause, self.cb_network_auto_pause.isChecked())
        adv_collapsible.addWidget(self.cb_network_auto_pause)
        
        self.cb_network_auto_resume = QtWidgets.QCheckBox("▶️ 恢复时自动继续")
        self.cb_network_auto_resume.setProperty('orig_text', "▶️ 恢复时自动继续")
        self.cb_network_auto_resume.setChecked(True)
        self.cb_network_auto_resume.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_resume, checked))
        self._set_checkbox_mark(self.cb_network_auto_resume, self.cb_network_auto_resume.isChecked())
        adv_collapsible.addWidget(self.cb_network_auto_resume)
        
        # 说明文本
        network_hint = QtWidgets.QLabel("💡 实时监控网络状态，断网时自动暂停，恢复后自动继续")
        network_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        network_hint.setWordWrap(True)
        adv_collapsible.addWidget(network_hint)
        
        scroll_layout.addWidget(adv_collapsible)
        
        # 添加弹性空间，使内容紧凑排列
        scroll_layout.addStretch()
        
        # 设置滚动区域
        scroll_area.setWidget(scroll_content)
        v.addWidget(scroll_area, 1)  # stretch=1 让滚动区域填满剩余空间
        
        return card

    def _spin_row(self, layout: QtWidgets.QVBoxLayout, label: str, low: int, high: int, val: int) -> QtWidgets.QSpinBox:
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label + ":")
        sp = QtWidgets.QSpinBox()
        sp.setRange(low, high)
        sp.setValue(val)
        row.addWidget(lab)
        row.addWidget(sp)
        layout.addLayout(row)
        return sp

    def _control_card(self) -> QtWidgets.QFrame:
        card, v = self._card("🎮 操作控制")
        
        # primary start - 优化按钮尺寸
        self.btn_start = QtWidgets.QPushButton("▶ 开始上传")
        self.btn_start.setProperty("class", "Primary")
        self.btn_start.setMinimumHeight(45)  # 增加按钮高度，更容易点击
        self.btn_start.clicked.connect(self._on_start)
        v.addWidget(self.btn_start)
        # secondary pause/stop
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)  # 增加按钮间距
        self.btn_pause = QtWidgets.QPushButton("⏸ 暂停上传")
        self.btn_pause.setProperty("class", "Warning")
        self.btn_pause.setMinimumHeight(40)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_resume)
        self.btn_stop = QtWidgets.QPushButton("⏹ 停止上传")
        self.btn_stop.setProperty("class", "Danger")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        row.addWidget(self.btn_pause)
        row.addWidget(self.btn_stop)
        v.addLayout(row)
        # separator
        v.addWidget(self._hline())
        # save + more
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(12)  # 增加按钮间距
        self.btn_save = QtWidgets.QPushButton("💾 保存配置")
        self.btn_save.setProperty("class", "Secondary")
        self.btn_save.setMinimumHeight(38)
        self.btn_save.clicked.connect(self._save_config)
        self.btn_more = QtWidgets.QToolButton()
        self.btn_more.setText("更多 ▾")
        self.btn_more.setMinimumHeight(38)
        popup_enum = getattr(QtWidgets.QToolButton, 'ToolButtonPopupMode', QtWidgets.QToolButton)
        self.btn_more.setPopupMode(getattr(popup_enum, 'InstantPopup'))
        menu = QtWidgets.QMenu(self)
        act_clear = menu.addAction("🗑️ 清空日志")
        act_clear.triggered.connect(self._clear_logs)
        menu.addSeparator()
        act_disk_cleanup = menu.addAction("💿 磁盘清理")
        act_disk_cleanup.triggered.connect(self._show_disk_cleanup)
        menu.addSeparator()
        act_login = menu.addAction("🔐 权限登录")
        act_login.triggered.connect(self._show_login)
        act_change_pwd = menu.addAction("🔑 修改密码")
        act_change_pwd.triggered.connect(self._show_change_password)
        menu.addSeparator()
        act_logout = menu.addAction("🚪 退出登录")
        act_logout.triggered.connect(self._logout)
        self.btn_more.setMenu(menu)
        row2.addWidget(self.btn_save)
        row2.addWidget(self.btn_more)
        v.addLayout(row2)
        
        # v3.0.0 修复：设置固定高度，防止被其他卡片挤压
        card.setFixedHeight(260)
        
        return card

    def _logout(self):
        """退出登录"""
        self.current_role = 'guest'
        self.role_label.setText("🔒 未登录")
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        self._update_ui_permissions()
        self._toast('已退出登录', 'info')

    def _compute_control_states(self, role: str, is_running: bool, enable_backup: bool) -> dict:
        """
        统一计算所有控件的启用/禁用状态
        
        规则：
        - guest: 任何时候不能改配置，只能控制开始/暂停/停止
        - user/admin: 未运行时可改配置；运行中完全不可改
        - 备份路径: 仅当"已启用备份"时可编辑
        - 运行中: 所有配置类控件禁用，无论角色
        
        Returns:
            dict: 控件名称 -> 是否启用的映射
        """
        is_user_or_admin = role in ['user', 'admin']
        can_edit_config = is_user_or_admin and not is_running
        
        # v2.2.0 超详细调试
        self._append_log(f"      [计算细节] role={role}, is_running={is_running}, enable_backup={enable_backup}")
        self._append_log(f"      [计算细节] is_user_or_admin={is_user_or_admin}, can_edit_config={can_edit_config}")
        
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
            'upload_settings': is_user_or_admin,  # 运行中也可查看但不可改
            # 文件类型复选框
            'file_filters': is_user_or_admin,
            # 自启动设置
            'startup_settings': is_user_or_admin,
            # v2.3.0 速率限制控件
            'cb_limit_rate': can_edit_config,
            'spin_max_rate': can_edit_config,
            # 上传控制按钮
            'btn_start': not is_running,
            'btn_pause': is_running,
            'btn_stop': is_running,
        }

    def _update_ui_permissions(self):
        """根据当前角色更新UI控件的启用状态"""
        self._append_log(f"🔐 更新权限: 当前角色={self.current_role}, 运行状态={'运行中' if self.is_running else '已停止'}")
        
        # v2.2.0 计算统一控件状态
        states = self._compute_control_states(self.current_role, self.is_running, self.enable_backup)
        
        # v2.2.0 详细调试：打印所有按钮的计算状态
        self._append_log(f"   [计算状态] 源按钮={states['btn_choose_src']}, 目标按钮={states['btn_choose_tgt']}, 备份按钮={states['btn_choose_bak']}")
        self._append_log(f"   [计算状态] 源只读={states['src_edit_readonly']}, 目标只读={states['tgt_edit_readonly']}, 备份只读={states['bak_edit_readonly']}")
        
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
        # v2.2.0 新增：通知开关（所有人可设置）
        if hasattr(self, 'cb_show_notifications'):
            self.cb_show_notifications.setEnabled(True)
        # v2.3.0 新增：速率限制控件权限
        if hasattr(self, 'cb_limit_rate'):
            self.cb_limit_rate.setEnabled(states['cb_limit_rate'])
            # spin_max_rate 需要同时满足：有权限 && checkbox已勾选
            if states['spin_max_rate'] and self.cb_limit_rate.isChecked():
                self.spin_max_rate.setEnabled(True)
            else:
                self.spin_max_rate.setEnabled(False)
        
        # 保存配置按钮
        self.btn_save.setEnabled(states['btn_save'])
        
        # 协议选择框
        if hasattr(self, 'combo_protocol'):
            self.combo_protocol.setEnabled(states['combo_protocol'])
        
        # 上传控制按钮
        self.btn_start.setEnabled(states['btn_start'])
        self.btn_pause.setEnabled(states['btn_pause'])
        self.btn_stop.setEnabled(states['btn_stop'])
        
        # v2.2.0 详细调试：验证实际应用后的按钮状态
        actual_src = self.btn_choose_src.isEnabled() if hasattr(self, 'btn_choose_src') else None
        actual_tgt = self.btn_choose_tgt.isEnabled() if hasattr(self, 'btn_choose_tgt') else None
        actual_bak = self.btn_choose_bak.isEnabled() if hasattr(self, 'btn_choose_bak') else None
        self._append_log(f"   [应用后实际] 源按钮={actual_src}, 目标按钮={actual_tgt}, 备份按钮={actual_bak}")
        self._append_log(f"   [应用后实际] 源只读={self.src_edit.isReadOnly()}, 目标只读={self.tgt_edit.isReadOnly()}, 备份只读={self.bak_edit.isReadOnly()}")
        
        # 检测异常：如果计算状态与实际状态不一致
        if actual_tgt is not None and actual_tgt != states['btn_choose_tgt']:
            self._append_log(f"   ⚠️ 警告：目标按钮状态不一致！计算={states['btn_choose_tgt']}, 实际={actual_tgt}")
        if actual_src is not None and actual_src != states['btn_choose_src']:
            self._append_log(f"   ⚠️ 警告：源按钮状态不一致！计算={states['btn_choose_src']}, 实际={actual_src}")

    def _clear_logs(self):
        try:
            self.log.clear()
            self._toast('已清空日志', 'info')
        except Exception:
            pass
    
    def _show_disk_cleanup(self):
        """显示磁盘清理对话框"""
        try:
            dialog = MainWindow.DiskCleanupDialog(self)
            dialog.exec()
        except Exception as e:
            self._append_log(f"❌ 打开磁盘清理对话框失败: {e}")
            self._toast('打开磁盘清理失败', 'danger')

    def _show_login(self):
        """显示权限登录对话框"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("🔐 权限登录")
        dialog.setModal(True)
        dialog.resize(400, 200)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(15)
        
        # 角色选择
        role_layout = QtWidgets.QHBoxLayout()
        role_label = QtWidgets.QLabel("登录角色:")
        role_label.setMinimumWidth(80)
        role_combo = QtWidgets.QComboBox()
        role_combo.addItems(["👤 用户", "👑 管理员"])
        role_layout.addWidget(role_label)
        role_layout.addWidget(role_combo)
        layout.addLayout(role_layout)
        
        # 密码
        pwd_layout = QtWidgets.QHBoxLayout()
        pwd_label = QtWidgets.QLabel("密码:")
        pwd_label.setMinimumWidth(80)
        pwd_input = QtWidgets.QLineEdit()
        echo_enum = getattr(QtWidgets.QLineEdit, 'EchoMode', QtWidgets.QLineEdit)
        pwd_input.setEchoMode(getattr(echo_enum, 'Password'))
        pwd_input.setPlaceholderText("请输入密码")
        pwd_layout.addWidget(pwd_label)
        pwd_layout.addWidget(pwd_input)
        layout.addLayout(pwd_layout)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setProperty("class", "Secondary")
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok = QtWidgets.QPushButton("登录")
        btn_ok.setProperty("class", "Primary")
        btn_ok.setDefault(True)  # 设置为默认按钮，支持回车触发
        
        def do_login():
            role_text = role_combo.currentText()
            password = pwd_input.text().strip()
            
            if not password:
                self._toast('请输入密码', 'warning')
                return
            
            # 哈希密码
            pwd_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
            
            # 验证密码
            if "用户" in role_text:
                if pwd_hash == self.user_password:
                    self.current_role = 'user'
                    self.role_label.setText("👤 用户")
                    self.role_label.setStyleSheet("background:#E3F2FD; color:#1976D2; padding:6px 12px; border-radius:6px; font-weight:700;")
                    self._append_log("=" * 50)
                    self._append_log("👤 用户登录成功！")
                    self._toast('用户登录成功！', 'success')
                    self._update_ui_permissions()
                    dialog.accept()
                else:
                    self._toast('密码错误', 'danger')
            elif "管理员" in role_text:
                if pwd_hash == self.admin_password:
                    self.current_role = 'admin'
                    self.role_label.setText("👑 管理员")
                    self.role_label.setStyleSheet("background:#DCFCE7; color:#166534; padding:6px 12px; border-radius:6px; font-weight:700;")
                    self._append_log("=" * 50)
                    self._append_log("👑 管理员登录成功！")
                    self._toast('管理员登录成功！', 'success')
                    self._update_ui_permissions()
                    dialog.accept()
                else:
                    self._toast('密码错误', 'danger')
        
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
        dialog.resize(400, 300)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(15)
        
        # 管理员可以选择修改哪个密码
        target_combo = None
        if self.current_role == 'admin':
            target_layout = QtWidgets.QHBoxLayout()
            target_label = QtWidgets.QLabel("修改对象:")
            target_label.setMinimumWidth(80)
            target_combo = QtWidgets.QComboBox()
            target_combo.addItems(["👤 用户密码", "👑 管理员密码"])
            target_layout.addWidget(target_label)
            target_layout.addWidget(target_combo)
            layout.addLayout(target_layout)
        
        # 原密码
        old_layout = QtWidgets.QHBoxLayout()
        old_label = QtWidgets.QLabel("原密码:")
        old_label.setMinimumWidth(80)
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
        new_label.setMinimumWidth(80)
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
        confirm_label.setMinimumWidth(80)
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
                    users = {}
                    if path.exists():
                        with open(path, 'r', encoding='utf-8') as f:
                            cfg = json.load(f)
                            users = cfg.get('users', {})
                    
                    users[target_role] = new_hash
                    
                    if path.exists():
                        with open(path, 'r', encoding='utf-8') as f:
                            cfg = json.load(f)
                    else:
                        cfg = {}
                    
                    cfg['users'] = users
                    
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(cfg, f, indent=2, ensure_ascii=False)
                    
                    self._append_log(f"✓ 密码已保存: {target_role}")
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
        # 启用/禁用子选项
        self.combo_hash.setEnabled(checked)
        self.combo_strategy.setEnabled(checked)
        
        if checked:
            self._append_log("🔍 已启用智能去重")
        else:
            self._append_log("⚪ 已禁用智能去重")
    
    def _on_rate_limit_toggled(self, checked: bool):
        """v2.3.0 切换速率限制开关"""
        self.limit_upload_rate = checked
        self.spin_max_rate.setEnabled(checked)
        self.config_modified = True
        
        if checked:
            rate = self.spin_max_rate.value()
            self._append_log(f"⚡ 已启用速率限制: {rate} MB/s")
        else:
            self._append_log("⚪ 已禁用速率限制")

    def _choose_ftp_share(self):
        """选择 FTP 共享目录"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择 FTP 共享目录", self.ftp_server_share.text()
        )
        if folder:
            self.ftp_server_share.setText(folder)
            self.config_modified = True
    
    def _test_ftp_server_config(self):
        """测试FTP服务器配置"""
        self._append_log("🧪 开始测试FTP服务器配置...")
        
        # 收集当前配置
        config = {
            'host': self.ftp_server_host.text().strip(),
            'port': self.ftp_server_port.value(),
            'username': self.ftp_server_user.text().strip(),
            'password': self.ftp_server_pass.text().strip(),
            'shared_folder': self.ftp_server_share.text().strip()
        }
        
        # 验证配置
        errors = []
        if not config['host']:
            errors.append("主机地址为空")
        if not config['username']:
            errors.append("用户名为空")
        if not config['password']:
            errors.append("密码为空")
        if not config['shared_folder']:
            errors.append("共享目录为空")
        elif not os.path.exists(config['shared_folder']):
            errors.append(f"共享目录不存在: {config['shared_folder']}")
        
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
    
    def _test_ftp_client_connection(self):
        """测试FTP客户端连接"""
        self._append_log("🔌 开始测试FTP客户端连接...")
        
        # 收集当前配置
        config = {
            'name': 'test_client',
            'host': self.ftp_client_host.text().strip(),
            'port': self.ftp_client_port.value(),
            'username': self.ftp_client_user.text().strip(),
            'password': self.ftp_client_pass.text().strip(),
            'remote_path': self.ftp_client_remote.text().strip(),
            'timeout': self.ftp_client_timeout.value(),
            'retry_count': self.ftp_client_retry.value(),
        }
        
        # 验证配置
        errors = []
        if not config['host']:
            errors.append("服务器地址为空")
        if not config['username']:
            errors.append("用户名为空")
        if not config['password']:
            errors.append("密码为空")
        if not config['remote_path']:
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
        """协议选择变化"""
        protocols = ['smb', 'ftp_server', 'ftp_client', 'both']
        self.current_protocol = protocols[index]
        
        # 更新说明文字
        self._update_protocol_description(index)
        
        # 显示/隐藏 FTP 配置
        show_ftp = index > 0  # 非 SMB 时显示
        self.ftp_config_widget.setVisible(show_ftp)
        
        # 控制各组件可见性
        if index == 0:  # SMB
            self.ftp_server_collapsible.setVisible(False)
            self.ftp_client_collapsible.setVisible(False)
        elif index == 1:  # FTP Server
            self.ftp_server_collapsible.setVisible(True)
            self.ftp_client_collapsible.setVisible(False)
        elif index == 2:  # FTP Client
            self.ftp_server_collapsible.setVisible(False)
            self.ftp_client_collapsible.setVisible(True)
        elif index == 3:  # Both
            self.ftp_server_collapsible.setVisible(True)
            self.ftp_client_collapsible.setVisible(True)
        
        self.config_modified = True
        self._append_log(f"📡 切换上传协议：{['SMB', 'FTP服务器', 'FTP客户端', '混合模式'][index]}")
        
        # v2.0 新增：更新协议状态显示
        self._update_protocol_status()
    
    def _update_protocol_description(self, index: int):
        """更新协议说明"""
        descriptions = [
            "📁 SMB (网络共享)：通过 Windows 网络共享上传文件到共享文件夹",
            "🖥️ FTP 服务器模式：本机作为 FTP 服务器，其他设备可连接上传文件",
            "📤 FTP 客户端模式：本机作为 FTP 客户端，连接到远程 FTP 服务器上传文件",
            "🔄 混合模式：同时运行 FTP 服务器和客户端，灵活应对不同场景"
        ]
        self.protocol_desc.setText(descriptions[index])
    
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
                self._add_to_startup()
            else:
                self._remove_from_startup()
            self.auto_start_windows = checked
        except Exception as e:
            self._toast(f'设置开机自启动失败: {e}', 'danger')
            # 恢复状态
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(not checked)
            self.cb_auto_start_windows.blockSignals(False)

    def _add_to_startup(self):
        """添加到Windows启动项"""
        try:
            # 获取程序路径
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
            else:
                exe_path = os.path.abspath(__file__)
            
            # 打开注册表
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            
            # 设置值
            winreg.SetValueEx(key, "ImageUploader", 0, winreg.REG_SZ, exe_path)
            winreg.CloseKey(key)
            
            self._append_log("✓ 已添加到开机自启动")
            self._toast('已设置开机自启动', 'success')
        except Exception as e:
            raise Exception(f"添加启动项失败: {str(e)}")

    def _remove_from_startup(self):
        """从Windows启动项移除"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            
            try:
                winreg.DeleteValue(key, "ImageUploader")
                self._append_log("✓ 已从开机自启动移除")
                self._toast('已取消开机自启动', 'success')
            except FileNotFoundError:
                pass  # 键不存在，忽略
            
            winreg.CloseKey(key)
        except Exception as e:
            raise Exception(f"移除启动项失败: {str(e)}")

    def _check_startup_status(self) -> bool:
        """检查当前是否在启动项中"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                winreg.QueryValueEx(key, "ImageUploader")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False

    def _auto_start_upload(self):
        """自动开始上传（启动时调用）"""
        if not self.auto_run_on_startup:
            return
        
        # 验证设置
        if not self.src_edit.text() or not self.tgt_edit.text() or not self.bak_edit.text():
            self._append_log("⚠ 自动运行失败：文件夹路径未设置")
            return
        
        self._append_log("🚀 自动运行已触发，1秒后开始上传...")
        self._on_start()

    def _status_card(self) -> QtWidgets.QFrame:
        card, v = self._card("📊 运行状态")
        # status pill
        self.lbl_status = QtWidgets.QLabel("🔴 已停止")
        self.lbl_status.setStyleSheet("background:#FEE2E2; color:#B91C1C; padding:6px 12px; font-weight:700; border-radius:12px; font-size:10pt;")
        v.addWidget(self.lbl_status)
        # chips - 优化网格布局，4列显示更紧凑
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)  # 增加间距
        self.lbl_uploaded = self._chip("已上传", "0", "#E3F2FD", "#1976D2")
        self.lbl_failed = self._chip("失败", "0", "#FFEBEE", "#C62828")
        self.lbl_skipped = self._chip("跳过", "0", "#FFF9C3", "#F57F17")
        self.lbl_rate = self._chip("速率", "0 MB/s", "#E8F5E9", "#2E7D32")
        self.lbl_queue = self._chip("归档队列", "0", "#F3E5F5", "#6A1B9A")
        self.lbl_time = self._chip("运行时间", "00:00:00", "#FFF3E0", "#E65100")
        # 新增：磁盘空间芯片
        self.lbl_target_disk = self._chip("目标磁盘", "--", "#E1F5FE", "#01579B")
        self.lbl_backup_disk = self._chip("归档磁盘", "--", "#F1F8E9", "#33691E")
        # v1.9 新增：网络状态芯片
        self.lbl_network = self._chip("网络状态", "未知", "#ECEFF1", "#546E7A")
        # v2.0 新增：协议和FTP状态芯片
        self.lbl_protocol = self._chip("上传协议", "SMB", "#E8EAF6", "#3F51B5")
        self.lbl_ftp_server = self._chip("FTP服务器", "未启动", "#FCE4EC", "#C2185B")
        self.lbl_ftp_client = self._chip("FTP客户端", "未连接", "#FFF8E1", "#F57C00")
        
        # 4列布局，在高分辨率下显示更好
        for i, w in enumerate([self.lbl_uploaded, self.lbl_failed, self.lbl_skipped, 
                               self.lbl_rate, self.lbl_queue, self.lbl_time,
                               self.lbl_target_disk, self.lbl_backup_disk, self.lbl_network,
                               self.lbl_protocol, self.lbl_ftp_server, self.lbl_ftp_client]):
            grid.addWidget(w, i//4, i%4)
        v.addLayout(grid)
        
        # 分隔线
        v.addWidget(self._hline())
        
        # 新增：当前文件信息
        current_file_label = QtWidgets.QLabel("📄 当前文件")
        current_file_label.setStyleSheet("font-weight:700; font-size:10pt; color:#424242; margin-top:4px;")
        v.addWidget(current_file_label)
        
        self.lbl_current_file = QtWidgets.QLabel("等待开始...")
        self.lbl_current_file.setStyleSheet("color:#616161; font-size:9pt; padding:4px 8px;")
        self.lbl_current_file.setWordWrap(True)
        v.addWidget(self.lbl_current_file)
        
        # 当前文件进度条
        self.pbar_file = QtWidgets.QProgressBar()
        self.pbar_file.setRange(0, 100)
        self.pbar_file.setValue(0)
        self.pbar_file.setTextVisible(True)
        self.pbar_file.setFormat("等待...")
        self.pbar_file.setStyleSheet("""
            QProgressBar {
                border: 2px solid #BDBDBD;
                border-radius: 6px;
                text-align: center;
                height: 20px;
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
        self.lbl_progress = QtWidgets.QLabel("等待开始...")
        v.addWidget(self.lbl_progress)
        self.pbar = QtWidgets.QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        v.addWidget(self.pbar)
        return card

    # v2.1 磁盘清理对话框
    class DiskCleanupDialog(QtWidgets.QDialog):  # type: ignore[misc]
        """磁盘清理对话框 - 支持选择文件夹路径和文件格式
        
        Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
        """
        
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("💿 磁盘清理工具")
            self.setModal(True)
            self.resize(500, 500)  # 增加高度以容纳自动清理配置
            
            self.parent_window = parent  # 保存父窗口引用，用于读取配置
            self.files_to_delete = []  # 待删除的文件列表
            
            self._build_ui()
        
        def _build_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setSpacing(15)
            layout.setContentsMargins(20, 20, 20, 20)
            
            # 标题说明
            title_label = QtWidgets.QLabel("选择要清理的文件夹和文件类型")
            title_label.setStyleSheet("font-size: 13pt; font-weight: 700; color: #1976D2;")
            layout.addWidget(title_label)
            
            desc_label = QtWidgets.QLabel(
                "⚠️ 警告：删除的文件将无法恢复！请确认后再执行清理操作。"
            )
            desc_label.setStyleSheet("color: #D32F2F; padding: 8px; background: #FFEBEE; border-radius: 6px;")
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)
            
            # 文件夹选择区域
            folder_group = QtWidgets.QGroupBox("📁 选择清理目标")
            folder_group.setStyleSheet(
                "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
                "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            )
            folder_layout = QtWidgets.QVBoxLayout(folder_group)
            folder_layout.setSpacing(12)
            
            # 从父窗口的输入框读取路径配置（实时读取最新值）
            backup_path = self.parent_window.bak_edit.text() if self.parent_window and hasattr(self.parent_window, 'bak_edit') else ""
            target_path = self.parent_window.tgt_edit.text() if self.parent_window and hasattr(self.parent_window, 'tgt_edit') else ""
            monitor_path = self.parent_window.auto_delete_folder if self.parent_window and hasattr(self.parent_window, 'auto_delete_folder') else ""
            
            # 备份文件夹
            self.cb_backup = QtWidgets.QCheckBox(f"🗂️ 备份文件夹")
            self.cb_backup.setChecked(True)
            if backup_path:
                self.cb_backup.setText(f"🗂️ 备份文件夹: {backup_path}")
                self.cb_backup.setToolTip(backup_path)
            else:
                self.cb_backup.setEnabled(False)
                self.cb_backup.setText("🗂️ 备份文件夹 (未配置)")
            folder_layout.addWidget(self.cb_backup)
            
            # 目标文件夹
            self.cb_target = QtWidgets.QCheckBox(f"📤 目标文件夹 (服务器)")
            if target_path:
                self.cb_target.setText(f"📤 目标文件夹: {target_path}")
                self.cb_target.setToolTip(target_path)
            else:
                self.cb_target.setEnabled(False)
                self.cb_target.setText("📤 目标文件夹 (未配置)")
            folder_layout.addWidget(self.cb_target)
            
            # 监控文件夹（带输入功能）
            self.cb_monitor = QtWidgets.QCheckBox("🔍 监控文件夹")
            folder_layout.addWidget(self.cb_monitor)
            
            monitor_row = QtWidgets.QHBoxLayout()
            monitor_row.setContentsMargins(30, 0, 0, 0)
            self.edit_monitor = QtWidgets.QLineEdit(monitor_path)
            self.edit_monitor.setPlaceholderText("选择监控文件夹路径...")
            btn_monitor = QtWidgets.QPushButton("浏览")
            btn_monitor.setProperty("class", "Secondary")
            btn_monitor.clicked.connect(self._choose_monitor)
            monitor_row.addWidget(self.edit_monitor, 1)
            monitor_row.addWidget(btn_monitor)
            folder_layout.addLayout(monitor_row)
            
            # 自定义文件夹（保留输入功能）
            self.cb_custom = QtWidgets.QCheckBox("📂 自定义文件夹")
            folder_layout.addWidget(self.cb_custom)
            
            custom_row = QtWidgets.QHBoxLayout()
            custom_row.setContentsMargins(30, 0, 0, 0)
            self.edit_custom = QtWidgets.QLineEdit()
            self.edit_custom.setPlaceholderText("选择自定义文件夹路径...")
            btn_custom = QtWidgets.QPushButton("浏览")
            btn_custom.setProperty("class", "Secondary")
            btn_custom.clicked.connect(self._choose_custom)
            custom_row.addWidget(self.edit_custom, 1)
            custom_row.addWidget(btn_custom)
            folder_layout.addLayout(custom_row)
            
            layout.addWidget(folder_group)
            
            # 文件格式选择区域
            format_group = QtWidgets.QGroupBox("📋 选择文件格式")
            format_group.setStyleSheet(
                "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
                "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            )
            format_layout = QtWidgets.QVBoxLayout(format_group)
            format_layout.setSpacing(10)
            
            # 快速选择按钮
            quick_row = QtWidgets.QHBoxLayout()
            btn_all = QtWidgets.QPushButton("全选")
            btn_all.setProperty("class", "Secondary")
            btn_all.clicked.connect(self._select_all_formats)
            btn_none = QtWidgets.QPushButton("取消全选")
            btn_none.setProperty("class", "Secondary")
            btn_none.clicked.connect(self._select_no_formats)
            btn_image = QtWidgets.QPushButton("仅图片")
            btn_image.setProperty("class", "Secondary")
            btn_image.clicked.connect(self._select_image_formats)
            quick_row.addWidget(btn_all)
            quick_row.addWidget(btn_none)
            quick_row.addWidget(btn_image)
            quick_row.addStretch()
            format_layout.addLayout(quick_row)
            
            # 文件格式复选框 - 网格布局
            formats_grid = QtWidgets.QGridLayout()
            formats_grid.setSpacing(8)
            
            self.format_checkboxes = {}
            formats = [
                ('.jpg', '图片'),
                ('.jpeg', '图片'),
                ('.png', '图片'),
                ('.bmp', '图片'),
                ('.gif', '图片'),
                ('.tiff', '图片'),
                ('.tif', '图片'),
                ('.raw', '图片'),
                ('.pdf', '文档'),
                ('.doc', '文档'),
                ('.docx', '文档'),
                ('.txt', '文本'),
                ('.log', '日志'),
                ('.zip', '压缩'),
                ('.rar', '压缩'),
                ('.tmp', '临时'),
            ]
            
            for idx, (ext, category) in enumerate(formats):
                cb = QtWidgets.QCheckBox(f"{ext} ({category})")
                cb.setChecked(ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.raw'])  # 默认选中图片
                self.format_checkboxes[ext] = cb
                row = idx // 4
                col = idx % 4
                formats_grid.addWidget(cb, row, col)
            
            format_layout.addLayout(formats_grid)
            
            # 自定义格式
            custom_format_row = QtWidgets.QHBoxLayout()
            custom_format_label = QtWidgets.QLabel("自定义格式:")
            self.edit_custom_format = QtWidgets.QLineEdit()
            self.edit_custom_format.setPlaceholderText("例如: .bak 或 .old (以点开头)")
            custom_format_row.addWidget(custom_format_label)
            custom_format_row.addWidget(self.edit_custom_format, 1)
            format_layout.addLayout(custom_format_row)
            
            layout.addWidget(format_group)
            
            # 自动清理配置区域（整合自动删除功能）
            auto_group = QtWidgets.QGroupBox("⚙️ 自动清理配置")
            auto_group.setStyleSheet(
                "QGroupBox { font-weight: 700; border: 2px solid #FFA726; "
                "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            )
            auto_layout = QtWidgets.QVBoxLayout(auto_group)
            auto_layout.setSpacing(10)
            
            # 启用自动清理
            self.cb_enable_auto = QtWidgets.QCheckBox("⏰ 启用自动清理")
            auto_enabled = self.parent_window.enable_auto_delete if self.parent_window and hasattr(self.parent_window, 'enable_auto_delete') else False
            self.cb_enable_auto.setChecked(auto_enabled)
            self.cb_enable_auto.toggled.connect(self._on_auto_clean_toggled)
            auto_layout.addWidget(self.cb_enable_auto)
            
            # 配置参数
            config_grid = QtWidgets.QGridLayout()
            config_grid.setSpacing(10)
            
            # 磁盘阈值
            threshold_label = QtWidgets.QLabel("磁盘阈值:")
            self.spin_threshold = QtWidgets.QSpinBox()
            self.spin_threshold.setRange(50, 95)
            auto_threshold = self.parent_window.auto_delete_threshold if self.parent_window and hasattr(self.parent_window, 'auto_delete_threshold') else 80
            self.spin_threshold.setValue(auto_threshold)
            self.spin_threshold.setSuffix(" %")
            self.spin_threshold.setToolTip("磁盘使用率达到此值时自动清理")
            self.spin_threshold.setEnabled(auto_enabled)
            config_grid.addWidget(threshold_label, 0, 0)
            config_grid.addWidget(self.spin_threshold, 0, 1)
            
            # 保留天数
            days_label = QtWidgets.QLabel("保留天数:")
            self.spin_keep_days = QtWidgets.QSpinBox()
            self.spin_keep_days.setRange(1, 365)
            auto_days = self.parent_window.auto_delete_keep_days if self.parent_window and hasattr(self.parent_window, 'auto_delete_keep_days') else 10
            self.spin_keep_days.setValue(auto_days)
            self.spin_keep_days.setSuffix(" 天")
            self.spin_keep_days.setToolTip("只删除超过此天数的文件")
            self.spin_keep_days.setEnabled(auto_enabled)
            config_grid.addWidget(days_label, 0, 2)
            config_grid.addWidget(self.spin_keep_days, 0, 3)
            
            # 检查间隔
            interval_label = QtWidgets.QLabel("检查间隔:")
            self.spin_check_interval = QtWidgets.QSpinBox()
            self.spin_check_interval.setRange(60, 3600)
            auto_interval = self.parent_window.auto_delete_check_interval if self.parent_window and hasattr(self.parent_window, 'auto_delete_check_interval') else 300
            self.spin_check_interval.setValue(auto_interval)
            self.spin_check_interval.setSuffix(" 秒")
            self.spin_check_interval.setToolTip("自动检查的时间间隔")
            self.spin_check_interval.setEnabled(auto_enabled)
            config_grid.addWidget(interval_label, 1, 0)
            config_grid.addWidget(self.spin_check_interval, 1, 1)
            
            auto_layout.addLayout(config_grid)
            
            # 说明文本
            auto_hint = QtWidgets.QLabel(
                "💡 启用后，程序会定期检查磁盘空间，当达到阈值时自动删除超过保留期限的文件"
            )
            auto_hint.setStyleSheet("color: #757575; font-size: 9px; padding: 8px;")
            auto_hint.setWordWrap(True)
            auto_layout.addWidget(auto_hint)
            
            # 保存配置按钮
            btn_save_auto = QtWidgets.QPushButton("💾 保存自动清理配置")
            btn_save_auto.setProperty("class", "Secondary")
            btn_save_auto.clicked.connect(self._save_auto_config)
            auto_layout.addWidget(btn_save_auto)
            
            layout.addWidget(auto_group)
            
            # 扫描结果区域
            result_group = QtWidgets.QGroupBox("📊 扫描结果")
            result_group.setStyleSheet(
                "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
                "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            )
            result_layout = QtWidgets.QVBoxLayout(result_group)
            
            self.result_text = QtWidgets.QPlainTextEdit()
            self.result_text.setReadOnly(True)
            self.result_text.setMaximumHeight(120)
            self.result_text.setPlainText("点击 '扫描文件' 开始查找可清理的文件...")
            result_layout.addWidget(self.result_text)
            
            layout.addWidget(result_group)
            
            # 按钮区域
            button_layout = QtWidgets.QHBoxLayout()
            button_layout.setSpacing(12)
            
            self.btn_scan = QtWidgets.QPushButton("🔍 扫描文件")
            self.btn_scan.setProperty("class", "Primary")
            self.btn_scan.setMinimumHeight(40)
            self.btn_scan.clicked.connect(self._scan_files)
            
            self.btn_delete = QtWidgets.QPushButton("🗑️ 执行清理")
            self.btn_delete.setProperty("class", "Danger")
            self.btn_delete.setMinimumHeight(40)
            self.btn_delete.setEnabled(False)
            self.btn_delete.clicked.connect(self._delete_files)
            
            btn_close = QtWidgets.QPushButton("❌ 关闭")
            btn_close.setProperty("class", "Secondary")
            btn_close.setMinimumHeight(40)
            btn_close.clicked.connect(self.reject)
            
            button_layout.addWidget(self.btn_scan)
            button_layout.addWidget(self.btn_delete)
            button_layout.addStretch()
            button_layout.addWidget(btn_close)
            
            layout.addLayout(button_layout)
        
        def _choose_custom(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择自定义文件夹")
            if path:
                self.edit_custom.setText(path)
        
        def _choose_monitor(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择监控文件夹")
            if path:
                self.edit_monitor.setText(path)
        
        def _select_all_formats(self):
            for cb in self.format_checkboxes.values():
                cb.setChecked(True)
        
        def _select_no_formats(self):
            for cb in self.format_checkboxes.values():
                cb.setChecked(False)
        
        def _select_image_formats(self):
            image_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.raw']
            for ext, cb in self.format_checkboxes.items():
                cb.setChecked(ext in image_formats)
        
        def _on_auto_clean_toggled(self, checked: bool):
            """自动清理开关切换"""
            self.spin_threshold.setEnabled(checked)
            self.spin_keep_days.setEnabled(checked)
            self.spin_check_interval.setEnabled(checked)
        
        def _save_auto_config(self):
            """保存自动清理配置到父窗口"""
            if not self.parent_window:
                return
            
            try:
                # 更新父窗口的自动删除配置
                self.parent_window.enable_auto_delete = self.cb_enable_auto.isChecked()
                self.parent_window.auto_delete_folder = self.edit_monitor.text().strip()  # 保存监控文件夹路径
                self.parent_window.auto_delete_threshold = self.spin_threshold.value()
                self.parent_window.auto_delete_keep_days = self.spin_keep_days.value()
                self.parent_window.auto_delete_check_interval = self.spin_check_interval.value()
                
                # 保存配置到文件
                self.parent_window._save_config()
                
                # 显示成功消息
                QtWidgets.QMessageBox.information(
                    self,
                    "✅ 配置已保存",
                    f"自动清理配置已成功保存！\n\n"
                    f"启用状态: {'是' if self.cb_enable_auto.isChecked() else '否'}\n"
                    f"监控文件夹: {self.edit_monitor.text().strip() or '未设置'}\n"
                    f"磁盘阈值: {self.spin_threshold.value()}%\n"
                    f"保留天数: {self.spin_keep_days.value()}天\n"
                    f"检查间隔: {self.spin_check_interval.value()}秒"
                )
            except Exception as e:
                QtWidgets.QMessageBox.warning(
                    self,
                    "❌ 保存失败",
                    f"保存配置时出错：{e}"
                )
        
        def _scan_files(self):
            """扫描符合条件的文件"""
            self.files_to_delete = []
            self.result_text.clear()
            
            # 获取要扫描的文件夹（从父窗口输入框读取最新路径）
            folders_to_scan = []
            if self.cb_backup.isChecked() and self.parent_window and hasattr(self.parent_window, 'bak_edit'):
                backup_path = self.parent_window.bak_edit.text().strip()
                if backup_path:
                    folders_to_scan.append(backup_path)
            if self.cb_target.isChecked() and self.parent_window and hasattr(self.parent_window, 'tgt_edit'):
                target_path = self.parent_window.tgt_edit.text().strip()
                if target_path:
                    folders_to_scan.append(target_path)
            if self.cb_monitor.isChecked() and self.edit_monitor.text().strip():
                monitor_path = self.edit_monitor.text().strip()
                if monitor_path:
                    folders_to_scan.append(monitor_path)
            if self.cb_custom.isChecked() and self.edit_custom.text().strip():
                folders_to_scan.append(self.edit_custom.text().strip())
            
            if not folders_to_scan:
                self.result_text.setPlainText("❌ 错误：请至少选择一个文件夹！")
                return
            
            # 获取要扫描的文件格式
            formats_to_scan = []
            for ext, cb in self.format_checkboxes.items():
                if cb.isChecked():
                    formats_to_scan.append(ext.lower())
            
            # 添加自定义格式
            custom_format = self.edit_custom_format.text().strip()
            if custom_format:
                if not custom_format.startswith('.'):
                    custom_format = '.' + custom_format
                formats_to_scan.append(custom_format.lower())
            
            if not formats_to_scan:
                self.result_text.setPlainText("❌ 错误：请至少选择一个文件格式！")
                return
            
            # 开始扫描
            self.result_text.appendPlainText("🔍 开始扫描...\n")
            self.result_text.appendPlainText(f"扫描目录: {len(folders_to_scan)} 个")
            self.result_text.appendPlainText(f"文件格式: {', '.join(formats_to_scan)}\n")
            
            total_size = 0
            for folder in folders_to_scan:
                if not os.path.exists(folder):
                    self.result_text.appendPlainText(f"⚠️ 跳过不存在的路径: {folder}")
                    continue
                
                self.result_text.appendPlainText(f"\n📁 扫描: {folder}")
                folder_count = 0
                folder_size = 0
                
                try:
                    for root, dirs, files in os.walk(folder):
                        for file in files:
                            file_lower = file.lower()
                            if any(file_lower.endswith(ext) for ext in formats_to_scan):
                                file_path = os.path.join(root, file)
                                try:
                                    file_size = os.path.getsize(file_path)
                                    self.files_to_delete.append((file_path, file_size))
                                    folder_count += 1
                                    folder_size += file_size
                                except Exception as e:
                                    self.result_text.appendPlainText(f"  ⚠️ 无法访问: {file} ({e})")
                    
                    self.result_text.appendPlainText(
                        f"  找到 {folder_count} 个文件，"
                        f"共 {folder_size / (1024*1024):.2f} MB"
                    )
                    total_size += folder_size
                except Exception as e:
                    self.result_text.appendPlainText(f"  ❌ 扫描失败: {e}")
            
            # 显示汇总
            self.result_text.appendPlainText("\n" + "="*50)
            self.result_text.appendPlainText(
                f"📊 扫描完成！共找到 {len(self.files_to_delete)} 个文件"
            )
            self.result_text.appendPlainText(
                f"💾 总大小: {total_size / (1024*1024):.2f} MB "
                f"({total_size / (1024*1024*1024):.3f} GB)"
            )
            
            # 启用删除按钮
            self.btn_delete.setEnabled(len(self.files_to_delete) > 0)
        
        def _delete_files(self):
            """删除扫描到的文件"""
            if not self.files_to_delete:
                return
            
            # 确认对话框
            total_size = sum(size for _, size in self.files_to_delete)
            reply = QtWidgets.QMessageBox.warning(
                self,
                "⚠️ 确认删除",
                f"确定要删除 {len(self.files_to_delete)} 个文件吗？\n\n"
                f"总大小: {total_size / (1024*1024):.2f} MB\n\n"
                f"⚠️ 警告：此操作不可恢复！",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No
            )
            
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            
            # 执行删除
            self.result_text.appendPlainText("\n" + "="*50)
            self.result_text.appendPlainText("🗑️ 开始删除文件...\n")
            
            deleted_count = 0
            deleted_size = 0
            failed_count = 0
            
            for file_path, file_size in self.files_to_delete:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    deleted_size += file_size
                except Exception as e:
                    failed_count += 1
                    self.result_text.appendPlainText(f"❌ 删除失败: {file_path}\n   错误: {e}")
            
            # 显示结果
            self.result_text.appendPlainText("\n" + "="*50)
            self.result_text.appendPlainText("✅ 清理完成！\n")
            self.result_text.appendPlainText(f"成功删除: {deleted_count} 个文件")
            self.result_text.appendPlainText(
                f"释放空间: {deleted_size / (1024*1024):.2f} MB "
                f"({deleted_size / (1024*1024*1024):.3f} GB)"
            )
            if failed_count > 0:
                self.result_text.appendPlainText(f"删除失败: {failed_count} 个文件")
            
            # 清空待删除列表并禁用删除按钮
            self.files_to_delete = []
            self.btn_delete.setEnabled(False)
            
            # 显示成功消息
            QtWidgets.QMessageBox.information(
                self,
                "✅ 清理完成",
                f"成功删除 {deleted_count} 个文件\n"
                f"释放空间 {deleted_size / (1024*1024):.2f} MB"
            )

    class CollapsibleBox(QtWidgets.QWidget):  # type: ignore[misc]
        """可折叠的组件
        
        Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
        """
        def __init__(self, title: str = "", parent: QtWidgets.QWidget = None):
            super().__init__(parent)
            self.toggle_button = QtWidgets.QToolButton()
            self.toggle_button.setStyleSheet("QToolButton { border: none; font-weight: 700; }")
            self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.toggle_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
            self.toggle_button.setText(title)
            self.toggle_button.setCheckable(True)
            self.toggle_button.setChecked(False)
            
            self.content_area = QtWidgets.QWidget()
            self.content_area.setVisible(False)
            self.content_layout = QtWidgets.QVBoxLayout(self.content_area)
            self.content_layout.setContentsMargins(20, 8, 8, 8)
            
            self.toggle_button.toggled.connect(self._on_toggle)
            
            main_layout = QtWidgets.QVBoxLayout(self)
            main_layout.setSpacing(0)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.addWidget(self.toggle_button)
            main_layout.addWidget(self.content_area)
        
        def _on_toggle(self, checked: bool):
            self.toggle_button.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
            )
            self.content_area.setVisible(checked)
        
        def setContentLayout(self, layout: QtWidgets.QLayout):
            """设置内容布局"""
            # 清除旧布局
            old_layout = self.content_area.layout()
            if old_layout is not None:
                QtWidgets.QWidget().setLayout(old_layout)
            self.content_area.setLayout(layout)
            layout.setContentsMargins(20, 8, 8, 8)
        
        def addWidget(self, widget: QtWidgets.QWidget):
            """添加widget到内容区域"""
            self.content_layout.addWidget(widget)
        
        def addLayout(self, layout: QtWidgets.QLayout):
            """添加layout到内容区域"""
            self.content_layout.addLayout(layout)

    class ChipWidget(QtWidgets.QFrame):  # type: ignore[misc]
        value_label: QtWidgets.QLabel
        def __init__(self, title: str, val: str, bg: str, fg: str, parent: QtWidgets.QWidget = None):
            super().__init__(parent)
            self.setStyleSheet(f"QFrame{{background:{bg}; border-radius:8px; padding:2px;}} QLabel{{color:{fg};}}")
            vv = QtWidgets.QVBoxLayout(self)
            vv.setSpacing(4)  # 增加标题和值之间的间距
            vv.setContentsMargins(10, 8, 10, 8)  # 增加内边距
            t = QtWidgets.QLabel(title)
            t.setStyleSheet("font-size:9.5pt; padding-top:2px;")
            self.value_label = QtWidgets.QLabel(val)
            self.value_label.setStyleSheet("font-weight:700; font-size:11.5pt; padding-bottom:2px;")
            vv.addWidget(t)
            vv.addWidget(self.value_label)
        def setValue(self, text: str):
            self.value_label.setText(text)

    def _chip(self, title: str, val: str, bg: str, fg: str) -> "MainWindow.ChipWidget":
        return MainWindow.ChipWidget(title, val, bg, fg, self)

    def _hline(self):
        line = QtWidgets.QFrame()
        shape_enum = getattr(QtWidgets.QFrame, 'Shape', QtWidgets.QFrame)
        line.setFrameShape(getattr(shape_enum, 'HLine'))
        line.setStyleSheet("color:#E5EAF0")
        return line

    def _log_card(self) -> QtWidgets.QFrame:
        card, v = self._card("📋 日志信息")
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
        self.log.setMinimumHeight(300)  # 减小最小高度，使用可折叠组件后可减少滚动需求
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
            self._mark_config_modified()
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
            self._mark_config_modified()
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
            self._mark_config_modified()
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
    
    def _validate_ftp_config(self) -> tuple:
        """
        验证FTP配置的有效性
        
        Returns:
            tuple: (是否有效, 错误消息列表)
        """
        errors = []
        
        # 如果不使用FTP，跳过验证
        if self.current_protocol == 'smb':
            return True, []
        
        self._append_log("🔍 正在验证FTP配置...")
        
        # 验证FTP服务器配置
        if self.current_protocol in ['ftp_server', 'both']:
            # 主机地址验证
            host = self.ftp_server_config.get('host', '').strip()
            if not host:
                errors.append("FTP服务器主机地址为空")
            elif host not in ['0.0.0.0', 'localhost', '127.0.0.1']:
                # 简单的IP格式验证
                import re
                if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', host):
                    errors.append(f"FTP服务器主机地址格式无效: {host}")
            
            # 端口验证
            port = self.ftp_server_config.get('port', 0)
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append(f"FTP服务器端口无效: {port}（范围：1-65535）")
            elif port < 1024 and port != 21:
                self._append_log(f"⚠️  FTP服务器使用特权端口 {port}，可能需要管理员权限")
            
            # 用户名验证
            username = self.ftp_server_config.get('username', '').strip()
            if not username:
                errors.append("FTP服务器用户名为空")
            elif len(username) < 3:
                errors.append("FTP服务器用户名至少需要3个字符")
            
            # 密码验证
            password = self.ftp_server_config.get('password', '').strip()
            if not password:
                errors.append("FTP服务器密码为空")
            elif len(password) < 6:
                errors.append("FTP服务器密码至少需要6个字符")
            
            # 共享目录验证
            share_folder = self.ftp_server_config.get('shared_folder', '').strip()
            if not share_folder:
                errors.append("FTP服务器共享目录为空")
            elif not os.path.exists(share_folder):
                errors.append(f"FTP服务器共享目录不存在: {share_folder}")
            elif not os.path.isdir(share_folder):
                errors.append(f"FTP服务器共享路径不是目录: {share_folder}")
            else:
                self._append_log(f"✓ FTP服务器共享目录有效: {share_folder}")
        
        # 验证FTP客户端配置
        if self.current_protocol in ['ftp_client', 'both']:
            # 主机地址验证
            host = self.ftp_client_config.get('host', '').strip()
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
            port = self.ftp_client_config.get('port', 0)
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append(f"FTP客户端端口无效: {port}（范围：1-65535）")
            
            # 用户名验证
            username = self.ftp_client_config.get('username', '').strip()
            if not username:
                errors.append("FTP客户端用户名为空")
            
            # 密码验证
            password = self.ftp_client_config.get('password', '').strip()
            if not password:
                errors.append("FTP客户端密码为空")
            
            # 远程路径验证
            remote_path = self.ftp_client_config.get('remote_path', '').strip()
            if not remote_path:
                errors.append("FTP客户端远程路径为空")
            elif not remote_path.startswith('/'):
                errors.append(f"FTP客户端远程路径应以 / 开头: {remote_path}")
        
        if errors:
            self._append_log(f"❌ FTP配置验证失败，发现 {len(errors)} 个错误")
        else:
            self._append_log("✓ FTP配置验证通过")
        
        return len(errors) == 0, errors

    def _save_config(self):
        """保存配置到文件"""
        # v2.2.0 权限检查：仅登录用户可保存配置
        if self.current_role == 'guest':
            self._append_log("❌ 未登录用户无权保存配置")
            self._toast('请先登录后再保存配置', 'warning')
            return
        
        self._append_log("💾 正在保存配置...")
        
        # v2.2.0 新增：保存前验证路径
        is_valid, errors = self._validate_paths()
        if not is_valid:
            error_msg = "\n".join(errors)
            self._append_log(f"❌ 路径验证失败，无法保存配置:\n{error_msg}")
            self._toast('路径验证失败，请检查配置', 'danger')
            return
        
        # v2.2.0 新增：验证FTP配置（如果使用FTP协议）
        if self.current_protocol != 'smb':
            is_valid, errors = self._validate_ftp_config()
            if not is_valid:
                error_msg = "\n".join(errors)
                self._append_log(f"❌ FTP配置验证失败，无法保存配置:\n{error_msg}")
                self._toast('FTP配置验证失败，请检查配置', 'danger')
                return
        
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
            'filter_tiff': self.cb_ext['.tiff'].isChecked(),
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
            'auto_delete_threshold': self.auto_delete_threshold,
            'auto_delete_keep_days': self.auto_delete_keep_days,
            'auto_delete_check_interval': self.auto_delete_check_interval,
            # v2.0 新增：FTP 协议配置
            'upload_protocol': self.current_protocol,
            # v2.2.0 新增：保存当前使用的协议模式
            'current_protocol': self.current_protocol,
            'ftp_server': {
                'host': self.ftp_server_host.text(),
                'port': self.ftp_server_port.value(),
                'username': self.ftp_server_user.text(),
                'password': self.ftp_server_pass.text(),
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
                'password': self.ftp_client_pass.text(),
                'remote_path': self.ftp_client_remote.text(),
                'timeout': self.ftp_client_timeout.value(),
                'retry_count': self.ftp_client_retry.value(),
                'passive_mode': self.cb_client_passive.isChecked(),
                'enable_tls': self.cb_client_tls.isChecked(),
            },
            'users': users,
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            
            # 保存成功后清除修改标记并更新保存的配置
            self.config_modified = False
            self.saved_config = cfg.copy()
            
            self._append_log("✓ 配置已成功保存到文件")
            self._toast('配置已保存', 'success')
        except Exception as e:
            self._append_log(f"❌ 配置保存失败: {e}")
            self._toast(f'保存失败: {e}', 'danger')

    def _load_config(self):
        """从配置文件加载设置"""
        self._append_log("📖 正在加载配置文件...")
        
        path = self.app_dir / 'config.json'
        if not path.exists():
            self._append_log("⚠ 配置文件不存在，使用默认配置")
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            
            self._append_log(f"✓ 配置文件加载成功")
            
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
            self.cb_ext['.tiff'].setChecked(cfg.get('filter_tiff', True))
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
                self.spin_max_rate.setEnabled(self.limit_upload_rate)
            
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
            
            # 根据去重开关状态启用/禁用子选项
            self.combo_hash.setEnabled(self.enable_deduplication)
            self.combo_strategy.setEnabled(self.enable_deduplication)
            
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
            self.auto_delete_threshold = cfg.get('auto_delete_threshold', 80)
            self.auto_delete_keep_days = cfg.get('auto_delete_keep_days', 10)
            self.auto_delete_check_interval = cfg.get('auto_delete_check_interval', 300)
            
            self.cb_enable_auto_delete.blockSignals(True)
            self.cb_enable_auto_delete.setChecked(self.enable_auto_delete)
            self.cb_enable_auto_delete.blockSignals(False)
            
            self.auto_del_folder_edit.setText(self.auto_delete_folder)
            self.spin_auto_del_threshold.setValue(self.auto_delete_threshold)
            self.spin_auto_del_keep_days.setValue(self.auto_delete_keep_days)
            self.spin_auto_del_interval.setValue(self.auto_delete_check_interval)
            
            # 根据开关状态启用/禁用子选项
            self.auto_del_folder_edit.setEnabled(self.enable_auto_delete)
            self.btn_choose_auto_del.setEnabled(self.enable_auto_delete)
            self.spin_auto_del_threshold.setEnabled(self.enable_auto_delete)
            self.spin_auto_del_keep_days.setEnabled(self.enable_auto_delete)
            self.spin_auto_del_interval.setEnabled(self.enable_auto_delete)
            
            # v2.0 新增：加载协议配置
            protocol = cfg.get('upload_protocol', 'smb')
            protocol_map = {
                'smb': 0,
                'ftp_server': 1,
                'ftp_client': 2,
                'both': 3
            }
            self.combo_protocol.setCurrentIndex(protocol_map.get(protocol, 0))
            
            # v2.2.0 新增：加载上次使用的协议模式
            saved_protocol = cfg.get('current_protocol', protocol)
            self.current_protocol = saved_protocol
            self._append_log(f"✓ 已加载上次协议模式: {saved_protocol}")
            # 更新协议状态显示
            self._update_protocol_status()
            
            # 加载 FTP 服务器配置
            ftp_server = cfg.get('ftp_server', {})
            self.ftp_server_host.setText(ftp_server.get('host', '0.0.0.0'))
            self.ftp_server_port.setValue(ftp_server.get('port', 2121))
            self.ftp_server_user.setText(ftp_server.get('username', 'upload_user'))
            self.ftp_server_pass.setText(ftp_server.get('password', 'upload_pass'))
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
            self.ftp_client_pass.setText(ftp_client.get('password', ''))
            self.ftp_client_remote.setText(ftp_client.get('remote_path', '/upload'))
            self.ftp_client_timeout.setValue(ftp_client.get('timeout', 30))
            self.ftp_client_retry.setValue(ftp_client.get('retry_count', 3))
            # v2.0 新增：加载高级选项
            self.cb_client_passive.setChecked(ftp_client.get('passive_mode', True))
            self.cb_client_tls.setChecked(ftp_client.get('enable_tls', False))
            
            # 保存已加载的配置（用于回退）
            self.saved_config = cfg.copy()
            self.config_modified = False
            
            self._append_log(f"✓ 已加载配置: 源={cfg.get('source_folder', '未设置')}")
            self._append_log(f"✓ 已加载配置: 目标={cfg.get('target_folder', '未设置')}")
            self._append_log(f"✓ 已加载配置: 备份={cfg.get('backup_folder', '未设置')}")
        except Exception as e:
            self._append_log(f"❌ 加载配置失败: {e}")

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
        
        # v2.0 新增：验证FTP配置（如果使用FTP协议）
        if self.current_protocol != 'smb':
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
                    self._save_config()
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
        # v2.1.1 修改：根据备份启用状态显示不同信息
        if self.enable_backup:
            self._append_log(f"  备份文件夹: {self.bak_edit.text()}")
        else:
            self._append_log(f"  备份功能: 已禁用（上传成功后将删除源文件）")
        self._append_log(f"  间隔时间: {self.spin_interval.value()}秒")
        self._append_log(f"  重试次数: {self.spin_retry.value()}次")
        
        filters = [ext for ext, cb in self.cb_ext.items() if cb.isChecked()]
        self._append_log(f"  文件类型: {', '.join(filters)}")
        self._append_log(f"  上传协议: {self.current_protocol}")
        
        # v2.0 新增：启动FTP服务器（如果需要）
        if self.current_protocol in ['ftp_server', 'both']:
            try:
                if not self.ftp_manager:
                    self.ftp_manager = FTPProtocolManager()  # type: ignore[misc]
                
                self._append_log("🔧 正在启动FTP服务器...")
                share_folder = self.ftp_server_config.get('shared_folder', '')
                if not share_folder or not os.path.exists(share_folder):
                    raise ValueError(f"FTP共享文件夹无效: {share_folder}")
                
                server_config = {
                    'host': self.ftp_server_config.get('host', '0.0.0.0'),
                    'port': self.ftp_server_config.get('port', 2121),
                    'username': self.ftp_server_config.get('username', 'upload_user'),
                    'password': self.ftp_server_config.get('password', 'upload_pass'),
                    'shared_folder': share_folder
                }
                
                success = self.ftp_manager.start_server(server_config)
                if not success:
                    raise RuntimeError("FTP服务器启动失败")
                
                server_status = self.ftp_manager.get_status()
                if server_status.get('server'):
                    srv = server_status['server']
                    self._append_log(f"✓ FTP服务器已启动:")
                    self._append_log(f"  地址: {srv['host']}:{srv['port']}")
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
                    port = self.ftp_server_config.get('port', 2121)
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
            self.ftp_client_config = {
                'host': self.ftp_client_host.text(),
                'port': self.ftp_client_port.value(),
                'username': self.ftp_client_user.text(),
                'password': self.ftp_client_pass.text(),
                'remote_path': self.ftp_client_remote.text(),
                'timeout': self.ftp_client_timeout.value(),
                'retry_count': self.ftp_client_retry.value(),
            }
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
            self.auto_delete_folder,
            self.auto_delete_threshold,
            self.auto_delete_keep_days,
            self.auto_delete_check_interval,
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
        self.worker.log.connect(self._append_log, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.stats.connect(self._on_stats, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.progress.connect(self._on_progress, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.file_progress.connect(self._on_file_progress, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.network_status.connect(self._on_network_status, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self._on_worker_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.status.connect(self._on_worker_status, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.ask_user_duplicate.connect(self._on_ask_duplicate, QtCore.Qt.ConnectionType.QueuedConnection)
        # v2.2.0 新增：连接错误通知信号
        self.worker.upload_error.connect(self._on_upload_error, QtCore.Qt.ConnectionType.QueuedConnection)
        # v2.2.0 新增：连接磁盘空间警告信号
        self.worker.disk_warning.connect(self._on_disk_warning, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker_thread.start()
        self._toast('开始上传', 'success')
        self._append_log("✓ 上传任务已启动")
        
        # v2.2.0 新增：显示通知
        self._show_notification(
            "上传已开始",
            f"正在上传文件到: {self.tgt_edit.text()}"
        )
        
        # v2.2.0 调试：打印开始上传后的按钮状态
        self._append_log(f"   [开始上传后快照] 源按钮={self.btn_choose_src.isEnabled()}, 目标按钮={self.btn_choose_tgt.isEnabled()}, 备份按钮={self.btn_choose_bak.isEnabled()}")
        self._append_log(f"   [开始上传后快照] 开始按钮={self.btn_start.isEnabled()}, 暂停按钮={self.btn_pause.isEnabled()}, 停止按钮={self.btn_stop.isEnabled()}")

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
        
        # v2.0 新增：停止FTP服务器（如果启动了）
        if self.ftp_manager:
            try:
                self._append_log("🔧 正在停止FTP服务...")
                self.ftp_manager.stop_all()
                self.ftp_manager = None
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
        # v2.2.0 调试：打印调用时的参数状态
        self._append_log(f"   [停止后权限] 当前角色={self.current_role}, 运行状态={self.is_running}, 备份启用={self.enable_backup}")
        
        # v2.2.0 重构：统一使用权限计算函数
        states = self._compute_control_states(self.current_role, self.is_running, self.enable_backup)
        
        # v2.2.0 调试：打印停止后的计算状态
        self._append_log(f"   [停止后计算] 源按钮={states['btn_choose_src']}, 目标按钮={states['btn_choose_tgt']}, 备份按钮={states['btn_choose_bak']}")
        self._append_log(f"   [停止后计算] 源只读={states['src_edit_readonly']}, 目标只读={states['tgt_edit_readonly']}")
        
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
        
        # v2.2.0 调试：验证停止后的实际状态
        actual_tgt = self.btn_choose_tgt.isEnabled() if hasattr(self, 'btn_choose_tgt') else None
        actual_src = self.btn_choose_src.isEnabled() if hasattr(self, 'btn_choose_src') else None
        self._append_log(f"   [停止后实际] 源按钮={actual_src}, 目标按钮={actual_tgt}")
        
        if actual_tgt is not None and actual_tgt != states['btn_choose_tgt']:
            self._append_log(f"   ⚠️ 警告：停止后目标按钮状态不一致！计算={states['btn_choose_tgt']}, 实际={actual_tgt}")
        
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
            dialog.resize(560, 300)

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
        """更新协议和FTP状态显示"""
        # 更新协议模式
        protocol_names = {
            'smb': 'SMB',
            'ftp_server': 'FTP服务器',
            'ftp_client': 'FTP客户端',
            'both': '混合模式'
        }
        protocol_text = protocol_names.get(self.current_protocol, 'SMB')
        self.lbl_protocol.setValue(protocol_text)
        
        # 更新FTP服务器状态（含图标指示器）
        if self.current_protocol in ['ftp_server', 'both']:
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
                except:
                    self.lbl_ftp_server.setValue("⚪ 未启动")
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
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
                except:
                    self.lbl_ftp_client.setValue("⚪ 未连接")
                    self.lbl_ftp_client.setStyleSheet(
                        "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
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
                if backup_path:
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
            except Exception as e:
                print(f"磁盘空间检查失败: {e}")
        
        # 提交到线程池异步执行
        try:
            self._log_executor.submit(update_disk_async)
        except Exception:
            pass
    
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
        self.setWindowState(self.windowState() & ~window_minimized | window_active)
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
        msg_box.setIcon(MessageBoxIcon.Information)
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
            MessageBoxButton.Yes | MessageBoxButton.No,
            MessageBoxButton.No
        )
        
        if reply == MessageBoxButton.Yes:
            if self.tray_icon:
                self.tray_icon.hide()
            QtWidgets.QApplication.quit()
    
    def _show_notification(self, title: str, message: str, icon_type: Optional[Any] = None):
        """显示系统通知
        
        Note: PySide6 6.x 的 showMessage API 有两种签名，我们使用 type: ignore[call-overload] 来忽略类型检查
        """
        if self.show_notifications and self.tray_icon and self.tray_icon.isVisible():
            if icon_type is None:
                icon_type = TrayIconType.Information
            self.tray_icon.showMessage(title, message, icon_type, 3000)  # type: ignore[call-overload]
    
    def changeEvent(self, event):
        """窗口状态改变事件"""
        if event.type() == EventType.WindowStateChange:
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
        
        # 关闭日志线程池
        try:
            self._log_executor.shutdown(wait=False)
        except Exception:
            pass
        
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


