# -*- coding: utf-8 -*-
"""
Main window UI module.
"""
import copy
import time
import datetime
import queue
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Any, TYPE_CHECKING, Protocol

# 创建logger
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
    from PySide6.QtNetwork import QLocalServer, QLocalSocket  # type: ignore[import-not-found]
    Signal = QtCore.Signal
else:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    Signal = QtCore.Signal

from src.core import (
    get_resource_path,
    get_app_version,
    get_app_title,
)
from src.core.i18n import t, set_language, get_language, add_language_listener, SUPPORTED_LANGUAGES  # v3.0.2: 多语言支持
from src.models import (
    ApplicationSettings,
    AutoCleanupRequest,
    PermissionContext,
    UploadTaskRequest,
    UserRole,
)
from src.ui.dialogs import ChangePasswordDialog, DiskCleanupDialog, LoginDialog
from src.ui.panels import UploadFoldersPanel, UploadLogPanel, UploadSettingsPanel, UploadStatusPanel
from src.ui.widgets import Toast

APP_VERSION = get_app_version()
APP_TITLE = get_app_title()
class SettingsGateway(Protocol):
    """Minimal configuration boundary required by the View."""

    last_error: str

    @property
    def config_exists(self) -> bool:
        ...

    def load_raw(self) -> dict:
        ...

    def save_raw(self, config: dict, preserve_users: bool = True) -> bool:
        ...

    def decode_ftp_password(self, section: dict, default: str = "") -> str: ...
    def encode_ftp_password(self, password: str, label: str) -> tuple[str, str]: ...


