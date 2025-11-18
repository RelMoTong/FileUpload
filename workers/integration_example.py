# -*- coding: utf-8 -*-
"""
Worker与UploadManager集成示例

展示如何使用重构后的架构：
1. Worker只负责IO操作
2. UploadManager管理业务逻辑
3. 通过回调函数连接两者
"""

from pathlib import Path
from workers import UploadWorker
from services.upload_manager import UploadManager, UploadTask, TaskPriority


class UploadController:
    """上传控制器 - 连接Worker和Manager的桥梁"""
    
    def __init__(self):
        # 创建Worker（IO层）
        self.worker = UploadWorker()
        
        # 创建Manager（业务逻辑层）
        self.manager = UploadManager()
        
        # 连接信号
        self._connect_signals()
        
        # 注册回调
        self._register_callbacks()
    
    def _connect_signals(self):
        """连接Worker信号到UI更新"""
        # 这些信号可以直接连接到UI组件
        # self.worker.log.connect(self.ui.append_log)
        # self.worker.stats.connect(self.ui.update_stats)
        # self.worker.progress.connect(self.ui.update_progress)
        pass
    
    def _register_callbacks(self):
        """注册Manager的回调到Worker"""
        # Manager通知Worker各种事件
        self.manager.on_upload_started(self._on_upload_started)
        self.manager.on_upload_progress(self._on_upload_progress)
        self.manager.on_upload_completed(self._on_upload_completed)
        self.manager.on_upload_failed(self._on_upload_failed)
    
    def start_upload(self, source_dir: str, target_dir: str, file_extensions: list):
        """开始上传流程"""
        # 1. 扫描文件（Worker的IO操作）
        files = self.worker.scan_files(source_dir, file_extensions)
        
        # 2. 添加任务到Manager
        for file_path in files:
            rel_path = Path(file_path).relative_to(source_dir)
            target_path = Path(target_dir) / rel_path
            
            task = UploadTask(
                source_path=file_path,
                target_path=str(target_path),
                priority=TaskPriority.NORMAL,
                max_retries=3
            )
            self.manager.add_task(task)
        
        # 3. 启动Worker，连接到Manager
        self.worker.start(
            task_provider=self._get_next_task,
            on_completed=self._on_task_completed,
            on_failed=self._on_task_failed,
            on_skipped=self._on_task_skipped
        )
    
    def pause(self):
        """暂停上传"""
        self.worker.pause()
        self.manager.pause()
    
    def resume(self):
        """恢复上传"""
        self.worker.resume()
        self.manager.resume()
    
    def stop(self):
        """停止上传"""
        self.worker.stop()
        self.manager.stop()
    
    # ============ Worker回调 ============
    
    def _get_next_task(self):
        """Worker请求下一个任务"""
        return self.manager.get_next_task()
    
    def _on_task_completed(self, task):
        """Worker通知任务完成"""
        self.manager.mark_task_success(task)
        # 可选：归档源文件
        # self._archive_file(task.source_path, task.backup_path)
    
    def _on_task_failed(self, task, error_msg, exception=None):
        """Worker通知任务失败"""
        self.manager.mark_task_failed(task, error_msg, exception)
    
    def _on_task_skipped(self, task, reason):
        """Worker通知任务跳过"""
        self.manager.mark_task_skipped(task, reason)
    
    # ============ Manager回调 ============
    
    def _on_upload_started(self):
        """上传开始"""
        self.worker.log.emit("🚀 开始上传任务")
    
    def _on_upload_progress(self, current: int, total: int):
        """上传进度更新"""
        self.worker.progress.emit(current, total, "")
    
    def _on_upload_completed(self, result):
        """上传完成"""
        self.worker.log.emit(f"✓ 上传完成: 成功{result.success_count}, 失败{result.failed_count}, 跳过{result.skipped_count}")
        self.worker.stats.emit(
            result.success_count,
            result.failed_count,
            result.skipped_count,
            f"{result.average_speed_mbps:.2f} MB/s"
        )
        
        # 如果有失败文件，导出清单
        if result.failed_count > 0:
            self.export_failed_report()
    
    def _on_upload_failed(self, error_msg):
        """上传失败"""
        self.worker.log.emit(f"❌ 上传失败: {error_msg}")
    
    # ============ 失败文件处理 ============
    
    def export_failed_report(self, filename: str = "failed_files_report.txt"):
        """导出失败文件清单"""
        from pathlib import Path
        output_path = Path.cwd() / filename
        
        if self.manager.export_failed_files_report(str(output_path)):
            self.worker.log.emit(f"📋 失败文件清单已导出: {output_path}")
            return True
        else:
            self.worker.log.emit("❌ 导出失败文件清单失败")
            return False
    
    def retry_all_failed_files(self, only_retryable: bool = True):
        """重试所有失败的文件
        
        Args:
            only_retryable: 是否只重试可重试的文件（根据ErrorInfo判断）
        """
        retried, kept = self.manager.retry_failed_tasks(only_retryable=only_retryable)
        
        if retried > 0:
            self.worker.log.emit(f"🔄 已将 {retried} 个文件加入重试队列")
            if kept > 0:
                self.worker.log.emit(f"⚠️ {kept} 个文件不可重试，已保留在失败列表")
        else:
            self.worker.log.emit("ℹ️ 没有可重试的文件")
        
        return retried, kept
    
    def retry_specific_files(self, task_ids: list):
        """重试指定的文件
        
        Args:
            task_ids: 任务ID列表
        """
        retried, not_found = self.manager.retry_specific_tasks(task_ids)
        
        if retried > 0:
            self.worker.log.emit(f"🔄 已将 {retried} 个文件加入重试队列")
        if not_found > 0:
            self.worker.log.emit(f"⚠️ {not_found} 个任务未找到")
        
        return retried, not_found
    
    def get_failed_files_summary(self):
        """获取失败文件摘要信息"""
        stats = self.manager.get_statistics()
        
        summary = {
            'total_failed': stats['failed_count'],
            'retryable': stats.get('retryable_failed_count', 0),
            'non_retryable': stats['failed_count'] - stats.get('retryable_failed_count', 0),
            'error_categories': stats.get('error_categories', {}),
        }
        
        return summary


