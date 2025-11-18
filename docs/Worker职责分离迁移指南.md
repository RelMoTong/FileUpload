# Worker职责分离 - 迁移指南

## 概述

本次重构将原有的庞大Worker类分离为两层架构：
- **Worker层（workers/）**：纯IO操作（文件、网络、哈希计算）
- **Manager层（services/）**：业务逻辑（队列管理、重试策略、状态控制）

## 架构对比

### 旧架构 (pyqt_app.py中的UploadWorker)

```python
class UploadWorker:
    - 文件IO操作
    - 网络检测
    - 哈希计算
    - 队列管理          ❌ 混杂业务逻辑
    - 重试策略          ❌ 混杂业务逻辑
    - 状态管理          ❌ 混杂业务逻辑
    - 统计信息          ❌ 混杂业务逻辑
    - UI信号发送
    - 去重处理          ❌ 混杂业务逻辑
    - 自动删除          ❌ 混杂业务逻辑
```

**问题**：
- 单一类超过800行
- 职责不清晰
- 难以测试
- 难以复用

### 新架构 (workers/ + services/)

```python
# workers/upload_worker.py
class UploadWorker:
    - 文件IO操作        ✓ 专注IO
    - 网络检测          ✓ 专注IO  
    - 哈希计算          ✓ 专注IO
    - 发送UI信号        ✓ 只负责通知

# services/upload_manager.py
class UploadManager:
    - 队列管理          ✓ 纯业务逻辑
    - 重试策略          ✓ 纯业务逻辑
    - 状态管理          ✓ 纯业务逻辑
    - 统计信息          ✓ 纯业务逻辑

# services/dedup_service.py
class DedupService:
    - 去重处理          ✓ 独立服务

# services/cleanup_manager.py
class CleanupManager:
    - 自动清理          ✓ 独立服务
```

**优势**：
- 职责清晰
- 易于测试
- 易于扩展
- 代码可读性高

## 迁移步骤

### 步骤1：引入新模块

```python
from workers import UploadWorker
from services.upload_manager import UploadManager, UploadTask, TaskPriority
from services.dedup_service import DedupService
from services.cleanup_manager import CleanupManager
```

### 步骤2：创建控制器类

```python
class UploadController:
    def __init__(self):
        # IO层
        self.worker = UploadWorker()
        
        # 业务逻辑层
        self.upload_manager = UploadManager()
        self.dedup_service = DedupService()
        self.cleanup_manager = CleanupManager()
        
        # 连接信号和回调
        self._connect_signals()
        self._register_callbacks()
```

### 步骤3：连接信号

```python
def _connect_signals(self):
    """将Worker信号连接到UI"""
    self.worker.log.connect(self.ui.append_log)
    self.worker.stats.connect(self.ui.update_stats)
    self.worker.progress.connect(self.ui.update_progress)
    self.worker.file_progress.connect(self.ui.update_file_progress)
    self.worker.network_status.connect(self.ui.update_network_status)
```

### 步骤4：注册回调

```python
def _register_callbacks(self):
    """注册Manager回调"""
    # UploadManager回调
    self.upload_manager.on_upload_started(self._on_upload_started)
    self.upload_manager.on_upload_progress(self._on_upload_progress)
    self.upload_manager.on_upload_completed(self._on_upload_completed)
    
    # CleanupManager回调
    self.cleanup_manager.on_cleanup_completed(self._on_cleanup_completed)
```

### 步骤5：实现上传流程

```python
def start_upload(self):
    # 1. 扫描文件（Worker IO操作）
    files = self.worker.scan_files(
        self.source_dir, 
        ['.jpg', '.png', '.gif']
    )
    
    # 2. 去重处理（DedupService）
    if self.enable_deduplication:
        files = self.dedup_service.filter_duplicates(files, self.target_dir)
    
    # 3. 创建上传任务（UploadManager）
    for file_path in files:
        task = UploadTask(
            source_path=file_path,
            target_path=self._get_target_path(file_path),
            backup_path=self._get_backup_path(file_path),
            max_retries=3
        )
        self.upload_manager.add_task(task)
    
    # 4. 启动Worker
    self.upload_manager.start_session()
    self.worker.start(
        task_provider=self.upload_manager.get_next_task,
        on_completed=self.upload_manager.mark_task_success,
        on_failed=self.upload_manager.mark_task_failed,
        on_skipped=self.upload_manager.mark_task_skipped
    )
```

