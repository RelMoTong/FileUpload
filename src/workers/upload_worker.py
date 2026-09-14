"""上传任务 Worker 模块

包含文件上传的核心逻辑，支持：
- 多协议上传（SMB、FTP客户端）
- 网络监控和自动暂停/恢复
- 会话内智能去重（MD5/SHA256）
- 速率限制
- 失败重试机制
- 异步归档
"""

import os
import time
import shutil
import threading
import datetime
import queue
import hashlib
import json
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

# 创建logger
logger = logging.getLogger(__name__)


def _normalize_windows_path_for_check(path: str) -> str:
    """Normalize Windows filesystem paths before passing them to shell/API checks."""
    if os.name == 'nt' and isinstance(path, str):
        return path.replace('/', '\\')
    return path


# 导入 Qt 库
from PySide6 import QtCore
Signal = QtCore.Signal

# 导入 FTP 客户端
try:
    from src.protocols.ftp import FTPClientUploader
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    FTPClientUploader = None  # type: ignore[assignment, misc]

# 导入断点续传模块
from src.core.file_identity import FileIdentity, normalize_file_path
from src.core.file_task_registry import FileTaskRegistry, FileTaskState
from src.core.resume_manager import ResumeManager, ResumableFileUploader
from src.core.safe_deletion import SafeDeletionPolicy, SafeDeletionRequest
from src.repositories import PendingArchiveRepository
from src.core.session_dedup_cache import SessionDedupCache
from src.core.pause_state import PauseState


