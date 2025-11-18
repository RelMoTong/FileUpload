# -*- coding: utf-8 -*-
"""
磁盘清理管理器 - 负责磁盘空间监控和自动清理

职责：
1. 监控磁盘空间使用情况
2. 自动删除规则管理
3. 执行磁盘清理操作
4. 生成清理报告
"""

import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class CleanupStrategy(Enum):
    """清理策略"""
    NONE = "none"  # 不清理
    BY_DATE = "by_date"  # 按日期清理
    BY_SIZE = "by_size"  # 按大小清理
    BY_COUNT = "by_count"  # 按文件数清理


@dataclass
class CleanupRule:
    """清理规则"""
    enabled: bool = False
    strategy: CleanupStrategy = CleanupStrategy.NONE
    threshold_days: int = 30  # 保留天数
    threshold_size_gb: float = 10.0  # 保留大小(GB)
    threshold_count: int = 1000  # 保留文件数
    target_path: str = ""  # 清理目标路径
    
    def is_valid(self) -> bool:
        """检查规则是否有效"""
        return (self.enabled and 
                self.strategy != CleanupStrategy.NONE and 
                os.path.exists(self.target_path))


@dataclass
class CleanupResult:
    """清理结果"""
    deleted_files: List[str] = None
    deleted_size: int = 0  # 删除的总大小（字节）
    freed_space: int = 0  # 释放的磁盘空间（字节）
    errors: List[Tuple[str, str]] = None  # 错误列表 (file, error)
    
    def __post_init__(self):
        if self.deleted_files is None:
            self.deleted_files = []
        if self.errors is None:
            self.errors = []
    
    @property
    def deleted_count(self) -> int:
        return len(self.deleted_files)
    
    @property
    def error_count(self) -> int:
        return len(self.errors)


class CleanupManager:
    """磁盘清理管理器"""
    
    def __init__(self):
        self._cleanup_rule: Optional[CleanupRule] = None
        self._running: bool = False
        
        # 回调
        self._on_cleanup_started: Optional[Callable[[], None]] = None
        self._on_cleanup_progress: Optional[Callable[[int, int], None]] = None
        self._on_cleanup_completed: Optional[Callable[[CleanupResult], None]] = None
    
    # ============ 规则管理 ============
    
    def set_rule(self, rule: CleanupRule):
        """设置清理规则"""
        self._cleanup_rule = rule
    
    def get_rule(self) -> Optional[CleanupRule]:
        """获取清理规则"""
        return self._cleanup_rule
    
    def has_valid_rule(self) -> bool:
        """是否有有效的清理规则"""
        return self._cleanup_rule is not None and self._cleanup_rule.is_valid()
    
    # ============ 磁盘空间监控 ============
    
    def get_disk_usage(self, path: str) -> Tuple[int, int, float]:
        """获取磁盘使用情况
        
        Returns:
            (total_bytes, used_bytes, free_percent)
        """
        try:
            if path.startswith('\\\\'):
                # 网络路径，返回特殊值
                return 0, 0, -1.0
            
            stat = shutil.disk_usage(path)
            total = stat.total
            used = stat.used
            free_percent = (stat.free / total * 100) if total > 0 else 0
            return total, used, free_percent
            
        except Exception:
            return 0, 0, -1.0
    
    def is_disk_full(self, path: str, threshold_percent: float = 10.0) -> bool:
        """检查磁盘是否接近满"""
        _, _, free_percent = self.get_disk_usage(path)
        if free_percent < 0:  # 网络路径或无法访问
            return False
        return free_percent < threshold_percent
    
    # ============ 清理操作 ============
    
    def execute_cleanup(self, dry_run: bool = False) -> CleanupResult:
        """执行清理操作
        
        Args:
            dry_run: 是否仅模拟运行（不实际删除）
        
        Returns:
            CleanupResult
        """
        result = CleanupResult()
        
        if not self.has_valid_rule():
            return result
        
        self._running = True
        if self._on_cleanup_started:
            self._on_cleanup_started()
        
        try:
            rule = self._cleanup_rule
            
            # 收集需要清理的文件
            files_to_delete = self._collect_files_for_cleanup(rule)
            total_files = len(files_to_delete)
            
            # 删除文件
            for idx, file_path in enumerate(files_to_delete):
                try:
                    if not dry_run:
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        result.deleted_size += file_size
                    
                    result.deleted_files.append(file_path)
                    
                    if self._on_cleanup_progress:
                        self._on_cleanup_progress(idx + 1, total_files)
                        
                except Exception as e:
                    result.errors.append((file_path, str(e)))
            
            # 计算释放的空间
            if not dry_run:
                result.freed_space = result.deleted_size
            
        finally:
            self._running = False
            if self._on_cleanup_completed:
                self._on_cleanup_completed(result)
        
        return result
    
    def _collect_files_for_cleanup(self, rule: CleanupRule) -> List[str]:
        """收集需要清理的文件"""
        files_to_delete = []
        target_path = Path(rule.target_path)
        
        if not target_path.exists():
            return files_to_delete
        
        # 收集所有文件
        all_files = []
        for root, _, files in os.walk(target_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    stat = os.stat(file_path)
                    all_files.append({
                        'path': file_path,
                        'size': stat.st_size,
                        'mtime': datetime.fromtimestamp(stat.st_mtime)
                    })
                except Exception:
                    continue
        
        # 根据策略筛选
        if rule.strategy == CleanupStrategy.BY_DATE:
            # 删除超过指定天数的文件
            cutoff_date = datetime.now() - timedelta(days=rule.threshold_days)
            files_to_delete = [
                f['path'] for f in all_files 
                if f['mtime'] < cutoff_date
            ]
        
        elif rule.strategy == CleanupStrategy.BY_SIZE:
            # 保留最新的文件直到总大小低于阈值
            threshold_bytes = int(rule.threshold_size_gb * 1024 * 1024 * 1024)
            all_files.sort(key=lambda x: x['mtime'], reverse=True)  # 最新的在前
            
            total_size = 0
            for f in all_files:
                if total_size >= threshold_bytes:
                    files_to_delete.append(f['path'])
                else:
                    total_size += f['size']
        
        elif rule.strategy == CleanupStrategy.BY_COUNT:
            # 只保留最新的N个文件
            all_files.sort(key=lambda x: x['mtime'], reverse=True)
            if len(all_files) > rule.threshold_count:
                files_to_delete = [f['path'] for f in all_files[rule.threshold_count:]]
        
        return files_to_delete
    
    def preview_cleanup(self) -> Tuple[int, int]:
        """预览清理结果（不实际删除）
        
        Returns:
            (file_count, total_size_bytes)
        """
        if not self.has_valid_rule():
            return 0, 0
        
        files_to_delete = self._collect_files_for_cleanup(self._cleanup_rule)
        total_size = sum(
            os.path.getsize(f) for f in files_to_delete 
            if os.path.exists(f)
        )
        return len(files_to_delete), total_size
    
    # ============ 状态查询 ============
    
    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running
    
    # ============ 回调注册 ============
    
    def on_cleanup_started(self, callback: Callable[[], None]):
        """注册清理开始回调"""
        self._on_cleanup_started = callback
    
    def on_cleanup_progress(self, callback: Callable[[int, int], None]):
        """注册清理进度回调 (current, total)"""
        self._on_cleanup_progress = callback
    
    def on_cleanup_completed(self, callback: Callable[[CleanupResult], None]):
        """注册清理完成回调"""
        self._on_cleanup_completed = callback
