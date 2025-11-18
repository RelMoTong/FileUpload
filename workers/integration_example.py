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
    
    def _on_task_failed(self, task, error_msg):
        """Worker通知任务失败"""
        self.manager.mark_task_failed(task, error_msg)
    
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
    
    def _on_upload_failed(self, error_msg):
        """上传失败"""
        self.worker.log.emit(f"❌ 上传失败: {error_msg}")


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


if __name__ == "__main__":
    example_usage()
