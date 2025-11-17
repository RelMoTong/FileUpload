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
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    Signal = QtCore.Signal  # PySide6 信号
    QT_LIB = 'PySide6'
except ImportError:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore
        Signal = QtCore.pyqtSignal  # PyQt5 信号
        QT_LIB = 'PyQt5'
    except ImportError:
        raise ImportError("Neither PySide6 nor PyQt5 is installed. Please install one of them.")

# 统一访问 Qt 枚举（兼容 Qt6 的强类型枚举命名）
QtEnum = QtCore.Qt

APP_TITLE = "图片异步上传工具 (PyQt)"


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


class Toast(QtWidgets.QWidget):  # type: ignore
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


class UploadWorker(QtCore.QObject):  # type: ignore
    # signals
    log = Signal(str)
    stats = Signal(int, int, int, str)   # uploaded, failed, skipped, rate
    progress = Signal(int, int, str)     # current, total, filename
    file_progress = Signal(str, int)     # current_file, progress_percent
    network_status = Signal(str)         # 'good'|'unstable'|'disconnected'
    finished = Signal()
    status = Signal(str)                 # 'running'|'paused'|'stopped'
    ask_user_duplicate = Signal(object)  # payload dict: {'file': str, 'duplicate': str, 'event': threading.Event, 'result': dict}

    def __init__(self, source: str, target: str, backup: str,
                 interval: int, mode: str, disk_threshold_percent: int, retry_count: int,
                 filters: List[str], app_dir: Path,
                 enable_deduplication: bool = False, hash_algorithm: str = 'md5',
                 duplicate_strategy: str = 'ask',
                 network_check_interval: int = 10, network_auto_pause: bool = True,
                 network_auto_resume: bool = True,
                 enable_auto_delete: bool = False, auto_delete_folder: str = '',
                 auto_delete_threshold: int = 80, auto_delete_keep_days: int = 10,
                 auto_delete_check_interval: int = 300):
        super().__init__()
        self.source = source
        self.target = target
        self.backup = backup
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
        """带进度的文件复制（适用于大文件），包含超时检测"""
        last_write_time = time.time()
        write_timeout = 5.0  # 5秒内没有写入视为超时
        
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
                        
                        # 更新进度（每复制1MB更新一次）
                        if self.current_file_size > 0:
                            progress = int(100 * copied / self.current_file_size)
                            self.file_progress.emit(self.current_file_name, progress)
                            # 每10%输出日志
                            if progress % 10 == 0 and progress > 0:
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
        v1.9.1 修改：根据 enable_backup 配置决定是归档还是删除
        """
        while self._running:
            try:
                # 1秒超时，避免死等
                item = self.archive_queue.get(timeout=1)
                src_path, bkp_path = item
                
                if not os.path.exists(src_path):
                    continue
                
                # v1.9.1 新增：根据配置选择归档或删除
                if self.backup and os.path.exists(os.path.dirname(self.backup)):
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
                                
                                # 对于大文件，显示上传进度
                                if self.current_file_size > 10 * 1024 * 1024:  # 大于10MB显示进度
                                    self._copy_with_progress(path, final_target)
                                else:
                                    # 小文件也使用超时保护
                                    def copy_file():
                                        shutil.copy2(path, final_target)
                                        return True
                                    
                                    copy_success = self._safe_path_operation(copy_file, timeout=10.0, default=False)
                                    if not copy_success:
                                        raise Exception("文件复制超时，网络可能已断开")
                                
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


class MainWindow(QtWidgets.QMainWindow):  # type: ignore
    # 内部信号用于线程安全的UI更新
    _disk_update_signal = Signal(str, float)  # disk_type, free_percent
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1160, 760)
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
        self.enable_backup = True  # v1.9.1 新增：是否启用备份
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
        
        # 日志文件路径（每天一个日志文件）
        self.log_file_path = None
        self._init_log_file()
        
        # 确保必要的目录存在
        self._ensure_directories()
        
        # 日志写入线程池（避免阻塞主线程）
        self._log_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LogWriter")
        
        # UI
        self._build_ui()
        self._load_config()
        self._apply_theme()
        self._update_ui_permissions()
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
        
        # 创建内容容器
        central = QtWidgets.QWidget()
        central.setMinimumWidth(1100)  # 设置最小宽度防止内容压缩
        scroll_area.setWidget(central)
        
        root = QtWidgets.QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(12, 12, 12, 12)

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
        ver = QtWidgets.QLabel("v2 (PyQt)")
        header.addWidget(title)
        header.addWidget(ver)
        header.addStretch(1)
        self.role_label = QtWidgets.QLabel("🔒 未登录")
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        header.addWidget(self.role_label)
        root.addLayout(header)

        # center three columns
        center = QtWidgets.QHBoxLayout()
        root.addLayout(center, 1)

        left = QtWidgets.QVBoxLayout()
        middle = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        center.addLayout(left, 1)
        center.addLayout(middle, 1)
        center.addLayout(right, 1)

        # left cards
        left.addWidget(self._folder_card())
        left.addWidget(self._settings_card(), 1)

        # middle cards
        middle.addWidget(self._control_card())
        middle.addWidget(self._status_card(), 1)

        # right - log card
        right.addWidget(self._log_card(), 1)

    def _card(self, title_text: str) -> Tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setObjectName("Card")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
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
        
        # v1.9.1 新增：启用备份复选框
        self.cb_enable_backup = QtWidgets.QCheckBox("✅ 启用备份功能")
        self.cb_enable_backup.setProperty('orig_text', "✅ 启用备份功能")
        self.cb_enable_backup.setChecked(True)
        self.cb_enable_backup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_enable_backup, checked))
        self.cb_enable_backup.toggled.connect(self._on_backup_toggled)
        self._set_checkbox_mark(self.cb_enable_backup, self.cb_enable_backup.isChecked())
        v.addWidget(self.cb_enable_backup)
        
        # 添加说明文本
        backup_hint = QtWidgets.QLabel("💡 启用后，上传成功的文件会移动到备份文件夹保存；禁用后文件上传成功会直接删除")
        backup_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px 0px;")
        backup_hint.setWordWrap(True)
        v.addWidget(backup_hint)
        
        # backup
        self.bak_edit, self.btn_choose_bak = self._path_row(v, "备份文件夹", self._choose_backup)
        return card

    def _path_row(self, layout: QtWidgets.QVBoxLayout, label: str, chooser):
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel(label + ":")
        edit = QtWidgets.QLineEdit()
        btn = QtWidgets.QPushButton("浏览")
        btn.setProperty("class", "Secondary")
        btn.clicked.connect(chooser)
        row.addWidget(lab)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        layout.addLayout(row)
        return edit, btn

    def _settings_card(self) -> QtWidgets.QFrame:
        card, v = self._card("⚙️ 上传设置")
        # interval
        self.spin_interval = self._spin_row(v, "间隔时间(秒)", 10, 3600, 30)
        self.spin_disk = self._spin_row(v, "磁盘阈值(%)", 5, 50, 10)
        self.spin_retry = self._spin_row(v, "失败重试次数", 0, 10, 3)
        self.spin_disk_check = self._spin_row(v, "磁盘检查间隔(秒)", 1, 60, 5)
        # 绑定磁盘检查间隔变化事件
        self.spin_disk_check.valueChanged.connect(lambda val: setattr(self, 'disk_check_interval', val))
        # filters
        filt_lab = QtWidgets.QLabel("文件类型限制")
        v.addWidget(filt_lab)
        grid = QtWidgets.QGridLayout()
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
        v.addLayout(grid)
        
        # 高级选项 - 使用可折叠组件
        v.addWidget(self._hline())
        self.advanced_collapsible = MainWindow.CollapsibleBox("⚙️ 高级选项")
        adv_content = QtWidgets.QWidget()
        adv_layout = QtWidgets.QVBoxLayout(adv_content)
        adv_layout.setContentsMargins(10, 5, 10, 5)
        adv_layout.setSpacing(8)
        
        self.cb_auto_start_windows = QtWidgets.QCheckBox("🚀 开机自启动")
        self.cb_auto_start_windows.setProperty('orig_text', "🚀 开机自启动")
        self.cb_auto_start_windows.setChecked(False)
        self.cb_auto_start_windows.toggled.connect(self._toggle_autostart)
        self.cb_auto_start_windows.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_start_windows, checked))
        self._set_checkbox_mark(self.cb_auto_start_windows, self.cb_auto_start_windows.isChecked())
        adv_layout.addWidget(self.cb_auto_start_windows)
        
        self.cb_auto_run_on_startup = QtWidgets.QCheckBox("▶ 启动时自动运行")
        self.cb_auto_run_on_startup.setProperty('orig_text', "▶ 启动时自动运行")
        self.cb_auto_run_on_startup.setChecked(False)
        self.cb_auto_run_on_startup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_auto_run_on_startup, checked))
        self._set_checkbox_mark(self.cb_auto_run_on_startup, self.cb_auto_run_on_startup.isChecked())
        adv_layout.addWidget(self.cb_auto_run_on_startup)
        
        self.advanced_collapsible.setContentLayout(adv_layout)
        v.addWidget(self.advanced_collapsible)
        
        # v1.9 新增：智能去重选项 - 使用可折叠组件
        v.addWidget(self._hline())
        self.dedup_collapsible = MainWindow.CollapsibleBox("🔍 智能去重 (v1.9)")
        dedup_content = QtWidgets.QWidget()
        dedup_layout = QtWidgets.QVBoxLayout(dedup_content)
        dedup_layout.setContentsMargins(10, 5, 10, 5)
        dedup_layout.setSpacing(8)
        
        self.cb_enable_dedup = QtWidgets.QCheckBox("🔍 启用智能去重")
        self.cb_enable_dedup.setProperty('orig_text', "🔍 启用智能去重")
        self.cb_enable_dedup.setChecked(False)
        self.cb_enable_dedup.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_enable_dedup, checked))
        self.cb_enable_dedup.toggled.connect(self._on_dedup_toggled)
        self._set_checkbox_mark(self.cb_enable_dedup, self.cb_enable_dedup.isChecked())
        dedup_layout.addWidget(self.cb_enable_dedup)
        
        # 哈希算法选择
        hash_row = QtWidgets.QHBoxLayout()
        hash_lab = QtWidgets.QLabel("哈希算法:")
        self.combo_hash = QtWidgets.QComboBox()
        self.combo_hash.addItems(["MD5", "SHA256"])
        self.combo_hash.setEnabled(False)
        hash_row.addWidget(hash_lab)
        hash_row.addWidget(self.combo_hash)
        dedup_layout.addLayout(hash_row)
        
        # 去重策略选择
        strategy_row = QtWidgets.QHBoxLayout()
        strategy_lab = QtWidgets.QLabel("重复策略:")
        self.combo_strategy = QtWidgets.QComboBox()
        self.combo_strategy.addItems(["跳过", "重命名", "覆盖", "询问"])
        self.combo_strategy.setEnabled(False)
        strategy_row.addWidget(strategy_lab)
        strategy_row.addWidget(self.combo_strategy)
        dedup_layout.addLayout(strategy_row)
        
        # 说明文本
        dedup_hint = QtWidgets.QLabel("💡 通过文件哈希检测重复，避免上传相同内容的文件")
        dedup_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        dedup_hint.setWordWrap(True)
        dedup_layout.addWidget(dedup_hint)
        
        self.dedup_collapsible.setContentLayout(dedup_layout)
        v.addWidget(self.dedup_collapsible)
        
        # v1.9 新增：网络监控选项 - 使用可折叠组件
        v.addWidget(self._hline())
        self.network_collapsible = MainWindow.CollapsibleBox("🌐 网络监控 (v1.9)")
        network_content = QtWidgets.QWidget()
        network_layout = QtWidgets.QVBoxLayout(network_content)
        network_layout.setContentsMargins(10, 5, 10, 5)
        network_layout.setSpacing(8)
        
        # 网络检测间隔
        self.spin_network_check = self._spin_row(network_layout, "检测间隔(秒)", 5, 60, 10)
        
        self.cb_network_auto_pause = QtWidgets.QCheckBox("⏸️ 断网时自动暂停")
        self.cb_network_auto_pause.setProperty('orig_text', "⏸️ 断网时自动暂停")
        self.cb_network_auto_pause.setChecked(True)
        self.cb_network_auto_pause.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_pause, checked))
        self._set_checkbox_mark(self.cb_network_auto_pause, self.cb_network_auto_pause.isChecked())
        network_layout.addWidget(self.cb_network_auto_pause)
        
        self.cb_network_auto_resume = QtWidgets.QCheckBox("▶️ 恢复时自动继续")
        self.cb_network_auto_resume.setProperty('orig_text', "▶️ 恢复时自动继续")
        self.cb_network_auto_resume.setChecked(True)
        self.cb_network_auto_resume.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_network_auto_resume, checked))
        self._set_checkbox_mark(self.cb_network_auto_resume, self.cb_network_auto_resume.isChecked())
        network_layout.addWidget(self.cb_network_auto_resume)
        
        # 说明文本
        network_hint = QtWidgets.QLabel("💡 实时监控网络状态，断网时自动暂停，恢复后自动继续")
        network_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
        network_hint.setWordWrap(True)
        network_layout.addWidget(network_hint)
        
        self.network_collapsible.setContentLayout(network_layout)
        v.addWidget(self.network_collapsible)
        
        # v1.9 新增：自动删除选项 - 使用可折叠组件
        v.addWidget(self._hline())
        self.autodel_collapsible = MainWindow.CollapsibleBox("🗑️ 自动删除 (v1.9)")
        autodel_content = QtWidgets.QWidget()
        autodel_layout = QtWidgets.QVBoxLayout(autodel_content)
        autodel_layout.setContentsMargins(10, 5, 10, 5)
        autodel_layout.setSpacing(8)
        
        self.cb_enable_auto_delete = QtWidgets.QCheckBox("🗑️ 启用自动删除")
        self.cb_enable_auto_delete.setProperty('orig_text', "🗑️ 启用自动删除")
        self.cb_enable_auto_delete.setChecked(False)
        self.cb_enable_auto_delete.toggled.connect(lambda checked: self._set_checkbox_mark(self.cb_enable_auto_delete, checked))
        self.cb_enable_auto_delete.toggled.connect(self._on_auto_delete_toggled)
        self._set_checkbox_mark(self.cb_enable_auto_delete, self.cb_enable_auto_delete.isChecked())
        autodel_layout.addWidget(self.cb_enable_auto_delete)
        
        # 监控文件夹
        self.auto_del_folder_edit, self.btn_choose_auto_del = self._path_row(autodel_layout, "监控文件夹", self._choose_auto_delete_folder)
        self.auto_del_folder_edit.setEnabled(False)
        self.btn_choose_auto_del.setEnabled(False)
        
        # 磁盘阈值
        self.spin_auto_del_threshold = self._spin_row(autodel_layout, "空间阈值(%)", 50, 95, 80)
        self.spin_auto_del_threshold.setEnabled(False)
        
        # 保留天数
        self.spin_auto_del_keep_days = self._spin_row(autodel_layout, "保留天数", 1, 365, 10)
        self.spin_auto_del_keep_days.setEnabled(False)
        
        # 检查间隔
        self.spin_auto_del_interval = self._spin_row(autodel_layout, "检查间隔(秒)", 60, 3600, 300)
        self.spin_auto_del_interval.setEnabled(False)
        
        # 说明文本
        auto_del_hint = QtWidgets.QLabel("💡 当磁盘使用率达到阈值时，自动删除超出保留期限的图片文件，释放空间")
        auto_del_hint.setStyleSheet("color:#757575; font-size:9px; padding:8px;")
        auto_del_hint.setWordWrap(True)
        autodel_layout.addWidget(auto_del_hint)
        
        # 警告提示
        auto_del_warning = QtWidgets.QLabel("⚠️ 警告：被删除的文件无法恢复，请谨慎设置保留天数！")
        auto_del_warning.setStyleSheet("color:#D32F2F; font-size:9px; padding:8px; font-weight:700;")
        auto_del_warning.setWordWrap(True)
        autodel_layout.addWidget(auto_del_warning)
        
        self.autodel_collapsible.setContentLayout(autodel_layout)
        v.addWidget(self.autodel_collapsible)
        
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
        # primary start
        self.btn_start = QtWidgets.QPushButton("▶ 开始上传")
        self.btn_start.setProperty("class", "Primary")
        self.btn_start.clicked.connect(self._on_start)
        v.addWidget(self.btn_start)
        # secondary pause/stop
        row = QtWidgets.QHBoxLayout()
        self.btn_pause = QtWidgets.QPushButton("⏸ 暂停上传")
        self.btn_pause.setProperty("class", "Warning")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_resume)
        self.btn_stop = QtWidgets.QPushButton("⏹ 停止上传")
        self.btn_stop.setProperty("class", "Danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        row.addWidget(self.btn_pause)
        row.addWidget(self.btn_stop)
        v.addLayout(row)
        # separator
        v.addWidget(self._hline())
        # save + more
        row2 = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("💾 保存配置")
        self.btn_save.setProperty("class", "Secondary")
        self.btn_save.clicked.connect(self._save_config)
        self.btn_more = QtWidgets.QToolButton()
        self.btn_more.setText("更多 ▾")
        popup_enum = getattr(QtWidgets.QToolButton, 'ToolButtonPopupMode', QtWidgets.QToolButton)
        self.btn_more.setPopupMode(getattr(popup_enum, 'InstantPopup'))
        menu = QtWidgets.QMenu(self)
        act_clear = menu.addAction("🗑️ 清空日志")
        act_clear.triggered.connect(self._clear_logs)
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
        return card

    def _logout(self):
        """退出登录"""
        self.current_role = 'guest'
        self.role_label.setText("🔒 未登录")
        self.role_label.setStyleSheet("background:#FFF3E0; color:#E67E22; padding:6px 12px; border-radius:6px; font-weight:700;")
        self._update_ui_permissions()
        self._toast('已退出登录', 'info')

    def _update_ui_permissions(self):
        """根据当前角色更新UI控件的启用状态
        v1.9.1 优化：未登录时只能开始/停止上传，所有配置功能禁用
        """
        self._append_log(f"🔐 更新权限: 当前角色={self.current_role}, 运行状态={'运行中' if self.is_running else '已停止'}")
        
        # 权限定义
        is_guest = self.current_role == 'guest'
        is_user_or_admin = self.current_role in ['user', 'admin']
        is_admin = self.current_role == 'admin'
        
        # === 核心逻辑：未登录时只能开始/停止上传 ===
        
        # 1. 文件夹选择按钮：仅登录用户可用
        if hasattr(self, 'btn_choose_src'):
            self.btn_choose_src.setEnabled(is_user_or_admin and not self.is_running)
        if hasattr(self, 'btn_choose_tgt'):
            self.btn_choose_tgt.setEnabled(is_user_or_admin and not self.is_running)
        if hasattr(self, 'btn_choose_bak'):
            self.btn_choose_bak.setEnabled(is_user_or_admin and not self.is_running)
        
        # 2. 文件夹路径输入框：未登录时全部只读
        self.src_edit.setReadOnly(is_guest or self.is_running)
        self.tgt_edit.setReadOnly(is_guest or self.is_running)
        self.bak_edit.setReadOnly(is_guest or self.is_running)
        
        # 3. 备份启用复选框：仅登录用户可用
        if hasattr(self, 'cb_enable_backup'):
            self.cb_enable_backup.setEnabled(is_user_or_admin and not self.is_running)
        
        # 4. 所有设置项：未登录时禁用
        self.spin_interval.setEnabled(is_user_or_admin and not self.is_running)
        self.spin_disk.setEnabled(is_user_or_admin and not self.is_running)
        self.spin_retry.setEnabled(is_user_or_admin and not self.is_running)
        self.spin_disk_check.setEnabled(is_user_or_admin and not self.is_running)
        
        # 5. 文件类型复选框：未登录时禁用
        for cb in self.cb_ext.values():
            cb.setEnabled(is_user_or_admin and not self.is_running)
        
        # 6. 高级选项：仅登录用户可用
        if hasattr(self, 'cb_auto_start_windows'):
            self.cb_auto_start_windows.setEnabled(is_user_or_admin and not self.is_running)
        if hasattr(self, 'cb_auto_run_on_startup'):
            self.cb_auto_run_on_startup.setEnabled(is_user_or_admin and not self.is_running)
        
        # 7. 智能去重：仅登录用户可用
        if hasattr(self, 'cb_enable_dedup'):
            self.cb_enable_dedup.setEnabled(is_user_or_admin and not self.is_running)
            self.combo_hash.setEnabled(is_user_or_admin and not self.is_running and self.cb_enable_dedup.isChecked())
            self.combo_strategy.setEnabled(is_user_or_admin and not self.is_running and self.cb_enable_dedup.isChecked())
        
        # 8. 网络监控：仅登录用户可用
        if hasattr(self, 'spin_network_check'):
            self.spin_network_check.setEnabled(is_user_or_admin and not self.is_running)
        if hasattr(self, 'cb_network_auto_pause'):
            self.cb_network_auto_pause.setEnabled(is_user_or_admin and not self.is_running)
        if hasattr(self, 'cb_network_auto_resume'):
            self.cb_network_auto_resume.setEnabled(is_user_or_admin and not self.is_running)
        
        # 9. 自动删除：仅登录用户可用
        if hasattr(self, 'cb_enable_auto_delete'):
            self.cb_enable_auto_delete.setEnabled(is_user_or_admin and not self.is_running)
            auto_del_enabled = is_user_or_admin and not self.is_running and self.cb_enable_auto_delete.isChecked()
            if hasattr(self, 'auto_del_folder_edit'):
                self.auto_del_folder_edit.setEnabled(auto_del_enabled)
            if hasattr(self, 'btn_choose_auto_del'):
                self.btn_choose_auto_del.setEnabled(auto_del_enabled)
            if hasattr(self, 'spin_auto_del_threshold'):
                self.spin_auto_del_threshold.setEnabled(auto_del_enabled)
            if hasattr(self, 'spin_auto_del_keep_days'):
                self.spin_auto_del_keep_days.setEnabled(auto_del_enabled)
            if hasattr(self, 'spin_auto_del_interval'):
                self.spin_auto_del_interval.setEnabled(auto_del_enabled)
        
        # 10. 保存配置按钮：仅登录用户可用
        self.btn_save.setEnabled(is_user_or_admin and not self.is_running)
        
        # 11. 上传控制按钮：所有人都可以使用（核心功能）
        self.btn_start.setEnabled(not self.is_running)
        self.btn_pause.setEnabled(self.is_running)
        self.btn_stop.setEnabled(self.is_running)
        
        # 12. 可折叠组件：控制展开状态
        # 未登录时可以查看但不能修改
        if hasattr(self, 'advanced_collapsible'):
            # 不禁用整个组件，只禁用内部控件
            pass

    def _clear_logs(self):
        try:
            self.log.clear()
            self._toast('已清空日志', 'info')
        except Exception:
            pass

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

    def _on_auto_delete_toggled(self, checked: bool):
        """切换自动删除开关"""
        self.enable_auto_delete = checked
        # 启用/禁用子选项
        self.auto_del_folder_edit.setEnabled(checked)
        self.btn_choose_auto_del.setEnabled(checked)
        self.spin_auto_del_threshold.setEnabled(checked)
        self.spin_auto_del_keep_days.setEnabled(checked)
        self.spin_auto_del_interval.setEnabled(checked)
        
        if checked:
            self._append_log("🗑️ 已启用自动删除")
        else:
            self._append_log("⚪ 已禁用自动删除")
    
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
        self.lbl_status.setStyleSheet("background:#FEE2E2; color:#B91C1C; padding:4px 10px; font-weight:700; border-radius:12px;")
        v.addWidget(self.lbl_status)
        # chips
        grid = QtWidgets.QGridLayout()
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
        for i, w in enumerate([self.lbl_uploaded, self.lbl_failed, self.lbl_skipped, 
                               self.lbl_rate, self.lbl_queue, self.lbl_time,
                               self.lbl_target_disk, self.lbl_backup_disk, self.lbl_network]):
            grid.addWidget(w, i//3, i%3)
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

    class CollapsibleBox(QtWidgets.QWidget):  # type: ignore
        """可折叠的组件"""
        def __init__(self, title: str = "", parent: QtWidgets.QWidget = None):
            super().__init__(parent)
            self.toggle_button = QtWidgets.QToolButton()
            self.toggle_button.setStyleSheet(
                "QToolButton { border: none; font-weight: 700; color: #1976D2; }"
            )
            self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            arrow_type = getattr(QtCore.Qt, 'ArrowType', QtCore.Qt)
            self.toggle_button.setArrowType(getattr(arrow_type, 'RightArrow'))
            self.toggle_button.setText(title)
            self.toggle_button.setCheckable(True)
            self.toggle_button.setChecked(False)
            
            self.content_area = QtWidgets.QWidget()
            self.content_area.setMaximumHeight(0)
            self.content_area.setMinimumHeight(0)
            
            lay = QtWidgets.QVBoxLayout(self)
            lay.setSpacing(0)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.toggle_button)
            lay.addWidget(self.content_area)
            
            self.toggle_button.clicked.connect(self.toggle)
        
        def toggle(self):
            checked = self.toggle_button.isChecked()
            arrow_type = getattr(QtCore.Qt, 'ArrowType', QtCore.Qt)
            self.toggle_button.setArrowType(
                getattr(arrow_type, 'DownArrow') if checked else getattr(arrow_type, 'RightArrow')
            )
            if checked:
                self.content_area.setMaximumHeight(16777215)
            else:
                self.content_area.setMaximumHeight(0)
        
        def setContentLayout(self, layout: QtWidgets.QVBoxLayout):
            lay = self.content_area.layout()
            if lay is not None:
                QtWidgets.QWidget().setLayout(lay)
            self.content_area.setLayout(layout)

    class ChipWidget(QtWidgets.QFrame):  # type: ignore
        value_label: QtWidgets.QLabel
        def __init__(self, title: str, val: str, bg: str, fg: str, parent: QtWidgets.QWidget = None):
            super().__init__(parent)
            self.setStyleSheet(f"QFrame{{background:{bg}; border-radius:8px;}} QLabel{{color:{fg};}}")
            vv = QtWidgets.QVBoxLayout(self)
            t = QtWidgets.QLabel(title)
            t.setStyleSheet("font-size:9pt; padding-top:6px;")
            self.value_label = QtWidgets.QLabel(val)
            self.value_label.setStyleSheet("font-weight:700; font-size:11pt; padding-bottom:6px;")
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
        # log area
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log)
        return card

    # actions
    def _on_backup_toggled(self, checked: bool):
        """切换备份开关"""
        self.enable_backup = checked
        # 启用/禁用备份路径输入和浏览按钮
        self.bak_edit.setEnabled(checked)
        self.btn_choose_bak.setEnabled(checked)
        
        if checked:
            self._append_log("✅ 已启用备份功能")
        else:
            self._append_log("⚪ 已禁用备份功能（上传成功后将删除源文件）")
        
        self._mark_config_modified()
    
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

    def _choose_auto_delete_folder(self):
        """选择自动删除监控文件夹"""
        current = self.auto_del_folder_edit.text()
        start_dir = current if current and os.path.exists(current) else ""
        
        self._append_log("📂 正在选择自动删除监控文件夹...")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择监控文件夹", start_dir)
        if d:
            self._append_log(f"✓ 已选择监控文件夹: {d}")
            self.auto_del_folder_edit.setText(d)
            self._mark_config_modified()
        else:
            self._append_log("✗ 取消选择监控文件夹")

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
        
        # v1.9.1 修改：只有启用备份时才验证备份路径
        if self.enable_backup:
            if not bak:
                errors.append("备份文件夹路径为空")
            elif not os.path.exists(bak):
                errors.append(f"备份文件夹不存在: {bak}")
            else:
                self._append_log(f"✓ 备份文件夹路径有效: {bak}")
        else:
            self._append_log("⚪ 备份功能已禁用，跳过备份路径验证")
        
        # 额外校验：三个路径必须互不相同，避免用户误填相同路径导致循环或数据覆盖
        try:
            def _norm(p: str) -> str:
                return os.path.normcase(os.path.abspath(p)) if p else ''

            n_src = _norm(src)
            n_tgt = _norm(tgt)
            n_bak = _norm(bak)

            if n_src and n_tgt and n_src == n_tgt:
                errors.append("源文件夹与目标文件夹路径相同，请选择不同的路径")
            # v1.9.1 修改：只有启用备份时才检查备份路径相同性
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

    def _save_config(self):
        """保存配置到文件"""
        self._append_log("💾 正在保存配置...")
        
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
            'enable_backup': self.cb_enable_backup.isChecked(),  # v1.9.1 新增
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
            # v1.9 新增：去重
            'enable_deduplication': self.cb_enable_dedup.isChecked(),
            'hash_algorithm': self.combo_hash.currentText().lower(),
            'duplicate_strategy': strategy_map.get(self.combo_strategy.currentText(), 'ask'),
            # v1.9 新增：网络监控
            'network_check_interval': self.spin_network_check.value(),
            'network_auto_pause': self.cb_network_auto_pause.isChecked(),
            'network_auto_resume': self.cb_network_auto_resume.isChecked(),
            # v1.9 新增：自动删除
            'enable_auto_delete': self.cb_enable_auto_delete.isChecked(),
            'auto_delete_folder': self.auto_del_folder_edit.text(),
            'auto_delete_threshold': self.spin_auto_del_threshold.value(),
            'auto_delete_keep_days': self.spin_auto_del_keep_days.value(),
            'auto_delete_check_interval': self.spin_auto_del_interval.value(),
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
            
            # v1.9.1 新增：加载备份启用状态
            self.enable_backup = cfg.get('enable_backup', True)
            self.cb_enable_backup.blockSignals(True)
            self.cb_enable_backup.setChecked(self.enable_backup)
            self.cb_enable_backup.blockSignals(False)
            # 设置备份路径输入框启用状态
            self.bak_edit.setEnabled(self.enable_backup)
            self.btn_choose_bak.setEnabled(self.enable_backup)
            
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
            
            # v1.9 新增：加载去重配置
            self.enable_deduplication = cfg.get('enable_deduplication', False)
            self.hash_algorithm = cfg.get('hash_algorithm', 'md5')
            self.duplicate_strategy = cfg.get('duplicate_strategy', 'ask')
            
            self.cb_enable_dedup.blockSignals(True)
            self.cb_enable_dedup.setChecked(self.enable_deduplication)
            self.cb_enable_dedup.blockSignals(False)
            
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
        
        # 2. 检查配置是否被修改但未保存
        if self.config_modified:
            self._append_log("⚠ 检测到配置已修改但未保存")
            
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
        # 按钮状态：开始禁用，暂停和停止启用
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ 暂停上传")
        self.btn_stop.setEnabled(True)
        
        # 禁用路径编辑（上传过程中）
        self.src_edit.setReadOnly(True)
        self.tgt_edit.setReadOnly(True)
        self.bak_edit.setReadOnly(True)
        # 禁用浏览按钮
        for btn in [self.src_edit.parent().findChild(QtWidgets.QPushButton),
                    self.tgt_edit.parent().findChild(QtWidgets.QPushButton),
                    self.bak_edit.parent().findChild(QtWidgets.QPushButton)]:
            if btn and btn.text() == "浏览":
                btn.setEnabled(False)
        
        self._append_log(f"📋 上传配置:")
        self._append_log(f"  源文件夹: {self.src_edit.text()}")
        self._append_log(f"  目标文件夹: {self.tgt_edit.text()}")
        # v1.9.1 修改：根据备份启用状态显示不同信息
        if self.enable_backup:
            self._append_log(f"  备份文件夹: {self.bak_edit.text()}")
        else:
            self._append_log(f"  备份功能: 已禁用（上传成功后将删除源文件）")
        self._append_log(f"  间隔时间: {self.spin_interval.value()}秒")
        self._append_log(f"  重试次数: {self.spin_retry.value()}次")
        
        filters = [ext for ext, cb in self.cb_ext.items() if cb.isChecked()]
        self._append_log(f"  文件类型: {', '.join(filters)}")
        
        # 获取去重策略映射
        strategy_map = {'跳过': 'skip', '重命名': 'rename', '覆盖': 'overwrite', '询问': 'ask'}
        duplicate_strategy = strategy_map.get(self.combo_strategy.currentText(), 'ask')
        
        self.worker = UploadWorker(
            self.src_edit.text(), self.tgt_edit.text(), self.bak_edit.text(),
            self.spin_interval.value(), 'periodic', self.spin_disk.value(), self.spin_retry.value(), 
            filters, self.app_dir,
            self.cb_enable_dedup.isChecked(),
            self.combo_hash.currentText().lower(),
            duplicate_strategy,
            self.spin_network_check.value(),
            self.cb_network_auto_pause.isChecked(),
            self.cb_network_auto_resume.isChecked(),
            # v1.9 新增：自动删除参数
            self.cb_enable_auto_delete.isChecked(),
            self.auto_del_folder_edit.text(),
            self.spin_auto_del_threshold.value(),
            self.spin_auto_del_keep_days.value(),
            self.spin_auto_del_interval.value()
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
        self.worker_thread.start()
        self._toast('开始上传', 'success')
        self._append_log("✓ 上传任务已启动")

    def _on_pause_resume(self):
        if not self.worker:
            return
        if self.is_paused:
            # 恢复上传
            self.is_paused = False
            self.worker.resume()
            self.btn_pause.setText("⏸ 暂停上传")
            self._toast('已恢复', 'info')
        else:
            # 暂停上传
            self.is_paused = True
            self.worker.pause()
            self.btn_pause.setText("▶ 恢复上传")
            self._toast('已暂停', 'warning')
        self._update_status_pill()

    def _on_stop(self):
        """停止上传"""
        self._append_log("🛑 正在停止上传任务...")
        
        if not self.worker:
            # 没有Worker，直接恢复UI
            self._restore_ui_after_stop()
            return
        
        self.is_running = False
        self.is_paused = False
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
        
        # 恢复路径编辑权限和按钮状态：与权限策略一致
        # 上传控制按钮：所有人都可以使用（包括未登录状态）
        is_user_or_admin = self.current_role in ['user', 'admin']
        self.src_edit.setReadOnly(not is_user_or_admin)
        self.tgt_edit.setReadOnly(not is_user_or_admin)
        self.bak_edit.setReadOnly(not is_user_or_admin)
        for btn in [self.src_edit.parent().findChild(QtWidgets.QPushButton),
                    self.tgt_edit.parent().findChild(QtWidgets.QPushButton),
                    self.bak_edit.parent().findChild(QtWidgets.QPushButton)]:
            if btn and btn.text() == "浏览":
                btn.setEnabled(is_user_or_admin)

        # 关键：停止后“开始”立刻可点（不受角色限制）
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ 暂停上传")
        self.btn_stop.setEnabled(False)
        self.pbar.setValue(0)
        self.pbar_file.setValue(0)
        self.pbar_file.setFormat("等待...")
        self.lbl_current_file.setText("等待开始...")
        self.lbl_progress.setText("已停止")
        self._update_status_pill()
        # 统一再走一遍权限更新逻辑，确保一致
        try:
            self._update_ui_permissions()
            # _update_ui_permissions 会根据 is_running False 设置开始按钮启用。
            # 但为了避免未来改动影响，这里再次确保开始按钮保持启用。
            self.btn_start.setEnabled(True)
        except Exception:
            pass
        self._toast('已停止', 'danger')
        self._append_log("✓ 上传任务已停止")
        self._append_log("=" * 50)

    def _on_stats(self, uploaded: int, failed: int, skipped: int, rate: str):
        self.lbl_uploaded.setValue(str(uploaded))
        self.lbl_failed.setValue(str(failed))
        self.lbl_skipped.setValue(str(skipped))
        self.lbl_rate.setValue(rate)

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
        # keep thread objects for GC safety
        pass

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
    
    def closeEvent(self, event):
        """窗口关闭事件，清理资源"""
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


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    try:
        sys.exit(app.exec_())
    except AttributeError:
        # PySide6 使用 exec()
        sys.exit(app.exec())


if __name__ == '__main__':
    main()
