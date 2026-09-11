# MVC 重构阶段 5 完成报告

## 修改文件

- `src/models/upload_task.py`：新增上传请求、校验结果、命令结果和运行快照模型。
- `src/services/upload_service.py`：独占 `UploadWorker` 创建、信号桥接和 `QThread` 生命周期。
- `src/controllers/upload_controller.py`：独占上传状态机、统计快照和 Worker 事件协调。
- `src/main.py`：在组合根创建并注入 `UploadController` 和 `UploadService`。
- `src/ui/main_window.py`：移除 Worker/线程持有与具体控制，只收集上传请求并渲染 Controller 事件。
- `tests/test_upload_mvc.py`：新增请求校验、状态转换、事件快照、服务委托及真实 QThread 生命周期测试。
- `tests/test_architecture_boundaries.py`：清空 View 的最后一个跨层依赖白名单。
- `tests/test_more_menu_permissions.py`、`tests/test_responsive_layout.py`：通过组合依赖创建主窗口。

## 修改原因

Worker 创建、线程退出、暂停恢复和运行状态属于应用流程职责。此前 View、Worker 各自保存运行状态，且 View 直接读取 Worker 内部 FTP 客户端和归档队列，容易出现状态不同步。本阶段将唯一运行快照放入 Controller，由 Service 隐藏 Qt 和 Worker 细节。

## 状态转换规则

- `stopped → running`：Controller 校验请求且 Service 成功创建任务。
- `running → paused`：暂停命令成功或 Worker 自动暂停。
- `paused → running`：恢复命令成功或 Worker 自动恢复。
- `running/paused → stopped`：停止命令、Worker 状态事件或完成事件。
- 其他转换会被 Controller 拒绝，不调用 Worker。

## 行为保持

- `UploadWorker` 的构造参数、上传协议、去重、备份、网络检测、自动清理通知和速率限制保持不变。
- 开始前仍校验三个路径及路径互异性。
- 暂停、恢复、停止、重复文件询问、错误通知、磁盘告警和完成通知保持原有界面行为。
- 停止上传仍只停止由上传任务启动的 FTP 服务器，不影响独立启动的 FTP 服务器。
- 关闭窗口时由 Controller/Service 统一停止 Worker 和线程。

## 验收结果

- `MainWindow` 不导入、不创建且不持有 `UploadWorker` 或上传 `QThread`。
- Controller 使用 Fake Service 完成状态转换和事件快照测试。
- Service 使用 Fake Qt Worker 验证 Worker 创建、QThread 启动、暂停、恢复、停止和完成事件。
- Worker 继续仅通过 Qt 信号输出结果，不操作 UI。
- UI 跨层依赖白名单已经为空。

## 测试结果

- 上传 Service/Controller 单元与线程生命周期测试：`6 passed`。
- 上传、架构和窗口专项测试：`18 passed, 5 subtests passed`。
- 完整测试：`64 passed, 5 subtests passed`。

## 风险

- `UploadWorker` 内部仍使用自身的 Python 后台线程执行扫描、上传与归档；本阶段只迁移其外层 Qt 生命周期，没有重写传输算法。
- 停止后 Worker 的后台循环需要短时间退出；Service 会保留资源引用直至 QThread 完成，并在应用退出时执行有超时的清理。
- 磁盘不足事件目前仍交给 MainWindow 中的清理流程，阶段 6 将继续迁移。

## 遗留问题

- 磁盘扫描、候选文件选择、自动清理和审计日志仍在 View 中。
- `MainWindow` 仍包含较多表单构建和清理界面代码，将在阶段 6、7 继续拆分。

## 下一阶段

执行阶段 6：建立 `CleanupService` 和 `CleanupController`，迁移扫描、删除、自动清理、取消和审计流程。
