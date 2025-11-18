# -*- coding: utf-8 -*-
"""
上传管理器 - 负责上传队列、重试、进度、状态管理

职责：
1. 维护上传任务队列
2. 管理上传状态（idle/running/paused/completed/failed）
3. 实现重试机制（单文件重试、失败文件批量重试）
4. 跟踪上传进度和统计信息
5. 提供上传结果（success/failed/skipped 文件清单）
"""

import os
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class UploadStatus(Enum):
    """上传状态枚举"""
    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 运行中
    PAUSED = "paused"  # 已暂停
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    RECONNECTING = "reconnecting"  # 重连中


class TaskPriority(Enum):
    """任务优先级"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class UploadTask:
    """上传任务数据类"""
    source_path: str  # 源文件路径
    target_path: str  # 目标路径
    backup_path: Optional[str] = None  # 备份路径
    priority: TaskPriority = TaskPriority.NORMAL  # 优先级
    retry_count: int = 0  # 已重试次数
    max_retries: int = 3  # 最大重试次数
    task_id: Optional[str] = None  # 任务ID
    created_at: Optional[datetime] = None  # 创建时间
    
    def __post_init__(self):
        if self.task_id is None:
            self.task_id = f"{int(time.time() * 1000)}_{hash(self.source_path)}"
        if self.created_at is None:
            self.created_at = datetime.now()


@dataclass
class UploadResult:
    """上传结果数据类"""
    success_files: List[str] = field(default_factory=list)  # 成功文件列表
    failed_files: List[Tuple[str, str]] = field(default_factory=list)  # 失败文件列表 (path, error)
    skipped_files: List[Tuple[str, str]] = field(default_factory=list)  # 跳过文件列表 (path, reason)
    total_size: int = 0  # 总大小（字节）
    uploaded_size: int = 0  # 已上传大小
    total_files: int = 0  # 总文件数
    processed_files: int = 0  # 已处理文件数
    start_time: Optional[datetime] = None  # 开始时间
    end_time: Optional[datetime] = None  # 结束时间
    
    @property
    def success_count(self) -> int:
        return len(self.success_files)
    
    @property
    def failed_count(self) -> int:
        return len(self.failed_files)
    
    @property
    def skipped_count(self) -> int:
        return len(self.skipped_files)
    
    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    @property
    def average_speed_mbps(self) -> float:
        """平均上传速度 (MB/s)"""
        if self.duration_seconds > 0 and self.uploaded_size > 0:
            return (self.uploaded_size / 1024 / 1024) / self.duration_seconds
        return 0.0


class UploadManager:
    """上传管理器 - 核心业务逻辑"""
    
    def __init__(self):
        self._task_queue: List[UploadTask] = []  # 任务队列
        self._current_task: Optional[UploadTask] = None  # 当前任务
        self._status: UploadStatus = UploadStatus.IDLE  # 当前状态
        self._result: UploadResult = UploadResult()  # 上传结果
        self._failed_tasks: List[UploadTask] = []  # 失败任务列表
        self._continuous_failures: int = 0  # 连续失败次数
        self._max_continuous_failures: int = 3  # 最大连续失败次数
        
        # 回调函数
        self._on_status_changed: Optional[Callable[[UploadStatus], None]] = None
        self._on_task_started: Optional[Callable[[UploadTask], None]] = None
        self._on_task_completed: Optional[Callable[[UploadTask, bool], None]] = None
        self._on_progress_updated: Optional[Callable[[float, str], None]] = None
    
    # ============ 状态管理 ============
    
    @property
    def status(self) -> UploadStatus:
        """获取当前状态"""
        return self._status
    
    def set_status(self, status: UploadStatus):
        """设置状态并触发回调"""
        if self._status != status:
            self._status = status
            if self._on_status_changed:
                self._on_status_changed(status)
    
    @property
    def is_idle(self) -> bool:
        return self._status == UploadStatus.IDLE
    
    @property
    def is_running(self) -> bool:
        return self._status == UploadStatus.RUNNING
    
    @property
    def is_paused(self) -> bool:
        return self._status == UploadStatus.PAUSED
    
    # ============ 队列管理 ============
    
    def add_task(self, task: UploadTask):
        """添加任务到队列"""
        self._task_queue.append(task)
        self._result.total_files += 1
        try:
            file_size = os.path.getsize(task.source_path)
            self._result.total_size += file_size
        except Exception:
            pass
    
    def add_tasks(self, tasks: List[UploadTask]):
        """批量添加任务"""
        for task in tasks:
            self.add_task(task)
    
    def clear_queue(self):
        """清空队列"""
        self._task_queue.clear()
        self._failed_tasks.clear()
        self._current_task = None
    
    def get_next_task(self) -> Optional[UploadTask]:
        """获取下一个任务（按优先级排序）"""
        if not self._task_queue:
            return None
        
        # 按优先级排序
        self._task_queue.sort(key=lambda t: t.priority.value, reverse=True)
        return self._task_queue.pop(0)
    
    @property
    def queue_size(self) -> int:
        """队列剩余任务数"""
        return len(self._task_queue)
    
    # ============ 任务执行 ============
    
    def start_task(self, task: UploadTask):
        """开始执行任务"""
        self._current_task = task
        if self._on_task_started:
            self._on_task_started(task)
    
    def complete_task(self, task: UploadTask, success: bool, error: Optional[str] = None):
        """完成任务"""
        self._result.processed_files += 1
        
        if success:
            self._result.success_files.append(task.source_path)
            self._continuous_failures = 0  # 重置连续失败计数
        else:
            # 检查是否需要重试
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                # 重新加入队列（降低优先级）
                task.priority = TaskPriority.LOW
                self._task_queue.append(task)
            else:
                # 超过重试次数，标记为失败
                self._result.failed_files.append((task.source_path, error or "Unknown error"))
                self._failed_tasks.append(task)
                self._continuous_failures += 1
        
        if self._on_task_completed:
            self._on_task_completed(task, success)
        
        self._current_task = None
    
    def skip_task(self, task: UploadTask, reason: str):
        """跳过任务"""
        self._result.skipped_files.append((task.source_path, reason))
        self._result.processed_files += 1
        self._current_task = None
    
    def update_progress(self, uploaded_bytes: int, current_file: str):
        """更新进度"""
        self._result.uploaded_size = uploaded_bytes
        if self._on_progress_updated:
            progress = (uploaded_bytes / self._result.total_size * 100) if self._result.total_size > 0 else 0
            self._on_progress_updated(progress, current_file)
    
    # ============ 重试机制 ============
    
    def should_pause_on_failures(self) -> bool:
        """是否应该因连续失败而暂停"""
        return self._continuous_failures >= self._max_continuous_failures
    
    def get_failed_tasks(self) -> List[UploadTask]:
        """获取所有失败任务"""
        return self._failed_tasks.copy()
    
    def retry_failed_tasks(self):
        """重试所有失败的任务"""
        for task in self._failed_tasks:
            task.retry_count = 0  # 重置重试计数
            self.add_task(task)
        self._failed_tasks.clear()
        self._continuous_failures = 0
    
    def reset_continuous_failures(self):
        """重置连续失败计数（用于重连后）"""
        self._continuous_failures = 0
    
    # ============ 结果统计 ============
    
    def start_session(self):
        """开始上传会话"""
        self._result = UploadResult()
        self._result.start_time = datetime.now()
        self._continuous_failures = 0
        self.set_status(UploadStatus.RUNNING)
    
    def end_session(self):
        """结束上传会话"""
        self._result.end_time = datetime.now()
        if self._result.failed_count > 0:
            self.set_status(UploadStatus.FAILED)
        else:
            self.set_status(UploadStatus.COMPLETED)
    
    def get_result(self) -> UploadResult:
        """获取上传结果"""
        return self._result
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_files': self._result.total_files,
            'processed_files': self._result.processed_files,
            'success_count': self._result.success_count,
            'failed_count': self._result.failed_count,
            'skipped_count': self._result.skipped_count,
            'total_size_mb': self._result.total_size / 1024 / 1024,
            'uploaded_size_mb': self._result.uploaded_size / 1024 / 1024,
            'average_speed_mbps': self._result.average_speed_mbps,
            'duration_seconds': self._result.duration_seconds,
            'queue_size': self.queue_size,
            'continuous_failures': self._continuous_failures,
        }
    
    # ============ 回调注册 ============
    
    def on_status_changed(self, callback: Callable[[UploadStatus], None]):
        """注册状态变化回调"""
        self._on_status_changed = callback
    
    def on_task_started(self, callback: Callable[[UploadTask], None]):
        """注册任务开始回调"""
        self._on_task_started = callback
    
    def on_task_completed(self, callback: Callable[[UploadTask, bool], None]):
        """注册任务完成回调"""
        self._on_task_completed = callback
    
    def on_progress_updated(self, callback: Callable[[float, str], None]):
        """注册进度更新回调"""
        self._on_progress_updated = callback
