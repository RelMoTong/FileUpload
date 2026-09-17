"""上传任务 Worker 模块
文件名：src/workers/upload_worker.py
文件作用：后台上传执行模块“upload_worker”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。


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
    """在交给 Windows Shell/API 检查前统一路径分隔符。

    用途：避免配置中使用 ``/`` 的路径传入 PowerShell、盘符判断或 Windows API 后出现误判。
    输入：任意路径字符串。
    输出：Windows 下返回反斜杠路径；其他平台原样返回。
    风险点：这里只做表示层规范化，不能调用 ``realpath``，否则失联网络盘可能发生阻塞 I/O。
    """
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
    """上传会话的后台 Worker：扫描、上传、重试、归档和网络监控的协调中心。

    用途：在后台运行文件上传主循环，避免目录扫描、网络 I/O 和归档阻塞 GUI 线程。
    输入：源/目标/备份路径、协议、过滤器、重试与网络策略等会话配置。
    输出：通过 Qt 信号发送日志、统计、进度、网络状态、重复文件询问与完成通知。
    关键步骤：冻结文件身份、上传各协议、确认成功后持久化归档意图、由独立归档线程处理源文件。
    风险点：源文件可能随时被替换；网络盘可能阻塞；上传成功不等于归档成功，三者必须分开处理。

    信号说明：
        log：日志文本。
        stats：累计上传、失败、跳过数与速率。
        progress：本轮文件序号、当前总数和文件名。
        file_progress：单文件百分比。
        network_status：网络状态（good、unstable、disconnected）。
        finished：主运行循环结束。
        status：运行状态（running、paused、stopped）。
        ask_user_duplicate：请求 UI 决定重复文件策略。
        upload_error：单文件上传错误。
        disk_warning：磁盘空间告警。

    说明：``type: ignore[misc]`` 是 Qt 动态导入导致的 Pylance 误报抑制标记。
    """

    # Qt 信号只传递轻量数据；真正的文件操作始终留在 Worker 或其内部线程中。
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
        """保存一次上传会话的配置、状态机依赖对象和后台线程控制字段。

        用途：构造阶段只准备内存状态，绝不访问网络目录或启动线程。
        输入：源/目标/备份路径、上传策略、协议配置、磁盘策略和应用数据目录。
        输出：一个尚未运行的 Worker；真正启动由 Qt 线程调用 ``start``。
        关键步骤：规范化安全边界值、创建任务注册表/归档仓库/暂停状态、初始化停止事件。
        风险点：不能在构造函数扫描路径；窗口创建时网络盘可能不可达，I/O 必须留到后台阶段。

        参数：
            source：源文件夹路径。
            target：SMB 目标文件夹，FTP 模式下仍作为远端相对路径基准。
            backup：上传成功后移动到的备份目录。
            interval：周期模式下两轮扫描的间隔秒数。
            mode：``periodic`` 或 ``once``。
            disk_threshold_percent：低于该可用空间百分比时暂停上传。
            retry_count：单文件允许的失败重试次数。
            filters：允许上传的扩展名列表。
            app_dir：日志、断点续传和归档日志的应用目录。
            enable_deduplication：是否启用 SMB 去重。
            hash_algorithm：``md5`` 或 ``sha256``。
            duplicate_strategy：``skip``、``rename``、``overwrite`` 或 ``ask``。
            network_check_interval：网络健康检查间隔秒数。
            network_auto_pause/network_auto_resume：网络断开/恢复后的自动暂停策略。
            enable_auto_delete：是否向主窗口发出自动清理需求通知。
            auto_delete_threshold/auto_delete_target_percent：自动清理阈值参数。
            upload_protocol：``smb``、``ftp_client`` 或 ``both``。
            ftp_client_config：FTP/FTPS 客户端连接参数。
            enable_backup：是否归档到备份目录；关闭时只允许移入回收站。
            limit_upload_rate/max_upload_rate_mbps：可选的 SMB 限速参数。
            file_upload_delay_seconds：发现第一文件后开始上传前的等待秒数。
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

        # 去重只在 SMB 目标上支持；FTP/FTPS 不共享本地文件系统索引，不能假装支持。
        self.enable_deduplication = enable_deduplication
        self.hash_algorithm = hash_algorithm.lower()
        self.duplicate_strategy = duplicate_strategy

        # 网络监控独立于上传主循环，发布网络暂停原因而不是直接修改“是否暂停”布尔值。
        self.network_check_interval = network_check_interval
        self.network_auto_pause = network_auto_pause
        self.network_auto_resume = network_auto_resume

        # Worker 仅检测磁盘并通知主窗口；实际删除统一由 CleanupController/Service 处理。
        self.enable_auto_delete = enable_auto_delete
        self.auto_delete_threshold = auto_delete_threshold
        self.auto_delete_target_percent = max(0, min(auto_delete_target_percent, auto_delete_threshold - 5))

        # 协议配置与 FTP 客户端对象分开保存，客户端只在真正上传时按需连接。
        self.upload_protocol = upload_protocol
        self.ftp_client_config = ftp_client_config or {}
        self.ftp_client = None

        # 三类后台工作各用独立线程和停止事件：上传主循环、归档、网络监控。
        self._running = False
        self._pause_state = PauseState()
        self._thread = None
        self._archive_thread = None
        self._net_running = False
        self._net_thread = None
        self._net_stop_event = threading.Event()

        # 统计只反映当前会话；控制器会通过信号维护 UI 可读取的快照。
        self.uploaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.rate = "0 MB/s"
        self.total_files = 0
        self.current = 0
        self.start_time = None

        # 当前文件字段只用于进度展示和诊断，不作为身份或删除授权依据。
        self.current_file_name = ""
        self.current_file_size = 0
        self.current_file_uploaded = 0

        # 重试队列在内存中；归档队列前必须先写入可恢复的持久化记录。
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

        # 网络状态采用连续好/坏样本，避免一次抖动就频繁暂停和恢复。
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

        # 达到重试上限后把失败原因追加到应用目录，方便现场排障。
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

        # “询问”策略可由用户选择应用到后续重复文件，避免每个文件都阻塞等待 UI。
        self._duplicate_ask_choice: Optional[str] = None

        # 断点续传记录独立于主进程内存，异常停止后下次会话仍可读取。
        self.resume_manager = ResumeManager(self.app_dir)
        self.resumable_uploader: Optional[ResumableFileUploader] = None

    @property
    def _paused(self) -> bool:
        """兼容旧调用方的暂停视图；真实状态由多个暂停原因共同决定。"""
        return self._pause_state.is_paused

    @_paused.setter
    def _paused(self, value: bool) -> None:
        # 旧测试或集成仍可写入该属性；它只会设置 manual 原因，不会清除网络/磁盘暂停。
        """内部辅助：完成“_paused”对应的既有局部工作。"""
        self._pause_state.set("manual", bool(value))

    @property
    def pause_reasons(self) -> frozenset[str]:
        """返回当前所有暂停原因，如 manual、network、disk、stopping。"""
        return self._pause_state.reasons

    def _set_pause_reason(self, reason: str, active: bool) -> None:
        """增减一个暂停原因，若有效暂停状态改变则向控制器发布状态事件。

        用途：让人工暂停、网络中断、磁盘不足和停止请求可以叠加，而非相互覆盖。
        输入：暂停原因文本和是否启用。
        输出：状态实际从暂停/非暂停切换时发送 paused 或 running 信号。
        风险点：不能直接写单一布尔值；网络恢复不应意外解除人工暂停或磁盘暂停。
        """
        changed = self._pause_state.set(reason, active)
        if changed and self._running:
            self.status.emit('paused' if self._pause_state.is_paused else 'running')

    def start(self) -> None:
        """完成启动前校验后，启动上传主线程、归档恢复和可选网络监控。

        用途：作为 QThread 启动信号连接的入口，不能由 UI 线程直接调用。
        输入：构造阶段已保存的会话配置。
        输出：校验通过后发出 running 状态，并创建 Python 上传主线程；失败则结束会话。
        关键步骤：防重复启动、验证路径/FTP、重置状态、恢复归档日志、检查续传、启动线程。
        风险点：启动失败必须发送 stopped 和 finished；否则服务层会永久持有无法工作的会话。
        """
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
            self._log_event(
                "⚠️", "NO_BACKUP", "备份已关闭，上传成功后将源文件移入回收站"
            )
        self._running = True
        self._pause_state.clear()
        self.network_pause_by_auto = False
        self._network_good_streak = 0
        self._network_bad_streak = 0
        self._net_stop_event.clear()
        self._archive_stop_event.clear()
        self._restore_pending_archives()
        self._restore_archive_persist_failures()

        # 只提示待续传记录；实际上传仍由主循环按统一的身份和网络规则处理。
        self._check_pending_resumes()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        # FTP-only 没有 SMB 目录可探测，跳过网络路径监控，FTP 连接错误会由上传分支报告。
        if self.upload_protocol != 'ftp_client':
            self._net_running = True
            self._net_thread = threading.Thread(target=self._network_monitor_loop, daemon=True)
            self._net_thread.start()

        self.status.emit('running')

    def _check_pending_resumes(self) -> None:
        """读取并提示可续传记录，帮助用户理解启动后优先处理的文件。"""
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
        """生成不修改状态的健康快照，供监控、日志和现场排障读取。

        输出包含线程/进程数量、暂停原因、网络状态、统计数、协议和续传状态；它不触发
        网络访问，因此可在出现问题时安全调用。
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
        """将当前健康快照压缩成一行日志，便于长期运行时定位异常趋势。"""
        status = self.get_health_status()
        self.log.emit(f"📊 健康检查: 运行={status['running']}, "
                     f"网络={status['network_status']}, "
                     f"上传/失败/跳过={status['uploaded_count']}/{status['failed_count']}/{status['skipped_count']}")

    def pause(self) -> None:
        """增加人工暂停原因；上传主循环会在安全检查点等待。"""
        if not self._running:
            return
        self._set_pause_reason("manual", True)

    def resume(self) -> None:
        """移除人工暂停原因；其他暂停原因仍然保留。"""
        if not self._running:
            return
        self._set_pause_reason("manual", False)

    def stop(self, wait: bool = False, timeout: float = 5.0) -> None:
        """请求停止上传、网络检查、远程文件操作和归档线程。

        用途：让服务层/应用退出可以统一关闭 Worker 内的所有后台工作。
        输入：保留的历史 ``wait`` 和 ``timeout`` 参数；当前停止请求本身保持非阻塞。
        输出：运行标记和停止事件被设置，FTP 连接被断开，状态发布为 stopped。
        关键步骤：停止续传器以保存进度、断开 FTP、终止可杀子进程、设置归档/网络停止事件。
        风险点：不能因为等待网络 I/O 而卡住 UI；实际有限等待由服务层的 shutdown 路径负责。
        """
        self.log.emit(f"🛑 正在停止上传任务 ({'安全模式' if wait else '快速模式'})...")
        self._running = False
        self._set_pause_reason("stopping", True)

        # 先通知续传上传器停止，它会保留当前进度以供下次启动继续。
        if self.resumable_uploader:
            self.resumable_uploader.stop()
            self.resumable_uploader = None
            self.log.emit("💾 上传进度已保存，下次启动可继续")

        # FTP 客户端可能持有网络 socket，停止时主动断开以缩短后台收尾时间。
        if self.ftp_client:
            try:
                self.ftp_client.disconnect()
                self.ftp_client = None
                self.log.emit("✓ FTP 客户端已断开")
            except Exception as e:
                self.log.emit(f"⚠️ FTP 客户端断开异常: {e}")

        self._terminate_fileop_processes()
        self._archive_stop_event.set()

        # 网络监控使用 Event.wait，设置事件可立即打断长检查间隔。
        self._net_running = False
        self._net_stop_event.set()

        self.log.emit("✓ 上传任务已停止")
        self.status.emit('stopped')

    def _apply_network_status(self, status: str) -> None:
        """将网络采样结果映射为 network 暂停原因，并使用连续样本抑制抖动。

        用途：网络短暂抖动时不反复暂停/恢复；真正断开时快速阻止新上传。
        输入：good、unstable 或 disconnected。
        输出：可能更新 network 暂停原因并发送状态日志。
        关键步骤：坏样本清零好样本并暂停；连续两个好样本后才自动恢复。
        风险点：自动恢复只能清除 network 原因，不能清除 manual、disk 或 stopping 原因。
        """
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
        """记录一次网络采样，并仅在状态改变时向 UI 发送日志和信号。"""
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
        """返回 Worker 内部是否仍有 Python 线程或网络文件操作子进程活动。"""
        threads = [self._thread, self._archive_thread, self._net_thread]
        return self._fileop_active_count() > 0 or any(
            thread is not None
            and hasattr(thread, "is_alive")
            and thread.is_alive()
            for thread in threads
        )

    def _network_monitor_loop(self) -> None:
        """在独立线程循环检测 SMB 目标/备份可写性，并发布网络状态。

        用途：避免上传主循环每次只处理一个文件时才发现共享目录已经离线。
        输入：构造时保存的目标路径、备份开关和监控间隔。
        输出：网络状态、暂停原因、周期统计心跳和断线提示。
        关键步骤：有超时地检查目录、记录状态、按状态调整下次检查间隔、等待停止事件。
        风险点：网络检查不能直接在此线程无限阻塞，所以共享目录探测使用有超时的子进程。
        """
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
                # 信号发送失败通常表示 UI 已关闭；忽略它以免监控循环因日志失败反复报错。
                pass

            # 自适应间隔
            interval = 1 if status in ('unstable', 'disconnected') else max(1, int(self.network_check_interval))
            if self._net_stop_event.wait(interval):
                break

    def _evaluate_smb_network_status(self, timeout: float = 1.5) -> str:
        """检查所有必需 SMB 路径的可写性，给出 good/unstable/disconnected。

        用途：区分“目标可写但备份不可写”的不稳定状态和全部不可访问的断开状态。
        输入：单次目录探测允许的最长秒数。
        输出：三个网络状态之一，同时缓存备份路径结果。
        关键步骤：检查目标、按需检查备份、根据两个布尔结果分类。
        风险点：主机能 ping 通不等于共享目录可写，必须验证实际目录和写入权限。
        """
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
        """在有限时间内验证 UNC 或映射盘目录的实际可读/可写性。

        用途：判断共享目录是否真的可用于上传，而不是只判断网络主机是否在线。
        输入：路径、超时秒数、失败时的默认值及是否要求写入权限。
        输出：在时间内完成目录/探针文件操作时返回布尔结果；超时或异常返回默认值。
        关键步骤：识别 UNC/映射盘、映射盘转换为 UNC、在 PowerShell 子进程中执行目录探测。
        风险点：不能在 Python 线程直接对失联 SMB 路径执行无超时 ``scandir/stat``，否则线程会假死。
        """
        def is_unc(p: str) -> bool:
            """识别 UNC 格式路径（以两个反斜杠开头）。"""
            return isinstance(p, str) and p.startswith('\\\\')

        def get_drive_root(p: str) -> str:
            """从盘符路径取得盘根，供 Windows DriveType API 使用。"""
            drive, _ = os.path.splitdrive(p)
            return drive + '\\' if drive else ''

        def is_mapped_drive(p: str) -> bool:
            """通过 Windows DriveType 判断盘符是否映射到远程共享。"""
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
            """将已映射盘符转换为 UNC 路径，以便探测真正的共享目录。"""
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
            """在独立 PowerShell 进程探测目录，超时时由 subprocess 终止等待。"""
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
        """在线程锁保护下读取仍在执行的网络文件操作子进程数量。"""
        with self._fileop_lock:
            return len(self._fileop_processes)

    def _terminate_fileop_processes(self) -> None:
        """终止当前网络文件操作子进程，供停止上传时快速打断失联路径操作。"""
        with self._fileop_lock:
            processes = tuple(self._fileop_processes)
        for process in processes:
            try:
                process.kill()
            except Exception:
                pass

    @staticmethod
    def _is_remote_path(path: str) -> bool:
        """判断 UNC 或映射网络盘路径；API 异常时宁可按本地路径处理并由后续操作失败。"""
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
        """在可超时、可终止的子进程中执行网络路径元数据操作。

        用途：为存在性、目录判断、建目录、磁盘容量和网络目录扫描提供可控的 I/O 边界。
        输入：操作名称、路径、超时、失败默认值，以及扫描时的扩展名过滤器。
        输出：根据操作返回布尔值、容量元组、路径列表或默认值。
        关键步骤：本地路径直接调用 Python API；网络路径使用槽位限制的 PowerShell 进程；超时后杀进程。
        风险点：绝不能让网络路径操作在线程超时后继续“遗留运行”；连续超时会暂时熔断以保护系统。
        """
        # 本地路径不需要 PowerShell：减少进程创建，也能可靠保留中文路径。
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
        # 熔断期间直接返回默认值，避免网络持续异常时不断创建新的 PowerShell 进程。
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
            # 每种操作的脚本都只从环境变量读取路径，避免把用户路径拼接进 PowerShell 命令文本。
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
            # 停止上传时需要找到并终止进程，因此启动后立即登记到锁保护集合。
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
            # 超时必须终止子进程；只返回默认值而不杀进程会造成进程和句柄持续累积。
            if process is not None:
                try:
                    process.kill()
                    process.communicate(timeout=1)
                except Exception:
                    pass
            self._fileop_timeout_count += 1
            # 三次连续超时后短暂熔断，给 SMB 重连和系统资源恢复留出时间。
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
        """在本地直接、在网络路径有超时地检查路径是否存在。"""
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("exists", path, timeout, False))
        try:
            return os.path.exists(path)
        except OSError:
            return False

    def _safe_path_isdir(self, path: str, timeout: float = 2.0) -> bool:
        """在本地直接、在网络路径有超时地检查路径是否为目录。"""
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("isdir", path, timeout, False))
        try:
            return os.path.isdir(path)
        except OSError:
            return False

    def _safe_make_dirs(self, path: str, timeout: float = 3.0) -> bool:
        """在本地直接、在网络路径有超时地确保目录存在。"""
        if self._is_remote_path(path):
            return bool(self._run_remote_fileop("mkdir", path, timeout, False))
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except OSError:
            return False

    def _log_event(self, level: str, code: str, message: str, **fields) -> None:
        """将结构化诊断字段压缩为一行用户日志；日志发送失败不会影响上传流程。"""
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
        """验证目录存在且为文件夹，必要时尝试安全创建。

        用途：启动前和上传前统一处理本地/网络路径目录规则。
        输入：路径、面向用户的目录名称以及是否允许创建。
        输出：目录可用时返回 ``True``；失败时写入具体日志并返回 ``False``。
        关键步骤：先存在性检查，再确认目录类型，最后按配置决定是否创建。
        风险点：源目录绝不自动创建，避免拼写错误后悄悄扫描一个空目录；目标/备份可按配置创建。
        """
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
        """验证 FTP/FTPS 上传所需依赖和最小连接配置。"""
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
        """按当前协议验证源、目标和备份路径。

        FTP-only 模式不要求本地 SMB 目标目录存在，但仍需要目标基准路径用于生成远端相对路径。
        """
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
        """判断备份是否启用且路径目前可写，优先复用最近一次网络探测结果。"""
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
        """获取当前网络状态；网络监控线程运行时读取其结果，否则按间隔主动探测。

        用途：上传每个新文件前确认 SMB 目标和备份是否仍可用。
        输出：good、unstable、disconnected 或尚未检测时的 ``None``。
        风险点：FTP-only 不用 SMB 路径健康检查；目录探测仍必须有超时。
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
        """记录文件任务状态机的转换，供恢复、重试和现场审计定位。"""
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
        """记录一次上传失败，并按退避时间将同一文件代际加入重试队列。

        用途：避免网络瞬断立即永久失败，同时防止无限重试占满会话。
        输入：失败文件、已成功协议状态以及可选已冻结文件身份。
        输出：未超过上限时更新重试队列/状态机；超过上限时写失败日志并标记 FAILED。
        关键步骤：捕获或复用身份、合并多协议成功状态、计算退避时间、写状态机转换。
        风险点：重试必须绑定 ``FileIdentity``；同路径换成新文件后不能把旧任务继续上传或归档。
        """
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
        """处理到期的重试任务，并在每个关键点复核文件代际。

        用途：在不重新上传已成功协议的前提下，恢复暂时失败的文件。
        输入：内存重试队列与当前 Worker 状态。
        输出：成功则上传并排队归档；失败则退避重排；过期/变化文件标记 STALE。
        关键步骤：检查运行/暂停、重捕获身份、到期判断、领取任务、上传、持久化归档、重新排队。
        风险点：不能直接按路径重试；源文件缺失或身份改变时必须丢弃旧任务，留给新扫描重新发现。
        """
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
                # 重试开始前固定本次文件代际；后续归档只能处理这一份相同身份的源文件。
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
        """将达到重试上限的文件和原因追加到失败日志，便于人工补传。"""
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
        """按 SMB、FTP/FTPS 或双协议执行上传，并保留每个协议已成功的状态。

        用途：双协议重试时只重传失败的那一侧，避免已成功的目标被重复写入。
        输入：源路径、目标基准路径和此前重试保存的协议成功字典。
        输出：整体是否成功，以及最新的 ``smb``/``ftp`` 成功状态。
        关键步骤：按协议分支调用具体上传函数；双协议分别检查并短路已成功的一侧。
        风险点：双协议只有两侧都成功才算提交成功；不能因为一侧成功就归档源文件。
        """
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
        """通过 SMB 执行可续传上传，并把异常转换为可重试的失败结果。"""
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
        """使用断点续传组件向 SMB 目标复制文件，并持续保存可恢复进度。

        用途：网络中断、暂停或应用关闭后，可从已上传位置继续而非从零开始。
        输入：源路径、SMB 目标路径和可选限速配置。
        输出：完整传输并提交时返回 ``True``；暂停或失败返回 ``False``，续传记录按策略保留。
        关键步骤：读取旧进度、创建进度回调、构造上传器、执行上传、最终清空内存上传器引用。
        风险点：异常时必须标记本次未完成但不能删除续传记录，否则会丢失已传字节的恢复依据。
        """
        try:
            # 先读取旧进度只用于提示和上传器恢复；不把旧数字当作当前文件身份依据。
            resume_info = self.resume_manager.get_resume_info(src, dst)
            if resume_info:
                uploaded = resume_info.get('uploaded_bytes', 0)
                total = resume_info.get('total_bytes', 0)
                percent = int(100 * uploaded / total) if total > 0 else 0
                self.log.emit(f"📂 发现续传记录: {os.path.basename(src)} ({percent}% 已完成)")

            # 回调只发送轻量 UI 进度；实际读写仍由 ResumableFileUploader 执行。
            def progress_callback(uploaded: int, total: int, filename: str):
                """作用：执行“progress_callback”的既有业务或基础设施职责。

                参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                返回结果：沿用当前实现的返回值、事件或异常语义。
                执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                """
                if total > 0:
                    progress = int(100 * uploaded / total)
                    self.file_progress.emit(filename, progress)
                    # 每 10% 输出一次日志
                    if progress > 0 and progress % 10 == 0:
                        self.log.emit(
                            f"📊 上传进度: {progress}% "
                            f"({uploaded/(1024*1024):.1f}MB/{total/(1024*1024):.1f}MB)"
                        )

            # 保存当前上传器引用，使 stop() 能请求中止并保留进度。
            self.resumable_uploader = ResumableFileUploader(
                resume_manager=self.resume_manager,
                buffer_size=1024 * 1024,  # 1MB
                progress_callback=progress_callback
            )

            # 限速值使用字节/秒；未启用限速时传 0 表示上传器不节流。
            rate_limit = self.max_upload_rate_bytes if self.limit_upload_rate else 0

            # 上传器内部负责原子续传记录和文件分块复制。
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
        """通过 FTP/FTPS 客户端上传，并以客户端的提交结果作为唯一成功依据。

        用途：支持不共享本地文件系统的远端 FTP/FTPS 目标。
        输入：本地源文件和用于计算远端相对路径的目标基准路径。
        输出：客户端确认成功时返回 ``True``；连接、配置或传输失败返回 ``False``。
        关键步骤：按需创建并连接客户端、计算远端路径、调用带结果对象的上传接口、记录状态码。
        风险点：不能只看 socket 未报错就判定成功；客户端必须完成远端大小验证和最终 rename 后才返回成功。
        """
        try:
            if not FTP_AVAILABLE or FTPClientUploader is None:
                self._log_event("❌", "FTP_UNAVAILABLE", "FTP 功能不可用")
                return False

            # FTP 连接延迟到第一文件才创建，避免仅打开设置窗口就占用远端会话。
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

            # 远端目录来自 FTP 配置；使用本地目标基准只为了复用与 SMB 相同的目录层级。
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
        """分块计算文件哈希，供 SMB 去重比较使用。

        用途：识别同名或不同名但内容相同的 SMB 目标文件。
        输入：文件路径和读取缓冲区大小。
        输出：成功返回十六进制摘要；暂停、停止或读取失败返回空字符串。
        关键步骤：按配置选择算法、分块读取、在每个循环检查运行/暂停状态、报告大文件进度。
        风险点：哈希是耗时 I/O；停止/暂停时必须尽快返回，且空摘要不能被误判成“内容相同”。
        """
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
        """每个目标根目录在一次会话内只流式建立一次有限去重缓存。

        用途：减少对 SMB 目标目录反复全量扫描，同时不把所有目标文件完整加载进复杂对象树。
        输入：SMB 目标根目录。
        输出：缓存可用时返回 ``True``；被暂停、停止或扫描异常时返回 ``False``。
        关键步骤：根目录变化时清空旧缓存、流式遍历目标、按批写入大小索引、记录当前代次。
        风险点：缓存只是性能优化；每次真正比较仍会重新 stat/hash，不能把缓存视为删除或覆盖授权。
        """
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
        """先按大小筛选，再对少量候选计算/复用哈希以寻找重复文件。

        用途：避免为整个目标目录计算哈希，降低 SMB 去重的 I/O 成本。
        输入：源文件哈希、目标目录和源文件大小。
        输出：找到内容一致文件时返回其路径，否则返回空字符串。
        关键步骤：保证缓存、按大小取候选、重新 stat、必要时计算目标哈希、比较摘要。
        风险点：目标文件可能被外部修改；大小或修改时间不一致时必须废弃缓存条目而非信任旧哈希。
        """
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
        """把刚成功上传的 SMB 目标补入当前会话去重缓存。"""
        if not self._dedup_cache_ready or not digest:
            return
        try:
            stat = self._stat_dedup_file(target_path)
        except OSError:
            return
        self._dedup_cache.put(int(stat.st_size), digest, target_path)

    @staticmethod
    def _stat_dedup_file(path: str) -> os.stat_result:
        """集中保留去重文件的 stat 调用，便于测试替换和明确其 I/O 边界。"""
        return os.stat(path)

    def _get_unique_filename(self, base_path: str) -> str:
        """为“重命名上传”策略寻找不会覆盖现有文件的目标路径。

        用途：重复文件策略选择 rename 时保留旧文件并生成新名称。
        输入：原始目标路径。
        输出：不存在的候选路径；极端冲突时返回带微秒时间戳的路径。
        关键步骤：依次尝试 ``文件名 (序号).扩展名``，达到上限后使用时间戳后缀。
        风险点：这是命名冲突缓解措施，不替代最终上传时的原子提交和远端冲突处理。
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
        """解析重复文件策略；ask 模式通过 Qt 信号请求 UI 并有超时保护。

        用途：让后台 Worker 不直接显示对话框，而由主线程收集用户选择。
        输入：源文件路径和检测到的目标重复路径。
        输出：skip、rename、overwrite 或缓存的“应用全部”选择；超时默认 skip。
        关键步骤：非 ask 直接返回策略；ask 时创建 Event/结果字典、发信号、轮询等待、读取选择。
        风险点：UI 已关闭或无人响应时绝不能无限等待；默认 skip 比覆盖或删除更安全。
        """
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
            # 信号发送失败通常表示 UI 已关闭，安全降级为跳过重复文件。
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
        """在独立线程消费已持久化归档任务，避免上传主循环被移动/回收站操作阻塞。

        用途：把“远端上传成功后如何处置源文件”与上传提交解耦。
        输入：``archive_queue`` 中已先写入归档仓库的任务。
        输出：成功后清除归档记录；失败时保留记录以供后续恢复。
        关键步骤：阻塞取队列、取消重复排队标记、处理单项、无论结果都调用 task_done。
        风险点：绝不能先删除归档记录再移动源文件；进程崩溃会导致源文件丢失且无法恢复。
        """
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
        """执行一条已持久化的归档任务，并在真正完成后才删除其日志记录。

        用途：在上传提交后安全移动源文件到备份目录，或在禁用备份时移入回收站。
        输入：包含源路径、目标路径、动作、文件身份及协议结果的持久化任务字典。
        输出：成功时完成归档并清理记录；不安全或失败时保留记录供恢复。
        关键步骤：读取身份、删除前匹配路径、根据动作移动/回收站、最后完成记录。
        风险点：身份无法复核时必须 fail-closed（不做破坏性操作）；永久删除动作永远拒绝。
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
            # 在移动或回收站之前确认“当前路径仍是上传成功时那一代文件”。
            if not expected_identity.matches_path(src_path):
                self._mark_archive_stale(src_path, "source_identity_changed")
                return
        except FileNotFoundError:
            self._mark_archive_stale(src_path, "source_missing_or_recreated")
            return
        except OSError as exc:
            # 身份无法验证时保留持久化记录，并且不执行任何破坏性归档动作。
            raise OSError(f"归档前无法确认源文件身份: {type(exc).__name__}: {exc}") from exc
        if action == "move":
            # 备份模式只在目标父目录可确定时移动，避免把源文件移动到不受控位置。
            parent = os.path.dirname(bkp_path)
            if not bkp_path or not parent:
                raise OSError("备份路径无效，待归档记录已保留")
            os.makedirs(parent, exist_ok=True)
            shutil.move(src_path, bkp_path)
            self.log.emit(f"📦 已归档: {os.path.basename(bkp_path)}")
            self.local_file_generated.emit(bkp_path, "archive")
        elif action == "trash":
            # 不启用备份时只允许回收站；安全策略会再次检查源目录范围与身份。
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
        # 文件动作已成功完成后才删除日志记录，这保证崩溃恢复时不会丢失待处理源文件。
        self._complete_archive_record(src_path)

    def _archive_identity_from_item(
        self,
        item: Dict[str, Any],
        source: str,
    ) -> Optional[FileIdentity]:
        """读取可执行的 v2 文件身份；旧格式或不完整记录按 fail-closed 处理。

        用途：确保磁盘上恢复的归档任务仍绑定到正确源文件，而不是仅保存一个路径字符串。
        输入：归档记录字典与预期源路径。
        输出：可验证身份时返回 ``FileIdentity``；记录不安全时标记 stale 并返回 ``None``。
        关键步骤：检查 identity 字典、反序列化、比较规范化路径、失败时写 stale 状态。
        风险点：兼容旧记录时不能“猜测”身份；宁可保留源文件让用户处理，也不能错归档新文件。
        """
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
        """将不安全的归档记录标记为 stale，并允许同路径新代际重新被扫描。

        用途：源文件被替换、缺失或身份记录异常时阻止旧归档任务继续动作。
        输入：源路径和可审计的原因代码。
        输出：仓库和任务注册表更新为 stale，或记录无法写入 stale 的严重错误。
        关键步骤：先写仓库状态、移除待归档门禁、同步更新同路径任务状态、输出日志。
        风险点：不移除门禁会使后来创建的新文件永远不再上传；过早移除则可能重试旧记录。
        """
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
        """先持久化归档意图，再把任务放入内存队列。

        用途：把“上传已成功”和“源文件可安全归档”之间建立可崩溃恢复的事务边界。
        输入：源/备份路径、已经冻结的文件身份和各协议提交结果。
        输出：意图记录写入成功并已入队时返回 ``True``；写入失败时安排持久化重试并返回 ``False``。
        关键步骤：选择 move/trash、先写仓库、更新任务状态、避免重复入队、最后 queue.put。
        风险点：绝不能反过来先入队再写日志；进程崩溃会留下已经上传但没有归档保护的源文件。
        """
        action = "move" if self.enable_backup else "trash"
        # 归档日志先落盘，成功后才允许后台线程对源文件执行移动或回收站操作。
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
        """在归档日志无法写入时保留已上传源文件，并以退避策略重试持久化。

        用途：避免“远端成功后立即删除源文件，但归档恢复记录没写入”的不可恢复风险。
        输入：归档任务全部字段和已经冻结的源文件身份。
        输出：内存重试记录、持久化失败 outbox 记录和任务状态更新。
        关键步骤：计算指数退避、保存内存记录、尽力写失败 outbox、设置待归档门禁、记录状态。
        风险点：达到重试上限后仍必须保留源文件并等待人工处理，不能为了清空队列而归档它。
        """
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
        """只重试归档日志写入，绝不因为日志失败而重放已经成功的上传。

        用途：恢复临时磁盘写入问题，同时保持远端对象幂等。
        输入：内存中的待持久化归档记录。
        输出：日志恢复成功则进入归档队列；源身份改变/记录失效则标记 stale；未到期记录保留。
        关键步骤：筛选到期记录、复核身份、再次写仓库、成功后入队，失败后更新退避状态。
        风险点：上传已经提交，重放上传可能覆盖或重复远端文件；此处只修复本地归档日志。
        """
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
                # 文件可能暂时不可访问。保留源文件门禁和重试记录，直到正常重试上限
                # 明确表明需要操作员介入，期间不能把同一路径的新代际误当成旧任务。
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
        """请求立即重试指定源文件的归档日志写入，供操作员人工介入后使用。"""
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
        """启动时恢复失败 outbox，重新建立待归档门禁和会话内重试记录。"""
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
        """归档动作成功后删除对应持久化记录、失败 outbox 与内存门禁。"""
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
        """启动时把上次中断遗留的持久化归档记录重新放入归档队列。

        用途：进程异常退出后，上传已提交但尚未移动/回收站的源文件不会被遗忘。
        风险点：恢复的每条记录仍会在归档线程中复核身份，不能因为来自本地日志就直接信任。
        """
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
        """读取目标或备份磁盘空间，并明确区分“磁盘满”与“无法读取”。

        用途：上传前判断是否需要暂停，并在需要时通知主窗口统一自动清理。
        输入：本地或网络路径。
        输出：``(空闲百分比, 总 GB, 空闲 GB)``；读取失败时三个值均为 ``None``。
        关键步骤：网络盘通过可终止子进程读取容量，本地盘直接使用 ``shutil.disk_usage``。
        风险点：``0.0`` 表示磁盘确实没有空间，``None`` 才表示检查失败；两者不能混为一谈。
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

        用途：上传前确保目标/备份至少保留最小空闲空间，并按配置请求统一自动清理。
        输入：当前目标、备份、协议和磁盘阈值配置。
        输出：可继续上传时返回 ``True``；空间不足时设置 disk 暂停原因并返回 ``False``。
        关键步骤：读取空间、判断是否请求自动清理、判断最小空闲阈值、更新暂停原因和告警频率。
        风险点：Worker 不再自行删除文件，仅发射 disk_cleanup_needed 信号，由主窗口统一清理引擎执行。
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
            # 只发通知，不在上传线程删除文件，避免上传和清理同时改动源/备份目录。
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
        """通过可终止 PowerShell 子进程逐行流式读取网络目录文件路径。

        用途：网络源目录扫描不在 Python 线程中直接阻塞，也不构造全量路径列表。
        输入：网络根目录和可选扩展名过滤。
        输出：逐个产生符合过滤条件的文件路径。
        关键步骤：受槽位限制地启动子进程、逐行读取 stdout、每项检查运行标记、finally 清理进程。
        风险点：停止时必须 kill 子进程；否则失联 SMB 扫描会遗留后台进程并阻碍应用退出。
        """
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
        """在网络流式枚举基础上应用统一的文件身份与待归档门禁。"""
        for path in self._stream_remote_files(self.source, self.filters):
            if self._should_yield_source_path(path):
                yield path

    def _should_yield_source_path(self, path: str) -> bool:
        """为本地和网络源扫描统一应用文件身份与待归档门禁。

        用途：确保同一文件代际不会在上传成功但归档未完成期间被重复上传。
        输入：枚举器得到的源文件路径。
        输出：可安全交给上传主循环时返回 ``True``，否则返回 ``False``。
        关键步骤：检查是否已有待归档记录、捕获当前身份、交给任务注册表判断是否应跳过。
        风险点：无法捕获身份的文件必须跳过；上传未经验证的代际会让本地/网络扫描行为不一致。
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
        """按目标路径类型枚举目标文件，供 SMB 去重缓存使用。"""
        if self._is_remote_path(target_dir):
            yield from self._stream_remote_files(target_dir)
            return
        for root, _, names in os.walk(target_dir):
            for name in names:
                yield os.path.join(root, name)

    def _get_image_files(self) -> Iterable[str]:
        """流式遍历源文件，不构造全量路径列表，并在每项应用扩展名和身份过滤。

        用途：支持海量本地/网络源目录，同时避免一次扫描把所有路径占满内存。
        输入：Worker 源目录、过滤器与运行状态。
        输出：逐个可上传源文件路径。
        关键步骤：网络目录委托可终止子进程；本地目录使用 os.walk；每项调用统一门禁。
        风险点：枚举仅决定候选，真正上传前仍会再次捕获身份，防止扫描期间文件变化。
        """
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
        """发现第一候选文件后按配置等待，给仍在写入的相机文件一个稳定窗口。

        风险点：该等待发生在后台上传主线程，不阻塞 GUI；它不替代上传前的文件身份捕获。
        """
        if images and self.file_upload_delay_seconds > 0:
            time.sleep(self.file_upload_delay_seconds)

    def _run(self) -> None:
        """执行上传主循环：恢复归档、处理暂停/网络/磁盘、流式扫描、上传并排队归档。

        用途：将一次上传会话的完整业务顺序固定在单个后台线程中。
        输入：构造时保存的会话配置以及其他线程发布的暂停/停止事件。
        输出：连续发送日志、进度、统计；循环停止后一定发送 ``finished``。
        关键步骤：启动归档线程、恢复日志、处理暂停/重试、流式取首项延迟、逐文件冻结身份、
        上传各协议、写归档意图、根据运行模式等待下一轮。
        风险点：源文件在任意步骤都可能变化；上传成功后必须先持久化归档任务，不能立即删除源文件。
        """
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
                # 归档日志恢复只访问本地状态，不能被网络或磁盘资格检查阻断。
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

                # 只预取第一项，用于在“发现文件”与“开始上传”之间执行配置的稳定等待；
                # 不把完整迭代器转换为列表，避免大目录占用大量内存。
                images = iter(self._get_image_files())
                try:
                    first_image = next(images)
                except StopIteration:
                    first_image = None
                if first_image is not None:
                    initial_image: str = first_image
                    self._wait_before_upload([initial_image])
                    def include_first() -> Iterator[str]:
                        """作用：执行“include_first”的既有业务或基础设施职责。

                        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                        返回结果：沿用当前实现的返回值、事件或异常语义。
                        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                        """
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

                            # 该快照绑定本次上传（或重复文件跳过）决定；独立归档线程在
                            # 改动源路径前还会再次核验，防止同一路径新文件被误归档。
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

                            # 只有通过重复策略后才真正上传；双协议全部成功才会进入归档队列。
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

                                # 速率仅用于界面显示和诊断，失败不影响上传已提交的事实。
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

                # 周期模式使用短 sleep 循环，便于停止/暂停请求在最长 0.2 秒内被观察到。
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
