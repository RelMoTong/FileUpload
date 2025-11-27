# -*- coding: utf-8 -*-
"""
PyQt版 图片异步上传工具（MVP）
- 三段布局：左（输入设置）、右（控制+状态）、底部（日志）
- 渐变进度条 + 百分比/文件名/剩余时间
- 状态胶囊 + 图标
- 日志自动滚动锁
- 辅助操作在“更多”菜单
- 简易后台线程执行上传与归档（不依赖 Tk 变量）

后续可逐步替换 Tk 版入口。
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
from typing import List, Tuple, Optional, Any, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# v2.0 新增：导入 FTP 协议模块
try:
    from src.protocols.ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    print("警告: FTP 模块导入失败，FTP 功能不可用")

# v2.3.1 新增：导入模块化组件（保持向后兼容）
try:
    from src.ui.widgets import Toast as ModularToast
    from src.ui.widgets import ChipWidget as ModularChipWidget
    from src.ui.widgets import CollapsibleBox as ModularCollapsibleBox
    from src.ui.widgets import DiskCleanupDialog as ModularDiskCleanupDialog
    from src.workers.upload_worker import UploadWorker as ModularUploadWorker
    MODULAR_COMPONENTS_AVAILABLE = True
except ImportError:
    MODULAR_COMPONENTS_AVAILABLE = False
    print("提示: 模块化组件未启用，使用内置组件")

# v2.3.0 新增：导入类型安全的 Qt 枚举访问器
from qt_types import MessageBoxIcon, MessageBoxButton, TrayIconType, EventType

# ????????????
from src.ui.main_window import MainWindow

# 运行时导入 Qt 库
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    Signal = QtCore.Signal  # PySide6 信号
    QT_LIB = 'PySide6'
except ImportError:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
        from PyQt5.QtNetwork import QLocalServer, QLocalSocket  # type: ignore[import-not-found]
        Signal = QtCore.pyqtSignal  # PyQt5 信号
        QT_LIB = 'PyQt5'
    except ImportError:
        raise ImportError("Neither PySide6 nor PyQt5 is installed. Please install one of them.")

# 类型检查时的额外导入（避免 Pylance 类继承误报）
if TYPE_CHECKING:
    # 确保类型检查器能识别 Qt 类作为有效基类
    # 这不会影响运行时，只是帮助静态分析工具
    pass

# 统一访问 Qt 枚举（兼容 Qt6 的强类型枚举命名）
QtEnum = QtCore.Qt

# v2.2.0 Qt枚举兼容性辅助函数（消除Pylance警告）
def get_qt_enum(enum_class, attr_name: str, fallback_value: int):
    """安全获取Qt枚举值，兼容PySide6/PyQt5"""
    try:
        return getattr(enum_class, attr_name, fallback_value)
    except AttributeError:
        return fallback_value

APP_TITLE = "图片异步上传工具 v3.0.1"
APP_VERSION = "3.0.1"


def get_app_dir() -> Path:
    """获取应用程序数据目录（用于配置和日志等可写文件）
    - 开发环境：返回脚本所在目录
    - 打包后：返回 exe 所在目录（用户可写）
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，返回 exe 所在目录
        return Path(sys.executable).parent
    return Path(__file__).parent