# ============ 使用示例 ============

def example_usage():
    """使用示例"""
    # 创建控制器
    controller = UploadController()
    
    # 开始上传
    controller.start_upload(
        source_dir="E:/Photos",
        target_dir="//server/share/Photos",
        file_extensions=['.jpg', '.png', '.gif']
    )
    
    # 暂停
    # controller.pause()
    
    # 恢复
    # controller.resume()
    
    # 停止
    # controller.stop()
    
    # ========== 失败处理示例 ==========
    
    # 1. 获取失败文件摘要
    # summary = controller.get_failed_files_summary()
    # print(f"失败文件: {summary['total_failed']}")
    # print(f"可重试: {summary['retryable']}")
    # print(f"错误类型: {summary['error_categories']}")
    
    # 2. 导出失败文件清单
    # controller.export_failed_report("failed_2025-11-18.txt")
    
    # 3. 重试所有可重试的失败文件
    # retried, kept = controller.retry_all_failed_files(only_retryable=True)
    # print(f"重试: {retried}, 保留: {kept}")
    
    # 4. 重试指定的文件
    # task_ids = ["1234567890_hash1", "1234567891_hash2"]
    # controller.retry_specific_files(task_ids)
    
    # 5. 获取特定类别的失败文件
    # from core.error_classifier import ErrorCategory
    # network_failures = controller.manager.get_failed_tasks_by_category(ErrorCategory.NETWORK)
    # print(f"网络错误: {len(network_failures)} 个文件")
    
    # 6. 获取高严重程度的失败文件
    # from core.error_classifier import ErrorSeverity
    # critical_failures = controller.manager.get_failed_tasks_by_severity(ErrorSeverity.HIGH)
    # print(f"严重错误: {len(critical_failures)} 个文件")


if __name__ == "__main__":
    example_usage()