class AuthGateway(Protocol):
    """Authentication and authorization boundary required by the View."""

    @property
    def current_role(self) -> UserRole:
        ...

    @property
    def default_password_roles(self) -> list[UserRole]:
        ...

    @property
    def password_change_required(self) -> bool:
        ...

    def load_users(self, users: Any) -> None:
        ...

    def set_current_role(self, role: UserRole) -> None:
        ...

    def login(self, role: UserRole, password: str) -> Any:
        ...

    def logout(self) -> None:
        ...

    def change_password(
        self,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> Any:
        ...

    def compute_permissions(self, context: PermissionContext) -> Any:
        ...

    def can_manage_disk_cleanup(self) -> bool:
        ...

    def is_authenticated(self) -> bool:
        ...

    def can_manage_ftp(self) -> bool:
        ...

    def password_change_block_reason(self) -> str:
        ...

    def disk_cleanup_block_reason(self) -> str:
        ...


class FTPGateway(Protocol):
    @property
    def available(self) -> bool: ...
    @property
    def tls_server_available(self) -> bool: ...
    @property
    def server_started_independently(self) -> bool: ...
    @property
    def server_started_by_upload(self) -> bool: ...
    def set_event_listener(self, listener: Any) -> None: ...
    def validate_server(self, config: dict) -> Any: ...
    def validate_client(self, config: dict) -> Any: ...
    def validate_configuration(
        self,
        enable_server: bool,
        protocol: str,
        server_config: dict,
        client_config: dict,
    ) -> Any: ...
    def test_server(self, config: dict) -> Any: ...
    def test_client(self, config: dict) -> Any: ...
    def start_client_test(self, config: dict, event_callback: Any) -> Any: ...
    def cancel_client_test(self) -> Any: ...
    @property
    def client_test_running(self) -> bool: ...
    def start_server(self, config: dict, source: str) -> Any: ...
    def stop_server(self) -> Any: ...
    def stop_upload_server(self) -> Any: ...
    def is_server_running(self) -> bool: ...
    def server_status(self) -> dict: ...
    def shutdown(self) -> None: ...


class UploadGateway(Protocol):
    @property
    def state(self) -> Any: ...
    def set_event_listener(self, listener: Any) -> None: ...
    def validate_request(self, request: UploadTaskRequest) -> Any: ...
    def start(self, request: UploadTaskRequest) -> Any: ...
    def pause(self) -> Any: ...
    def resume(self) -> Any: ...
    def stop(self) -> Any: ...
    def set_running(self, running: bool) -> None: ...
    def resolve_duplicate(
        self, payload: Any, choice: str, apply_all: bool = False
    ) -> None: ...
    def ftp_client_status(self) -> dict: ...
    def archive_queue_size(self) -> int: ...
    def request_stop_all(self) -> Any: ...
    @property
    def has_running_workers(self) -> bool: ...
    def shutdown(self) -> None: ...


class CleanupGateway(Protocol):
    def set_auto_listener(self, listener: Any) -> None: ...
    def configure_index(self, request: AutoCleanupRequest) -> bool: ...
    def record_generated_file(self, path: str, source: str = "upload") -> bool: ...
    def validate_auto_request(self, request: AutoCleanupRequest) -> Any: ...
    def validate_folder_group(self, folders: Any) -> Any: ...
    def maybe_trigger_auto_cleanup(
        self, request: AutoCleanupRequest, reason: str = ""
    ) -> bool: ...
    def cancel_auto_cleanup(self) -> None: ...
    def cancel(self) -> None: ...
    @property
    def has_running_workers(self) -> bool: ...
    def shutdown(self) -> None: ...


class RuntimeGateway(Protocol):
    @property
    def app_dir(self) -> Path: ...
    def initialize(self) -> Any: ...
    def append_log(self, line: str) -> None: ...
    def request_disk_space(
        self,
        target_path: str,
        backup_path: str,
        backup_enabled: bool,
        network_available: bool,
        callback: Any,
    ) -> None: ...
    def reconcile_startup(self, auto_enabled: bool, explicit: bool = False) -> Any: ...
    def disable_startup(self) -> Any: ...
    def shutdown(self) -> None: ...


class LifecycleGateway(Protocol):
    def request_shutdown(self, view: Any) -> Any: ...
    def shutdown(self, view: Any) -> Any: ...


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
    _ftp_client_test_signal = Signal(dict)
    _permission_changed_signal = Signal()  # 角色/运行状态变更
    app_exit_requested = Signal()
    
    def __init__(
        self,
        settings_controller: Optional[SettingsGateway] = None,
        auth_controller: Optional[AuthGateway] = None,
        ftp_controller: Optional[FTPGateway] = None,
        upload_controller: Optional[UploadGateway] = None,
        cleanup_controller: Optional[CleanupGateway] = None,
        runtime_controller: Optional[RuntimeGateway] = None,
        lifecycle_controller: Optional[LifecycleGateway] = None,
    ):
        super().__init__()
        if settings_controller is None:
            raise ValueError("MainWindow requires a SettingsController")
        if auth_controller is None:
            raise ValueError("MainWindow requires an AuthController")
        if ftp_controller is None:
            raise ValueError("MainWindow requires an FTPController")
        if upload_controller is None:
            raise ValueError("MainWindow requires an UploadController")
        if cleanup_controller is None:
            raise ValueError("MainWindow requires a CleanupController")
        if runtime_controller is None:
            raise ValueError("MainWindow requires a RuntimeController")
        if lifecycle_controller is None:
            raise ValueError("MainWindow requires a LifecycleController")
        self.settings_controller = settings_controller
        self.auth_controller = auth_controller
        self.ftp_controller = ftp_controller
        self.upload_controller = upload_controller
        self.cleanup_controller = cleanup_controller
        self.runtime_controller = runtime_controller
        self.lifecycle_controller = lifecycle_controller
        self._exit_pending = False
        self._shutdown_timeout_pending = False
        self.app_exit_requested.connect(self._handle_app_exit_requested)
        self.ftp_controller.set_event_listener(self._emit_ftp_server_event)
        self.upload_controller.set_event_listener(self._handle_upload_event)
        self.cleanup_controller.set_auto_listener(self._handle_auto_cleanup_event)
        self.setWindowTitle(APP_TITLE)
        self._init_responsive_metrics()
        self.setMinimumSize(self.window_min_width, self.window_min_height)
        self.resize(self.window_initial_width, self.window_initial_height)
        self.app_dir = self.runtime_controller.app_dir
        
        # 连接内部信号
        self._disk_update_signal.connect(self.render_disk_space)
        self._async_log_signal.connect(self._append_log)
        self._ftp_server_event_signal.connect(self._handle_ftp_server_event)
        self._ftp_client_test_signal.connect(self._handle_ftp_client_test_event)
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
        self.config_modified = False  # 配置是否被修改
        self._config_loading = False  # 配置加载期间守卫标志
        self.saved_config = {}  # 保存的配置（用于回退）
        self.last_config_save_error = ''
        self.disk_check_interval = 5  # 磁盘空间检查间隔（秒）
        self.file_upload_delay_seconds = 1.5  # 扫描到文件后的上传延迟（秒，仅配置文件）
        self.disk_check_counter = 0  # 磁盘空间检查计数器
        
        # v1.9 新增：文件去重配置
        self.enable_deduplication = False  # 是否启用智能去重
        self.hash_algorithm = 'md5'  # 哈希算法：md5 或 sha256
        self.duplicate_strategy = 'ask'  # 去重策略：skip, rename, overwrite, ask
        
        # v1.9 新增：网络监控配置
        self.network_check_interval = 10  # 网络检测间隔（秒）
        self.network_auto_pause = True  # 网络断开自动暂停
        self.network_auto_resume = True  # 网络恢复自动继续
        
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
            'cert_file': '',
            'key_file': '',
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
        
        self.runtime_controller.initialize()
        self._auto_cleanup_timer = QtCore.QTimer(self)
        self._auto_cleanup_timer.timeout.connect(self._auto_cleanup_tick)
        self._auto_cleanup_last_warn = 0.0
        
        # v2.2.0 新增：系统托盘配置
        self.minimize_to_tray = True  # 最小化到托盘
        self.show_notifications = True  # 显示通知
        self.tray_icon = None  # 托盘图标对象
        
        # v2.3.0 新增：速率限制配置
        self.limit_upload_rate = False
        self.max_upload_rate_mbps = 10.0
        
        # UI
        self._build_ui()
        self._load_config()
        self._update_auto_cleanup_schedule()
        self.cleanup_controller.configure_index(
            self._collect_auto_cleanup_request("startup")
        )
        self._apply_theme()
        self._update_ui_permissions()
        
        # v2.2.0 新增：初始化系统托盘
        self._init_tray_icon()
        
        # 自动运行检查
        if self.auto_run_on_startup:
            QtCore.QTimer.singleShot(1000, self._auto_start_upload)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def current_role(self) -> str:
        """Compatibility view of the controller-owned role state."""
        return self.auth_controller.current_role.value

    @current_role.setter
    def current_role(self, role: str) -> None:
        self.auth_controller.set_current_role(UserRole(role))

    @property
    def default_password_roles(self) -> List[str]:
        labels = {
            UserRole.USER: '用户',
            UserRole.ADMIN: '管理员',
        }
        return [labels[role] for role in self.auth_controller.default_password_roles]

    @property
    def is_running(self) -> bool:
        return self.upload_controller.state.is_running

    @is_running.setter
    def is_running(self, running: bool) -> None:
        self.upload_controller.set_running(bool(running))

    @property
    def is_paused(self) -> bool:
        return self.upload_controller.state.is_paused

    @property
    def start_time(self) -> Optional[float]:
        return self.upload_controller.state.start_time

    @property
    def uploaded(self) -> int:
        return self.upload_controller.state.uploaded

    @property
    def failed(self) -> int:
        return self.upload_controller.state.failed

    @property
    def skipped(self) -> int:
        return self.upload_controller.state.skipped

    @property
    def network_status(self) -> str:
        return self.upload_controller.state.network_status.value

    def _handle_upload_event(self, event: dict) -> None:
        """Render controller-normalized upload events on the UI thread."""
        event_type = event.get("type", "")
        if event_type == "log":
            self._append_log(str(event.get("message", "")))
        elif event_type == "stats":
            self.render_upload_stats(
                int(event.get("uploaded", 0)),
                int(event.get("failed", 0)),
                int(event.get("skipped", 0)),
                str(event.get("rate", "0 MB/s")),
            )
        elif event_type == "progress":
            self.render_upload_progress(
                int(event.get("current", 0)),
                int(event.get("total", 0)),
                str(event.get("filename", "")),
            )
        elif event_type == "file_progress":
            self.render_file_progress(
                str(event.get("filename", "")), int(event.get("progress", 0))
            )
        elif event_type == "network_status":
            self.render_network_status(str(event.get("status", "unknown")))
        elif event_type == "status":
            self.render_worker_status(str(event.get("status", "stopped")))
        elif event_type == "finished":
            self.render_upload_finished()
        elif event_type == "duplicate":
            self._on_ask_duplicate(event.get("payload", {}))
        elif event_type == "upload_error":
            self.render_upload_error(
                str(event.get("filename", "")), str(event.get("message", ""))
            )
        elif event_type == "disk_warning":
            self._on_disk_warning(
                float(event.get("target_percent", 0)),
                float(event.get("backup_percent", 0)),
                int(event.get("threshold", 0)),
            )
        elif event_type == "disk_cleanup_needed":
            self._on_worker_disk_cleanup_needed()
        elif event_type == "local_file_generated":
            self.cleanup_controller.record_generated_file(
                str(event.get("path", "")), str(event.get("source", "upload"))
            )

    def _handle_auto_cleanup_event(self, event: dict) -> None:
        """Render automatic cleanup events emitted by the controller."""
        if event.get("type") == "log":
            self._emit_async_log(str(event.get("message", "")))

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

    def _apply_theme(self):
        stylesheet = """
            QWidget{font-family:'Microsoft YaHei UI', 'Segoe UI'; font-size:11pt; color:#1F2937; background:#E3F2FD;}
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
        logo_label = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(str(logo_path))
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaledToHeight(self._scale_px(40, 28))
            logo_label.setPixmap(scaled_pixmap)
            logo_label.setStyleSheet("background: transparent;")
            header.addWidget(logo_label)
            header.addSpacing(self._scale_px(12, 8))
        else:
            logger.warning("⚠️ Logo 文件加载失败: %s", logo_path)
        
        self.header_title = QtWidgets.QLabel(t('header_title'))
        self.header_title.setObjectName("Title")
        ver = QtWidgets.QLabel(f"v{APP_VERSION} (Qt)")
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

    def _folder_card(self) -> QtWidgets.QWidget:
        self.upload_folders_panel = UploadFoldersPanel(self)
        return self.upload_folders_panel

    def _settings_card(self) -> QtWidgets.QWidget:
        self.upload_settings_panel = UploadSettingsPanel(self)
        return self.upload_settings_panel

    def _control_card(self) -> QtWidgets.QFrame:
        card, v, self.title_control = self._card("🎮 操作控制", "card_control")
        
        # primary start - 优化按钮尺寸
        self.btn_start = QtWidgets.QPushButton("▶ 开始上传")
        self.btn_start.setProperty("class", "Primary")
        self.btn_start.setMinimumHeight(self._scale_px(35, 30))
        self.btn_start.clicked.connect(self._request_start_upload)
        v.addWidget(self.btn_start)
        # secondary pause/stop
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(self._scale_px(12, 8))
        self.btn_pause = QtWidgets.QPushButton("⏸ 暂停上传")
        self.btn_pause.setProperty("class", "Warning")
        self.btn_pause.setMinimumHeight(self._scale_px(35, 30))
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_upload_pause)
        self.btn_stop = QtWidgets.QPushButton("⏹ 停止上传")
        self.btn_stop.setProperty("class", "Danger")
        self.btn_stop.setMinimumHeight(self._scale_px(35, 30))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._request_stop_upload)
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
        self.btn_save.clicked.connect(self._request_save_settings)
        self.btn_more = QtWidgets.QToolButton()
        self.btn_more.setText("更多 ▾")
        self.btn_more.setMinimumHeight(self._scale_px(30, 28))
        popup_enum = getattr(QtWidgets.QToolButton, 'ToolButtonPopupMode', QtWidgets.QToolButton)
        self.btn_more.setPopupMode(getattr(popup_enum, 'InstantPopup'))
        menu = QtWidgets.QMenu(self)
        act_clear = menu.addAction("🗑️ 清空日志")
        act_clear.triggered.connect(self.clear_log_view)
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
        self.auth_controller.logout()
        self.role_label.setText(t('role_guest'))
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        self._update_ui_permissions()
        self._toast(t('logged_out'), 'info')

    def _compute_control_states(self) -> dict:
        """Collect View context and request a permission render model."""
        context = PermissionContext(
            is_running=self.is_running,
            enable_backup=self.enable_backup,
            protocol_uses_ftp=getattr(self, 'current_protocol', 'smb') != 'smb',
            ftp_server_enabled=bool(getattr(self, 'enable_ftp_server', False)),
            ftp_server_running=bool(getattr(self, '_is_ftp_server_running', lambda: False)()),
            dedup_enabled=bool(getattr(self, 'enable_deduplication', False)),
            rate_limit_enabled=bool(getattr(self, 'limit_upload_rate', False)),
        )
        return self.auth_controller.compute_permissions(context).to_mapping()

    def _can_manage_disk_cleanup(self) -> bool:
        """当前角色是否允许执行磁盘清理相关操作。"""
        return self.auth_controller.can_manage_disk_cleanup()

    def _get_disk_cleanup_block_reason(self) -> str:
        """获取磁盘清理被禁止时的原因。"""
        return self.auth_controller.disk_cleanup_block_reason()

    def render_permissions(self):
        """根据当前角色更新UI控件的启用状态"""
        logger.debug(f"更新权限: 当前角色={self.current_role}, 运行状态={'运行中' if self.is_running else '已停止'}")
        
        # 计算统一控件状态
        states = self._compute_control_states()
        
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
            'ftp_server_cert', 'btn_choose_ftp_cert',
            'ftp_server_key', 'btn_choose_ftp_key',
            'ftp_server_max_conn', 'ftp_server_max_conn_per_ip', 'btn_test_ftp_server',
            'btn_toggle_server_pass',
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(states['ftp_server_controls'])
        if hasattr(self, 'cb_server_tls'):
            tls_available = bool(
                getattr(self.ftp_controller, 'tls_server_available', False)
            )
            self.cb_server_tls.setEnabled(
                states['ftp_server_controls'] and tls_available
            )
            if not tls_available:
                self.cb_server_tls.setToolTip(
                    'FTPS 服务器不可用：当前运行环境缺少 pyOpenSSL'
                )
        self._update_ftp_tls_controls()
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
        if self._task_requests_blocked():
            self._apply_exit_pending_controls()
        
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

    def _apply_exit_pending_controls(self) -> None:
        """退出等待期间禁止所有可创建或改变后台任务的入口。"""
        for name in ("btn_start", "btn_pause", "btn_stop", "btn_toggle_ftp_server"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(False)
        for name in ("tray_start_action", "tray_pause_action", "tray_stop_action"):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(False)
        menu_items = getattr(self, "menu_items", {})
        disk_cleanup = menu_items.get("disk_cleanup") if menu_items else None
        if disk_cleanup is not None:
            disk_cleanup.setEnabled(False)

    def _task_requests_blocked(self) -> bool:
        return self._exit_pending or self._shutdown_timeout_pending

    @property
    def cleanup_role(self) -> str:
        return self.current_role

    @property
    def cleanup_settings_error(self) -> str:
        return self.last_config_save_error

    def cleanup_settings_snapshot(self) -> dict:
        """Public, presentation-safe state consumed by the cleanup dialog."""
        return {
            'backup_path': self.bak_edit.text(),
            'target_path': self.tgt_edit.text(),
            'enable_auto_delete': self.enable_auto_delete,
            'auto_delete_folders': list(self.auto_delete_folders),
            'auto_delete_folder': self.auto_delete_folder,
            'auto_delete_threshold': self.auto_delete_threshold,
            'auto_delete_target_percent': self.auto_delete_target_percent,
            'auto_delete_check_interval': self.auto_delete_check_interval,
            'auto_delete_keep_days': self.auto_delete_keep_days,
            'auto_delete_formats': list(self.auto_delete_formats),
            'auto_delete_use_trash': self.auto_delete_use_trash,
        }

    def save_auto_cleanup_settings(self, config: dict) -> bool:
        return self._save_auto_cleanup_config(config)

    def clear_log_view(self):
        try:
            self.log.clear()
            self._toast('已清空日志', 'info')
        except Exception:
            pass

    def _load_user_passwords(self, cfg: dict) -> None:
        """从配置中读取角色密码哈希，并标记默认弱口令。"""
        self.auth_controller.load_users(cfg.get('users', {}))

    def _warn_if_default_password_in_use(self, role: str) -> None:
        """登录成功后明确告知强制改密状态。"""
        weak_roles = set(self.auth_controller.default_password_roles)
        if role == 'user' and UserRole.USER in weak_roles:
            self._append_log("⚠️ 用户角色仍在使用默认口令，修改密码前已禁用业务操作。")
            self._toast('当前使用默认口令，必须先修改密码', 'warning')
        elif role == 'admin' and UserRole.ADMIN in weak_roles:
            self._append_log("⚠️ 管理员仍在使用默认口令，修改密码前已禁用业务操作。")
            self._toast('管理员使用默认口令，必须先修改密码', 'warning')

    def _write_config_payload(self, cfg: dict) -> bool:
        """将配置写回磁盘，并保存错误信息。"""
        self.last_config_save_error = ''
        if self.settings_controller is None:
            self.last_config_save_error = '配置控制器未初始化'
            return False
        success = self.settings_controller.save_raw(cfg, preserve_users=False)
        if not success:
            self.last_config_save_error = self.settings_controller.last_error or '配置保存失败'
        return success

    def _read_config_payload(self) -> dict:
        """通过配置控制器读取兼容的原始配置字典。"""
        if self.settings_controller is None:
            return ApplicationSettings().to_config()
        return self.settings_controller.load_raw()

    def _emit_async_log(self, message: str) -> None:
        """从后台线程安全地投递日志到主线程。"""
        try:
            self._async_log_signal.emit(message)
        except Exception:
            pass
    
    def _show_disk_cleanup(self):
        """显示磁盘清理对话框"""
        if self._task_requests_blocked():
            self._append_log("⚠️ 退出流程中，已拒绝新的磁盘清理任务")
            return
        reason = self._get_disk_cleanup_block_reason()
        if reason:
            self._append_log(f"⚠️ 磁盘清理已阻止: {reason}")
            self._toast(reason, 'warning')
            return
        try:
            dialog = DiskCleanupDialog(
                self,
                self.cleanup_controller,
                settings_gateway=self,
            )
            dialog.exec()
        except Exception as e:
            self._append_log(f"❌ 打开磁盘清理对话框失败: {e}")
            self._toast('打开磁盘清理失败', 'danger')

    def _show_login(self):
        """显示权限登录对话框"""
        dialog = LoginDialog(
            self,
            scale_px=self._scale_px,
            dialog_size=self._clamped_dialog_size(400, 200),
        )
        dialog.validation_failed.connect(
            lambda message: self._toast(message, "warning")
        )
        dialog.login_requested.connect(
            lambda role, password: self._on_login_requested(dialog, role, password)
        )
        dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()

    def _on_login_requested(
        self, dialog: LoginDialog, role: UserRole, password: str
    ) -> None:
        result = self.auth_controller.login(role, password)
        if not result.success:
            dialog.render_authentication_failed()
            self._toast(t('wrong_password'), 'danger')
            return

        self.render_authenticated_role(result.role)
        success_text = (
            t('user_login_success')
            if result.role is UserRole.USER
            else t('admin_login_success')
        )
        self._append_log("=" * 50)
        self._append_log(success_text)
        self._toast(success_text, 'success')
        self._update_ui_permissions()
        self._warn_if_default_password_in_use(result.role.value)
        dialog.render_authenticated()
        if result.uses_default_password:
            QtCore.QTimer.singleShot(0, self._show_change_password)

    def render_authenticated_role(self, role: UserRole) -> None:
        if role is UserRole.USER:
            self.role_label.setText(t('role_user'))
            self.role_label.setStyleSheet(
                "background:#E3F2FD; color:#1976D2; padding:6px 12px; "
                "border-radius:6px; font-weight:700;"
            )
        else:
            self.role_label.setText(t('role_admin'))
            self.role_label.setStyleSheet(
                "background:#DCFCE7; color:#166534; padding:6px 12px; "
                "border-radius:6px; font-weight:700;"
            )

    def _show_change_password(self):
        """显示修改密码对话框"""
        block_reason = self.auth_controller.password_change_block_reason()
        if block_reason:
            self._toast(block_reason, 'warning')
            return
        
        dialog = ChangePasswordDialog(
            self,
            scale_px=self._scale_px,
            dialog_size=self._clamped_dialog_size(400, 300),
        )
        if (
            self.auth_controller.password_change_required
            or self.auth_controller.current_role is UserRole.USER
        ):
            target = self.auth_controller.current_role
            index = dialog.target_combo.findData(target)
            if index >= 0:
                dialog.target_combo.setCurrentIndex(index)
            dialog.target_combo.setEnabled(False)
        dialog.change_requested.connect(
            lambda role, old, new, confirm: self._on_change_password_requested(
                dialog, role, old, new, confirm
            )
        )
        dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()

    def _on_change_password_requested(
        self,
        dialog: ChangePasswordDialog,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> None:
        result = self.auth_controller.change_password(
            target_role,
            old_password,
            new_password,
            confirm_password,
        )
        if not result.success:
            dialog.render_change_failed()
            kind = 'danger' if '错误' in result.error or '失败' in result.error else 'warning'
            self._toast(result.error, kind)
            return

        success_text = (
            '用户密码修改成功！'
            if target_role is UserRole.USER
            else '管理员密码修改成功！'
        )
        self._toast(success_text, 'success')
        self._append_log(f"✓ 密码已保存: {target_role.value}")
        self._update_ui_permissions()
        dialog.render_changed()

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

    def _update_ftp_tls_controls(self, _checked: bool = False) -> None:
        """证书输入只在 TLS 启用且服务器配置可编辑时开放。"""
        tls_enabled = bool(
            getattr(self, 'cb_server_tls', None)
            and self.cb_server_tls.isChecked()
            and self.cb_server_tls.isEnabled()
        )
        for name in (
            'ftp_server_cert', 'btn_choose_ftp_cert',
            'ftp_server_key', 'btn_choose_ftp_key',
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(tls_enabled)

    def _choose_ftp_cert(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            t('select_tls_cert'),
            self.ftp_server_cert.text(),
            "Certificate (*.pem *.crt *.cer);;All Files (*)",
        )
        if path:
            self.ftp_server_cert.setText(path)
            self.config_modified = True

    def _choose_ftp_key(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            t('select_tls_key'),
            self.ftp_server_key.text(),
            "Private Key (*.pem *.key);;All Files (*)",
        )
        if path:
            self.ftp_server_key.setText(path)
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
            'cert_file': self.ftp_server_cert.text().strip(),
            'key_file': self.ftp_server_key.text().strip(),
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

    def _validate_ftp_server_config_only(self, server_cfg: dict) -> List[str]:
        """只验证内置 FTP 服务器配置，不检查客户端或上传路径。"""
        result = self.ftp_controller.validate_server(server_cfg)
        for warning in result.warnings:
            self._append_log(f"⚠️  {warning}")
        if result.is_valid:
            self._append_log(f"✓ FTP服务器共享目录有效: {server_cfg.get('shared_folder', '')}")
        return list(result.errors)
    
    def _test_ftp_server_config(self):
        """测试FTP服务器配置"""
        self._append_log("🧪 开始测试FTP服务器配置...")
        server_cfg = self._collect_ftp_server_config()
        self.ftp_server_config = copy.deepcopy(server_cfg)
        self._append_log(
            f"🔧 正在测试FTP服务器 {server_cfg['host']}:{server_cfg['port']}..."
        )
        result = self.ftp_controller.test_server(server_cfg)
        for warning in result.warnings:
            self._append_log(f"⚠️  {warning}")
        if result.success:
            self._append_log("✓ FTP服务器测试成功！")
            self._append_log(f"  地址: {server_cfg['host']}:{server_cfg['port']}")
            self._append_log(f"  用户: {server_cfg['username']}")
            self._append_log(f"  共享: {server_cfg['shared_folder']}")
            self._append_log("✓ 测试服务器已停止")
            QtWidgets.QMessageBox.information(
                self,
                "测试成功",
                f"FTP服务器配置有效！\n\n"
                f"地址: {server_cfg['host']}:{server_cfg['port']}\n"
                f"用户: {server_cfg['username']}\n"
                f"共享: {server_cfg['shared_folder']}",
            )
            return

        details = "\n".join(result.errors) or result.message or "FTP服务器无法启动"
        self._append_log(f"❌ FTP服务器测试失败: {details}")
        QtWidgets.QMessageBox.critical(self, "测试失败", details)

    def _is_ftp_server_running(self) -> bool:
        return self.ftp_controller.is_server_running()

    def _emit_ftp_server_event(self, event: dict) -> None:
        """FTP 服务器线程回调入口，转发到 UI 线程处理。"""
        try:
            self._ftp_server_event_signal.emit(event)
        except Exception as e:
            logger.debug(f"转发FTP事件失败: {type(e).__name__}: {e}")

    def _toggle_ftp_server_only(self):
        if self._task_requests_blocked():
            self._append_log("⚠️ 退出流程中，已拒绝 FTP 服务状态变更")
            return
        if self._is_ftp_server_running():
            self._stop_ftp_server_only(manual=True)
        else:
            self._start_ftp_server_only(manual=True)

    def _start_ftp_server_only(self, manual: bool = False) -> bool:
        """独立启动内置 FTP 服务器，不创建上传 worker。"""
        if self._task_requests_blocked():
            self._append_log("⚠️ 退出流程中，已拒绝新的 FTP 服务任务")
            return False
        if manual and not self.auth_controller.can_manage_ftp():
            self._append_log("❌ 仅管理员可启动FTP服务器")
            self._toast('仅管理员可启动FTP服务器', 'warning')
            return False
        if not self.ftp_controller.available:
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
        self._append_log("🔧 正在启动FTP服务器（独立模式）...")
        result = self.ftp_controller.start_server(server_cfg, source="independent")
        for warning in result.warnings:
            self._append_log(f"⚠️  {warning}")
        if result.success:
            self.enable_ftp_server = True
            self.cb_enable_ftp_server.blockSignals(True)
            self.cb_enable_ftp_server.setChecked(True)
            self.cb_enable_ftp_server.blockSignals(False)
            address = result.status.get('address') or f"{server_cfg.get('host')}:{server_cfg.get('port')}"
            self._append_log(f"✓ FTP服务器已启动（独立模式）: {address}")
            self._toast('FTP服务器已启动', 'success')
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        details = "\n".join(result.errors) or result.message or "未知错误"
        self._append_log(f"❌ FTP服务器启动失败: {details}")
        self._toast(f'FTP服务器启动失败: {details}', 'danger')
        if manual and result.errors:
            QtWidgets.QMessageBox.critical(self, "FTP服务器配置错误", details)
        self._update_ui_permissions()
        self._update_protocol_status()
        return False

    def _stop_ftp_server_only(self, manual: bool = False) -> bool:
        """独立停止内置 FTP 服务器，不影响上传 worker。"""
        if manual and not self.auth_controller.can_manage_ftp():
            self._append_log("❌ 仅管理员可停止FTP服务器")
            self._toast('仅管理员可停止FTP服务器', 'warning')
            return False
        if not self._is_ftp_server_running():
            self._append_log("ℹ️ FTP服务器未运行")
            self.ftp_controller.stop_server()
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        self._append_log("🔧 正在停止FTP服务器...")
        result = self.ftp_controller.stop_server()
        if result.success:
            self._append_log("✓ FTP服务器已停止")
            self._toast('FTP服务器已停止', 'info')
            self._update_ui_permissions()
            self._update_protocol_status()
            return True
        self._append_log(f"⚠️ 停止FTP服务器时出错: {result.message}")
        self._update_ui_permissions()
        self._update_protocol_status()
        return False

    def _handle_ftp_server_event(self, event: dict):
        message = event.get("display_message", "")
        if message:
            self._append_log(message)
    
    def _test_ftp_client_connection(self):
        """启动或取消后台 FTP 客户端连接测试。"""
        if self.ftp_controller.client_test_running:
            result = self.ftp_controller.cancel_client_test()
            self._append_log(result.message or "已请求取消FTP客户端连接测试")
            return
        self._append_log("🔌 开始测试FTP客户端连接...")
        client_cfg = self._collect_ftp_client_config()
        self.ftp_client_config = copy.deepcopy(client_cfg)
        self._append_log(
            f"🔗 正在连接FTP服务器 {client_cfg['host']}:{client_cfg['port']}..."
        )
        result = self.ftp_controller.start_client_test(
            client_cfg, self._ftp_client_test_signal.emit
        )
        if result.success:
            self.btn_test_ftp_client.setText("取消测试")
            return

        details = "\n".join(result.errors) or result.message or "FTP客户端连接失败"
        self._append_log(f"❌ FTP客户端连接测试无法启动: {details}")
        QtWidgets.QMessageBox.critical(self, "测试失败", details)

    def _handle_ftp_client_test_event(self, event: dict) -> None:
        """只在 GUI 线程渲染 FTP 测试进度和最终结果。"""
        event_type = str(event.get("type", ""))
        if event_type == "progress":
            self._append_log(
                f"🔄 FTP连接尝试 {event.get('attempt', 0)}/{event.get('total', 0)}"
            )
            return

        self.btn_test_ftp_client.setText(t('test_connection'))
        self._update_ui_permissions()
        result = event.get("result")
        if event_type == "cancelled":
            self._append_log("⏹️ FTP客户端连接测试已取消")
            return
        if result is not None and result.success:
            self._append_log("✓ FTP客户端连接测试成功！")
            client_cfg = self.ftp_client_config
            self._append_log(f"  服务器: {client_cfg['host']}:{client_cfg['port']}")
            self._append_log(f"  用户: {client_cfg['username']}")
            self._append_log(f"  远程路径: {client_cfg['remote_path']}")
            self._append_log("✓ 已断开连接")
            if self._exit_pending:
                return
            QtWidgets.QMessageBox.information(
                self,
                "测试成功",
                f"FTP客户端连接成功！\n\n"
                f"服务器: {client_cfg['host']}:{client_cfg['port']}\n"
                f"用户: {client_cfg['username']}\n"
                f"远程路径: {client_cfg['remote_path']}",
            )
            return

        details = (
            "\n".join(result.errors) or result.message or "FTP客户端连接失败"
            if result is not None
            else "FTP客户端连接测试异常结束"
        )
        self._append_log(f"❌ FTP客户端连接测试失败: {details}")
        if not self._exit_pending:
            QtWidgets.QMessageBox.critical(self, "测试失败", details)
    
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
        """Forward a startup-registration request to the runtime controller."""
        if not self.auth_controller.is_authenticated():
            self._toast('需要登录后才能设置开机自启动', 'warning')
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(not checked)
            self.cb_auto_start_windows.blockSignals(False)
            return

        result = (
            self.runtime_controller.reconcile_startup(True, explicit=True)
            if checked
            else self.runtime_controller.disable_startup()
        )
        for message in result.messages:
            self._append_log(message)
        if not result.success:
            self._toast(f'设置开机自启动失败: {result.error}', 'danger')
            self.cb_auto_start_windows.blockSignals(True)
            self.cb_auto_start_windows.setChecked(not checked)
            self.cb_auto_start_windows.blockSignals(False)
            return
        self.auto_start_windows = result.enabled
        self._toast(
            '已设置开机自启动' if result.enabled else '已取消开机自启动',
            'success',
        )

    def _check_startup_status(self) -> bool:
        """Render messages from startup self-healing and return enabled state."""
        result = self.runtime_controller.reconcile_startup(
            self.auto_start_windows, explicit=False
        )
        for message in result.messages:
            self._append_log(message)
        if not result.success:
            self._append_log(f"⚠️ 开机自启动检查失败: {result.error}")
            return False
        return bool(result.enabled)
    def _auto_start_upload(self):
        """自动开始上传（启动时调用）"""
        if self._task_requests_blocked():
            return
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
        self._request_start_upload()

    def _status_card(self) -> QtWidgets.QWidget:
        self.upload_status_panel = UploadStatusPanel(self)
        return self.upload_status_panel

    def _hline(self):
        line = QtWidgets.QFrame()
        shape_enum = getattr(QtWidgets.QFrame, 'Shape', QtWidgets.QFrame)
        line.setFrameShape(getattr(shape_enum, 'HLine'))
        line.setStyleSheet("color:#E5EAF0")
        return line

    def _log_card(self) -> QtWidgets.QWidget:
        self.upload_log_panel = UploadLogPanel(self)
        return self.upload_log_panel

    # actions
    def _choose_source(self):
        """选择源文件夹"""
        # 获取当前路径作为默认打开位置
        current = self.src_edit.text()
        start_dir = current
        
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
        start_dir = current
        
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
        start_dir = current
        
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

    def _collect_upload_request(self) -> UploadTaskRequest:
        strategy_map = {'跳过': 'skip', '重命名': 'rename', '覆盖': 'overwrite', '询问': 'ask'}
        duplicate_strategy = strategy_map.get(self.combo_strategy.currentText(), 'ask')
        filters = tuple(ext for ext, cb in self.cb_ext.items() if cb.isChecked())
        ftp_config = None
        if self.current_protocol in ['ftp_client', 'both']:
            ftp_config = self._collect_ftp_client_config()
            self.ftp_client_config = copy.deepcopy(ftp_config)
        return UploadTaskRequest(
            source=self.src_edit.text().strip(),
            target=self.tgt_edit.text().strip(),
            backup=self.bak_edit.text().strip(),
            interval=self.spin_interval.value(),
            mode='periodic',
            disk_threshold_percent=self.spin_disk.value(),
            retry_count=self.spin_retry.value(),
            filters=filters,
            app_dir=self.app_dir,
            enable_deduplication=self.cb_dedup_enable.isChecked(),
            hash_algorithm=self.combo_hash.currentText().lower(),
            duplicate_strategy=duplicate_strategy,
            network_check_interval=self.spin_network_check.value(),
            network_auto_pause=self.cb_network_auto_pause.isChecked(),
            network_auto_resume=self.cb_network_auto_resume.isChecked(),
            enable_auto_delete=self.enable_auto_delete,
            auto_delete_threshold=self.auto_delete_threshold,
            auto_delete_target_percent=self.auto_delete_target_percent,
            upload_protocol=self.current_protocol,
            ftp_client_config=ftp_config,
            enable_backup=self.enable_backup,
            limit_upload_rate=self.cb_limit_rate.isChecked(),
            max_upload_rate_mbps=self.spin_max_rate.value(),
            file_upload_delay_seconds=self.file_upload_delay_seconds,
        )

    def _validate_paths(self) -> tuple:
        """Ask the upload controller to validate the current form paths."""
        request = self._collect_upload_request()
        self._append_log("🔍 正在验证文件夹路径...")
        result = self.upload_controller.validate_request(request)
        if result.is_valid:
            self._append_log(f"✓ 源文件夹路径有效: {request.source}")
            self._append_log(f"✓ 目标文件夹路径有效: {request.target}")
            if request.enable_backup:
                self._append_log(f"✓ 备份文件夹路径有效: {request.backup}")
            self._append_log("✓ 所有路径验证通过")
        else:
            self._append_log(f"❌ 路径验证失败，发现 {len(result.errors)} 个错误")
        return result.is_valid, list(result.errors)

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
        self._append_log("🔍 正在验证FTP配置...")
        server_cfg = self._collect_ftp_server_config()
        client_cfg = self._collect_ftp_client_config()
        self.ftp_server_config = copy.deepcopy(server_cfg)
        self.ftp_client_config = copy.deepcopy(client_cfg)
        result = self.ftp_controller.validate_configuration(
            self.enable_ftp_server,
            self.current_protocol,
            server_cfg,
            client_cfg,
        )
        for warning in result.warnings:
            self._append_log(f"⚠️  {warning}")
        if result.errors:
            self._append_log(f"❌ FTP配置验证失败，发现 {len(result.errors)} 个错误")
        else:
            self._append_log("✓ FTP配置验证通过")
        return result.is_valid, list(result.errors)

    def _request_save_settings(self) -> bool:
        """保存配置到文件"""
        self.last_config_save_error = ''

        # v2.2.0 权限检查：仅登录用户可保存配置
        if not self.auth_controller.is_authenticated():
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
        users = {}
        try:
            old_cfg = self._read_config_payload()
            users = old_cfg.get('users', {})
        except Exception:
            pass

        try:
            ftp_server_password, ftp_server_password_encrypted = self.settings_controller.encode_ftp_password(
                self.ftp_server_pass.text(),
                'FTP服务器',
            )
            ftp_client_password, ftp_client_password_encrypted = self.settings_controller.encode_ftp_password(
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
            'file_upload_delay_seconds': self.file_upload_delay_seconds,
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
                'cert_file': self.ftp_server_cert.text().strip(),
                'key_file': self.ftp_server_key.text().strip(),
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
            self.cleanup_controller.configure_index(
                self._collect_auto_cleanup_request("config_save")
            )
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

        group_valid, group_error, _ = self.cleanup_controller.validate_folder_group(folders)
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
            cfg = self._read_config_payload()
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
            self.cleanup_controller.configure_index(
                self._collect_auto_cleanup_request("cleanup_config_save")
            )
            return True
        except Exception as e:
            self.last_config_save_error = str(e)
            self._append_log(f"❌ 自动清理配置保存失败: {e}")
            return False

    def _load_config(self):
        """从配置文件加载设置"""
        self._config_loading = True
        self._append_log("📖 正在加载配置文件...")
        
        config_exists = bool(
            self.settings_controller is not None
            and self.settings_controller.config_exists
        )
        if not config_exists:
            self._append_log("⚠ 配置文件不存在，已生成默认配置")
        try:
            cfg = self._read_config_payload()

            load_error = (
                self.settings_controller.last_error
                if self.settings_controller is not None
                else ""
            )
            if load_error:
                self._append_log(
                    f"❌ 配置文件加载失败；原文件已保留，当前使用内存默认值：{load_error}"
                )
            else:
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
            try:
                self.file_upload_delay_seconds = max(
                    0.0, float(cfg.get('file_upload_delay_seconds', 1.5))
                )
            except (TypeError, ValueError):
                self.file_upload_delay_seconds = 1.5
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
            self.ftp_server_pass.setText(self.settings_controller.decode_ftp_password(ftp_server))
            self.ftp_server_share.setText(ftp_server.get('shared_folder', ''))
            # v2.0 新增：加载高级选项
            self.cb_server_passive.setChecked(ftp_server.get('enable_passive', True))
            self.ftp_server_passive_start.setValue(ftp_server.get('passive_ports_start', 60000))
            self.ftp_server_passive_end.setValue(ftp_server.get('passive_ports_end', 65535))
            self.cb_server_tls.setChecked(ftp_server.get('enable_tls', False))
            self.ftp_server_cert.setText(ftp_server.get('cert_file', ''))
            self.ftp_server_key.setText(ftp_server.get('key_file', ''))
            self._update_ftp_tls_controls()
            self.ftp_server_max_conn.setValue(ftp_server.get('max_connections', 256))
            self.ftp_server_max_conn_per_ip.setValue(ftp_server.get('max_connections_per_ip', 5))
            
            # 加载 FTP 客户端配置
            ftp_client = cfg.get('ftp_client', {})
            self.ftp_client_host.setText(ftp_client.get('host', ''))
            self.ftp_client_port.setValue(ftp_client.get('port', 21))
            self.ftp_client_user.setText(ftp_client.get('username', ''))
            self.ftp_client_pass.setText(self.settings_controller.decode_ftp_password(ftp_client))
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

    def _request_start_upload(self):
        """开始上传"""
        if self._task_requests_blocked():
            self._append_log("⚠️ 退出流程中，已拒绝新的上传任务")
            return
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
            if not self.auth_controller.is_authenticated():
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
                    if not self._request_save_settings():
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
        request = self._collect_upload_request()
        self._append_log(f"📋 上传配置:")
        self._append_log(f"  源文件夹: {request.source}")
        self._append_log(f"  目标文件夹: {request.target}")
        if request.enable_backup:
            self._append_log(f"  备份文件夹: {request.backup}")
        else:
            self._append_log(f"  备份功能: 已禁用（上传成功后将删除源文件）")
        self._append_log(f"  间隔时间: {request.interval}秒")
        self._append_log(f"  重试次数: {request.retry_count}次")
        self._append_log(f"  文件类型: {', '.join(request.filters)}")
        self._append_log(f"  上传协议: {request.upload_protocol}")
        
        # v2.0 新增：启动FTP服务器（v3.1.0 重构：由独立开关控制）
        if self.enable_ftp_server:
            if self._is_ftp_server_running():
                self._append_log("ℹ️ FTP服务器已在运行，上传任务不会重复启动")
            else:
                self._append_log("🔧 正在启动FTP服务器...")
                server_cfg = self._collect_ftp_server_config()
                self.ftp_server_config = copy.deepcopy(server_cfg)
                result = self.ftp_controller.start_server(server_cfg, source="upload")
                for warning in result.warnings:
                    self._append_log(f"⚠️  {warning}")
                if not result.success:
                    details = "\n".join(result.errors) or result.message or "未知错误"
                    self._append_log(f"❌ [FTP] FTP服务器启动失败: {details}")
                    self._toast(f'FTP服务器启动失败: {details}', 'danger')
                    self._update_status_pill()
                    self._update_ui_permissions()
                    return
                self._append_log("✓ FTP服务器已启动:")
                if result.status.get('address'):
                    self._append_log(f"  地址: {result.status['address']}")
                if result.status.get('shared_folder'):
                    self._append_log(f"  共享: {result.status['shared_folder']}")
            self._update_protocol_status()

        if request.ftp_client_config:
            self._append_log(
                f"📡 FTP客户端配置: {request.ftp_client_config['host']}:"
                f"{request.ftp_client_config['port']}"
            )
            self._append_log(
                f"  超时时间: {request.ftp_client_config['timeout']}秒, "
                f"重试次数: {request.ftp_client_config['retry_count']}次"
            )

        result = self.upload_controller.start(request)
        if not result.success:
            details = "\n".join(result.errors) or result.message or "上传任务启动失败"
            self._append_log(f"❌ 上传任务启动失败: {details}")
            self._toast(f'上传任务启动失败: {details}', 'danger')
            if self.ftp_controller.server_started_by_upload:
                self.ftp_controller.stop_upload_server()
            self._update_status_pill()
            self._update_ui_permissions()
            return

        self._auto_cleanup_timer.stop()
        self._update_status_pill()
        self._update_ui_permissions()
        self._toast('开始上传', 'success')
        self._append_log("✓ 上传任务已启动")
        
        # v2.2.0 新增：显示通知
        self._show_notification(
            "上传已开始",
            f"正在上传文件到: {self.tgt_edit.text()}"
        )

    def _toggle_upload_pause(self):
        if self._task_requests_blocked():
            return
        if not self.is_running:
            return
        if self.is_paused:
            result = self.upload_controller.resume()
            if not result.success:
                self._append_log(f"⚠️ 恢复上传失败: {result.message}")
                return
            self.btn_pause.setText("⏸ 暂停上传")
            self._toast('已恢复', 'info')
            # v2.2.0 系统托盘通知
            self._show_notification(
                "上传已恢复",
                "继续上传任务..."
            )
        else:
            result = self.upload_controller.pause()
            if not result.success:
                self._append_log(f"⚠️ 暂停上传失败: {result.message}")
                return
            self.btn_pause.setText("▶ 恢复上传")
            self._toast('已暂停', 'warning')
            # v2.2.0 系统托盘通知
            self._show_notification(
                "上传已暂停",
                f"已上传: {self.uploaded}个文件"
            )
        self._update_status_pill()

    def _request_stop_upload(self):
        """停止上传"""
        self._append_log("🛑 正在停止上传任务...")
        result = self.upload_controller.stop()
        if not result.success:
            self._append_log(f"⚠️ 停止上传任务失败: {result.message}")
        
        # v2.0 新增：停止由上传任务启动的FTP服务器；独立启动的服务器不受上传停止影响
        if (
            self.ftp_controller.server_started_by_upload
            and not self.ftp_controller.server_started_independently
        ):
            self._append_log("🔧 正在停止FTP服务...")
            result = self.ftp_controller.stop_upload_server()
            if result.success:
                self._append_log("✓ FTP服务已停止")
                self._update_protocol_status()
            else:
                self._append_log(f"⚠️ 停止FTP服务时出错: {result.message}")
        
        # 立即恢复UI（不等待线程完全退出，提升响应速度）
        self._restore_ui_after_stop()
    
    def _restore_ui_after_stop(self):
        """恢复停止后的UI状态"""
        states = self._compute_control_states()
        
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

    def render_upload_stats(self, uploaded: int, failed: int, skipped: int, rate: str):
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

    def render_upload_progress(self, current: int, total: int, filename: str):
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
    
    def render_file_progress(self, filename: str, progress: int):
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

            resolved = False

            def done(ok: bool):
                nonlocal resolved
                choice = 'skip'
                if ok and rb_rename.isChecked():
                    choice = 'rename'
                elif ok and rb_overwrite.isChecked():
                    choice = 'overwrite'
                self.upload_controller.resolve_duplicate(
                    payload, choice, cb_apply.isChecked() if ok else False
                )
                resolved = True
                dialog.accept() if ok else dialog.reject()

            btn_cancel.clicked.connect(lambda: done(False))
            btn_ok.clicked.connect(lambda: done(True))

            dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()
            if not resolved:
                self.upload_controller.resolve_duplicate(payload, 'skip', False)
        except Exception:
            self.upload_controller.resolve_duplicate(payload, 'skip', False)
    
    def render_network_status(self, status: str):
        """更新网络状态显示"""
        if status == 'good':
            self.lbl_network.setValue("🟢 正常")
            # 更新芯片样式为绿色
            self.lbl_network.setStyleSheet("QFrame{background:#E8F5E9; border-radius:8px;} QLabel{color:#2E7D32;}")
        elif status == 'unstable':
            self.lbl_network.setValue("🟡 不稳定")
            # 更新芯片样式为黄色
            self.lbl_network.setStyleSheet("QFrame{background:#FFF9C4; border-radius:8px;} QLabel{color:#F57F17;}")
        elif status == 'disconnected':
            self.lbl_network.setValue("🔴 已断开")
            # 更新芯片样式为红色
            self.lbl_network.setStyleSheet("QFrame{background:#FFEBEE; border-radius:8px;} QLabel{color:#C62828;}")
        else:
            self.lbl_network.setValue(t('network_unknown'))
            self.lbl_network.setStyleSheet("QFrame{background:#ECEFF1; border-radius:8px;} QLabel{color:#546E7A;}")

    def render_worker_status(self, s: str):
        self.render_status_pill()

    def render_upload_finished(self):
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
    
    def render_upload_error(self, filename: str, error_message: str):
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
        self._request_auto_cleanup("磁盘空间不足")

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
        
        self.runtime_controller.append_log(line)

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
    
    def render_status_pill(self):
        if self.is_paused:
            self.lbl_status.setText("🟡 已暂停")
            self.lbl_status.setStyleSheet("background:#FEF9C3; color:#A16207; padding:4px 10px; font-weight:700; border-radius:12px;")
        elif self.is_running:
            self.lbl_status.setText("🟢 运行中")
            self.lbl_status.setStyleSheet("background:#DCFCE7; color:#166534; padding:4px 10px; font-weight:700; border-radius:12px;")
        else:
            self.lbl_status.setText("🔴 已停止")
            self.lbl_status.setStyleSheet("background:#FEE2E2; color:#B91C1C; padding:4px 10px; font-weight:700; border-radius:12px;")
    
    def render_protocol_status(self):
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
            try:
                server_info = self.ftp_controller.server_status()
                if server_info.get('running'):
                    connections = server_info.get('connections', 0)
                    self.lbl_ftp_server.setValue(
                        f"🟢 运行中 ({connections}个连接)" if connections else "🟢 运行中 (0)"
                    )
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#DCFCE7; color:#166534; padding:4px 8px; border-radius:4px; font-size:9pt; font-weight:500;"
                    )
                elif server_info:
                    self.lbl_ftp_server.setValue("🔴 已停止")
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#FEE2E2; color:#B91C1C; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
                else:
                    self.lbl_ftp_server.setValue("⚪ 未启动")
                    self.lbl_ftp_server.setStyleSheet(
                        "background:#F5F5F5; color:#757575; padding:4px 8px; border-radius:4px; font-size:9pt;"
                    )
            except Exception as e:
                logger.error(f"FTP服务器状态获取异常: {type(e).__name__}: {e}")
                self.lbl_ftp_server.setValue("⚠️ 状态异常")
                self.lbl_ftp_server.setStyleSheet(
                    "background:#FEE2E2; color:#B91C1C; padding:4px 8px; border-radius:4px; font-size:9pt;"
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
            client_status = self.upload_controller.ftp_client_status()
            if client_status:
                try:
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

    # Compatibility aliases for existing menu/test integrations. New event
    # dispatch uses the render_* methods above.
    def _update_ui_permissions(self) -> None:
        self.render_permissions()

    def _update_status_pill(self) -> None:
        self.render_status_pill()

    def _update_protocol_status(self) -> None:
        self.render_protocol_status()

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
            self.lbl_queue.setValue(str(self.upload_controller.archive_queue_size()))
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

    def _update_disk_space(self) -> None:
        """Forward the current form paths to the runtime disk service."""
        self.runtime_controller.request_disk_space(
            self.tgt_edit.text(),
            self.bak_edit.text(),
            self.enable_backup,
            self.network_status == 'good',
            lambda disk_type, free_percent: self._disk_update_signal.emit(
                disk_type, free_percent
            ),
        )
    def _get_auto_cleanup_folders(self) -> List[str]:
        """Collect configured cleanup folders without touching the filesystem."""
        raw = self.auto_delete_folders if isinstance(self.auto_delete_folders, list) else []
        if not raw and self.auto_delete_folder:
            raw = [self.auto_delete_folder]
        result: List[str] = []
        seen = set()
        for path in raw:
            value = str(path).strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _collect_auto_cleanup_request(self, trigger_source: str) -> AutoCleanupRequest:
        return AutoCleanupRequest(
            enabled=bool(self.enable_auto_delete),
            folders=tuple(self._get_auto_cleanup_folders()),
            trigger_percent=int(self.auto_delete_threshold),
            target_percent=int(self.auto_delete_target_percent),
            formats=tuple(self.auto_delete_formats or []),
            use_trash=bool(self.auto_delete_use_trash),
            trigger_source=trigger_source,
        )

    def _on_worker_disk_cleanup_needed(self) -> None:
        """Worker disk warning is an application event handled by CleanupController."""
        self._request_auto_cleanup(
            "Worker检测到磁盘空间不足", trigger_source="worker"
        )

    def _request_auto_cleanup(
        self, reason: str = "", trigger_source: str = "disk_warning"
    ) -> bool:
        if self._task_requests_blocked():
            return False
        request = self._collect_auto_cleanup_request(trigger_source)
        return self.cleanup_controller.maybe_trigger_auto_cleanup(request, reason)

    def _update_auto_cleanup_schedule(self) -> None:
        if not hasattr(self, "_auto_cleanup_timer"):
            return
        if not self.enable_auto_delete:
            self._auto_cleanup_timer.stop()
            return
        request = self._collect_auto_cleanup_request("timer")
        validation = self.cleanup_controller.validate_auto_request(request)
        if not validation.is_valid:
            self._auto_cleanup_timer.stop()
            for error in validation.errors:
                self._append_log(f"⚠️ {error}")
            return
        interval = max(60, int(self.auto_delete_check_interval))
        self._auto_cleanup_timer.start(interval * 1000)
        self._append_log(f"ℹ️ 自动清理已启用，每 {interval} 秒检查一次")

    def _auto_cleanup_tick(self) -> None:
        self._request_auto_cleanup(trigger_source="timer")
    def render_disk_space(self, disk_type: str, free_percent: float):
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
        self.tray_start_action.triggered.connect(self._request_start_upload)
        
        self.tray_pause_action = tray_menu.addAction("⏸️ 暂停上传")
        self.tray_pause_action.triggered.connect(self._toggle_upload_pause)
        self.tray_pause_action.setEnabled(False)
        
        self.tray_stop_action = tray_menu.addAction("⏹️ 停止上传")
        self.tray_stop_action.triggered.connect(self._request_stop_upload)
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
        if self._exit_pending:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            '确认退出',
            '确定要退出程序吗？\n\n如果有上传任务正在运行，将会被中止。',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.app_exit_requested.emit()

    def _handle_app_exit_requested(self) -> None:
        result = self.lifecycle_controller.request_shutdown(self)
        for error in result.errors:
            logger.error("关闭资源失败: %s", error)
    
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

    def prepare_for_shutdown(self) -> None:
        """Stop View timers before domain controllers begin shutting down."""
        if hasattr(self, '_auto_cleanup_timer'):
            self._auto_cleanup_timer.stop()
        if hasattr(self, '_timer'):
            self._timer.stop()
        cancel_ftp_test = getattr(self.ftp_controller, "cancel_client_test", None)
        if callable(cancel_ftp_test):
            cancel_ftp_test()

    def set_all_tasks_pending_stop(self) -> None:
        """进入退出等待状态，防止产生新的后台任务。"""
        if self._exit_pending:
            return
        self._shutdown_timeout_pending = False
        self._exit_pending = True
        self.prepare_for_shutdown()
        self._apply_exit_pending_controls()
        self._append_log("⏳ 正在等待后台任务安全停止...")

    def abort_pending_exit(self, errors: tuple[str, ...]) -> None:
        """后台任务超时时取消退出并恢复可操作界面。"""
        self._exit_pending = False
        self._shutdown_timeout_pending = bool(
            self.upload_controller.has_running_workers
            or self.cleanup_controller.has_running_workers
        )
        if hasattr(self, '_timer'):
            self._timer.start()
        self._update_auto_cleanup_schedule()
        if self.tray_icon is not None and not self.tray_icon.isVisible():
            self.tray_icon.show()
        self._show_window()
        self._update_ui_permissions()
        message = "部分后台任务未能停止，已取消退出"
        self._append_log(f"❌ {message}")
        for error in errors:
            self._append_log(f"⚠️ {error}")
        self._toast(message, 'danger')
        if self._shutdown_timeout_pending:
            QtCore.QTimer.singleShot(250, self._refresh_shutdown_timeout_state)

    def _refresh_shutdown_timeout_state(self) -> None:
        if not self._shutdown_timeout_pending or self._exit_pending:
            return
        if (
            self.upload_controller.has_running_workers
            or self.cleanup_controller.has_running_workers
        ):
            QtCore.QTimer.singleShot(250, self._refresh_shutdown_timeout_state)
            return
        self._shutdown_timeout_pending = False
        self._append_log("✓ 超时后台任务已自然停止，操作入口已恢复")
        self._update_ui_permissions()

    def release_view_resources(self) -> None:
        """Release tray and local-server resources after background work stops."""
        if self.tray_icon is not None:
            self.tray_icon.hide()
        local_server = getattr(self, 'local_server', None)
        if local_server is not None:
            local_server.close()
    
    def closeEvent(self, event):
        """窗口关闭事件，清理资源"""
        # 如果启用托盘且不是真正退出，则隐藏到托盘
        if (
            not self._exit_pending
            and self.minimize_to_tray
            and self.tray_icon
            and self.tray_icon.isVisible()
        ):
            event.ignore()
            self.hide()
            if self.show_notifications:
                self._show_notification(
                    "程序已隐藏",
                    "程序仍在后台运行\n右键托盘图标可选择退出"
                )
            return

        event.ignore()
        if not self._exit_pending:
            self.app_exit_requested.emit()
    
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
                if self.tray_icon is not None and self.tray_icon.isVisible():
                    self.tray_icon.showMessage(
                        "程序已在运行",
                        "实例已激活",
                        QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                        3000,
                    )
                    self._log_message("已显示单实例托盘通知")
        
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