def get_resource_path(relative_path: str) -> Path:
    """获取资源文件的绝对路径（支持打包）
    
    用于读取只读资源文件，如 Logo、默认配置等
    
    Args:
        relative_path: 相对于资源目录的路径，如 'assets/logo.png'
    
    Returns:
        资源文件的绝对路径
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # 打包后，资源文件在 _internal 目录（sys._MEIPASS）
        # 使用 getattr 避免类型检查错误（_MEIPASS 是运行时动态属性）
        base_path = Path(getattr(sys, '_MEIPASS'))
    else:
        # 开发环境，资源文件在脚本目录
        base_path = Path(__file__).parent
    return base_path / relative_path


class Toast(QtWidgets.QWidget):  # type: ignore[misc]
    """Toast 通知组件
    
    Note: 使用 type: ignore[misc] 是因为 Qt 模块在 try-except 中动态导入，
    Pylance 无法在静态分析时确定基类有效性，但运行时完全正确。
    """
    def __init__(self, parent: QtWidgets.QWidget, message: str, kind: str = 'info', duration_ms: int = 2500):
        super().__init__(parent)
        wt = getattr(QtEnum, 'WindowType', QtEnum)
        wa = getattr(QtEnum, 'WidgetAttribute', QtEnum)
        self.setWindowFlags(
            getattr(wt, 'FramelessWindowHint')
            | getattr(wt, 'Tool')
            | getattr(wt, 'WindowStaysOnTopHint')
        )
        self.setAttribute(getattr(wa, 'WA_TranslucentBackground'))
        colors = {
            'info':    ("#E0F2FE", "#039CA1"),
            'success': ("#DCFCE7", "#166534"),
            'warning': ("#FEF9C3", "#A16207"),
            'danger':  ("#FEE2E2", "#B91C1C"),
        }
        bg, fg = colors.get(kind, colors['info'])
        layout = QtWidgets.QHBoxLayout(self)
        frame = QtWidgets.QFrame(self)
        frame.setStyleSheet(f"QFrame{{background:{bg}; border:1px solid rgba(0,0,0,0.06); border-radius:8px;}}")
        inner = QtWidgets.QHBoxLayout(frame)
        label = QtWidgets.QLabel(message)
        label.setStyleSheet(f"color:{fg}; padding:8px 12px; font-size:11pt;")
        inner.addWidget(label)
        layout.addWidget(frame)
        self.adjustSize()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(duration_ms)

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        if self.parent():
            p = self.parent()
            geo = p.geometry()
            self.adjustSize()
            x = geo.x() + geo.width() - self.width() - 16
            y = geo.y() + 80
            self.move(x, y)
        return super().showEvent(e)


class UploadWorker(QtCore.QObject):  # type: ignore[misc]
    # signals
    log = Signal(str)
    stats = Signal(int, int, int, str)   # uploaded, failed, skipped, rate
    progress = Signal(int, int, str)     # current, total, filename
    file_progress = Signal(str, int)     # current_file, progress_percent
    network_status = Signal(str)         # 'good'|'unstable'|'disconnected'
    finished = Signal()
    status = Signal(str)                 # 'running'|'paused'|'stopped'
    ask_user_duplicate = Signal(object)  # payload dict: {'file': str, 'duplicate': str, 'event': threading.Event, 'result': dict}
    upload_error = Signal(str, str)      # v2.2.0 新增：filename, error_message
    disk_warning = Signal(float, float, int)  # v2.2.0 新增：target_percent, backup_percent, threshold

    def __init__(self, source: str, target: str, backup: str,
                 interval: int, mode: str, disk_threshold_percent: int, retry_count: int,
                 filters: List[str], app_dir: Path,
                 enable_deduplication: bool = False, hash_algorithm: str = 'md5',
                 duplicate_strategy: str = 'ask',
                 network_check_interval: int = 10, network_auto_pause: bool = True,
                 network_auto_resume: bool = True,
                 enable_auto_delete: bool = False, auto_delete_folder: str = '',
                 auto_delete_threshold: int = 80, auto_delete_keep_days: int = 10,
                 auto_delete_check_interval: int = 300,
                 # v2.0 新增：协议相关参数
                 upload_protocol: str = 'smb',
                 ftp_client_config: Optional[dict] = None,
                 # v2.2.0 新增：备份启用状态
                 enable_backup: bool = True,
                 # v2.3.0 新增：速率限制参数
                 limit_upload_rate: bool = False,
                 max_upload_rate_mbps: float = 10.0):
        super().__init__()
        self.source = source
        self.target = target
        self.backup = backup
        # v2.2.0 新增：保存备份启用状态
        self.enable_backup = enable_backup
        # v2.3.0 新增：速率限制配置
        self.limit_upload_rate = limit_upload_rate
        self.max_upload_rate_bytes = int(max_upload_rate_mbps * 1024 * 1024) if limit_upload_rate else 0
        self.interval = interval
        self.mode = mode
        self.disk_threshold_percent = max(5, disk_threshold_percent)
        self.retry_count = retry_count
        self.filters = [ext.lower() for ext in filters]
        self.app_dir = app_dir
        # 去重配置
        self.enable_deduplication = enable_deduplication
        self.hash_algorithm = hash_algorithm.lower()
        self.duplicate_strategy = duplicate_strategy
        # 网络监控配置
        self.network_check_interval = network_check_interval
        self.network_auto_pause = network_auto_pause
        self.network_auto_resume = network_auto_resume
        # 自动删除配置
        self.enable_auto_delete = enable_auto_delete
        self.auto_delete_folder = auto_delete_folder
        self.auto_delete_threshold = auto_delete_threshold
        self.auto_delete_keep_days = auto_delete_keep_days
        self.auto_delete_check_interval = auto_delete_check_interval
        # v2.0 新增：协议配置
        self.upload_protocol = upload_protocol  # 'smb', 'ftp_client', 'both'
        self.ftp_client_config = ftp_client_config or {}
        self.ftp_client = None  # FTP客户端实例
        
        self._running = False
        self._paused = False
        self._thread = None
        self._archive_thread = None
        # stats
        self.uploaded = 0
        self.failed = 0
        self.skipped = 0
        self.rate = "0 MB/s"
        self.total_files = 0
        self.current = 0
        self.start_time = None
        # 当前文件信息
        self.current_file_name = ""
        self.current_file_size = 0
        self.current_file_uploaded = 0
        # 失败重试队列
        self.retry_queue = {}  # {file_path: retry_count}
        # 归档队列
        self.archive_queue = queue.Queue()
        # 网络连接状态
        self.network_retry_count = 0
        self.network_auto_retry = True
        self.last_network_check = 0
        self.current_network_status = 'unknown'  # good, unstable, disconnected, unknown
        self.network_pause_by_auto = False  # 是否由网络中断自动暂停
        self._last_space_warn = 0.0
        # 失败日志文件
        self.failed_log_path = self.app_dir / "failed_files.log"
        # 线程池用于执行可能阻塞的文件操作
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="FileOp")
        # 独立线程池用于网络可达性快速检测，避免与文件操作互相阻塞
        self._net_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NetChk")
        # 询问模式的全局选择（可由用户选择“应用于后续”）
        self._duplicate_ask_choice = None  # None| 'skip'|'rename'|'overwrite'

    def start(self):
        if self._running:
            return
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 启动网络监控线程（独立于上传主循环）
        self._net_running = True
        self._net_thread = threading.Thread(target=self._network_monitor_loop, daemon=True)
        self._net_thread.start()
        self.status.emit('running')

    def pause(self):
        if not self._running:
            return
        self._paused = True
        self.status.emit('paused')

    def resume(self):
        if not self._running:
            return
        self._paused = False
        self.status.emit('running')

    def stop(self):
        self._running = False
        self._paused = False
        
        # v2.0 新增：关闭FTP客户端连接
        if self.ftp_client:
            try:
                self.ftp_client.disconnect()
                self.ftp_client = None
            except Exception as e:
                pass  # 忽略断开连接错误
        
        # 关闭线程池
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except:
            pass
        # 停止网络监控线程
        self._net_running = False
        # 关闭网络检测线程池
        try:
            self._net_executor.shutdown(wait=False, cancel_futures=True)
        except:
            pass
        self.status.emit('stopped')

    def _network_monitor_loop(self):
        """独立网络监控线程，周期性检测并发射状态信号，避免上传循环阻塞导致状态不更新"""
        last_status = 'unknown'
        while getattr(self, '_net_running', False):
            # 轻量探测
            try:
                # 目标优先
                target_ok = self._safe_net_check(self.target, timeout=0.3, default=False)
                if target_ok:
                    status = 'good'
                else:
                    backup_ok = self._safe_net_check(self.backup, timeout=0.3, default=False)
                    status = 'unstable' if backup_ok else 'disconnected'
            except Exception:
                status = 'disconnected'

            if status != last_status:
                # 日志仅在状态变化时输出
                if status == 'good' and last_status in ('unstable', 'disconnected'):
                    self.log.emit('✅ 网络已恢复正常')
                elif status == 'unstable':
                    self.log.emit('⚠️ 网络不稳定：目标不可达，但备份可达')
                elif status == 'disconnected':
                    self.log.emit('❌ 网络连接中断')
                self.network_status.emit(status)
                self.current_network_status = status
                last_status = status

                # 自动暂停/恢复
                if status == 'disconnected' and self.network_auto_pause and not self._paused:
                    self.network_pause_by_auto = True
                    self.pause()
                if status == 'good' and self.network_auto_resume and self.network_pause_by_auto:
                    self.network_pause_by_auto = False
                    self.resume()

            # 断开状态下每3次输出一次心跳
            if status == 'disconnected':
                self.network_retry_count += 1
                if self.network_retry_count % 3 == 0:
                    self.log.emit(f"🔌 网络仍未恢复 (第{self.network_retry_count}次检测)")
            else:
                self.network_retry_count = 0

            # 定时发送一次统计心跳，保证UI在网络恢复/暂停期间也能持续刷新
            try:
                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
            except Exception:
                pass

            # 自适应间隔：异常时更快探测，正常时使用用户设置
            interval = 1 if status in ('unstable', 'disconnected') else max(1, int(self.network_check_interval))
            time.sleep(interval)

    def _safe_net_check(self, path: str, timeout: float = 1.5, default=False) -> bool:
        """在独立的网络检测线程池中检查路径可达性。
        优先对网络路径（UNC/映射盘）做快速 ping 主机，避免 os.path.exists 卡住；
        本地路径则以 exists 为准（线程池+超时）。"""
        def is_unc(p: str) -> bool:
            return isinstance(p, str) and p.startswith('\\\\')

        def get_drive_root(p: str) -> str:
            drive, _ = os.path.splitdrive(p)
            return drive + '\\' if drive else ''

        def is_mapped_drive(p: str) -> bool:
            try:
                root = get_drive_root(p)
                if not root:
                    return False
                import ctypes
                DRIVE_REMOTE = 4
                GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW
                GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
                GetDriveTypeW.restype = ctypes.c_uint
                dtype = GetDriveTypeW(root)
                return dtype == DRIVE_REMOTE
            except Exception:
                return False

        def mapped_to_unc(p: str) -> str:
            """将映射盘路径转换为 UNC（最佳努力）。"""
            try:
                import ctypes
                from ctypes import wintypes
                # WNetGetConnectionW 获取映射盘对应的 UNC 前缀
                WNetGetConnectionW = ctypes.windll.mpr.WNetGetConnectionW
                WNetGetConnectionW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
                WNetGetConnectionW.restype = wintypes.DWORD
                drive, tail = os.path.splitdrive(p)
                if not drive:
                    return ''
                # 缓冲区
                buf_len = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(1024)
                rc = WNetGetConnectionW(drive + '\\', buf, ctypes.byref(buf_len))
                if rc == 0:
                    unc_prefix = buf.value  # 例如 \\server\share
                    # 拼出完整 UNC 路径
                    rel = p[len(drive):].lstrip('\\/')
                    return os.path.join(unc_prefix, rel).replace('/', '\\')
                return ''
            except Exception:
                return ''

        def extract_host_from_unc(unc: str) -> str:
            try:
                # UNC: \\server\share\...
                parts = unc.split('\\')
                # ['', '', 'server', 'share', ...]
                return parts[2] if len(parts) > 2 else ''
            except Exception:
                return ''

        def ping_host(host: str, ms: int) -> bool:
            try:
                import subprocess
                # -n 1: 一次回显；-w ms: 超时毫秒
                completed = subprocess.run(['ping', '-n', '1', '-w', str(ms), host],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=max(0.2, ms/1000.0 + 0.5))
                return completed.returncode == 0
            except Exception:
                return False

        try:
            if not path:
                return bool(default)
            # UNC 直接 ping 主机
            if is_unc(path):
                host = extract_host_from_unc(path)
                if host:
                    return ping_host(host, int(timeout*1000))
                return bool(default)
            # 映射盘：转换为 UNC 再 ping
            if is_mapped_drive(path):
                unc = mapped_to_unc(path)
                host = extract_host_from_unc(unc) if unc else ''
                if host:
                    return ping_host(host, int(timeout*1000))
                # 回退 exists（线程池+超时）
                future = self._net_executor.submit(os.path.exists, path)
                return bool(future.result(timeout=timeout))
            # 本地路径：直接 exists（线程池+超时）
            future = self._net_executor.submit(os.path.exists, path)
            return bool(future.result(timeout=timeout))
        except Exception:
            return bool(default)

    # helpers
    def _safe_path_operation(self, func, *args, timeout: float = 3.0, default=None):
        """
        安全执行文件系统操作，使用线程池带超时机制防止阻塞
        func: 要执行的函数
        args: 函数参数
        timeout: 超时时间（秒）
        default: 超时或异常时的默认返回值
        """
        try:
            # 提交任务到线程池
            future = self._executor.submit(func, *args)
            # 等待结果，带超时
            result = future.result(timeout=timeout)
            return result
        except FuturesTimeoutError:
            # 超时 - 确保日志信号能发送
            try:
                self.log.emit(f"⏱️ 文件操作超时（{timeout}秒），可能网络中断")
            except:
                pass
            return default
        except Exception as e:
            # 其他异常
            try:
                self.log.emit(f"⚠️ 文件操作异常: {str(e)[:50]}")
            except:
                pass
            return default
    
    def _check_network_connection(self) -> str:
        """
        增强的网络连接检查（根据配置间隔检查，使用超时机制防止阻塞）
        返回：'good' | 'unstable' | 'disconnected'
        """
        # 当独立网络监控线程已运行时，这里仅做“被动”更新，避免重复日志与信号
        if getattr(self, '_net_running', False):
            now = time.time()
            if now - self.last_network_check < self.network_check_interval:
                return self.current_network_status
            # 轻量探测，仅更新缓存，不发射网络信号、不输出日志
            try:
                target_ok = self._safe_path_operation(os.path.exists, self.target, timeout=1.5, default=False)
            except Exception:
                target_ok = False
            if target_ok:
                self.current_network_status = 'good'
            else:
                try:
                    backup_ok = self._safe_path_operation(os.path.exists, self.backup, timeout=1.0, default=False)
                except Exception:
                    backup_ok = False
                self.current_network_status = 'unstable' if backup_ok else 'disconnected'
            self.last_network_check = now
            return self.current_network_status

        now = time.time()
        # 根据配置的间隔检查
        if now - self.last_network_check < self.network_check_interval:
            return self.current_network_status
        
        self.last_network_check = now
        
        # 多层次检测（使用安全操作，带超时）
        # 1. 尝试访问目标文件夹（主要检测，2秒超时）
        try:
            target_ok = self._safe_path_operation(os.path.exists, self.target, timeout=2.0, default=False)
        except Exception:
            target_ok = False
        
        if target_ok:
            # 成功访问，网络良好
            old_status = self.current_network_status
            self.current_network_status = 'good'
            self.network_retry_count = 0
            
            # 状态变化时发送信号和日志
            if old_status == 'disconnected':
                self.log.emit("✅ 网络已恢复正常")
                # 如果是自动暂停，则自动恢复
                if self.network_auto_resume and self.network_pause_by_auto:
                    self.log.emit("🔄 网络恢复，自动继续上传...")
                    time.sleep(1)  # 等待1秒确保网络稳定
                    self.network_pause_by_auto = False
                    self.resume()
            elif old_status != 'good':
                pass  # 状态改善
            
            # 总是发送状态信号（确保UI更新）
            self.network_status.emit('good')
            return 'good'
        
        # 目标不可达，继续检测
        self.network_retry_count += 1
        
        # 2. 尝试访问备份文件夹（辅助检测，2秒超时）
        try:
            backup_ok = self._safe_path_operation(os.path.exists, self.backup, timeout=2.0, default=False)
        except Exception:
            backup_ok = False
        
        if backup_ok:
            # 目标不可达，但备份可达 - 网络不稳定
            old_status = self.current_network_status
            self.current_network_status = 'unstable'
            
            if old_status != 'unstable':
                self.log.emit(f"⚠️ 网络不稳定：目标文件夹不可访问，备份文件夹正常")
            
            # 总是发送状态信号
            self.network_status.emit('unstable')
            return 'unstable'
        
        # 3. 完全断开
        old_status = self.current_network_status
        self.current_network_status = 'disconnected'
        
        if old_status != 'disconnected':
            self.log.emit(f"❌ 网络连接中断（目标和备份文件夹均不可访问）")
            
            # 自动暂停
            if self.network_auto_pause and not self._paused:
                self.log.emit("⏸️ 检测到网络中断，自动暂停上传...")
                self.network_pause_by_auto = True
                self.pause()
        else:
            # 已经是断开状态，定期提示
            if self.network_retry_count % 3 == 0:
                self.log.emit(f"🔌 网络仍未恢复 (第{self.network_retry_count}次检测)")
        
        # 总是发送状态信号
        self.network_status.emit('disconnected')
        return 'disconnected'

    def _handle_upload_failure(self, file_path: str):
        """处理上传失败：非阻塞式重试调度（带指数回退）
        retry_queue 结构：{ path: { 'count': int, 'next': float } }
        """
        item = self.retry_queue.get(file_path)
        if item is None:
            item = {'count': 1, 'next': 0.0}
        else:
            item['count'] += 1
        
        retry_count = item['count']
        if retry_count > self.retry_count:
            # 超过重试次数，记录到失败日志
            self._log_failed_file(file_path, f"重试{retry_count-1}次后仍然失败")
            if file_path in self.retry_queue:
                del self.retry_queue[file_path]
            self.log.emit(f"❌ 文件上传失败，已记录到失败日志: {os.path.basename(file_path)}")
            return
        
        # 计算下一次重试时间（非阻塞调度）
        wait_times = [10, 30, 60]
        wait_time = wait_times[min(retry_count - 1, len(wait_times) - 1)]
        item['next'] = time.time() + wait_time
        self.retry_queue[file_path] = item
        self.log.emit(f"⚠ 文件将在稍后重试 ({retry_count}/{self.retry_count})，等待{wait_time}秒: {os.path.basename(file_path)}")

    def _process_retry_queue(self):
        """处理重试队列（非阻塞，按到期时间触发）"""
        if not self.retry_queue:
            return
        now = time.time()
        retry_list = list(self.retry_queue.items())  # (path, item)
        for file_path, item in retry_list:
            if not self._running:
                break
            if self._paused:
                continue
            # 文件不存在则移除
            if not os.path.exists(file_path):
                del self.retry_queue[file_path]
                continue
            retry_count = item.get('count', 1)
            next_at = item.get('next', 0.0)
            if now < next_at:
                # 还没到时间
                continue
            # 到时间尝试重试
            self.log.emit(f"📤 开始重试上传 ({retry_count}/{self.retry_count}): {os.path.basename(file_path)}")
            rel = os.path.relpath(file_path, self.source)
            tgt = os.path.join(self.target, rel)
            bkp = os.path.join(self.backup, rel)
            try:
                tgt_exists = self._safe_path_operation(os.path.exists, tgt, timeout=2.0, default=False)
                if tgt_exists:
                    del self.retry_queue[file_path]
                    continue
                # 创建目录
                self._safe_path_operation(lambda: os.makedirs(os.path.dirname(tgt), exist_ok=True), timeout=3.0, default=False)
                # 复制文件
                copy_success = self._safe_path_operation(lambda: shutil.copy2(file_path, tgt) or True, timeout=10.0, default=False)
                if not copy_success:
                    raise Exception("文件复制超时")
                # 成功
                self.archive_queue.put((file_path, bkp))
                del self.retry_queue[file_path]
                self.uploaded += 1
                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                self.log.emit(f"✓ 重试成功: {os.path.basename(file_path)}")
            except Exception as e:
                # 失败则再次调度
                item['count'] = retry_count + 1
                if item['count'] > self.retry_count:
                    self._log_failed_file(file_path, f"重试{retry_count}次后仍然失败: {str(e)[:50]}")
                    del self.retry_queue[file_path]
                    self.failed += 1
                    self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                    self.log.emit(f"❌ 文件上传失败，已记录到失败日志: {os.path.basename(file_path)}")
                else:
                    wait_times = [10, 30, 60]
                    wait_time = wait_times[min(item['count'] - 1, len(wait_times) - 1)]
                    item['next'] = time.time() + wait_time
                    self.retry_queue[file_path] = item
                    self.log.emit(f"⚠ 重试失败，已重新排队 ({item['count']}/{self.retry_count})，等待{wait_time}秒: {os.path.basename(file_path)}")

    def _log_failed_file(self, file_path: str, reason: str):
        """记录失败文件到日志文件"""
        try:
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.failed_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {file_path} - {reason}\n")
        except Exception as e:
            self.log.emit(f"写入失败日志出错: {e}")
    
    def _copy_with_progress(self, src: str, dst: str, buffer_size: int = 1024 * 1024):
        """v2.3.0 带进度和速率限制的文件复制"""
        last_write_time = time.time()
        write_timeout = 5.0  # 5秒内没有写入视为超时
        
        # v2.3.0 速率限制：如果启用，减小buffer以提高精确度
        if self.limit_upload_rate and self.max_upload_rate_bytes > 0:
            buffer_size = min(buffer_size, 64 * 1024)  # 64KB chunks
        
        try:
            with open(src, 'rb') as fsrc:
                with open(dst, 'wb') as fdst:
                    copied = 0
                    while True:
                        if not self._running or self._paused:
                            break
                        
                        # 检查写入超时（可能是网络断开）
                        if time.time() - last_write_time > write_timeout:
                            self.log.emit(f"⏱️ 文件写入超时（{write_timeout}秒），可能网络已断开")
                            raise Exception("文件写入超时")
                        
                        # v2.3.0 速率限制：记录开始时间
                        chunk_start = time.time()
                        
                        buf = fsrc.read(buffer_size)
                        if not buf:
                            break
                        
                        # 写入操作
                        try:
                            fdst.write(buf)
                            last_write_time = time.time()  # 重置超时计时器
                        except Exception as e:
                            self.log.emit(f"⚠️ 文件写入失败: {str(e)[:50]}")
                            raise
                        
                        copied += len(buf)
                        
                        # v2.3.0 速率限制：计算应该花费的时间
                        if self.limit_upload_rate and self.max_upload_rate_bytes > 0:
                            expected_time = len(buf) / self.max_upload_rate_bytes
                            elapsed_time = time.time() - chunk_start
                            if elapsed_time < expected_time:
                                time.sleep(expected_time - elapsed_time)
                        
                        # 更新进度（每复制1MB更新一次）
                        if self.current_file_size > 0:
                            progress = int(100 * copied / self.current_file_size)
                            self.file_progress.emit(self.current_file_name, progress)
                            # 每10%输出日志
                            if progress % 10 == 0 and progress > 0:
                                # v2.3.0 显示实时速率
                                actual_speed_mbps = (copied / (1024 * 1024)) / (time.time() - chunk_start + 0.001)
                                if self.limit_upload_rate:
                                    self.log.emit(f"📊 上传进度: {progress}% ({copied/(1024*1024):.1f}MB/{self.current_file_size/(1024*1024):.1f}MB) [限速: {self.max_upload_rate_bytes/(1024*1024):.1f}MB/s]")
                                else:
                                    self.log.emit(f"📊 上传进度: {progress}% ({copied/(1024*1024):.1f}MB/{self.current_file_size/(1024*1024):.1f}MB)")
            
            # 复制文件元数据
            shutil.copystat(src, dst)
        except Exception as e:
            # 如果复制失败，删除不完整的文件
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except:
                    pass
            raise e
    
    # v2.0 新增：多协议上传支持
    def _upload_file_by_protocol(self, src: str, dst: str) -> bool:
        """
        根据配置的协议上传文件
        
        Args:
            src: 源文件路径
            dst: 目标文件路径（SMB路径或本地路径）
        
        Returns:
            bool: 上传是否成功
        """
        if self.upload_protocol == 'smb':
            # SMB协议：直接使用文件系统复制
            return self._upload_via_smb(src, dst)
        elif self.upload_protocol == 'ftp_client':
            # FTP客户端模式：上传到FTP服务器
            return self._upload_via_ftp(src, dst)
        elif self.upload_protocol == 'both':
            # 混合模式：同时使用SMB和FTP
            smb_ok = self._upload_via_smb(src, dst)
            ftp_ok = self._upload_via_ftp(src, dst)
            return smb_ok or ftp_ok  # 任一成功即视为成功
        else:
            self.log.emit(f"❌ 未知的上传协议: {self.upload_protocol}")
            return False
    
    def _upload_via_smb(self, src: str, dst: str) -> bool:
        """通过SMB协议上传文件（使用shutil.copy2）"""
        try:
            # 对于大文件，显示上传进度
            if self.current_file_size > 10 * 1024 * 1024:  # 大于10MB
                self._copy_with_progress(src, dst)
            else:
                # 小文件也使用超时保护
                def copy_file():
                    shutil.copy2(src, dst)
                    return True
                
                copy_success = self._safe_path_operation(copy_file, timeout=10.0, default=False)
                if not copy_success:
                    raise Exception("文件复制超时，网络可能已断开")
            
            return True
        except Exception as e:
            self.log.emit(f"❌ SMB上传失败: {e}")
            return False
    
    def _upload_via_ftp(self, src: str, dst: str) -> bool:
        """通过FTP协议上传文件"""
        try:
            # 初始化FTP客户端（如果还未初始化）
            if not self.ftp_client and self.ftp_client_config:
                self.ftp_client = FTPClientUploader(self.ftp_client_config)
                if not self.ftp_client.connect():
                    # v2.0 增强：详细错误日志
                    host = self.ftp_client_config.get('host', 'unknown')
                    port = self.ftp_client_config.get('port', 21)
                    self.log.emit(f"❌ [FTP-CONN] 无法连接到 {host}:{port}")
                    self.ftp_client = None
                    return False
            
            if not self.ftp_client:
                self.log.emit("❌ [FTP-INIT] FTP客户端未初始化")
                return False
            
            # 计算远程路径（使用相对路径）
            rel_path = os.path.relpath(dst, self.target)
            remote_path = self.ftp_client_config.get('remote_path', '/upload')
            remote_file = f"{remote_path}/{rel_path}".replace('\\', '/')
            
            # 上传文件
            success = self.ftp_client.upload_file(Path(src), remote_file)
            if success:
                self.log.emit(f"✓ FTP上传成功: {os.path.basename(remote_file)}")
                return True
            else:
                # v2.0 增强：详细错误日志
                self.log.emit(f"❌ [FTP-UPLOAD] 上传失败: {os.path.basename(remote_file)}")
                return False
                
        except Exception as e:
            # v2.0 增强：详细错误日志，包含异常类型
            error_type = type(e).__name__
            self.log.emit(f"❌ [FTP-ERROR] {error_type}: {e}")
            return False
    
    def _calculate_file_hash(self, file_path: str, buffer_size: int = 8192) -> str:
        """计算文件哈希值（MD5或SHA256）"""
        try:
            if self.hash_algorithm == 'sha256':
                hasher = hashlib.sha256()
            else:
                hasher = hashlib.md5()
            
            file_size = os.path.getsize(file_path)
            
            with open(file_path, 'rb') as f:
                processed = 0
                while True:
                    if not self._running or self._paused:
                        return ""
                    
                    data = f.read(buffer_size)
                    if not data:
                        break
                    hasher.update(data)
                    processed += len(data)
                    
                    # 大文件显示哈希计算进度
                    if file_size > 50 * 1024 * 1024:  # 大于50MB
                        progress = int(100 * processed / file_size)
                        if progress % 10 == 0:  # 每10%显示一次
                            self.log.emit(f"🔍 计算哈希值... {progress}%")
            
            return hasher.hexdigest()
        except Exception as e:
            self.log.emit(f"⚠ 哈希计算失败: {e}")
            return ""
    
    def _find_duplicate_by_hash(self, file_hash: str, target_dir: str) -> str:
        """在目标文件夹中查找相同哈希的文件"""
        if not file_hash:
            return ""
        
        try:
            for root, _, files in os.walk(target_dir):
                for name in files:
                    if not self._running or self._paused:
                        return ""
                    
                    target_file = os.path.join(root, name)
                    try:
                        target_hash = self._calculate_file_hash(target_file)
                        if target_hash == file_hash:
                            return target_file
                    except Exception:
                        continue
            return ""
        except Exception:
            return ""
    
    def _get_unique_filename(self, base_path: str) -> str:
        """生成唯一的文件名（添加序号）"""
        if not os.path.exists(base_path):
            return base_path
        
        directory = os.path.dirname(base_path)
        filename = os.path.basename(base_path)
        name, ext = os.path.splitext(filename)
        
        counter = 1
        while True:
            new_name = f"{name} ({counter}){ext}"
            new_path = os.path.join(directory, new_name)
            if not os.path.exists(new_path):
                return new_path
            counter += 1
            if counter > 9999:  # 防止无限循环
                return base_path

    def _archive_worker(self):
        """独立归档线程（避免阻塞上传）
        v2.1.1 修改：根据 enable_backup 配置决定是归档还是删除
        """
        while self._running:
            try:
                # 1秒超时，避免死等
                item = self.archive_queue.get(timeout=1)
                src_path, bkp_path = item
                
                if not os.path.exists(src_path):
                    continue
                
                # v2.1.1：根据备份启用状态决定操作
                if self.enable_backup and self.backup and os.path.exists(os.path.dirname(self.backup)):
                    # 启用备份：移动到备份文件夹
                    os.makedirs(os.path.dirname(bkp_path), exist_ok=True)
                    shutil.move(src_path, bkp_path)
                    self.log.emit(f"📦 已归档: {os.path.basename(bkp_path)}")
                else:
                    # 未启用备份：直接删除源文件
                    os.remove(src_path)
                    self.log.emit(f"🗑️ 已删除: {os.path.basename(src_path)}")
            except queue.Empty:
                continue
            except Exception as e:
                self.log.emit(f"归档失败: {e}")

    def _disk_ok(self, path: str) -> Tuple[float, float, float]:
        """检查磁盘空间（带超时保护）"""
        def check():
            try:
                parent = os.path.dirname(path) or path
                usage = shutil.disk_usage(parent)
                total_gb = usage.total / (1024 ** 3)
                free_gb = usage.free / (1024 ** 3)
                free_percent = (usage.free / usage.total) * 100 if usage.total > 0 else 0
                return free_percent, total_gb, free_gb
            except Exception:
                return 0.0, 0.0, 0.0
        
        # 使用安全操作，2秒超时
        result = self._safe_path_operation(check, timeout=2.0, default=(0.0, 0.0, 0.0))
        return result if result is not None else (0.0, 0.0, 0.0)

    def _get_image_files(self) -> List[str]:
        """扫描图片文件（带超时保护）"""
        def scan():
            if not os.path.exists(self.source):
                return []
            files = []
            for root, _, names in os.walk(self.source):
                if not self._running:  # 支持中断
                    break
                for n in names:
                    ext = os.path.splitext(n)[1].lower()
                    if ext in self.filters:
                        files.append(os.path.join(root, n))
            return files
        
        # 使用安全操作，5秒超时（扫描可能需要更长时间）
        result = self._safe_path_operation(scan, timeout=5.0, default=[])
        return result if result is not None else []

    def _run(self):
        self.log.emit("🚀 开始图片上传服务（上传与归档已分离）")
        self.start_time = time.time()
        
        # 启动独立归档线程
        self._archive_thread = threading.Thread(target=self._archive_worker, daemon=True)
        self._archive_thread.start()
        self.log.emit("📦 归档线程已启动")
        
        # 重置状态
        self.uploaded = 0
        self.failed = 0
        self.skipped = 0
        self.retry_queue.clear()
        
        try:
            while self._running:
                # 暂停
                pause_log_counter = 0
                while self._paused and self._running:
                    time.sleep(0.2)
                    # 每10秒（50次循环）输出一次暂停状态日志
                    pause_log_counter += 1
                    if pause_log_counter >= 50:
                        pause_log_counter = 0
                        self.log.emit("⏸️ 上传已暂停，等待恢复...")
                if not self._running:
                    break

                # 网络连接检查（根据配置间隔）
                try:
                    network_status = self._check_network_connection()
                except Exception as e:
                    self.log.emit(f"⚠️ 网络检测异常: {str(e)[:50]}")
                    network_status = 'disconnected'
                
                # 如果网络断开且已暂停，等待网络恢复
                if network_status == 'disconnected' and self._paused:
                    self.log.emit("🔌 等待网络恢复中...")
                    time.sleep(1)
                    continue

                # 空间检查（带警告）
                tf_ok, _, _ = self._disk_ok(self.target)
                bf_ok, _, _ = self._disk_ok(self.backup)
                if tf_ok < self.disk_threshold_percent or bf_ok < self.disk_threshold_percent:
                    now = time.time()
                    if now - self._last_space_warn > 10:
                        self._last_space_warn = now
                        self.log.emit(f"⚠ 磁盘空间不足！目标:{tf_ok:.0f}%，备份:{bf_ok:.0f}%（阈值:{self.disk_threshold_percent}%）")
                        # v2.2.0 发送磁盘空间警告信号
                        self.disk_warning.emit(tf_ok, bf_ok, self.disk_threshold_percent)
                    time.sleep(2)
                    continue

                # 处理重试队列
                self._process_retry_queue()

                # 扫描与处理
                images = self._get_image_files()
                self.total_files = len(images)
                self.current = 0
                self.progress.emit(self.current, self.total_files, "")

                for path in images:
                    if not self._running:
                        break
                    while self._paused and self._running:
                        time.sleep(0.2)
                    if not self._running:
                        break
                    
                    # 在每个文件上传前快速检查网络状态
                    network_status = self._check_network_connection()
                    if network_status == 'disconnected':
                        self.log.emit("⚠️ 网络已断开，停止上传新文件")
                        time.sleep(1)
                        continue

                    rel = os.path.relpath(path, self.source)
                    tgt = os.path.join(self.target, rel)
                    bkp = os.path.join(self.backup, rel)
                    
                    # 安全创建目录（带超时）
                    try:
                        self._safe_path_operation(
                            lambda: os.makedirs(os.path.dirname(tgt), exist_ok=True),
                            timeout=3.0
                        )
                    except Exception as e:
                        self.log.emit(f"❌ 无法创建目标目录: {e}")
                        self.failed += 1
                        self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                        continue

                    fname = os.path.basename(path)
                    self.current_file_name = fname
                    
                    self.log.emit(f"📤 开始上传: {fname}")
                    self.progress.emit(self.current, self.total_files, fname)
                    start_t = time.time()
                    try:
                        # 基本检查：目标文件是否存在（不启用去重时的默认行为，带超时）
                        tgt_exists = self._safe_path_operation(os.path.exists, tgt, timeout=2.0, default=False)
                        if tgt_exists and not self.enable_deduplication:
                            self.log.emit(f"⏭ 文件已存在，跳过: {fname}")
                            self.skipped += 1
                            self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                            self.file_progress.emit(fname, 100)
                        else:
                            # 获取文件大小
                            try:
                                self.current_file_size = os.path.getsize(path)
                            except:
                                self.current_file_size = 0
                            
                            # 发送开始上传信号（0%）
                            self.file_progress.emit(fname, 0)
                            
                            # ===== 智能去重逻辑 =====
                            should_upload = True
                            final_target = tgt
                            
                            if self.enable_deduplication:
                                self.log.emit(f"🔍 检查重复文件（{self.hash_algorithm.upper()}）...")
                                file_hash = self._calculate_file_hash(path)
                                
                                if file_hash:
                                    duplicate = self._find_duplicate_by_hash(file_hash, self.target)
                                    
                                    if duplicate:
                                        self.log.emit(f"⚠ 发现重复文件: {os.path.basename(duplicate)}")
                                        
                                        if self.duplicate_strategy == 'skip':
                                            self.log.emit(f"⏭ 跳过重复文件: {fname}")
                                            self.skipped += 1
                                            self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                                            should_upload = False
                                            # 直接归档源文件
                                            self.archive_queue.put((path, bkp))
                                        elif self.duplicate_strategy == 'rename':
                                            final_target = self._get_unique_filename(tgt)
                                            self.log.emit(f"📝 重命名: {os.path.basename(final_target)}")
                                        elif self.duplicate_strategy == 'overwrite':
                                            self.log.emit(f"🔄 覆盖现有文件")
                                            try:
                                                os.remove(duplicate)
                                            except Exception:
                                                pass
                                        # 'ask' 策略暂时按 skip 处理（需要UI弹窗，后续实现）
                                        elif self.duplicate_strategy == 'ask':
                                            # 如果已有用户选择“应用于后续”，直接使用
                                            choice = self._duplicate_ask_choice
                                            if choice is None:
                                                # 通过信号请求主线程弹窗
                                                evt = threading.Event()
                                                payload = {
                                                    'file': path,
                                                    'duplicate': duplicate,
                                                    'event': evt,
                                                    'result': {},
                                                }
                                                try:
                                                    self.ask_user_duplicate.emit(payload)
                                                    # 最长等待120秒用户选择
                                                    evt.wait(timeout=120)
                                                except Exception:
                                                    pass
                                                choice = payload.get('result', {}).get('choice') or 'skip'
                                                apply_all = bool(payload.get('result', {}).get('apply_all'))
                                                if apply_all:
                                                    self._duplicate_ask_choice = choice
                                            # 根据选择处理
                                            if choice == 'skip':
                                                self.log.emit(f"⏭ 跳过重复文件: {fname}")
                                                self.skipped += 1
                                                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                                                should_upload = False
                                                self.archive_queue.put((path, bkp))
                                            elif choice == 'rename':
                                                final_target = self._get_unique_filename(tgt)
                                                self.log.emit(f"📝 重命名: {os.path.basename(final_target)}")
                                            elif choice == 'overwrite':
                                                self.log.emit(f"🔄 覆盖现有文件")
                                                try:
                                                    os.remove(duplicate)
                                                except Exception:
                                                    pass
                            
                            # ===== 执行上传 =====
                            if should_upload:
                                # 创建目标目录（带超时保护）
                                def create_dir():
                                    os.makedirs(os.path.dirname(final_target), exist_ok=True)
                                
                                dir_created = self._safe_path_operation(create_dir, timeout=3.0, default=False)
                                if dir_created is False:
                                    raise Exception("创建目标目录超时，网络可能已断开")
                                
                                # v2.0 新增：使用协议路由上传文件
                                upload_success = self._upload_file_by_protocol(path, final_target)
                                
                                if not upload_success:
                                    raise Exception("文件上传失败")
                                
                                self.uploaded += 1
                                # 速率计算
                                try: 
                                    size_mb = os.path.getsize(final_target) / (1024*1024)
                                    dur = max(time.time()-start_t, 1e-6)
                                    rate = size_mb / dur
                                    self.rate = f"{rate:.2f} MB/s"
                                except Exception:
                                    pass
                                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                                self.file_progress.emit(fname, 100)
                                self.log.emit(f"✓ 上传成功: {os.path.basename(final_target)}")
                                # 放入归档队列
                                self.archive_queue.put((path, bkp))
                            else:
                                self.file_progress.emit(fname, 100)
                    except Exception as e:
                        self.failed += 1
                        self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                        self.log.emit(f"✗ 上传失败 {fname}: {e}")
                        # v2.2.0 发送错误通知信号
                        self.upload_error.emit(fname, str(e))
                        # 添加到重试队列
                        self._handle_upload_failure(path)

                    self.current += 1
                    self.progress.emit(self.current, self.total_files, fname)

                # 间隔
                if self.mode == 'periodic':
                    for _ in range(max(1, self.interval*5)):
                        if not self._running or self._paused:
                            break
                        time.sleep(0.2)
                else:
                    time.sleep(1)
        finally:
            self.log.emit("🛑 上传服务已停止")
            self.finished.emit()


# v2.3.1 模块化组件别名（优先使用模块化版本，回退到内置版本）
# 这样可以逐步迁移到模块化架构，同时保持向后兼容
if MODULAR_COMPONENTS_AVAILABLE:
    # 使用模块化组件（推荐）
    Toast = ModularToast  # type: ignore[misc, assignment]
    ChipWidget = ModularChipWidget  # type: ignore[misc, assignment]
    CollapsibleBox = ModularCollapsibleBox  # type: ignore[misc, assignment]
    DiskCleanupDialog = ModularDiskCleanupDialog  # type: ignore[misc, assignment]
    UploadWorker = ModularUploadWorker  # type: ignore[misc, assignment]
# else: 使用内置组件（已在下方定义）



def main():
    app = QtWidgets.QApplication(sys.argv)
    
    # v2.3.1 单例模式增强：使用 LocalSocket 尝试唤醒已运行的实例
    server_name = "ImageUploadTool_SingleInstance_Server"
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    
    # 尝试连接到已运行的实例
    if socket.waitForConnected(500):  # 等待500ms
        # 连接成功，说明程序已在运行
        # 发送唤醒消息
        socket.write(b"WAKEUP")
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        
        # 显示提示（可选，也可以静默退出）
        # 这里选择静默退出，因为已经唤醒了旧实例
        return
    
    # 连接失败，说明没有其他实例在运行
    # 使用共享内存作为辅助锁（防止极端情况下的竞态条件）
    shared_mem = QtCore.QSharedMemory("ImageUploadTool_SingleInstance")
    if not shared_mem.create(1):
        # 极少情况：LocalServer 未响应但共享内存存在
        # 这可能是上次程序异常退出导致的，提示用户
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("程序启动异常")
        msg.setText("检测到程序可能未正常退出")
        msg.setInformativeText("建议：\n1. 检查任务管理器是否有残留进程\n2. 重启计算机后重试")
        msg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        msg.exec() if hasattr(msg, 'exec') else msg.exec_()
        return
    
    # 创建主窗口
    w = MainWindow()
    w.show()
    
    # 兼容 PyQt5 和 PySide6
    try:
        sys.exit(app.exec())  # PySide6 / PyQt6
    except AttributeError:
        sys.exit(app.exec_())  # PyQt5


if __name__ == '__main__':
    main()
