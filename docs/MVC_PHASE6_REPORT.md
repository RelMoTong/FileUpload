# MVC 重构阶段 6 完成报告

## 结果

磁盘扫描、手动删除、自动清理、候选文件策略和清理审计已经迁移到 `CleanupService` 与 `CleanupController`。`MainWindow` 和 `DiskCleanupDialog` 只收集输入、转发命令并渲染事件，不再直接扫描或删除文件。

## 新增职责

- `src/models/cleanup_task.py`：定义手动扫描、删除、自动清理及结果模型。
- `src/services/cleanup_service.py`：负责路径校验、磁盘分组、候选选择、回收站/永久删除、手动 Worker 生命周期和自动清理策略。
- `src/controllers/cleanup_controller.py`：协调手动清理事件、自动清理串行执行、重复触发保护、取消和关闭流程。
- `src/repositories/cleanup_audit_repository.py`：以线程安全方式写入独立的每日 JSONL 清理审计日志。
- `src/main.py`：在组合根创建并注入清理 Repository、Service 和 Controller。

## 保持的行为

- 扫描仍支持多目录、扩展名过滤、保留天数和取消。
- 手动清理仍支持回收站与永久删除，并保持确认和进度展示。
- 自动清理仍按全局修改时间从旧到新处理候选文件。
- 自动清理仍保留触发阈值、目标阈值、同盘限制、20 次失败上限和回收站未释放空间时停止等规则。
- 应用关闭时会取消自动清理，并统一释放手动 Worker、Qt 线程和自动清理执行器。
- `START`、`SCAN_FAIL`、`DELETE_OK`、`DELETE_FAIL` 和 `END` 审计事件保持独立记录。

## 架构约束

- UI 不导入具体的 Controller、Service、Repository 或 Worker 实现，只依赖本地协议和模型。
- 架构测试禁止 `main_window.py` 与 `widgets.py` 调用扫描和删除文件的底层函数。
- 扫描和删除 Worker 仅通过事件回调返回日志、进度和完成结果，不直接访问控件。

## 验证

- 清理策略、启动修复和审计测试：`16 passed`。
- 清理生命周期、策略和架构专项测试：`21 passed`。
- 完整回归：`64 passed, 5 subtests passed`。

## 风险与后续

- 自动清理仍依赖真实磁盘剩余空间变化判断是否达到目标；不同文件系统和回收站实现可能延迟报告空间变化。
- 手动扫描取消是协作式取消，会在遍历到下一目录或文件时生效，不能强制中断正在执行的单次系统调用。
- 阶段 7 将继续拆分 View、统一 `render_*()` 方法，并删除剩余的界面内持久化与业务方法。