class UploadWorker(QtCore.QObject):  # type: ignore[misc]
    """文件上传 Worker
    
    后台线程执行文件上传任务，支持多种协议和高级功能。
    
    Signals:
        log: 日志消息
        stats: 统计信息 (uploaded, failed, skipped, rate)
        progress: 进度信息 (current, total, filename)
        file_progress: 单文件进度 (filename, percent)
        network_status: 网络状态 ('good'|'unstable'|'disconnected')
        finished: 任务完成
        status: 运行状态 ('running'|'paused'|'stopped')
        ask_user_duplicate: 请求用户处理重复文件
        upload_error: 上传错误 (filename, error_message)
        disk_warning: 磁盘空间警告 (target_percent, backup_percent, threshold)
    
    Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
    """
    
    # Signals
    log = Signal(str)
    stats = Signal(int, int, int, str)   # uploaded, failed, skipped, rate
    progress = Signal(int, int, str)     # current, total, filename
    file_progress = Signal(str, int)     # current_file, progress_percent
    network_status = Signal(str)         # 'good'|'unstable'|'disconnected'
    finished = Signal()
    status = Signal(str)                 # 'running'|'paused'|'stopped'
    ask_user_duplicate = Signal(object)  # payload dict
    upload_error = Signal(str, str)      # filename, error_message
    disk_warning = Signal(float, float, int)  # target_percent, backup_percent, threshold
    disk_cleanup_needed = Signal()       # 请求主窗口执行统一自动清理
    local_file_generated = Signal(str, str)  # path, source(upload|archive)

    def __init__(
        self,
        source: str,
        target: str,
        backup: str,
        interval: int,
        mode: str,
        disk_threshold_percent: int,
        retry_count: int,
        filters: List[str],
        app_dir: Path,
        enable_deduplication: bool = False,
        hash_algorithm: str = 'md5',
        duplicate_strategy: str = 'ask',
        network_check_interval: int = 10,
        network_auto_pause: bool = True,
        network_auto_resume: bool = True,
        enable_auto_delete: bool = False,
        auto_delete_threshold: int = 80,
        auto_delete_target_percent: int = 40,
        upload_protocol: str = 'smb',
        ftp_client_config: Optional[Dict[str, Any]] = None,
        enable_backup: bool = True,
        limit_upload_rate: bool = False,
        max_upload_rate_mbps: float = 10.0,
        file_upload_delay_seconds: float = 1.5
    ):
        """初始化上传 Worker
        
        Args:
            source: 源文件夹路径
            target: 目标文件夹路径
            backup: 备份文件夹路径
            interval: 上传间隔（秒）
            mode: 运行模式 ('periodic' | 'once')
            disk_threshold_percent: 磁盘空间阈值（百分比）
            retry_count: 失败重试次数
            filters: 文件扩展名过滤器列表
            app_dir: 应用程序目录
            enable_deduplication: 是否启用去重
            hash_algorithm: 哈希算法 ('md5' | 'sha256')
            duplicate_strategy: 重复处理策略 ('skip'|'rename'|'overwrite'|'ask')
            network_check_interval: 网络检查间隔（秒）
            network_auto_pause: 网络中断时自动暂停
            network_auto_resume: 网络恢复时自动恢复
            enable_auto_delete: 启用自动删除（磁盘不足时通知主窗口清理）
            auto_delete_threshold: 自动删除磁盘阈值（使用率触发值）
            auto_delete_target_percent: 自动删除目标阈值（清理后回落到此值）
            upload_protocol: 上传协议 ('smb'|'ftp_client'|'both')
            ftp_client_config: FTP客户端配置
            enable_backup: 是否启用备份
            limit_upload_rate: 是否限制上传速率
            max_upload_rate_mbps: 最大上传速率（MB/s）
            file_upload_delay_seconds: 扫描到文件后、开始上传前的延迟秒数
        """
        super().__init__()
        self.source = source
        self.target = target
        self.backup = backup
        self.enable_backup = enable_backup
        self.limit_upload_rate = limit_upload_rate
        self.max_upload_rate_bytes = int(max_upload_rate_mbps * 1024 * 1024) if limit_upload_rate else 0
        self.file_upload_delay_seconds = max(0.0, float(file_upload_delay_seconds))
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
        
        # 自动删除配置（Worker 仅做磁盘检测，实际清理由主窗口统一执行）
        self.enable_auto_delete = enable_auto_delete
        self.auto_delete_threshold = auto_delete_threshold
        self.auto_delete_target_percent = max(0, min(auto_delete_target_percent, auto_delete_threshold - 5))
        
        # 协议配置
        self.upload_protocol = upload_protocol
        self.ftp_client_config = ftp_client_config or {}
        self.ftp_client = None
        
        # 运行状态
        self._running = False
        self._pause_state = PauseState()
        self._thread = None
        self._archive_thread = None
        self._net_running = False
        self._net_thread = None
        self._net_stop_event = threading.Event()
        
        # 统计数据
        self.uploaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.rate = "0 MB/s"
        self.total_files = 0
        self.current = 0
        self.start_time = None
        
        # 当前文件信息
        self.current_file_name = ""
        self.current_file_size = 0
        self.current_file_uploaded = 0
        
        # 队列
        self.retry_queue: Dict[str, Dict[str, Any]] = {}
        self._task_registry = FileTaskRegistry()
        self.archive_queue: queue.Queue = queue.Queue()
        self._archive_stop_event = threading.Event()
        self._archive_repository = PendingArchiveRepository(self.app_dir)
        self._pending_archive_sources: set[str] = set()
        self._queued_archive_sources: set[str] = set()
        self._archive_persist_retries: Dict[str, Dict[str, Any]] = {}
        self._archive_persist_retry_limit = 5
        self._archive_persist_retry_base_seconds = 1.0
        self._archive_persist_retry_max_seconds = 30.0
        
        # 网络状态
        self.network_retry_count = 0
        self.network_auto_retry = True
        self.last_network_check = 0.0
        self.current_network_status = None  # None=未检测, 'good'/'unstable'/'disconnected'=已检测
        self.network_pause_by_auto = False
        self._network_good_streak = 0
        self._network_bad_streak = 0
        self._last_network_path_probe = 0.0
        self._last_backup_path_ok = False
        self._last_space_warn = 0.0
        
        # 失败日志
        self.failed_log_path = self.app_dir / "failed_files.log"
        
        # 网络路径元数据操作使用可终止子进程，禁止超时后遗弃 FileOp 线程。
        self._fileop_lock = threading.Lock()
        self._fileop_processes: set[subprocess.Popen[Any]] = set()
        self._fileop_slots = threading.BoundedSemaphore(2)
        self._fileop_timeout_count = 0
        self._fileop_circuit_until = 0.0
        self._dedup_not_supported_warned = False
        self._dedup_cache = SessionDedupCache(4096)
        self._dedup_cache_ready = False
        self._dedup_cache_root = ""
        self._dedup_generation = 0
        
        # 去重询问模式的全局选择
        self._duplicate_ask_choice: Optional[str] = None
        
        # 断点续传管理器
        self.resume_manager = ResumeManager(self.app_dir)
        self.resumable_uploader: Optional[ResumableFileUploader] = None

    @property
    def _paused(self) -> bool:
        """Compatibility view of the unified pause state."""
        return self._pause_state.is_paused

    @_paused.setter
    def _paused(self, value: bool) -> None:
        # Legacy callers can still force a manual pause in tests/integrations.
        self._pause_state.set("manual", bool(value))

    @property
    def pause_reasons(self) -> frozenset[str]:
        return self._pause_state.reasons

    def _set_pause_reason(self, reason: str, active: bool) -> None:
        changed = self._pause_state.set(reason, active)
        if changed and self._running:
            self.status.emit('paused' if self._pause_state.is_paused else 'running')

    def start(self) -> None:
        """启动上传任务"""
        if self._running:
            return
        self._duplicate_ask_choice = None
        self._dedup_not_supported_warned = False
        self._dedup_cache_ready = False
        if not self._validate_paths() or not self._validate_ftp_config():
            self.status.emit('stopped')
            self.finished.emit()
            return

        self._log_event(
            "ℹ️",
            "CONFIG",
            "运行配置已加载",
            protocol=self.upload_protocol,
            dedup=self.enable_deduplication,
            strategy=self.duplicate_strategy,
            hash=self.hash_algorithm,
            retry=self.retry_count,
            limit_rate=self.limit_upload_rate,
            interval=self.interval,
            mode=self.mode,
            backup=self.enable_backup
        )
        if not self.enable_backup:
            self._log_event("⚠️", "NO_BACKUP", "备份已关闭，上传成功后将删除源文件")
        self._running = True
        self._pause_state.clear()
        self.network_pause_by_auto = False
        self._network_good_streak = 0
        self._network_bad_streak = 0
        self._net_stop_event.clear()
        self._archive_stop_event.clear()
        self._restore_pending_archives()
        self._restore_archive_persist_failures()
        
        # 检查待续传的文件
        self._check_pending_resumes()
        
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
        # 启动网络监控线程（FTP-only 跳过网络路径监控）
        if self.upload_protocol != 'ftp_client':
            self._net_running = True
            self._net_thread = threading.Thread(target=self._network_monitor_loop, daemon=True)
            self._net_thread.start()
        
        self.status.emit('running')
    
    def _check_pending_resumes(self) -> None:
        """检查并提示待续传的文件"""
        try:
            pending = self.resume_manager.get_pending_resumes()
            if pending:
                self.log.emit(f"📂 发现 {len(pending)} 个待续传文件，将优先处理")
                for record in pending[:3]:  # 只显示前3个
                    filename = os.path.basename(record.get('source_path', ''))
                    uploaded = record.get('uploaded_bytes', 0)
                    total = record.get('total_bytes', 0)
                    percent = int(100 * uploaded / total) if total > 0 else 0
                    self.log.emit(f"  📄 {filename}: {percent}% 已完成")
                if len(pending) > 3:
                    self.log.emit(f"  ... 还有 {len(pending) - 3} 个文件")
        except Exception as e:
            self.log.emit(f"⚠️ 检查续传记录失败: {e}")

    def get_health_status(self) -> dict:
        """获取运行健康状态（用于监控和排障）
        
        Returns:
            健康状态字典，包含各项指标
        """
        status = {
            'running': self._running,
            'paused': self._paused,
            'pause_reasons': tuple(sorted(self.pause_reasons)),
            'network_status': self.current_network_status,
            'uploaded_count': self.uploaded_count,
            'failed_count': self.failed_count,
            'skipped_count': self.skipped_count,
            'protocol': self.upload_protocol,
            'ftp_connected': self.ftp_client is not None,
            'resume_active': self.resumable_uploader is not None,
            'fileop_active_count': self._fileop_active_count(),
            'fileop_legacy_task_count': 0,
            'fileop_timeout_count': self._fileop_timeout_count,
            'fileop_circuit_open': time.monotonic() < self._fileop_circuit_until,
        }
        return status

    def log_health_status(self) -> None:
        """记录当前健康状态到日志"""
        status = self.get_health_status()
        self.log.emit(f"📊 健康检查: 运行={status['running']}, "
                     f"网络={status['network_status']}, "
                     f"上传/失败/跳过={status['uploaded_count']}/{status['failed_count']}/{status['skipped_count']}")

    def pause(self) -> None:
        """暂停上传任务"""
        if not self._running:
            return
        self._set_pause_reason("manual", True)

    def resume(self) -> None:
        """恢复上传任务"""
        if not self._running:
            return
        self._set_pause_reason("manual", False)

    def stop(self, wait: bool = False, timeout: float = 5.0) -> None:
        """停止上传任务
        
        Args:
            wait: 是否等待正在执行的任务完成（安全停止）
            timeout: 等待超时时间（秒），仅在 wait=True 时有效
        """
        self.log.emit(f"🛑 正在停止上传任务 ({'安全模式' if wait else '快速模式'})...")
        self._running = False
        self._set_pause_reason("stopping", True)
        
        # 停止断点续传上传器（保存进度）
        if self.resumable_uploader:
            self.resumable_uploader.stop()
            self.resumable_uploader = None
            self.log.emit("💾 上传进度已保存，下次启动可继续")
        
        # 关闭FTP客户端
        if self.ftp_client:
            try:
                self.ftp_client.disconnect()
                self.ftp_client = None
                self.log.emit("✓ FTP 客户端已断开")
            except Exception as e:
                self.log.emit(f"⚠️ FTP 客户端断开异常: {e}")
        
        self._terminate_fileop_processes()
        self._archive_stop_event.set()
        
        # 停止网络监控
        self._net_running = False
        self._net_stop_event.set()

        self.log.emit("✓ 上传任务已停止")
        self.status.emit('stopped')

    def _apply_network_status(self, status: str) -> None:
        """Apply a published network event to the sole pause state."""
        if status == "disconnected":
            self._network_bad_streak += 1
            self._network_good_streak = 0
            if self.network_auto_pause and self._network_bad_streak >= 1:
                if "network" not in self.pause_reasons:
                    self.log.emit("⏸️ 检测到网络中断，自动暂停上传...")
                self.network_pause_by_auto = True
                self._set_pause_reason("network", True)
        elif status == "good":
            self._network_good_streak += 1
            self._network_bad_streak = 0
            if (
                self.network_auto_resume
                and self.network_pause_by_auto
                and self._network_good_streak >= 2
            ):
                self.log.emit("🔄 网络已恢复，自动继续上传...")
                self.network_pause_by_auto = False
                self._set_pause_reason("network", False)
        else:
            self._network_good_streak = 0

    def _record_network_status(self, status: str) -> None:
        """Publish one network sample and let the pause state consume it."""
        previous = self.current_network_status
        self.current_network_status = status
        if status != previous:
            if status == 'good' and previous in ('unstable', 'disconnected'):
                self.log.emit('✅ 网络已恢复正常')
            elif status == 'unstable':
                self.log.emit('⚠️ 网络不稳定：目标或备份路径不可写')
            elif status == 'disconnected':
                self.log.emit('❌ 网络连接中断')
            self.network_status.emit(status)
        self._apply_network_status(status)

    def has_running_tasks(self) -> bool:
        """返回 Worker 内部是否仍有 Python 线程或线程池任务活动。"""
        threads = [self._thread, self._archive_thread, self._net_thread]
        return self._fileop_active_count() > 0 or any(
            thread is not None
            and hasattr(thread, "is_alive")
            and thread.is_alive()
            for thread in threads
        )

    def _network_monitor_loop(self) -> None:
        """网络监控循环（独立线程）"""
        while getattr(self, '_net_running', False):
            try:
                # 只有所有必需的 SMB 路径均可写时才显示“正常”。
                status = self._evaluate_smb_network_status(timeout=1.5)
            except Exception as e:
                # 网络检查异常，假设断开
                logger.debug(f"网络监控检查异常: {type(e).__name__}: {e}")
                status = 'disconnected'

            self._record_network_status(status)
            # 断开状态心跳
            if status == 'disconnected':
                self.network_retry_count += 1
                if self.network_retry_count % 3 == 0:
                    self.log.emit(f"🔌 网络仍未恢复 (第{self.network_retry_count}次检测)")
            else:
                self.network_retry_count = 0

            # 发送统计心跳
            try:
                self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
            except Exception:
                # Signal发送失败静默忽略（UI可能已关闭，避免循环错误）
                pass

            # 自适应间隔
            interval = 1 if status in ('unstable', 'disconnected') else max(1, int(self.network_check_interval))
            if self._net_stop_event.wait(interval):
                break

    def _evaluate_smb_network_status(self, timeout: float = 1.5) -> str:
        """Return SMB path health, including every required writable path."""
        target_ok = self._safe_net_check(
            self.target, timeout=timeout, default=False, require_write=True
        )
        backup_required = bool(self.enable_backup and self.backup)
        backup_ok = (
            self._safe_net_check(
                self.backup, timeout=timeout, default=False, require_write=True
            )
            if backup_required
            else True
        )
        self._last_backup_path_ok = backup_ok if backup_required else True
        self._last_network_path_probe = time.monotonic()
        if target_ok and backup_ok:
            return 'good'
        if target_ok or (backup_required and backup_ok):
            return 'unstable'
        return 'disconnected'

    def _safe_net_check(
        self,
        path: str,
        timeout: float = 1.5,
        default: bool = False,
        require_write: bool = True,
    ) -> bool:
        """安全检查网络路径可达性
        
        UNC/映射盘必须通过带超时的目录访问或写入探测，主机能被 ping
        通不再被视为共享目录可用。写入探测在独立 PowerShell 子进程中
        执行，避免失联的网络路径阻塞网络监控线程。
        """
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
                # Windows API调用失败（非Windows平台或API不可用）
                return False

        def mapped_to_unc(p: str) -> str:
            try:
                import ctypes
                from ctypes import wintypes
                WNetGetConnectionW = ctypes.windll.mpr.WNetGetConnectionW
                WNetGetConnectionW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
                WNetGetConnectionW.restype = wintypes.DWORD
                drive, _ = os.path.splitdrive(p)
                if not drive:
                    return ''
                buf_len = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(1024)
                rc = WNetGetConnectionW(drive, buf, ctypes.byref(buf_len))
                if rc == 0:
                    unc_prefix = buf.value
                    rel = p[len(drive):].lstrip('\\/')
                    return os.path.join(unc_prefix, rel).replace('/', '\\')
                return ''
            except Exception:
                # Windows API调用失败或非Windows平台
                return ''

        def path_access_with_timeout(
            p: str, seconds: float, write_required: bool
        ) -> bool:
            try:
                create_flag = 0
                if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    create_flag = subprocess.CREATE_NO_WINDOW
                env = os.environ.copy()
                env['IMAGE_UPLOAD_HEALTH_PATH'] = _normalize_windows_path_for_check(p)
                if write_required:
                    command = (
                        "$p=$env:IMAGE_UPLOAD_HEALTH_PATH; "
                        "if (-not (Test-Path -LiteralPath $p -PathType Container)) { exit 1 }; "
                        "$probe=Join-Path $p ('.image_upload_health_' + [guid]::NewGuid().ToString('N') + '.tmp'); "
                        "$stream=$null; "
                        "try { "
                        "$stream=[System.IO.File]::Open($probe,[System.IO.FileMode]::CreateNew,"
                        "[System.IO.FileAccess]::Write,[System.IO.FileShare]::None); "
                        "$stream.Dispose(); $stream=$null; "
                        "Remove-Item -LiteralPath $probe -Force -ErrorAction Stop; exit 0 "
                        "} catch { "
                        "if ($null -ne $stream) { $stream.Dispose() }; "
                        "if (Test-Path -LiteralPath $probe) { "
                        "Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }; exit 1 }"
                    )
                else:
                    command = (
                        "$p=$env:IMAGE_UPLOAD_HEALTH_PATH; "
                        "if (-not (Test-Path -LiteralPath $p -PathType Container)) { exit 1 }; "
                        "try { Get-ChildItem -LiteralPath $p -Force -ErrorAction Stop "
                        "| Select-Object -First 1 | Out-Null; exit 0 } catch { exit 1 }"
                    )
                completed = subprocess.run(
                    [
                        'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                        '-Command', command,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(0.5, seconds),
                    creationflags=create_flag,
                    env=env,
                )
                return completed.returncode == 0
            except subprocess.TimeoutExpired:
                return bool(default)
            except Exception:
                # 命令执行失败（非Windows平台或PowerShell不可用）
                return bool(default)

        try:
            if not path:
                return bool(default)
            path = _normalize_windows_path_for_check(path)
            
            # UNC 路径：必须验证共享目录本身，而不是只验证主机在线。
            if is_unc(path):
                return path_access_with_timeout(path, timeout, require_write)
            
            # 映射盘：转换为 UNC 后验证实际共享目录权限。
            if is_mapped_drive(path):
                unc = mapped_to_unc(path)
                return path_access_with_timeout(unc or path, timeout, require_write)
            
            # 本地路径：直接检查
            return bool(os.path.exists(path))
        except Exception:
            # 网络检查失败，返回默认值
            return bool(default)

    def _fileop_active_count(self) -> int:
        with self._fileop_lock:
            return len(self._fileop_processes)

    def _terminate_fileop_processes(self) -> None:
        with self._fileop_lock:
            processes = tuple(self._fileop_processes)
        for process in processes:
            try:
                process.kill()
            except Exception:
                pass

    @staticmethod
    def _is_remote_path(path: str) -> bool:
        if not isinstance(path, str) or not path:
            return False
        normalized = _normalize_windows_path_for_check(path)
        if normalized.startswith('\\\\'):
            return True
        if os.name != 'nt':
            return False
        try:
            import ctypes
            drive, _ = os.path.splitdrive(normalized)
            if not drive:
                return False
            return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == 4
        except Exception:
            return False

    def _run_remote_fileop(
        self,
        operation: str,
        path: str,
        timeout: float,
        default: Any,
        filters: Optional[List[str]] = None,
    ) -> Any:
        """Run network-path metadata work in a bounded, killable subprocess."""
        # Local paths do not need a PowerShell hop.  Besides avoiding needless
        # process creation, direct APIs preserve Unicode paths reliably.
        if not self._is_remote_path(path):
            try:
                if operation == "exists":
                    return os.path.exists(path)
                if operation == "isdir":
                    return os.path.isdir(path)
                if operation == "mkdir":
                    os.makedirs(path, exist_ok=True)
                    return True
                if operation == "disk_usage":
                    usage = shutil.disk_usage(path)
                    return int(usage.total), int(usage.free)
                if operation == "scan":
                    allowed = {str(item).lower() for item in (filters or [])}
                    return [
                        str(item)
                        for item in Path(path).rglob("*")
                        if item.is_file()
                        and (
                            not allowed
                            or item.suffix.lower() in allowed
                        )
                    ]
            except (OSError, ValueError):
                return default
        if time.monotonic() < self._fileop_circuit_until:
            return default
        if not self._fileop_slots.acquire(blocking=False):
            return default
        process: Optional[subprocess.Popen[Any]] = None
        try:
            prefix = (
                "$ErrorActionPreference='Stop'; "
                "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
                "$p=$env:IMAGE_UPLOAD_FILEOP_PATH; "
            )
            commands = {
                "exists": "if (Test-Path -LiteralPath $p) { exit 0 } else { exit 1 }",
                "isdir": (
                    "if (Test-Path -LiteralPath $p -PathType Container) "
                    "{ exit 0 } else { exit 1 }"
                ),
                "mkdir": (
                    "[System.IO.Directory]::CreateDirectory($p) | Out-Null; exit 0"
                ),
                "disk_usage": (
                    "$item=Get-Item -LiteralPath $p; $root=$item.PSDrive.Root; "
                    "$drive=[System.IO.DriveInfo]::new($root); "
                    "Write-Output ($drive.TotalSize.ToString()+'|'+"
                    "$drive.AvailableFreeSpace.ToString())"
                ),
                "scan": (
                    "$items=@(Get-ChildItem -LiteralPath $p -File -Recurse | "
                    "ForEach-Object { $_.FullName }); "
                    "$items | ConvertTo-Json -Compress"
                ),
            }
            command = prefix + commands[operation]
            env = os.environ.copy()
            env["IMAGE_UPLOAD_FILEOP_PATH"] = _normalize_windows_path_for_check(path)
            env["IMAGE_UPLOAD_FILEOP_FILTERS"] = json.dumps(
                [str(item).lower() for item in (filters or [])]
            )
            create_flag = (
                subprocess.CREATE_NO_WINDOW
                if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW')
                else 0
            )
            process = subprocess.Popen(
                [
                    'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                    '-Command', command,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=create_flag,
                env=env,
            )
            with self._fileop_lock:
                self._fileop_processes.add(process)
            stdout, stderr = process.communicate(timeout=max(0.5, timeout))
            if process.returncode != 0:
                detail = (stderr or b"").decode("utf-8", errors="replace").strip()
                if detail:
                    logger.debug("网络文件操作失败 %s: %s", operation, detail[:300])
                return default
            self._fileop_timeout_count = 0
            if operation in {"exists", "isdir", "mkdir"}:
                return True
            text = (stdout or b"").decode("utf-8-sig", errors="replace").strip()
            if operation == "disk_usage":
                total_text, free_text = text.split("|", 1)
                return int(total_text), int(free_text)
            if operation == "scan":
                if not text:
                    return []
                parsed = json.loads(text)
                items = [parsed] if isinstance(parsed, str) else list(parsed)
                allowed = {str(item).lower() for item in (filters or [])}
                return [
                    item for item in items
                    if not allowed or os.path.splitext(str(item))[1].lower() in allowed
                ]
            return default
        except subprocess.TimeoutExpired:
            if process is not None:
                try:
                    process.kill()
                    process.communicate(timeout=1)
                except Exception:
                    pass
            self._fileop_timeout_count += 1
            if self._fileop_timeout_count >= 3:
                self._fileop_circuit_until = time.monotonic() + 30.0
                self.log.emit("⛔ 网络文件操作连续超时，熔断30秒")
            else:
                self.log.emit(f"⏱️ 网络文件操作超时（{timeout}秒）")
            return default
        except Exception as exc:
            self.log.emit(f"⚠️ 网络文件操作异常: {str(exc)[:100]}")
            return default
        finally:
            if process is not None:
                with self._fileop_lock:
                    self._fileop_processes.discard(process)
            self._fileop_slots.release()

    def _safe_path_exists(self, path: str, timeout: float = 2.0) -> bool:
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("exists", path, timeout, False))
        try:
            return os.path.exists(path)
        except OSError:
            return False

    def _safe_path_isdir(self, path: str, timeout: float = 2.0) -> bool:
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("isdir", path, timeout, False))
        try:
            return os.path.isdir(path)
        except OSError:
            return False

    def _safe_make_dirs(self, path: str, timeout: float = 3.0) -> bool:
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("mkdir", path, timeout, False))
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except OSError:
            return False

    def _log_event(self, level: str, code: str, message: str, **fields) -> None:
        try:
            suffix = ""
            if fields:
                parts = [f"{k}={v}" for k, v in fields.items()]
                suffix = " | " + " ".join(parts)
            self.log.emit(f"{level} [{code}] {message}{suffix}")
        except Exception:
            # 日志发送失败静默忽略（避免循环错误）
            pass

    def _ensure_dir(self, path: str, label: str, create: bool = True) -> bool:
        if not path:
            self._log_event("❌", "PATH_EMPTY", f"{label}路径未设置")
            return False
        exists = self._safe_path_exists(path, timeout=2.0)
        if exists:
            is_dir = self._safe_path_isdir(path, timeout=2.0)
            if not is_dir:
                self._log_event("❌", "PATH_NOT_DIR", f"{label}路径不是文件夹", path=path)
                return False
            return True
        if not create:
            self._log_event("❌", "PATH_NOT_FOUND", f"{label}路径不存在或不可访问", path=path)
            return False

        created = self._safe_make_dirs(path, timeout=3.0)
        if created is False:
            self._log_event("❌", "PATH_CREATE_FAIL", f"{label}路径不可创建，可能无权限或网络中断", path=path)
            return False
        self._log_event("ℹ️", "PATH_CREATED", f"{label}路径不存在，已自动创建", path=path)
        return True

    def _validate_ftp_config(self) -> bool:
        if self.upload_protocol in ('ftp_client', 'both'):
            if not FTP_AVAILABLE or FTPClientUploader is None:
                self._log_event("❌", "FTP_UNAVAILABLE", "FTP 功能不可用，无法启动上传")
                return False
            host = self.ftp_client_config.get('host', '')
            if not host:
                self._log_event("❌", "FTP_CONFIG", "FTP 配置缺少 host，无法启动上传")
                return False
        return True

    def _validate_paths(self) -> bool:
        ok = True
        ok = self._ensure_dir(self.source, "源", create=False) and ok
        if self.upload_protocol == 'ftp_client':
            if not self.target:
                self._log_event("❌", "PATH_EMPTY", "目标路径未设置，FTP 模式需要该路径用于生成远端相对路径")
                ok = False
        else:
            ok = self._ensure_dir(self.target, "目标", create=True) and ok
        if self.enable_backup:
            ok = self._ensure_dir(self.backup, "备份", create=True) and ok
        return ok

    def _is_backup_path_ready(self) -> bool:
        """Check whether backup path is enabled and reachable."""
        if not self.enable_backup or not self.backup:
            return False
        cache_age = time.monotonic() - self._last_network_path_probe
        if cache_age < max(1.0, float(self.network_check_interval)):
            return self._last_backup_path_ok
        ready = self._safe_net_check(
            self.backup, timeout=1.5, default=False, require_write=True
        )
        self._last_backup_path_ok = ready
        self._last_network_path_probe = time.monotonic()
        return ready

    def _check_network_connection(self) -> Optional[str]:
        """检查网络连接状态
        
        Returns:
            'good' | 'unstable' | 'disconnected' | None (未检测)
        """
        if self.upload_protocol == 'ftp_client':
            return 'good'
        if getattr(self, '_net_running', False):
            return self.current_network_status

        now = time.time()
        if now - self.last_network_check < self.network_check_interval:
            return self.current_network_status
        
        self.last_network_check = now
        
        try:
            status = self._evaluate_smb_network_status(timeout=2.0)
        except Exception as e:
            logger.debug(f"标记网络状态时检查失败: {type(e).__name__}: {e}")
            status = 'disconnected'
        
        self._record_network_status(status)
        if status == 'good':
            self.network_retry_count = 0
        else:
            self.network_retry_count += 1
        return status

    def _log_task_transition(
        self, identity: FileIdentity, state: FileTaskState, reason: str
    ) -> None:
        self._log_event(
            "ℹ️" if state not in {FileTaskState.FAILED, FileTaskState.STALE} else "⚠️",
            "TASK_STATE",
            "文件任务状态转换",
            task_id=identity.sha256[:16],
            file=os.path.basename(identity.normalized_path),
            state=state.value,
            reason=reason,
        )

    def _handle_upload_failure(
        self,
        file_path: str,
        protocol_state: Optional[Dict[str, bool]] = None,
        identity: Optional[FileIdentity] = None,
    ) -> None:
        """处理上传失败（带重试调度）"""
        try:
            task_identity = identity or FileIdentity.capture(file_path)
        except OSError:
            return
        item = self.retry_queue.get(file_path)
        if item is None:
            item = {'count': 1, 'next': 0.0, 'identity': task_identity}
        else:
            item['count'] += 1

        if protocol_state:
            state = item.get('protocol_state', {})
            for key, value in protocol_state.items():
                state[key] = state.get(key, False) or value
            item['protocol_state'] = state
        
        retry_count = item['count']
        if retry_count > self.retry_count:
            self._log_failed_file(file_path, f"重试{retry_count-1}次后仍然失败")
            if file_path in self.retry_queue:
                del self.retry_queue[file_path]
            self._task_registry.mark_failed(task_identity, "retry_exhausted")
            self._log_task_transition(task_identity, FileTaskState.FAILED, "retry_exhausted")
            self.failed_count += 1
            self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
            self._log_event(
                "❌",
                "UPLOAD_GIVEUP",
                "已放弃上传（重试次数耗尽）",
                file=os.path.basename(file_path),
                attempts=retry_count - 1
            )
            self.log.emit(f"❌ 文件上传失败，已记录到失败日志: {os.path.basename(file_path)}")
            return
        
        wait_times = [10, 30, 60]
        wait_time = wait_times[min(retry_count - 1, len(wait_times) - 1)]
        item['next'] = time.time() + wait_time
        item['identity'] = task_identity
        self.retry_queue[file_path] = item
        self._task_registry.schedule_retry(
            task_identity,
            retry_count,
            item['next'],
            protocol_results=item.get('protocol_state'),
            reason="upload_failed",
        )
        self._log_task_transition(task_identity, FileTaskState.RETRY_WAIT, "upload_failed")
        self.log.emit(f"⚠ 文件将在稍后重试 ({retry_count}/{self.retry_count})，等待{wait_time}秒: {os.path.basename(file_path)}")

    def _process_retry_queue(self) -> None:
        """处理重试队列"""
        if not self.retry_queue:
            return
        
        now = time.time()
        retry_list = list(self.retry_queue.items())
        
        for file_path, item in retry_list:
            if not self._running or self._paused:
                break
            identity = item.get('identity')
            if not isinstance(identity, FileIdentity):
                try:
                    identity = FileIdentity.capture(file_path)
                except OSError:
                    del self.retry_queue[file_path]
                    continue
                item['identity'] = identity
            if not os.path.exists(file_path):
                del self.retry_queue[file_path]
                self._task_registry.mark_stale(identity, "retry_source_missing")
                self._log_task_transition(identity, FileTaskState.STALE, "retry_source_missing")
                continue
            
            retry_count = item.get('count', 1)
            next_at = item.get('next', 0.0)
            
            if now < next_at:
                continue

            try:
                if not identity.matches_path(file_path):
                    del self.retry_queue[file_path]
                    self._task_registry.mark_stale(identity, "retry_source_identity_changed")
                    self._log_task_transition(
                        identity, FileTaskState.STALE, "retry_source_identity_changed"
                    )
                    continue
            except OSError:
                continue
            if self._task_registry.get(identity) is None:
                self._task_registry.schedule_retry(
                    identity,
                    retry_count,
                    next_at,
                    protocol_results=item.get('protocol_state'),
                    reason="legacy_retry_queue_recovered",
                )
            if not self._task_registry.claim_due_retry(identity, now):
                continue
            self._log_task_transition(identity, FileTaskState.UPLOADING, "retry_claimed")
            
            self.log.emit(f"📤 开始重试上传 ({retry_count}/{self.retry_count}): {os.path.basename(file_path)}")
            rel = os.path.relpath(file_path, self.source)
            tgt = os.path.join(self.target, rel)
            bkp = os.path.join(self.backup, rel)
            
            try:
                # Freeze the exact generation before this retry starts.  The
                # archive action may only operate on this same identity.
                archive_identity = identity
                protocol_state = item.get('protocol_state', {})
                if self.upload_protocol in ('smb', 'both'):
                    tgt_exists = self._safe_path_exists(tgt, timeout=2.0)
                    if tgt_exists and self.upload_protocol != 'both':
                        del self.retry_queue[file_path]
                        self._task_registry.set_state(
                            identity, FileTaskState.WAITING, reason="target_exists"
                        )
                        continue

                    self._safe_make_dirs(os.path.dirname(tgt), timeout=3.0)

                copy_success, protocol_state = self._upload_file_by_protocol(
                    file_path,
                    tgt,
                    protocol_state=protocol_state
                )
                item['protocol_state'] = protocol_state
                if not copy_success:
                    raise Exception("文件上传失败")

                self._task_registry.set_state(
                    identity,
                    FileTaskState.UPLOADED,
                    reason="retry_upload_committed",
                    protocol_results=protocol_state,
                )
                archive_ok = self._queue_archive(
                    file_path,
                    bkp,
                    archive_identity,
                    protocol_state,
                )
                if not archive_ok:
                    self._log_event("⚠️", "ARCHIVE_PERSIST_RETRY", "上传已提交，归档记录等待会话内重试", file=os.path.basename(file_path))
                del self.retry_queue[file_path]
                self.uploaded_count += 1
                self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
                self.log.emit(f"✓ 重试成功: {os.path.basename(file_path)}")
                if self.upload_protocol in ('smb', 'both'):
                    self.local_file_generated.emit(tgt, "upload")
            except Exception as e:
                item['count'] = retry_count + 1
                if item['count'] > self.retry_count:
                    self._log_failed_file(file_path, f"重试{retry_count}次后仍然失败: {str(e)[:100]}")
                    del self.retry_queue[file_path]
                    self._task_registry.mark_failed(identity, "retry_exhausted")
                    self._log_task_transition(identity, FileTaskState.FAILED, "retry_exhausted")
                    self.failed_count += 1
                    self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
                    self.log.emit(f"❌ 文件上传失败，已记录到失败日志: {os.path.basename(file_path)}")
                else:
                    wait_times = [10, 30, 60]
                    wait_time = wait_times[min(item['count'] - 1, len(wait_times) - 1)]
                    item['next'] = time.time() + wait_time
                    self.retry_queue[file_path] = item
                    self._task_registry.schedule_retry(
                        identity,
                        item['count'],
                        item['next'],
                        protocol_results=item.get('protocol_state'),
                        reason="retry_failed",
                    )
                    self._log_task_transition(identity, FileTaskState.RETRY_WAIT, "retry_failed")
                    self.log.emit(f"⚠ 重试失败，已重新排队 ({item['count']}/{self.retry_count})，等待{wait_time}秒: {os.path.basename(file_path)}")

    def _log_failed_file(self, file_path: str, reason: str) -> None:
        """记录失败文件到日志"""
        try:
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.failed_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {file_path} - {reason}\n")
        except Exception as e:
            self.log.emit(f"写入失败日志出错: {e}")

    def _upload_file_by_protocol(
        self,
        src: str,
        dst: str,
        protocol_state: Optional[Dict[str, bool]] = None
    ) -> Tuple[bool, Dict[str, bool]]:
        """根据协议上传文件，支持记录已成功的协议。"""
        state = dict(protocol_state or {})
        if self.upload_protocol == 'smb':
            smb_ok = state.get('smb', False) or self._upload_via_smb(src, dst)
            return smb_ok, {'smb': smb_ok}
        elif self.upload_protocol == 'ftp_client':
            ftp_ok = state.get('ftp', False) or self._upload_via_ftp(src, dst)
            return ftp_ok, {'ftp': ftp_ok}
        elif self.upload_protocol == 'both':
            smb_ok = state.get('smb', False)
            if not smb_ok:
                smb_ok = self._upload_via_smb(src, dst)
            ftp_ok = state.get('ftp', False)
            if not ftp_ok:
                ftp_ok = self._upload_via_ftp(src, dst)
            return smb_ok and ftp_ok, {'smb': smb_ok, 'ftp': ftp_ok}
        else:
            self._log_event("❌", "PROTO_UNKNOWN", "未知的上传协议", protocol=self.upload_protocol)
            return False, state

    def _upload_via_smb(self, src: str, dst: str) -> bool:
        """通过 SMB 上传文件，所有文件均支持断点续传。"""
        try:
            return self._upload_with_resume(src, dst)
        except Exception as e:
            self._log_event(
                "❌",
                "SMB_ERROR",
                "SMB 上传失败",
                error=type(e).__name__,
                detail=str(e)[:100]
            )
            return False
    
    def _upload_with_resume(self, src: str, dst: str) -> bool:
        """使用断点续传上传文件。"""
        try:
            # 检查是否有续传记录
            resume_info = self.resume_manager.get_resume_info(src, dst)
            if resume_info:
                uploaded = resume_info.get('uploaded_bytes', 0)
                total = resume_info.get('total_bytes', 0)
                percent = int(100 * uploaded / total) if total > 0 else 0
                self.log.emit(f"📂 发现续传记录: {os.path.basename(src)} ({percent}% 已完成)")
            
            # 创建进度回调
            def progress_callback(uploaded: int, total: int, filename: str):
                if total > 0:
                    progress = int(100 * uploaded / total)
                    self.file_progress.emit(filename, progress)
                    # 每 10% 输出一次日志
                    if progress > 0 and progress % 10 == 0:
                        self.log.emit(
                            f"📊 上传进度: {progress}% "
                            f"({uploaded/(1024*1024):.1f}MB/{total/(1024*1024):.1f}MB)"
                        )
            
            # 创建可续传上传器
            self.resumable_uploader = ResumableFileUploader(
                resume_manager=self.resume_manager,
                buffer_size=1024 * 1024,  # 1MB
                progress_callback=progress_callback
            )
            
            # 计算速率限制
            rate_limit = self.max_upload_rate_bytes if self.limit_upload_rate else 0
            
            # 执行上传
            success, error_msg = self.resumable_uploader.upload_with_resume(
                source_path=src,
                target_path=dst,
                rate_limit_bytes=rate_limit
            )
            
            if success:
                self.log.emit(f"✓ 文件上传完成: {os.path.basename(src)}")
                return True
            else:
                if "中断" in error_msg:
                    self.log.emit(f"⏸️ 上传已暂停，进度已保存: {os.path.basename(src)}")
                else:
                    self.log.emit(f"❌ 上传失败: {error_msg}")
                return False
                
        except Exception as e:
            self.log.emit(f"❌ 断点续传上传失败: {e}")
            # 标记上传失败但保留续传记录
            self.resume_manager.complete_upload(src, success=False)
            return False
        finally:
            self.resumable_uploader = None

    def _upload_via_ftp(self, src: str, dst: str) -> bool:
        """通过 FTP 上传文件"""
        try:
            if not FTP_AVAILABLE or FTPClientUploader is None:
                self._log_event("❌", "FTP_UNAVAILABLE", "FTP 功能不可用")
                return False
            
            if not self.ftp_client and self.ftp_client_config:
                self.ftp_client = FTPClientUploader(self.ftp_client_config)
                if not self.ftp_client.connect(cancel_event=self._net_stop_event):
                    host = self.ftp_client_config.get('host', 'unknown')
                    port = self.ftp_client_config.get('port', 21)
                    self._log_event("❌", "FTP_CONN", "无法连接到 FTP 服务器", host=host, port=port)
                    self.ftp_client = None
                    return False
            
            if not self.ftp_client:
                self._log_event("❌", "FTP_INIT", "FTP 客户端未初始化")
                return False
            
            rel_path = os.path.relpath(dst, self.target)
            remote_path = self.ftp_client_config.get('remote_path', '/upload')
            remote_file = f"{remote_path}/{rel_path}".replace('\\', '/')
            
            transfer = self.ftp_client.upload_file_result(Path(src), remote_file)
            if transfer.success:
                self._log_event(
                    "✅",
                    "FTP_OK",
                    "FTP 上传成功",
                    file=os.path.basename(remote_file),
                    remote=remote_file
                )
                return True
            else:
                self._log_event(
                    "❌",
                    "FTP_UPLOAD",
                    "FTP 上传失败",
                    file=os.path.basename(remote_file),
                    remote=remote_file,
                    ftp_result_code=transfer.status.get("code", "unknown"),
                    error=transfer.message,
                )
                return False
                
        except Exception as e:
            error_type = type(e).__name__
            self._log_event(
                "❌",
                "FTP_ERROR",
                "FTP 上传异常",
                error=error_type,
                detail=str(e)[:100]
            )
            return False

    def _calculate_file_hash(self, file_path: str, buffer_size: int = 8192) -> str:
        """计算文件哈希值"""
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
                    
                    if file_size > 50 * 1024 * 1024:
                        progress = int(100 * processed / file_size)
                        if progress % 10 == 0:
                            self.log.emit(f"🔍 计算哈希值... {progress}%")
            
            return hasher.hexdigest()
        except Exception as e:
            self.log.emit(f"⚠ 哈希计算失败: {e}")
            return ""

    def _ensure_dedup_cache(self, target_dir: str) -> bool:
        """每次运行只流式同步一次目标目录元数据。"""
        normalized_root = normalize_file_path(target_dir)
        if (
            self._dedup_cache_ready
            and self._dedup_cache_root == normalized_root
        ):
            return True
        self._dedup_cache.clear()
        batch: list[tuple[str, int, int]] = []
        try:
            for target_file in self._iter_target_files(target_dir):
                if not self._running or self._paused:
                    return False
                try:
                    stat = self._stat_dedup_file(target_file)
                except OSError:
                    continue
                batch.append((target_file, int(stat.st_size), int(stat.st_mtime_ns)))
                if len(batch) >= 500:
                    for target_file, size, _mtime in batch:
                        self._dedup_cache.put(size, "", target_file)
                    batch.clear()
            for target_file, size, _mtime in batch:
                self._dedup_cache.put(size, "", target_file)
            self._dedup_generation += 1
            self._dedup_cache_root = normalized_root
            self._dedup_cache_ready = True
            return True
        except OSError as exc:
            logger.debug("去重索引扫描失败: %s: %s", type(exc).__name__, exc)
            return False

    def _find_duplicate_by_hash(
        self, file_hash: str, target_dir: str, file_size: Optional[int] = None
    ) -> str:
        """先按大小查索引，再仅对候选文件计算或复用哈希。"""
        if not file_hash or file_size is None or file_size < 0:
            return ""
        if not self._ensure_dedup_cache(target_dir):
            self._log_event(
                "⚠️", "DEDUP_CACHE", "去重缓存不可用，已跳过跨文件名去重",
                error="session cache unavailable",
            )
            return ""
        try:
            candidates = tuple((path, 0, digest) for (size, digest), path in self._dedup_cache.items() if size == file_size)
            for target_file, indexed_mtime, cached_hash in candidates:
                if not self._running or self._paused:
                    return ""
                try:
                    stat = self._stat_dedup_file(target_file)
                except OSError:
                    self._dedup_cache.remove_path(target_file)
                    continue
                current_size = int(stat.st_size)
                current_mtime = int(stat.st_mtime_ns)
                if current_size != file_size:
                    self._dedup_cache.remove_path(target_file)
                    continue
                target_hash = cached_hash
                if not target_hash or current_mtime != indexed_mtime:
                    target_hash = self._calculate_file_hash(target_file)
                    if target_hash:
                        self._dedup_cache.put(current_size, target_hash, target_file)
                if target_hash == file_hash:
                    return target_file
            return ""
        except (OSError, IOError) as e:
            logger.debug(f"查询去重索引失败: {type(e).__name__}: {e}")
            return ""

    def _record_dedup_target(self, target_path: str, digest: str) -> None:
        if not self._dedup_cache_ready or not digest:
            return
        try:
            stat = self._stat_dedup_file(target_path)
        except OSError:
            return
        self._dedup_cache.put(int(stat.st_size), digest, target_path)

    @staticmethod
    def _stat_dedup_file(path: str) -> os.stat_result:
        return os.stat(path)

    def _get_unique_filename(self, base_path: str) -> str:
        """生成唯一文件名
        
        Returns:
            str: 唯一的文件路径
            
        注意：如果尝试9999次仍未找到唯一名称，将使用时间戳后缀强制生成唯一名
        """
        if not os.path.exists(base_path):
            return base_path
        
        directory = os.path.dirname(base_path)
        filename = os.path.basename(base_path)
        name, ext = os.path.splitext(filename)
        
        counter = 1
        max_attempts = 9999
        while counter <= max_attempts:
            new_name = f"{name} ({counter}){ext}"
            new_path = os.path.join(directory, new_name)
            if not os.path.exists(new_path):
                return new_path
            counter += 1
        
        # 超过最大尝试次数，使用时间戳强制生成唯一名
        import time
        timestamp = int(time.time() * 1000000)  # 微秒级时间戳
        new_name = f"{name}_conflict_{timestamp}{ext}"
        new_path = os.path.join(directory, new_name)
        self.log.emit(f"⚠️ 文件名冲突严重（已尝试{max_attempts}次），使用时间戳后缀: {new_name}")
        return new_path

    def _resolve_duplicate_choice(self, src_path: str, dup_path: str) -> str:
        if self.duplicate_strategy != 'ask':
            return self.duplicate_strategy
        if self._duplicate_ask_choice:
            return self._duplicate_ask_choice
        event = threading.Event()
        result: Dict[str, Any] = {}
        payload = {
            'file': src_path,
            'duplicate': dup_path,
            'event': event,
            'result': result,
        }
        try:
            self.ask_user_duplicate.emit(payload)
        except Exception as e:
            # Signal发送失败（UI可能已关闭）
            logger.debug(f"发送重复文件询问失败: {type(e).__name__}")
            return 'skip'
        wait_start = time.time()
        while self._running or not self.archive_queue.empty():
            if event.wait(timeout=0.2):
                break
            if time.time() - wait_start > 120:
                break
        if not event.is_set():
            try:
                self.log.emit("?? 重复文件处理超时，默认跳过")
            except Exception:
                # 日志发送失败静默忽略
                pass
            return 'skip'
        choice = result.get('choice', 'skip')
        if result.get('apply_all'):
            self._duplicate_ask_choice = choice
        return choice

    def _archive_worker(self) -> None:
        """归档 Worker（独立线程）"""
        while not self._archive_stop_event.is_set():
            src_path = ""
            item_received = False
            try:
                item = self.archive_queue.get(timeout=1)
                item_received = True
                src_path = str(item.get("source", ""))
                self._queued_archive_sources.discard(
                    self._archive_repository.normalize(src_path)
                )
                self._process_archive_item(item)
            except queue.Empty:
                continue
            except Exception as e:
                self._log_event(
                    "❌",
                    "ARCHIVE_FAIL",
                    "归档失败",
                    file=os.path.basename(src_path) if src_path else "",
                    error=type(e).__name__
                )
            finally:
                if item_received:
                    try:
                        self.archive_queue.task_done()
                    except ValueError:
                        pass

    def _process_archive_item(self, item: Dict[str, Any]) -> None:
        """执行一条已持久化的归档任务。

        仅在移动/删除真正完成后删除日志记录；任何异常都由调用者
        记录，记录本身保留到下次启动重试。
        """
        src_path = str(item.get("source", ""))
        bkp_path = str(item.get("destination", ""))
        action = str(item.get("action", "move")).lower()
        if not src_path:
            raise ValueError("待归档记录缺少源路径")
        expected_identity = self._archive_identity_from_item(item, src_path)
        if expected_identity is None:
            return
        try:
            if not expected_identity.matches_path(src_path):
                self._mark_archive_stale(src_path, "source_identity_changed")
                return
        except FileNotFoundError:
            self._mark_archive_stale(src_path, "source_missing_or_recreated")
            return
        except OSError as exc:
            # Identity could not be verified.  Keep the durable pending record
            # and do not take a destructive archive action.
            raise OSError(f"归档前无法确认源文件身份: {type(exc).__name__}: {exc}") from exc
        if action == "move":
            parent = os.path.dirname(bkp_path)
            if not bkp_path or not parent:
                raise OSError("备份路径无效，待归档记录已保留")
            os.makedirs(parent, exist_ok=True)
            shutil.move(src_path, bkp_path)
            self.log.emit(f"📦 已归档: {os.path.basename(bkp_path)}")
            self.local_file_generated.emit(bkp_path, "archive")
        elif action == "trash":
            delete_result = SafeDeletionPolicy().delete(
                SafeDeletionRequest(
                    path=src_path,
                    allowed_roots=(self.source,),
                    identity_verifier=lambda: (
                        expected_identity.matches_path(src_path),
                        "源文件身份已变化",
                    ),
                    mode="trash",
                    automatic=True,
                )
            )
            if not delete_result.success:
                raise OSError(delete_result.message)
            self._log_event(
                "⚠️", "TRASH_SRC", "源文件已移入回收站",
                file=os.path.basename(src_path),
            )
            self.log.emit(f"🗑️ 已移入回收站: {os.path.basename(src_path)}")
        elif action == "delete":
            raise OSError("归档永久删除已禁止，待归档记录已保留")
        else:
            raise ValueError(f"未知归档动作: {action}")
        self._complete_archive_record(src_path)

    def _archive_identity_from_item(
        self,
        item: Dict[str, Any],
        source: str,
    ) -> Optional[FileIdentity]:
        """Read an actionable v2 identity; legacy records are fail-closed."""
        try:
            identity_data = item.get("identity")
            if not isinstance(identity_data, dict):
                raise ValueError("missing identity")
            identity = FileIdentity.from_mapping(identity_data)
            if identity.normalized_path != self._archive_repository.normalize(source):
                raise ValueError("identity path does not match source")
            return identity
        except (TypeError, ValueError) as exc:
            self._mark_archive_stale(source, f"unsafe_record:{exc}")
            return None

    def _mark_archive_stale(self, source: str, reason: str) -> None:
        """Make an unsafe record auditable and allow a new generation to scan."""
        if self._archive_repository.mark_stale(source, reason):
            self._pending_archive_sources.discard(
                self._archive_repository.normalize(source)
            )
            self._log_event(
                "⚠️",
                "ARCHIVE_STALE",
                "待归档文件身份已变化，已跳过归档",
                file=os.path.basename(source),
                reason=reason,
            )
            for task in self._task_registry.for_path(source):
                if task.state in {FileTaskState.ARCHIVE_PENDING, FileTaskState.UPLOADED}:
                    self._task_registry.mark_stale(task.identity, reason)
                    self._log_task_transition(task.identity, FileTaskState.STALE, reason)
            return
        self._log_event(
            "❌",
            "ARCHIVE_JOURNAL",
            "无法标记不安全的待归档记录",
            file=os.path.basename(source),
            error=self._archive_repository.last_error,
        )

    def _queue_archive(
        self,
        source: str,
        destination: str,
        identity: FileIdentity,
        protocol_results: Optional[Dict[str, bool]] = None,
    ) -> bool:
        action = "move" if self.enable_backup else "trash"
        if not self._archive_repository.add(
            source,
            destination,
            action,
            identity,
            protocol_results,
        ):
            self._schedule_archive_persist_retry(
                source,
                destination,
                action,
                identity,
                protocol_results,
            )
            self._log_event(
                "❌", "ARCHIVE_JOURNAL", "无法保存待归档记录，源文件已保留",
                file=os.path.basename(source), error=self._archive_repository.last_error,
            )
            return False
        normalized = self._archive_repository.normalize(source)
        self._archive_persist_retries.pop(normalized, None)
        self._archive_repository.remove_failure(source)
        self._pending_archive_sources.add(normalized)
        self._task_registry.set_state(
            identity,
            FileTaskState.ARCHIVE_PENDING,
            reason="archive_queued",
            protocol_results=dict(protocol_results or {}),
        )
        self._log_task_transition(identity, FileTaskState.ARCHIVE_PENDING, "archive_queued")
        if normalized in self._queued_archive_sources:
            return True
        self._queued_archive_sources.add(normalized)
        self.archive_queue.put(
            {
                "source": source,
                "destination": destination,
                "action": action,
                "identity": identity.to_mapping(),
                "protocol_results": dict(protocol_results or {}),
            }
        )
        return True

    def _schedule_archive_persist_retry(
        self,
        source: str,
        destination: str,
        action: str,
        identity: FileIdentity,
        protocol_results: Optional[Dict[str, bool]] = None,
    ) -> None:
        """Retain an uploaded generation until its archive journal is durable."""
        normalized = self._archive_repository.normalize(source)
        existing = self._archive_persist_retries.get(normalized)
        attempts = int(existing.get("attempts", 0)) + 1 if existing else 1
        delay = min(
            self._archive_persist_retry_base_seconds * (2 ** max(0, attempts - 1)),
            self._archive_persist_retry_max_seconds,
        )
        exhausted = attempts >= self._archive_persist_retry_limit
        record = {
            "source": source,
            "destination": destination,
            "action": action,
            "identity": identity,
            "protocol_results": dict(protocol_results or {}),
            "attempts": attempts,
            "next_retry_at": time.time() + delay,
            "last_error": self._archive_repository.last_error,
            "exhausted": exhausted,
        }
        self._archive_persist_retries[normalized] = record
        durable_record = dict(record)
        durable_record["identity"] = identity.to_mapping()
        if not self._archive_repository.record_failure(durable_record):
            self._log_event(
                "❌", "ARCHIVE_FAILURE_OUTBOX", "归档失败 outbox 无法持久化，已保留源文件并需要人工介入",
                file=os.path.basename(source), error=self._archive_repository.last_error,
            )
        self._pending_archive_sources.add(normalized)
        self._task_registry.set_state(
            identity,
            FileTaskState.ARCHIVE_PERSIST_FAILED,
            reason="archive_journal_write_failed",
            protocol_results=dict(protocol_results or {}),
        )
        self._log_task_transition(
            identity,
            FileTaskState.ARCHIVE_PERSIST_FAILED,
            "archive_journal_write_failed",
        )
        if exhausted:
            self._log_event(
                "❌",
                "ARCHIVE_PERSIST_EXHAUSTED",
                "归档记录多次保存失败，源文件已保留，需人工重试",
                file=os.path.basename(source),
                attempts=attempts,
                error=self._archive_repository.last_error,
            )
        else:
            self._log_event(
                "⚠️",
                "ARCHIVE_PERSIST_RETRY",
                "归档记录保存失败，源文件已保留并将在会话内重试",
                file=os.path.basename(source),
                attempts=attempts,
                retry_in_seconds=delay,
                error=self._archive_repository.last_error,
            )

    def _process_archive_persist_retries(self) -> None:
        """Retry journal writes only; successful uploads are never replayed."""
        now = time.time()
        for normalized, record in list(self._archive_persist_retries.items()):
            if record.get("exhausted") or now < float(record.get("next_retry_at", 0.0)):
                continue
            identity = record.get("identity")
            source = str(record.get("source", ""))
            if not isinstance(identity, FileIdentity) or not source:
                self._archive_persist_retries.pop(normalized, None)
                self._pending_archive_sources.discard(normalized)
                continue
            try:
                if not identity.matches_path(source):
                    self._archive_persist_retries.pop(normalized, None)
                    self._pending_archive_sources.discard(normalized)
                    self._archive_repository.remove_failure(source)
                    self._task_registry.mark_stale(
                        identity, "archive_journal_source_identity_changed"
                    )
                    self._log_task_transition(
                        identity, FileTaskState.STALE, "archive_journal_source_identity_changed"
                    )
                    continue
            except FileNotFoundError:
                self._archive_persist_retries.pop(normalized, None)
                self._pending_archive_sources.discard(normalized)
                self._archive_repository.remove_failure(source)
                self._task_registry.mark_stale(identity, "archive_journal_source_missing")
                self._log_task_transition(
                    identity, FileTaskState.STALE, "archive_journal_source_missing"
                )
                continue
            except OSError:
                # The file may be temporarily unavailable.  Preserve both the
                # source reservation and retry record until the normal retry
                # limit can make the need for operator action explicit.
                self._schedule_archive_persist_retry(
                    source,
                    str(record.get("destination", "")),
                    str(record.get("action", "move")),
                    identity,
                    record.get("protocol_results"),
                )
                continue

            if self._archive_repository.add(
                source,
                str(record.get("destination", "")),
                str(record.get("action", "move")),
                identity,
                record.get("protocol_results"),
            ):
                self._archive_persist_retries.pop(normalized, None)
                self._archive_repository.remove_failure(source)
                self._task_registry.set_state(
                    identity,
                    FileTaskState.ARCHIVE_PENDING,
                    reason="archive_journal_recovered",
                    protocol_results=record.get("protocol_results"),
                )
                self._log_task_transition(
                    identity, FileTaskState.ARCHIVE_PENDING, "archive_journal_recovered"
                )
                if normalized not in self._queued_archive_sources:
                    self._queued_archive_sources.add(normalized)
                    self.archive_queue.put(
                        {
                            "source": source,
                            "destination": str(record.get("destination", "")),
                            "action": str(record.get("action", "move")),
                            "identity": identity.to_mapping(),
                            "protocol_results": dict(record.get("protocol_results") or {}),
                        }
                    )
                self._log_event(
                    "✅", "ARCHIVE_PERSIST_RECOVERED", "归档记录已保存，等待归档执行",
                    file=os.path.basename(source),
                )
                continue

            self._schedule_archive_persist_retry(
                source,
                str(record.get("destination", "")),
                str(record.get("action", "move")),
                identity,
                record.get("protocol_results"),
            )

    def retry_archive_persistence(self, source: str) -> bool:
        """Request an immediate operator-triggered retry of a failed journal write."""
        normalized = self._archive_repository.normalize(source)
        record = self._archive_persist_retries.get(normalized)
        if record is None:
            return False
        record["attempts"] = 0
        record["exhausted"] = False
        record["next_retry_at"] = 0.0
        self._process_archive_persist_retries()
        return normalized not in self._archive_persist_retries

    def _restore_archive_persist_failures(self) -> None:
        for persisted in self._archive_repository.load_failures():
            source = str(persisted.get("source", ""))
            try:
                identity_data = persisted.get("identity")
                if not isinstance(identity_data, dict):
                    continue
                identity = FileIdentity.from_mapping(identity_data)
            except (TypeError, ValueError):
                continue
            if not source or identity.normalized_path != self._archive_repository.normalize(source):
                continue
            normalized = self._archive_repository.normalize(source)
            restored = dict(persisted)
            restored["identity"] = identity
            self._archive_persist_retries[normalized] = restored
            self._pending_archive_sources.add(normalized)
            self._task_registry.set_state(
                identity,
                FileTaskState.ARCHIVE_PERSIST_FAILED,
                reason="archive_journal_failure_recovered",
                protocol_results=restored.get("protocol_results"),
            )

    def _complete_archive_record(self, source: str) -> None:
        identities = self._task_registry.for_path(source)
        if self._archive_repository.remove(source):
            self._pending_archive_sources.discard(
                self._archive_repository.normalize(source)
            )
            for task in identities:
                if task.state is FileTaskState.ARCHIVE_PENDING:
                    self._task_registry.set_state(
                        task.identity, FileTaskState.ARCHIVED, reason="archive_completed"
                    )
                    self._log_task_transition(
                        task.identity, FileTaskState.ARCHIVED, "archive_completed"
                    )
        else:
            self._log_event(
                "⚠️", "ARCHIVE_JOURNAL", "归档完成但记录清理失败",
                file=os.path.basename(source), error=self._archive_repository.last_error,
            )

    def _restore_pending_archives(self) -> None:
        restored = 0
        for record in self._archive_repository.load():
            source = str(record.get("source", ""))
            if not source:
                continue
            if record.get("state") == "stale":
                continue
            if self._archive_identity_from_item(record, source) is None:
                continue
            self._pending_archive_sources.add(
                self._archive_repository.normalize(source)
            )
            normalized = self._archive_repository.normalize(source)
            if normalized in self._queued_archive_sources:
                continue
            self._queued_archive_sources.add(normalized)
            self.archive_queue.put(dict(record))
            restored += 1
        if restored:
            self.log.emit(f"📦 已恢复 {restored} 个待归档任务，源文件不会重复上传")

    def _disk_ok(self, path: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """检查磁盘空间
        
        Returns:
            (free_percent, total_gb, free_gb) 元组
            - 成功: 返回实际的空闲百分比（0-100）、总容量GB、剩余空间GB
            - 失败: 返回 (None, None, None) 表示检查失败，调用方应区别对待
            
        注意：0.0% 表示磁盘真的满了，None 表示检查失败（网络盘离线等）
        """
        try:
            parent = os.path.dirname(path) or path
            if self._is_remote_path(parent):
                values = self._run_remote_fileop(
                    "disk_usage", parent, 2.0, None
                )
                if values is None:
                    return None, None, None
                total, free = values
            else:
                usage = shutil.disk_usage(parent)
                total, free = usage.total, usage.free
            total_gb = total / (1024 ** 3)
            free_gb = free / (1024 ** 3)
            free_percent = (free / total) * 100 if total > 0 else 0.0
            return free_percent, total_gb, free_gb
        except (OSError, IOError, TypeError, ValueError) as exc:
            logger.debug(f"磁盘空间检查失败: {type(exc).__name__}: {exc}")
            return None, None, None

    def _ensure_disk_space(self) -> bool:
        """检查磁盘空间，不足时通知主窗口执行清理。

        Worker 不再自行删除文件，仅发射 disk_cleanup_needed 信号，
        由主窗口的统一清理引擎执行。
        """
        if self.upload_protocol == 'ftp_client':
            tf_ok = 100.0
        else:
            tf_ok, _, _ = self._disk_ok(self.target)
            if tf_ok is None:
                self.log.emit("⚠️ 目标磁盘检查失败，跳过清理")
                return True
        bf_ok = 100.0
        backup_check = False
        if self._is_backup_path_ready():
            bf_ok, _, _ = self._disk_ok(self.backup)
            if bf_ok is None:
                self.log.emit("⚠️ 备份磁盘检查失败，仅检查目标磁盘")
                bf_ok = 100.0
            backup_check = True

        used_target = 100.0 - tf_ok
        used_backup = 100.0 - bf_ok if backup_check else 0.0

        should_cleanup = self.enable_auto_delete and (
            used_target >= self.auto_delete_threshold
            or (backup_check and used_backup >= self.auto_delete_threshold)
            or tf_ok < self.disk_threshold_percent
            or (backup_check and bf_ok < self.disk_threshold_percent)
        )

        if should_cleanup:
            # 通知主窗口执行清理（由主窗口统一引擎处理）
            self.disk_cleanup_needed.emit()

        if tf_ok < self.disk_threshold_percent or (backup_check and bf_ok < self.disk_threshold_percent):
            self._set_pause_reason("disk", True)
            now = time.time()
            if now - self._last_space_warn > 10:
                self._last_space_warn = now
                self._log_event(
                    "⚠️",
                    "DISK_LOW",
                    "磁盘空间不足",
                    target=f"{tf_ok:.0f}%",
                    backup=f"{bf_ok:.0f}%" if backup_check else "n/a",
                    threshold=f"{self.disk_threshold_percent}%"
                )
                self.disk_warning.emit(tf_ok, bf_ok, self.disk_threshold_percent)
            return False
        self._set_pause_reason("disk", False)
        return True

    def _stream_remote_files(
        self, root_path: str, filters: Optional[List[str]] = None
    ) -> Iterator[str]:
        if time.monotonic() < self._fileop_circuit_until:
            return
        if not self._fileop_slots.acquire(blocking=False):
            return
        process: Optional[subprocess.Popen[Any]] = None
        try:
            command = (
                "$ErrorActionPreference='Stop'; "
                "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
                "$p=$env:IMAGE_UPLOAD_FILEOP_PATH; "
                "Get-ChildItem -LiteralPath $p -File -Recurse | "
                "ForEach-Object { [Console]::Out.WriteLine($_.FullName) }"
            )
            env = os.environ.copy()
            env["IMAGE_UPLOAD_FILEOP_PATH"] = _normalize_windows_path_for_check(
                root_path
            )
            create_flag = (
                subprocess.CREATE_NO_WINDOW
                if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW')
                else 0
            )
            process = subprocess.Popen(
                [
                    'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                    '-Command', command,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=create_flag,
                env=env,
            )
            with self._fileop_lock:
                self._fileop_processes.add(process)
            stdout = process.stdout
            if stdout is None:
                return
            for raw_line in iter(stdout.readline, b""):
                if not self._running:
                    process.kill()
                    break
                path = raw_line.decode("utf-8-sig", errors="replace").rstrip("\r\n")
                ext = os.path.splitext(path)[1].lower()
                if path and (not filters or ext in filters):
                    yield path
            process.wait(timeout=1)
        except Exception as exc:
            logger.debug("网络目录流式扫描失败: %s: %s", type(exc).__name__, exc)
            if process is not None:
                try:
                    process.kill()
                except Exception:
                    pass
        finally:
            if process is not None:
                with self._fileop_lock:
                    self._fileop_processes.discard(process)
            self._fileop_slots.release()

    def _stream_remote_image_files(self) -> Iterator[str]:
        for path in self._stream_remote_files(self.source, self.filters):
            if self._should_yield_source_path(path):
                yield path

    def _should_yield_source_path(self, path: str) -> bool:
        """Apply the shared identity and pending-archive gate for source scans.

        Enumeration remains protocol-specific, while this method owns the
        generation check used by both local and network-backed streams. A file
        whose identity cannot be captured is skipped by both paths; uploading an
        unverified generation would otherwise create divergent behavior.
        """
        normalized = self._archive_repository.normalize(path)
        if normalized in self._pending_archive_sources:
            return False
        try:
            identity = FileIdentity.capture(path)
        except OSError:
            return False
        return not self._task_registry.should_skip_scan(identity)

    def _iter_target_files(self, target_dir: str) -> Iterator[str]:
        if self._is_remote_path(target_dir):
            yield from self._stream_remote_files(target_dir)
            return
        for root, _, names in os.walk(target_dir):
            for name in names:
                yield os.path.join(root, name)

    def _get_image_files(self) -> Iterable[str]:
        """流式遍历图片文件，不构造全量路径列表。"""
        if self._is_remote_path(self.source):
            yield from self._stream_remote_image_files()
            return
        if not os.path.exists(self.source):
            return
        for root, _, names in os.walk(self.source):
            if not self._running:
                break
            for name in names:
                if not self._running:
                    break
                ext = os.path.splitext(name)[1].lower()
                if not self.filters or ext in self.filters:
                    path = os.path.join(root, name)
                    if self._should_yield_source_path(path):
                        yield path

    def _wait_before_upload(self, images: List[str]) -> None:
        """发现待上传文件后，按配置延迟本轮上传。"""
        if images and self.file_upload_delay_seconds > 0:
            time.sleep(self.file_upload_delay_seconds)

    def _run(self) -> None:
        """主运行循环"""
        self.log.emit("🚀 开始图片上传服务（上传与归档已分离）")
        self.log.emit(f"📡 上传协议: {self.upload_protocol}")
        self._log_event(
            "ℹ️",
            "SERVICE_START",
            "上传服务启动",
            source=self.source,
            target=self.target,
            backup=self.backup if self.enable_backup else "disabled"
        )
        self.start_time = time.time()
        self._health_check_counter = 0  # 健康检查计数器
        
        # 启动归档线程
        self._archive_thread = threading.Thread(target=self._archive_worker, daemon=True)
        self._archive_thread.start()
        self.log.emit("📦 归档线程已启动")
        
        # 重置统计
        self.uploaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.retry_queue.clear()
        
        try:
            while self._running:
                # Archive-journal recovery is local state work and must not be
                # blocked by upload network or disk eligibility checks.
                self._process_archive_persist_retries()
                # 定期健康检查（每 60 次循环，约每 30 秒）
                self._health_check_counter += 1
                if self._health_check_counter >= 60:
                    self._health_check_counter = 0
                    self.log_health_status()
                
                # 所有暂停原因由 PauseState 管理，网络线程负责发布恢复事件。
                pause_log_counter = 0
                while self._paused and self._running:
                    time.sleep(0.2)
                    pause_log_counter += 1
                    if "disk" in self.pause_reasons and pause_log_counter % 5 == 0:
                        self._ensure_disk_space()
                    
                    if pause_log_counter >= 50:  # 每10秒显示一次暂停提示
                        pause_log_counter = 0
                        self.log.emit("⏸️ 上传已暂停，等待恢复...")
                
                if not self._running:
                    break

                # 网络检查
                try:
                    network_status = self._check_network_connection()
                except Exception as e:
                    self.log.emit(f"⚠️ 网络检测异常: {str(e)[:100]}")
                    network_status = 'disconnected'
                
                if network_status == 'disconnected' and self._paused:
                    self.log.emit("🔌 等待网络恢复中...")
                    time.sleep(1)
                    continue

                # 磁盘空间检查（不足则清理并暂停）
                if not self._ensure_disk_space():
                    time.sleep(2)
                    continue

                # 处理重试队列
                self._process_retry_queue()

                # 扫描文件
                images = iter(self._get_image_files())
                try:
                    first_image = next(images)
                except StopIteration:
                    first_image = None
                if first_image is not None:
                    initial_image: str = first_image
                    self._wait_before_upload([initial_image])
                    def include_first() -> Iterator[str]:
                        yield initial_image
                        yield from images
                    image_stream: Iterable[str] = include_first()
                else:
                    image_stream = ()
                self.total_files = 0
                self.current = 0
                self.progress.emit(self.current, self.total_files, "")

                # 处理每个文件
                for path in image_stream:
                    self.total_files += 1
                    if not self._running:
                        break
                    if not self._ensure_disk_space():
                        time.sleep(2)
                        break
                    
                    # 暂停处理由统一状态查询；网络恢复不会清除 manual/disk 原因。
                    pause_check_counter = 0
                    while self._paused and self._running:
                        time.sleep(0.2)
                        pause_check_counter += 1
                        if "disk" in self.pause_reasons and pause_check_counter % 5 == 0:
                            self._ensure_disk_space()
                    
                    if not self._running:
                        break
                    
                    # 检查网络
                    network_status = self._check_network_connection()
                    if network_status == 'disconnected':
                        self.log.emit("⚠️ 网络已断开，停止上传新文件")
                        time.sleep(1)
                        continue

                    rel = os.path.relpath(path, self.source)
                    tgt = os.path.join(self.target, rel)
                    bkp = os.path.join(self.backup, rel)
                    fname = os.path.basename(path)
                    try:
                        task_identity = FileIdentity.capture(path)
                    except OSError as exc:
                        self._log_event(
                            "⚠️", "TASK_CAPTURE", "无法冻结文件身份，已跳过本轮",
                            file=fname, error=type(exc).__name__,
                        )
                        continue
                    if not self._task_registry.claim_for_upload(task_identity):
                        continue
                    self._log_task_transition(
                        task_identity, FileTaskState.UPLOADING, "scanner_claimed"
                    )
                    
                    # 创建目标目录（FTP-only 不需要本地目标目录）
                    if self.upload_protocol in ('smb', 'both'):
                        try:
                            if not self._safe_make_dirs(
                                os.path.dirname(tgt), timeout=3.0
                            ):
                                raise OSError("目标目录创建失败")
                        except Exception as e:
                            self._log_event(
                                "❌",
                                "TARGET_DIR",
                                "无法创建目标目录，可能无权限或网络中断",
                                path=os.path.dirname(tgt)
                            )
                            self.upload_error.emit(fname, str(e))
                            self._handle_upload_failure(path, identity=task_identity)
                            continue

                    self.current_file_name = fname
                    
                    self.log.emit(f"📤 开始上传: {fname}")
                    self.progress.emit(self.current, self.total_files, fname)
                    start_t = time.time()
                    protocol_state = None
                    
                    try:
                        # 检查文件是否已存在（FTP-only 不依赖本地目标路径）
                        tgt_exists = False
                        if self.upload_protocol in ('smb', 'both'):
                            tgt_exists = self._safe_path_exists(tgt, timeout=2.0)
                        
                        if tgt_exists and not self.enable_deduplication and self.upload_protocol != 'both':
                            self._log_event("⏭", "EXISTS_SKIP", "文件已存在，已跳过", file=fname)
                            self.skipped_count += 1
                            self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
                            self.file_progress.emit(fname, 100)
                            self._task_registry.release_for_scan(
                                task_identity, "target_exists"
                            )
                        else:
                            # 获取文件大小
                            try:
                                self.current_file_size = os.path.getsize(path)
                            except (OSError, IOError) as e:
                                logger.debug(f"获取文件大小失败 {fname}: {type(e).__name__}")
                                self.current_file_size = 0

                            # This snapshot is bound to the upload (or the
                            # duplicate-skip decision) and checked again by
                            # the separate archive thread before it mutates
                            # the source path.
                            archive_identity = task_identity
                            
                            self.file_progress.emit(fname, 0)
                            
                            dedup_supported = self.enable_deduplication and self.upload_protocol == 'smb'
                            if self.enable_deduplication and not dedup_supported and not self._dedup_not_supported_warned:
                                self._log_event("⚠️", "DEDUP_UNSUPPORTED", "当前协议不支持去重，已跳过去重检查")
                                self._dedup_not_supported_warned = True

                            should_upload = True
                            final_target = tgt
                            src_hash = ""
                            if dedup_supported:
                                duplicate_path = ""
                                src_hash = self._calculate_file_hash(path)
                                if tgt_exists:
                                    if src_hash:
                                        tgt_hash = self._calculate_file_hash(tgt)
                                        if tgt_hash and tgt_hash != src_hash:
                                            self.log.emit("?? 同名文件内容不同，按策略处理")
                                    else:
                                        self.log.emit("?? 哈希计算失败，按同名文件处理")
                                    duplicate_path = tgt
                                elif src_hash:
                                    duplicate_path = self._find_duplicate_by_hash(
                                        src_hash, self.target, self.current_file_size
                                    )

                                if duplicate_path:
                                    self._log_event(
                                        "ℹ️",
                                        "DUP_FOUND",
                                        "检测到重复文件",
                                        file=fname,
                                        duplicate=os.path.basename(duplicate_path)
                                    )
                                    choice = self._resolve_duplicate_choice(path, duplicate_path)
                                    choice = (choice or 'skip').lower()
                                    if choice not in ('skip', 'rename', 'overwrite'):
                                        choice = 'skip'
                                    if choice == 'skip':
                                        self._log_event("⚠️", "DUP_SKIP", "重复文件已跳过", file=fname)
                                        self.skipped_count += 1
                                        self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
                                        self.file_progress.emit(fname, 100)
                                        archive_ok = self._queue_archive(
                                            path,
                                            bkp,
                                            archive_identity,
                                            protocol_state,
                                        )
                                        if not archive_ok:
                                            self._log_event(
                                                "⚠️", "ARCHIVE_PERSIST_RETRY",
                                                "重复文件归档记录等待会话内重试", file=fname,
                                            )
                                        should_upload = False
                                    elif choice == 'rename':
                                        self._log_event("ℹ️", "DUP_RENAME", "重复文件将重命名上传", file=fname)
                                        final_target = self._get_unique_filename(tgt)
                                    elif choice == 'overwrite':
                                        self._log_event("⚠️", "DUP_OVERWRITE", "重复文件将覆盖上传", file=fname)
                                        final_target = tgt
                            
                            # 执行上传
                            if should_upload:
                                if self.upload_protocol in ('smb', 'both'):
                                    dir_created = self._safe_make_dirs(
                                        os.path.dirname(final_target), timeout=3.0
                                    )
                                    
                                    if dir_created is False:
                                        raise Exception("创建目标目录超时，网络可能已断开")
                                
                                upload_success, protocol_state = self._upload_file_by_protocol(
                                    path,
                                    final_target,
                                    protocol_state=protocol_state
                                )
                                
                                if not upload_success:
                                    raise Exception("文件上传失败")

                                self._task_registry.set_state(
                                    archive_identity,
                                    FileTaskState.UPLOADED,
                                    reason="upload_committed",
                                    protocol_results=protocol_state,
                                )
                                self._log_task_transition(
                                    archive_identity, FileTaskState.UPLOADED, "upload_committed"
                                )
                                
                                self.uploaded_count += 1
                                
                                # 计算速率
                                try:
                                    rate_path = final_target if self.upload_protocol in ('smb', 'both') else path
                                    size_mb = os.path.getsize(rate_path) / (1024*1024)
                                    dur = max(time.time()-start_t, 1e-6)
                                    rate = size_mb / dur
                                    self.rate = f"{rate:.2f} MB/s"
                                except (OSError, IOError, ZeroDivisionError):
                                    # 速率计算失败静默忽略（文件可能已删除）
                                    pass
                                
                                self.stats.emit(self.uploaded_count, self.failed_count, self.skipped_count, self.rate)
                                self.file_progress.emit(fname, 100)
                                self.log.emit(f"✓ 上传成功: {os.path.basename(final_target)}")
                                if self.upload_protocol in ('smb', 'both'):
                                    self.local_file_generated.emit(final_target, "upload")
                                    if dedup_supported:
                                        self._record_dedup_target(final_target, src_hash)
                                archive_ok = self._queue_archive(
                                    path,
                                    bkp,
                                    archive_identity,
                                    protocol_state,
                                )
                                if not archive_ok:
                                    self._log_event(
                                        "⚠️", "ARCHIVE_PERSIST_RETRY",
                                        "上传已提交，归档记录等待会话内重试", file=fname,
                                    )
                            else:
                                self.file_progress.emit(fname, 100)
                                
                    except Exception as e:
                        self._log_event(
                            "❌",
                            "UPLOAD_FAIL",
                            "上传失败",
                            file=fname,
                            error=type(e).__name__
                        )
                        self.log.emit(f"✗ 上传失败 {fname}: {e}")
                        self.upload_error.emit(fname, str(e))
                        self._handle_upload_failure(
                            path, protocol_state=protocol_state, identity=task_identity
                        )

                    self.current += 1
                    self.progress.emit(self.current, self.total_files, fname)

                # 间隔控制
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
