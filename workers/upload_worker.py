# -*- coding: utf-8 -*-
"""
上传Worker - 负责IO操作和信号发送

职责分离原则：
1. Worker只负责：文件IO、网络操作、哈希计算
2. 所有业务逻辑由UploadManager处理
3. 所有UI更新通过信号槽
4. 不直接维护状态，通过回调与Manager通信
"""

import os
import time
import queue
import shutil
import hashlib
import threading
from pathlib import Path
from typing import Optional, Callable, List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

try:
    from PySide6 import QtCore
    from PySide6.QtCore import Signal, QObject
except ImportError:
    from PyQt5 import QtCore
    from PyQt5.QtCore import pyqtSignal as Signal, QObject


class UploadWorker(QObject):
    """上传Worker - 纯IO操作层"""
    
    # 信号定义
    log = Signal(str)  # 日志消息
    stats = Signal(int, int, int, str)  # uploaded, failed, skipped, rate
    progress = Signal(int, int, str)  # current, total, filename
    file_progress = Signal(str, int)  # filename, percent
    network_status = Signal(str)  # good/unstable/disconnected
    finished = Signal()  # 完成
    status = Signal(str)  # running/paused/stopped
    
    def __init__(self):
        super().__init__()
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        
        # 线程池
        self._file_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="FileIO")
        self._net_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NetCheck")
        
        # 回调接口（由UploadManager注册）
        self._get_next_task: Optional[Callable] = None
        self._on_task_completed: Optional[Callable] = None
        self._on_task_failed: Optional[Callable] = None
        self._on_task_skipped: Optional[Callable] = None
    
    # ============ 控制接口 ============
    
    def start(self, task_provider: Callable, 
              on_completed: Callable, 
              on_failed: Callable,
              on_skipped: Callable):
        """启动Worker
        
        Args:
            task_provider: 获取下一个任务的回调函数
            on_completed: 任务完成回调
            on_failed: 任务失败回调
            on_skipped: 任务跳过回调
        """
        if self._running:
            return
        
        self._get_next_task = task_provider
        self._on_task_completed = on_completed
        self._on_task_failed = on_failed
        self._on_task_skipped = on_skipped
        
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.status.emit('running')
    
    def pause(self):
        """暂停"""
        if not self._running:
            return
        self._paused = True
        self.status.emit('paused')
    
    def resume(self):
        """恢复"""
        if not self._running:
            return
        self._paused = False
        self.status.emit('running')
    
    def stop(self):
        """停止"""
        self._running = False
        self._paused = False
        
        # 关闭线程池
        try:
            self._file_executor.shutdown(wait=False, cancel_futures=True)
            self._net_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        
        self.status.emit('stopped')
    
    # ============ 主循环 ============
    
    def _run_loop(self):
        """主工作循环"""
        self.log.emit("🚀 Worker已启动")
        
        while self._running:
            # 暂停处理
            if self._paused:
                time.sleep(0.2)
                continue
            
            # 获取下一个任务
            if not self._get_next_task:
                time.sleep(0.5)
                continue
            
            task = self._get_next_task()
            if task is None:
                # 没有任务，短暂休眠
                time.sleep(0.5)
                continue
            
            # 处理任务
            self._process_task(task)
        
        self.finished.emit()
        self.log.emit("✓ Worker已停止")
    
    def _process_task(self, task):
        """处理单个上传任务
        
        Args:
            task: UploadTask对象
        """
        source = task.source_path
        target = task.target_path
        filename = os.path.basename(source)
        
        try:
            # 检查源文件是否存在
            if not self._safe_file_operation(os.path.exists, source, timeout=2.0, default=False):
                if self._on_task_skipped:
                    self._on_task_skipped(task, "源文件不存在")
                return
            
            # 检查目标是否已存在
            if self._safe_file_operation(os.path.exists, target, timeout=2.0, default=False):
                if self._on_task_skipped:
                    self._on_task_skipped(task, "目标文件已存在")
                return
            
            # 创建目标目录
            target_dir = os.path.dirname(target)
            create_result = self._safe_file_operation(
                lambda: os.makedirs(target_dir, exist_ok=True) or True,
                timeout=3.0,
                default=False
            )
            if not create_result:
                raise Exception("无法创建目标目录")
            
            # 复制文件（带进度）
            self.log.emit(f"📤 上传: {filename}")
            self._copy_file_with_progress(source, target, filename)
            
            # 通知完成
            if self._on_task_completed:
                self._on_task_completed(task)
            
            self.log.emit(f"✓ 完成: {filename}")
        
        except Exception as e:
            error_msg = str(e)
            self.log.emit(f"❌ 失败: {filename} - {error_msg}")
            
            if self._on_task_failed:
                self._on_task_failed(task, error_msg)
    
    # ============ IO操作 ============
    
    def _copy_file_with_progress(self, src: str, dst: str, filename: str, 
                                  buffer_size: int = 1024 * 1024):
        """带进度的文件复制"""
        file_size = os.path.getsize(src)
        
        with open(src, 'rb') as fsrc:
            with open(dst, 'wb') as fdst:
                copied = 0
                last_progress = -1
                
                while True:
                    if not self._running or self._paused:
                        # 清理不完整文件
                        if os.path.exists(dst):
                            try:
                                os.remove(dst)
                            except:
                                pass
                        raise Exception("上传被中断")
                    
                    buf = fsrc.read(buffer_size)
                    if not buf:
                        break
                    
                    fdst.write(buf)
                    copied += len(buf)
                    
                    # 更新进度
                    if file_size > 0:
                        progress = int(100 * copied / file_size)
                        if progress != last_progress and progress % 5 == 0:
                            self.file_progress.emit(filename, progress)
                            last_progress = progress
        
        # 复制文件元数据
        try:
            shutil.copystat(src, dst)
        except:
            pass
    
    def calculate_file_hash(self, file_path: str, algorithm: str = 'md5', 
                           buffer_size: int = 8192) -> str:
        """计算文件哈希值
        
        Args:
            file_path: 文件路径
            algorithm: 哈希算法 (md5/sha256)
            buffer_size: 缓冲区大小
        
        Returns:
            哈希值字符串
        """
        try:
            if algorithm.lower() == 'sha256':
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
                    
                    # 大文件显示进度
                    if file_size > 50 * 1024 * 1024:  # > 50MB
                        progress = int(100 * processed / file_size)
                        if progress % 20 == 0:
                            self.log.emit(f"🔍 计算哈希值... {progress}%")
            
            return hasher.hexdigest()
        
        except Exception as e:
            self.log.emit(f"⚠ 哈希计算失败: {e}")
            return ""
    
    def find_duplicate_by_hash(self, file_hash: str, target_dir: str) -> str:
        """在目标目录查找相同哈希的文件
        
        Returns:
            重复文件路径，未找到返回空字符串
        """
        if not file_hash:
            return ""
        
        try:
            for root, _, files in os.walk(target_dir):
                for name in files:
                    if not self._running or self._paused:
                        return ""
                    
                    target_file = os.path.join(root, name)
                    try:
                        target_hash = self.calculate_file_hash(target_file)
                        if target_hash == file_hash:
                            return target_file
                    except Exception:
                        continue
            return ""
        except Exception:
            return ""
    
    def scan_files(self, source_dir: str, extensions: List[str]) -> List[str]:
        """扫描指定扩展名的文件
        
        Args:
            source_dir: 源目录
            extensions: 扩展名列表 (如 ['.jpg', '.png'])
        
        Returns:
            文件路径列表
        """
        files = []
        
        try:
            for root, _, names in os.walk(source_dir):
                if not self._running:
                    break
                
                for name in names:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in extensions:
                        files.append(os.path.join(root, name))
        except Exception as e:
            self.log.emit(f"⚠ 文件扫描失败: {e}")
        
        return files
    
    # ============ 网络检查 ============
    
    def check_path_accessible(self, path: str, timeout: float = 2.0) -> bool:
        """检查路径是否可访问"""
        result = self._safe_file_operation(os.path.exists, path, timeout=timeout, default=False)
        return bool(result)
    
    def check_disk_space(self, path: str) -> Tuple[float, float, float]:
        """检查磁盘空间
        
        Returns:
            (free_percent, total_gb, free_gb)
        """
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
        
        result = self._safe_file_operation(check, timeout=2.0, default=(0.0, 0.0, 0.0))
        return result if result is not None else (0.0, 0.0, 0.0)
    
    # ============ 辅助方法 ============
    
    def _safe_file_operation(self, func, *args, timeout: float = 3.0, default=None):
        """安全执行文件操作（带超时）"""
        try:
            future = self._file_executor.submit(func, *args)
            result = future.result(timeout=timeout)
            return result
        except FuturesTimeoutError:
            self.log.emit(f"⏱️ 文件操作超时（{timeout}秒）")
            return default
        except Exception as e:
            self.log.emit(f"⚠️ 文件操作异常: {str(e)[:50]}")
            return default
    
    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running
    
    @property
    def is_paused(self) -> bool:
        """是否已暂停"""
        return self._paused