## 关键改进点

### 1. 重试机制

**旧方式**：Worker内部维护retry_queue字典

```python
# 旧代码
self.retry_queue = {}  # {file_path: retry_count}
```

**新方式**：UploadManager自动管理

```python
# 新代码
class UploadTask:
    retry_count: int = 0
    max_retries: int = 3
    
# UploadManager自动处理重试
def complete_task(self, task, success, error=None):
    if not success and task.retry_count < task.max_retries:
        task.retry_count += 1
        self._task_queue.append(task)  # 自动重新入队
```

### 2. 统计信息

**旧方式**：Worker直接维护计数器

```python
# 旧代码
self.uploaded = 0
self.failed = 0
self.skipped = 0
```

**新方式**：UploadResult封装

```python
# 新代码
@dataclass
class UploadResult:
    success_files: List[str]
    failed_files: List[Tuple[str, str]]
    skipped_files: List[Tuple[str, str]]
    
    @property
    def success_count(self) -> int:
        return len(self.success_files)
```

### 3. 网络检测

**旧方式**：Worker内部实现复杂的网络监控线程

```python
# 旧代码（800+行中的200行）
def _network_monitor_loop(self):
    # 复杂的网络监控逻辑
    ...
```

**新方式**：Worker提供简单的检查接口，业务逻辑在外部

```python
# 新代码（简化）
def check_path_accessible(self, path: str, timeout: float = 2.0) -> bool:
    return self._safe_file_operation(os.path.exists, path, timeout=timeout, default=False)

# 网络监控可以在Controller中实现
class UploadController:
    def _start_network_monitor(self):
        # 独立的网络监控线程
        pass
```

### 4. 去重处理

**旧方式**：Worker内部处理去重逻辑

```python
# 旧代码（混在Worker中）
def _calculate_file_hash(self, file_path):
    ...
def _find_duplicate_by_hash(self, file_hash, target_dir):
    ...
```

**新方式**：独立的DedupService

```python
# 新代码
dedup_service = DedupService()
dedup_service.enable_deduplication(True, 'md5')
unique_files = dedup_service.filter_duplicates(files, target_dir)
```

## 测试改进

### 旧架构测试困难

```python
# 难以测试：需要模拟整个Worker
def test_upload():
    worker = UploadWorker(...)  # 需要传入大量参数
    # 难以隔离测试某个功能
```

### 新架构易于测试

```python
# 可以独立测试各个组件
def test_upload_manager():
    manager = UploadManager()
    task = UploadTask(source_path="test.jpg", target_path="target/test.jpg")
    manager.add_task(task)
    assert manager.queue_size == 1

def test_worker_io():
    worker = UploadWorker()
    files = worker.scan_files("/test", ['.jpg'])
    assert len(files) > 0
```

## 兼容性说明

### 向后兼容

旧的UploadWorker类仍然保留在pyqt_app.py中，现有代码可以继续使用。

### 渐进式迁移

建议按以下顺序迁移：

1. **第一阶段**：在新功能中使用新架构
2. **第二阶段**：将旧功能逐步迁移
3. **第三阶段**：完全移除旧Worker类

## 常见问题

### Q1: 性能会受影响吗？

**答**：不会。新架构只是重新组织代码，IO操作的执行方式相同。实际上由于职责清晰，可能更容易优化。

### Q2: 需要修改UI代码吗？

**答**：最小化修改。只需要：
1. 创建UploadController
2. 连接信号到UI组件
3. 调用controller.start_upload()

UI组件本身不需要修改。

### Q3: 如何处理现有的配置？

**答**：配置项保持不变，只是传递给不同的组件：
```python
# 上传配置 → UploadManager
upload_manager.set_max_retries(config['retry_count'])

# 去重配置 → DedupService  
dedup_service.enable_deduplication(config['enable_dedup'], config['hash_algorithm'])

# 清理配置 → CleanupManager
cleanup_manager.set_rule(cleanup_rule)
```

## 下一步计划

1. ✅ 创建新Worker类（workers/upload_worker.py）
2. ✅ 补充UploadManager方法
3. ✅ 创建集成示例
4. ⏳ 在pyqt_app.py中集成新架构
5. ⏳ 编写单元测试
6. ⏳ 性能测试和优化
7. ⏳ 文档完善

## 参考代码

完整示例代码：`workers/integration_example.py`
