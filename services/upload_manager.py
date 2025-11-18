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

# 导入错误分类器
from core.error_classifier import ErrorClassifier, ErrorInfo, ErrorCategory, ErrorSeverity


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
    error_info: Optional[ErrorInfo] = None  # 错误信息（失败时记录）
    last_error_time: Optional[datetime] = None  # 最后一次错误时间
    
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
    failed_tasks: List[UploadTask] = field(default_factory=list)  # 失败任务列表（包含详细ErrorInfo）
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
        
        # 错误分类器
        self._error_classifier = ErrorClassifier()
        
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
    
    def complete_task(self, task: UploadTask, success: bool, error: Optional[str] = None, 
                     exception: Optional[Exception] = None):
        """完成任务
        
        Args:
            task: 上传任务
            success: 是否成功
            error: 错误消息（字符串）
            exception: 异常对象（用于分类）
        """
        self._result.processed_files += 1
        
        if success:
            self._result.success_files.append(task.source_path)
            self._continuous_failures = 0  # 重置连续失败计数
        else:
            # 使用ErrorClassifier分类错误
            if exception:
                error_info = self._error_classifier.classify_exception(exception, task.source_path)
            elif error:
                # 字符串错误，使用旧接口
                error_type, short_msg, advice = self._error_classifier.classify_error(error)
                error_info = ErrorInfo(
                    category=ErrorCategory.UNKNOWN,
                    severity=ErrorSeverity.MEDIUM,
                    message=short_msg,
                    suggestion=advice,
                    is_retryable=error_type not in ['permission', 'disk_full', 'file_not_found'],
                    original_error=error
                )
            else:
                error_info = ErrorInfo(
                    category=ErrorCategory.UNKNOWN,
                    severity=ErrorSeverity.MEDIUM,
                    message="未知错误",
                    suggestion="请检查网络连接和文件权限",
                    is_retryable=True,
                    original_error="Unknown error"
                )
            
            # 保存错误信息
            task.error_info = error_info
            task.last_error_time = datetime.now()
            
            # 判断是否应该重试
            should_retry = self._error_classifier.should_retry(
                error_info, 
                task.retry_count, 
                task.max_retries
            )
            
            if should_retry and task.retry_count < task.max_retries:
                task.retry_count += 1
                # 重新加入队列（降低优先级）
                task.priority = TaskPriority.LOW
                self._task_queue.append(task)
            else:
                # 超过重试次数或不应重试，标记为失败
                error_msg = error_info.get_user_message()
                self._result.failed_files.append((task.source_path, error_msg))
                self._result.failed_tasks.append(task)  # 保存完整任务信息
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
    
    def export_failed_files_report(self, output_path: str) -> bool:
        """导出失败文件清单
        
        Args:
            output_path: 输出文件路径
        
        Returns:
            是否成功导出
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("===== 上传失败文件清单 =====\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"失败文件总数: {len(self._failed_tasks)}\n")
                f.write("=" * 60 + "\n\n")
                
                for idx, task in enumerate(self._failed_tasks, 1):
                    f.write(f"[{idx}] 文件: {task.source_path}\n")
                    f.write(f"    目标: {task.target_path}\n")
                    f.write(f"    重试次数: {task.retry_count}/{task.max_retries}\n")
                    
                    if task.error_info:
                        f.write(f"    错误类别: {task.error_info.category.value}\n")
                        f.write(f"    严重程度: {task.error_info.severity.value}\n")
                        f.write(f"    错误消息: {task.error_info.message}\n")
                        f.write(f"    建议: {task.error_info.suggestion}\n")
                        f.write(f"    可重试: {'是' if task.error_info.is_retryable else '否'}\n")
                        if task.error_info.original_error:
                            f.write(f"    原始错误: {task.error_info.original_error}\n")
                    
                    if task.last_error_time:
                        f.write(f"    最后错误时间: {task.last_error_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    
                    f.write("\n")
                
                f.write("=" * 60 + "\n")
                f.write("统计信息:\n")
                
                # 按错误类别统计
                category_count: Dict[ErrorCategory, int] = {}
                for task in self._failed_tasks:
                    if task.error_info:
                        cat = task.error_info.category
                        category_count[cat] = category_count.get(cat, 0) + 1
                
                for cat, count in category_count.items():
                    f.write(f"  {cat.value}: {count} 个文件\n")
                
                # 统计可重试的文件
                retryable_count = sum(1 for task in self._failed_tasks 
                                     if task.error_info and task.error_info.is_retryable)
                f.write(f"\n可重试文件: {retryable_count} 个\n")
                f.write(f"不可重试文件: {len(self._failed_tasks) - retryable_count} 个\n")
            
            return True
        except Exception as e:
            print(f"导出失败文件清单失败: {e}")
            return False
    
    def retry_failed_tasks(self, only_retryable: bool = True):
        """重试失败的任务
        
        Args:
            only_retryable: 是否只重试可重试的任务（根据ErrorInfo.is_retryable判断）
        """
        tasks_to_retry = []
        tasks_to_keep = []
        
        for task in self._failed_tasks:
            # 判断是否应该重试
            should_retry = True
            if only_retryable and task.error_info:
                should_retry = task.error_info.is_retryable
            
            if should_retry:
                task.retry_count = 0  # 重置重试计数
                task.error_info = None  # 清除错误信息
                task.priority = TaskPriority.NORMAL  # 恢复正常优先级
                tasks_to_retry.append(task)
            else:
                # 不可重试的任务保留在失败列表
                tasks_to_keep.append(task)
        
        # 重新加入队列
        for task in tasks_to_retry:
            self.add_task(task)
        
        # 更新失败任务列表
        self._failed_tasks = tasks_to_keep
        self._continuous_failures = 0
        
        return len(tasks_to_retry), len(tasks_to_keep)
    
    def retry_specific_tasks(self, task_ids: List[str]):
        """重试指定的任务
        
        Args:
            task_ids: 要重试的任务ID列表
        
        Returns:
            (成功重试数, 未找到数)
        """
        retried = 0
        not_found = 0
        
        tasks_to_retry = []
        remaining_tasks = []
        
        for task in self._failed_tasks:
            if task.task_id in task_ids:
                task.retry_count = 0
                task.error_info = None
                task.priority = TaskPriority.HIGH  # 手动重试使用高优先级
                tasks_to_retry.append(task)
                retried += 1
            else:
                remaining_tasks.append(task)
        
        not_found = len(task_ids) - retried
        
        # 重新加入队列
        for task in tasks_to_retry:
            self.add_task(task)
        
        self._failed_tasks = remaining_tasks
        
        return retried, not_found
    
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
        # 统计错误类别
        error_categories = {}
        for task in self._failed_tasks:
            if task.error_info:
                cat = task.error_info.category.value
                error_categories[cat] = error_categories.get(cat, 0) + 1
        
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
            'error_categories': error_categories,
            'retryable_failed_count': sum(1 for t in self._failed_tasks if t.error_info and t.error_info.is_retryable),
        }
    
    def get_failed_tasks_by_category(self, category: ErrorCategory) -> List[UploadTask]:
        """按错误类别获取失败任务
        
        Args:
            category: 错误类别
        
        Returns:
            符合条件的失败任务列表
        """
        return [
            task for task in self._failed_tasks
            if task.error_info and task.error_info.category == category
        ]
    
    def get_failed_tasks_by_severity(self, min_severity: ErrorSeverity) -> List[UploadTask]:
        """按严重程度获取失败任务
        
        Args:
            min_severity: 最低严重程度
        
        Returns:
            符合条件的失败任务列表
        """
        severity_order = {
            ErrorSeverity.LOW: 0,
            ErrorSeverity.MEDIUM: 1,
            ErrorSeverity.HIGH: 2,
            ErrorSeverity.CRITICAL: 3
        }
        min_level = severity_order.get(min_severity, 0)
        
        return [
            task for task in self._failed_tasks
            if task.error_info and severity_order.get(task.error_info.severity, 0) >= min_level
        ]
    
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
    
    # ============ 简化的控制接口（适配Worker） ============
    
    def pause(self):
        """暂停上传"""
        if self._status == UploadStatus.RUNNING:
            self.set_status(UploadStatus.PAUSED)
    
    def resume(self):
        """恢复上传"""
        if self._status == UploadStatus.PAUSED:
            self.set_status(UploadStatus.RUNNING)
    
    def stop(self):
        """停止上传"""
        self.end_session()
    
    def mark_task_success(self, task: UploadTask):
        """标记任务成功（简化接口）"""
        self.complete_task(task, success=True)
    
    def mark_task_failed(self, task: UploadTask, error_msg: str, exception: Optional[Exception] = None):
        """标记任务失败（简化接口）
        
        Args:
            task: 上传任务
            error_msg: 错误消息
            exception: 异常对象（可选，用于更精确的错误分类）
        """
        self.complete_task(task, success=False, error=error_msg, exception=exception)
    
    def mark_task_skipped(self, task: UploadTask, reason: str):
        """标记任务跳过（简化接口）"""
        self.skip_task(task, reason)
    
    def on_upload_started(self, callback: Callable[[], None]):
        """注册上传开始回调"""
        # 使用status_changed回调来实现
        original_callback = self._on_status_changed
        
        def wrapper(status: UploadStatus):
            if status == UploadStatus.RUNNING:
                callback()
            if original_callback:
                original_callback(status)
        
        self._on_status_changed = wrapper
    
    def on_upload_completed(self, callback: Callable[[UploadResult], None]):
        """注册上传完成回调"""
        original_callback = self._on_status_changed
        
        def wrapper(status: UploadStatus):
            if status in (UploadStatus.COMPLETED, UploadStatus.FAILED):
                callback(self._result)
            if original_callback:
                original_callback(status)
        
        self._on_status_changed = wrapper
    
    def on_upload_failed(self, callback: Callable[[str], None]):
        """注册上传失败回调"""
        original_callback = self._on_status_changed
        
        def wrapper(status: UploadStatus):
            if status == UploadStatus.FAILED:
                error_msg = f"上传失败：{self._result.failed_count}个文件失败"
                callback(error_msg)
            if original_callback:
                original_callback(status)
        
        self._on_status_changed = wrapper
    
    def on_upload_progress(self, callback: Callable[[int, int], None]):
        """注册上传进度回调"""
        original_callback = self._on_progress_updated
        
        def wrapper(progress: float, current_file: str):
            callback(self._result.processed_files, self._result.total_files)
            if original_callback:
                original_callback(progress, current_file)
        
        self._on_progress_updated = wrapper
