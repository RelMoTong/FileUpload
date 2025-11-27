"""上传任务 Worker 模块

包含文件上传的核心逻辑，支持：
- 多协议上传（SMB、FTP客户端）
- 网络监控和自动暂停/恢复
- 智能去重（MD5/SHA256）
- 速率限制
- 失败重试机制
- 异步归档
"""

import os
import sys
import time
import shutil
import threading
import datetime
import queue
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# 导入 Qt 库
try:
    from PySide6 import QtCore
    Signal = QtCore.Signal
except ImportError:
    from PyQt5 import QtCore  # type: ignore[import-not-found]
    Signal = QtCore.pyqtSignal

# 导入 FTP 客户端
try:
    from src.protocols.ftp import FTPClientUploader
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    FTPClientUploader = None  # type: ignore[assignment, misc]


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
        auto_delete_folder: str = '',
        auto_delete_threshold: int = 80,
        auto_delete_keep_days: int = 10,
        auto_delete_check_interval: int = 300,
        upload_protocol: str = 'smb',
        ftp_client_config: Optional[Dict[str, Any]] = None,
        enable_backup: bool = True,
        limit_upload_rate: bool = False,
        max_upload_rate_mbps: float = 10.0
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
            enable_auto_delete: 启用自动删除
            auto_delete_folder: 自动删除监控文件夹
            auto_delete_threshold: 自动删除磁盘阈值
            auto_delete_keep_days: 自动删除保留天数
            auto_delete_check_interval: 自动删除检查间隔
            upload_protocol: 上传协议 ('smb'|'ftp_client'|'both')
            ftp_client_config: FTP客户端配置
            enable_backup: 是否启用备份
            limit_upload_rate: 是否限制上传速率
            max_upload_rate_mbps: 最大上传速率（MB/s）
        """
        super().__init__()
        self.source = source
        self.target = target
        self.backup = backup
        self.enable_backup = enable_backup
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
        
        # 协议配置
        self.upload_protocol = upload_protocol
        self.ftp_client_config = ftp_client_config or {}
        self.ftp_client = None
        
        # 运行状态
        self._running = False
        self._paused = False
        self._thread = None
        self._archive_thread = None
        self._net_running = False
        self._net_thread = None
        
        # 统计数据
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
        
        # 队列
        self.retry_queue: Dict[str, Dict[str, Any]] = {}
        self.archive_queue: queue.Queue = queue.Queue()
        
        # 网络状态
        self.network_retry_count = 0
        self.network_auto_retry = True
        self.last_network_check = 0.0
        self.current_network_status = 'unknown'
        self.network_pause_by_auto = False
        self._last_space_warn = 0.0
        
        # 失败日志
        self.failed_log_path = self.app_dir / "failed_files.log"
        
        # 线程池
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="FileOp")
        self._net_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NetChk")
        
        # 去重询问模式的全局选择
        self._duplicate_ask_choice: Optional[str] = None

    def start(self) -> None:
        """启动上传任务"""
        if self._running:
            return
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
        # 启动网络监控线程
        self._net_running = True
        self._net_thread = threading.Thread(target=self._network_monitor_loop, daemon=True)
        self._net_thread.start()
        
        self.status.emit('running')

    def pause(self) -> None:
        """暂停上传任务"""
        if not self._running:
            return
        self._paused = True
        self.status.emit('paused')

    def resume(self) -> None:
        """恢复上传任务"""
        if not self._running:
            return
        self._paused = False
        self.status.emit('running')

    def stop(self) -> None:
        """停止上传任务"""
        self._running = False
        self._paused = False
        
        # 关闭FTP客户端
        if self.ftp_client:
            try:
                self.ftp_client.disconnect()
                self.ftp_client = None
            except Exception:
                pass
        
        # 关闭线程池
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        
        # 停止网络监控
        self._net_running = False
        try:
            self._net_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        
        self.status.emit('stopped')

    def _network_monitor_loop(self) -> None:
        """网络监控循环（独立线程）"""
        last_status = 'unknown'
        
        while getattr(self, '_net_running', False):
            try:
                # 检测网络状态
                target_ok = self._safe_net_check(self.target, timeout=0.3, default=False)
                if target_ok:
                    status = 'good'
                else:
                    backup_ok = self._safe_net_check(self.backup, timeout=0.3, default=False)
                    status = 'unstable' if backup_ok else 'disconnected'
            except Exception:
                status = 'disconnected'

            # 状态变化时发送日志和信号
            if status != last_status:
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

            # 断开状态心跳
            if status == 'disconnected':
                self.network_retry_count += 1
                if self.network_retry_count % 3 == 0:
                    self.log.emit(f"🔌 网络仍未恢复 (第{self.network_retry_count}次检测)")
            else:
                self.network_retry_count = 0

            # 发送统计心跳
            try:
                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
            except Exception:
                pass

            # 自适应间隔
            interval = 1 if status in ('unstable', 'disconnected') else max(1, int(self.network_check_interval))
            time.sleep(interval)

    def _safe_net_check(self, path: str, timeout: float = 1.5, default: bool = False) -> bool:
        """安全检查网络路径可达性
        
        优先使用 ping 检测网络路径（UNC/映射盘），避免 os.path.exists 阻塞。
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
                rc = WNetGetConnectionW(drive + '\\', buf, ctypes.byref(buf_len))
                if rc == 0:
                    unc_prefix = buf.value
                    rel = p[len(drive):].lstrip('\\/')
                    return os.path.join(unc_prefix, rel).replace('/', '\\')
                return ''
            except Exception:
                return ''

        def extract_host_from_unc(unc: str) -> str:
            try:
                parts = unc.split('\\')
                return parts[2] if len(parts) > 2 else ''
            except Exception:
                return ''

        def ping_host(host: str, ms: int) -> bool:
            try:
                import subprocess
                completed = subprocess.run(
                    ['ping', '-n', '1', '-w', str(ms), host],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(0.2, ms/1000.0 + 0.5)
                )
                return completed.returncode == 0
            except Exception:
                return False

        try:
            if not path:
                return bool(default)
            
            # UNC 路径：直接 ping
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
                future = self._net_executor.submit(os.path.exists, path)
                return bool(future.result(timeout=timeout))
            
            # 本地路径：直接检查
            future = self._net_executor.submit(os.path.exists, path)
            return bool(future.result(timeout=timeout))
        except Exception:
            return bool(default)

    def _safe_path_operation(self, func, *args, timeout: float = 3.0, default=None):
        """安全执行文件系统操作（带超时）"""
        try:
            future = self._executor.submit(func, *args)
            result = future.result(timeout=timeout)
            return result
        except FuturesTimeoutError:
            try:
                self.log.emit(f"⏱️ 文件操作超时（{timeout}秒），可能网络中断")
            except Exception:
                pass
            return default
        except Exception as e:
            try:
                self.log.emit(f"⚠️ 文件操作异常: {str(e)[:50]}")
            except Exception:
                pass
            return default

    def _check_network_connection(self) -> str:
        """检查网络连接状态"""
        if getattr(self, '_net_running', False):
            now = time.time()
            if now - self.last_network_check < self.network_check_interval:
                return self.current_network_status
            
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
        if now - self.last_network_check < self.network_check_interval:
            return self.current_network_status
        
        self.last_network_check = now
        
        try:
            target_ok = self._safe_path_operation(os.path.exists, self.target, timeout=2.0, default=False)
        except Exception:
            target_ok = False
        
        if target_ok:
            old_status = self.current_network_status
            self.current_network_status = 'good'
            self.network_retry_count = 0
            
            if old_status == 'disconnected':
                self.log.emit("✅ 网络已恢复正常")
                if self.network_auto_resume and self.network_pause_by_auto:
                    self.log.emit("🔄 网络恢复，自动继续上传...")
                    time.sleep(1)
                    self.network_pause_by_auto = False
                    self.resume()
            
            self.network_status.emit('good')
            return 'good'
        
        self.network_retry_count += 1
        
        try:
            backup_ok = self._safe_path_operation(os.path.exists, self.backup, timeout=2.0, default=False)
        except Exception:
            backup_ok = False
        
        if backup_ok:
            old_status = self.current_network_status
            self.current_network_status = 'unstable'
            
            if old_status != 'unstable':
                self.log.emit(f"⚠️ 网络不稳定：目标文件夹不可访问，备份文件夹正常")
            
            self.network_status.emit('unstable')
            return 'unstable'
        
        old_status = self.current_network_status
        self.current_network_status = 'disconnected'
        
        if old_status != 'disconnected':
            self.log.emit(f"❌ 网络连接中断（目标和备份文件夹均不可访问）")
            
            if self.network_auto_pause and not self._paused:
                self.log.emit("⏸️ 检测到网络中断，自动暂停上传...")
                self.network_pause_by_auto = True
                self.pause()
        else:
            if self.network_retry_count % 3 == 0:
                self.log.emit(f"🔌 网络仍未恢复 (第{self.network_retry_count}次检测)")
        
        self.network_status.emit('disconnected')
        return 'disconnected'

    def _handle_upload_failure(self, file_path: str) -> None:
        """处理上传失败（带重试调度）"""
        item = self.retry_queue.get(file_path)
        if item is None:
            item = {'count': 1, 'next': 0.0}
        else:
            item['count'] += 1
        
        retry_count = item['count']
        if retry_count > self.retry_count:
            self._log_failed_file(file_path, f"重试{retry_count-1}次后仍然失败")
            if file_path in self.retry_queue:
                del self.retry_queue[file_path]
            self.log.emit(f"❌ 文件上传失败，已记录到失败日志: {os.path.basename(file_path)}")
            return
        
        wait_times = [10, 30, 60]
        wait_time = wait_times[min(retry_count - 1, len(wait_times) - 1)]
        item['next'] = time.time() + wait_time
        self.retry_queue[file_path] = item
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
            
            if not os.path.exists(file_path):
                del self.retry_queue[file_path]
                continue
            
            retry_count = item.get('count', 1)
            next_at = item.get('next', 0.0)
            
            if now < next_at:
                continue
            
            self.log.emit(f"📤 开始重试上传 ({retry_count}/{self.retry_count}): {os.path.basename(file_path)}")
            rel = os.path.relpath(file_path, self.source)
            tgt = os.path.join(self.target, rel)
            bkp = os.path.join(self.backup, rel)
            
            try:
                tgt_exists = self._safe_path_operation(os.path.exists, tgt, timeout=2.0, default=False)
                if tgt_exists:
                    del self.retry_queue[file_path]
                    continue
                
                self._safe_path_operation(
                    lambda: os.makedirs(os.path.dirname(tgt), exist_ok=True),
                    timeout=3.0,
                    default=False
                )
                
                copy_success = self._safe_path_operation(
                    lambda: shutil.copy2(file_path, tgt) or True,
                    timeout=10.0,
                    default=False
                )
                
                if not copy_success:
                    raise Exception("文件复制超时")
                
                self.archive_queue.put((file_path, bkp))
                del self.retry_queue[file_path]
                self.uploaded += 1
                self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                self.log.emit(f"✓ 重试成功: {os.path.basename(file_path)}")
                
            except Exception as e:
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

    def _log_failed_file(self, file_path: str, reason: str) -> None:
        """记录失败文件到日志"""
        try:
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.failed_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {file_path} - {reason}\n")
        except Exception as e:
            self.log.emit(f"写入失败日志出错: {e}")

    def _copy_with_progress(self, src: str, dst: str, buffer_size: int = 1024 * 1024) -> None:
        """带进度和速率限制的文件复制"""
        last_write_time = time.time()
        write_timeout = 5.0
        
        if self.limit_upload_rate and self.max_upload_rate_bytes > 0:
            buffer_size = min(buffer_size, 64 * 1024)
        
        try:
            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                copied = 0
                
                while True:
                    if not self._running or self._paused:
                        break
                    
                    if time.time() - last_write_time > write_timeout:
                        self.log.emit(f"⏱️ 文件写入超时（{write_timeout}秒），可能网络已断开")
                        raise Exception("文件写入超时")
                    
                    chunk_start = time.time()
                    buf = fsrc.read(buffer_size)
                    if not buf:
                        break
                    
                    try:
                        fdst.write(buf)
                        last_write_time = time.time()
                    except Exception as e:
                        self.log.emit(f"⚠️ 文件写入失败: {str(e)[:50]}")
                        raise
                    
                    copied += len(buf)
                    
                    # 速率限制
                    if self.limit_upload_rate and self.max_upload_rate_bytes > 0:
                        expected_time = len(buf) / self.max_upload_rate_bytes
                        elapsed_time = time.time() - chunk_start
                        if elapsed_time < expected_time:
                            time.sleep(expected_time - elapsed_time)
                    
                    # 更新进度
                    if self.current_file_size > 0:
                        progress = int(100 * copied / self.current_file_size)
                        self.file_progress.emit(self.current_file_name, progress)
                        
                        if progress % 10 == 0 and progress > 0:
                            if self.limit_upload_rate:
                                self.log.emit(
                                    f"📊 上传进度: {progress}% "
                                    f"({copied/(1024*1024):.1f}MB/{self.current_file_size/(1024*1024):.1f}MB) "
                                    f"[限速: {self.max_upload_rate_bytes/(1024*1024):.1f}MB/s]"
                                )
                            else:
                                self.log.emit(
                                    f"📊 上传进度: {progress}% "
                                    f"({copied/(1024*1024):.1f}MB/{self.current_file_size/(1024*1024):.1f}MB)"
                                )
            
            shutil.copystat(src, dst)
            
        except Exception as e:
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except Exception:
                    pass
            raise e

    def _upload_file_by_protocol(self, src: str, dst: str) -> bool:
        """根据协议上传文件"""
        if self.upload_protocol == 'smb':
            return self._upload_via_smb(src, dst)
        elif self.upload_protocol == 'ftp_client':
            return self._upload_via_ftp(src, dst)
        elif self.upload_protocol == 'both':
            smb_ok = self._upload_via_smb(src, dst)
            ftp_ok = self._upload_via_ftp(src, dst)
            return smb_ok or ftp_ok
        else:
            self.log.emit(f"❌ 未知的上传协议: {self.upload_protocol}")
            return False

    def _upload_via_smb(self, src: str, dst: str) -> bool:
        """通过 SMB 上传文件"""
        try:
            if self.current_file_size > 10 * 1024 * 1024:
                self._copy_with_progress(src, dst)
            else:
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
        """通过 FTP 上传文件"""
        try:
            if not FTP_AVAILABLE or FTPClientUploader is None:
                self.log.emit("❌ FTP 功能不可用")
                return False
            
            if not self.ftp_client and self.ftp_client_config:
                self.ftp_client = FTPClientUploader(self.ftp_client_config)
                if not self.ftp_client.connect():
                    host = self.ftp_client_config.get('host', 'unknown')
                    port = self.ftp_client_config.get('port', 21)
                    self.log.emit(f"❌ [FTP-CONN] 无法连接到 {host}:{port}")
                    self.ftp_client = None
                    return False
            
            if not self.ftp_client:
                self.log.emit("❌ [FTP-INIT] FTP客户端未初始化")
                return False
            
            rel_path = os.path.relpath(dst, self.target)
            remote_path = self.ftp_client_config.get('remote_path', '/upload')
            remote_file = f"{remote_path}/{rel_path}".replace('\\', '/')
            
            success = self.ftp_client.upload_file(Path(src), remote_file)
            if success:
                self.log.emit(f"✓ FTP上传成功: {os.path.basename(remote_file)}")
                return True
            else:
                self.log.emit(f"❌ [FTP-UPLOAD] 上传失败: {os.path.basename(remote_file)}")
                return False
                
        except Exception as e:
            error_type = type(e).__name__
            self.log.emit(f"❌ [FTP-ERROR] {error_type}: {e}")
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

    def _find_duplicate_by_hash(self, file_hash: str, target_dir: str) -> str:
        """在目标文件夹中查找重复文件"""
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
        """生成唯一文件名"""
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
            if counter > 9999:
                return base_path

    def _archive_worker(self) -> None:
        """归档 Worker（独立线程）"""
        while self._running:
            try:
                item = self.archive_queue.get(timeout=1)
                src_path, bkp_path = item
                
                if not os.path.exists(src_path):
                    continue
                
                if self.enable_backup and self.backup and os.path.exists(os.path.dirname(self.backup)):
                    os.makedirs(os.path.dirname(bkp_path), exist_ok=True)
                    shutil.move(src_path, bkp_path)
                    self.log.emit(f"📦 已归档: {os.path.basename(bkp_path)}")
                else:
                    os.remove(src_path)
                    self.log.emit(f"🗑️ 已删除: {os.path.basename(src_path)}")
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.log.emit(f"归档失败: {e}")

    def _disk_ok(self, path: str) -> Tuple[float, float, float]:
        """检查磁盘空间"""
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
        
        result = self._safe_path_operation(check, timeout=2.0, default=(0.0, 0.0, 0.0))
        return result if result is not None else (0.0, 0.0, 0.0)

    def _get_image_files(self) -> List[str]:
        """扫描图片文件"""
        def scan():
            if not os.path.exists(self.source):
                return []
            files = []
            for root, _, names in os.walk(self.source):
                if not self._running:
                    break
                for n in names:
                    ext = os.path.splitext(n)[1].lower()
                    if ext in self.filters:
                        files.append(os.path.join(root, n))
            return files
        
        result = self._safe_path_operation(scan, timeout=5.0, default=[])
        return result if result is not None else []

    def _run(self) -> None:
        """主运行循环"""
        self.log.emit("🚀 开始图片上传服务（上传与归档已分离）")
        self.start_time = time.time()
        
        # 启动归档线程
        self._archive_thread = threading.Thread(target=self._archive_worker, daemon=True)
        self._archive_thread.start()
        self.log.emit("📦 归档线程已启动")
        
        # 重置统计
        self.uploaded = 0
        self.failed = 0
        self.skipped = 0
        self.retry_queue.clear()
        
        try:
            while self._running:
                # 暂停处理
                pause_log_counter = 0
                while self._paused and self._running:
                    time.sleep(0.2)
                    pause_log_counter += 1
                    if pause_log_counter >= 50:
                        pause_log_counter = 0
                        self.log.emit("⏸️ 上传已暂停，等待恢复...")
                
                if not self._running:
                    break

                # 网络检查
                try:
                    network_status = self._check_network_connection()
                except Exception as e:
                    self.log.emit(f"⚠️ 网络检测异常: {str(e)[:50]}")
                    network_status = 'disconnected'
                
                if network_status == 'disconnected' and self._paused:
                    self.log.emit("🔌 等待网络恢复中...")
                    time.sleep(1)
                    continue

                # 磁盘空间检查
                tf_ok, _, _ = self._disk_ok(self.target)
                bf_ok, _, _ = self._disk_ok(self.backup)
                
                if tf_ok < self.disk_threshold_percent or bf_ok < self.disk_threshold_percent:
                    now = time.time()
                    if now - self._last_space_warn > 10:
                        self._last_space_warn = now
                        self.log.emit(
                            f"⚠ 磁盘空间不足！目标:{tf_ok:.0f}%，"
                            f"备份:{bf_ok:.0f}%（阈值:{self.disk_threshold_percent}%）"
                        )
                        self.disk_warning.emit(tf_ok, bf_ok, self.disk_threshold_percent)
                    time.sleep(2)
                    continue

                # 处理重试队列
                self._process_retry_queue()

                # 扫描文件
                images = self._get_image_files()
                self.total_files = len(images)
                self.current = 0
                self.progress.emit(self.current, self.total_files, "")

                # 处理每个文件
                for path in images:
                    if not self._running:
                        break
                    
                    while self._paused and self._running:
                        time.sleep(0.2)
                    
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
                    
                    # 创建目标目录
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
                        # 检查文件是否已存在
                        tgt_exists = self._safe_path_operation(
                            os.path.exists, tgt, timeout=2.0, default=False
                        )
                        
                        if tgt_exists and not self.enable_deduplication:
                            self.log.emit(f"⏭ 文件已存在，跳过: {fname}")
                            self.skipped += 1
                            self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                            self.file_progress.emit(fname, 100)
                        else:
                            # 获取文件大小
                            try:
                                self.current_file_size = os.path.getsize(path)
                            except Exception:
                                self.current_file_size = 0
                            
                            self.file_progress.emit(fname, 0)
                            
                            # 去重逻辑（简化版，完整逻辑见 pyqt_app.py）
                            should_upload = True
                            final_target = tgt
                            
                            # 执行上传
                            if should_upload:
                                def create_dir():
                                    os.makedirs(os.path.dirname(final_target), exist_ok=True)
                                
                                dir_created = self._safe_path_operation(
                                    create_dir, timeout=3.0, default=False
                                )
                                
                                if dir_created is False:
                                    raise Exception("创建目标目录超时，网络可能已断开")
                                
                                upload_success = self._upload_file_by_protocol(path, final_target)
                                
                                if not upload_success:
                                    raise Exception("文件上传失败")
                                
                                self.uploaded += 1
                                
                                # 计算速率
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
                                self.archive_queue.put((path, bkp))
                            else:
                                self.file_progress.emit(fname, 100)
                                
                    except Exception as e:
                        self.failed += 1
                        self.stats.emit(self.uploaded, self.failed, self.skipped, self.rate)
                        self.log.emit(f"✗ 上传失败 {fname}: {e}")
                        self.upload_error.emit(fname, str(e))
                        self._handle_upload_failure(path)

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
