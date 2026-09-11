# MVC 重构阶段 8 完成报告

## 结果

MVC 依赖已经收口到唯一组合根 `src/main.py`。Repository、Service、长期运行 Model、Controller 和 `MainWindow` 均由入口显式创建并注入；应用退出由 `LifecycleController` 按固定顺序协调且保证幂等。

## 主要修改

- 新增 `LifecycleController` 和 `LifecycleShutdownResult`，集中协调 View、清理、上传、FTP、运行时与托盘资源的退出。
- `main.py` 显式创建 Repository、Service、`AuthModel`、`UploadRuntimeState`、Controller 和 View，并把具体 MVC 导入延迟到依赖检查成功之后。
- `AuthController` 改为依赖 `AuthBusinessService` 协议，不再导入具体 Service。
- `UploadController` 的运行状态由组合根注入。
- `CleanupService` 退出时取消并等待扫描、删除两个 QThread 后再释放引用。
- `UploadService` 退出时先让 Worker 安全关闭文件与网络线程池，再等待上传 QThread。
- `MainWindow.closeEvent()` 只调用生命周期 Controller；View 负责停定时器以及最终隐藏托盘、关闭本地单实例服务。
- 修正 Qt 主题的中文字体优先级，并把版本标签从错误的 `PyQt` 改为通用 `Qt`。

## 明确的生命周期顺序

启动顺序：依赖检查 → QApplication/单实例 → Repository → Service → Model → Controller → View → Qt 事件循环。

退出顺序：View 定时器 → 自动/手动清理任务 → 上传 Worker、内部线程池与 QThread → FTP 服务与客户端 → 日志/磁盘查询线程池 → 托盘与本地单实例服务。

## 架构守卫

`tests/test_architecture_boundaries.py` 现在验证：

- UI 不导入配置持久化、Controller、Service、Repository、Worker 或 Protocol 实现。
- Model 不导入 Qt View、Controller 或 UI。
- Controller 不导入具体 Service/Repository/UI/Worker/Protocol 或底层 IO 模块，也不直接调用文件 IO。
- `main.py` 创建完整生产对象图，具体 MVC 组件不在其他生产模块实例化。
- 具体 MVC 组件不会在依赖检查之前被提前导入。

`tests/test_lifecycle_mvc.py` 使用 Fake View 和 Fake Service 验证关闭顺序、幂等性以及单个参与者失败后继续释放资源。清理与上传测试补充了实际线程等待和本地 SMB 文件上传/归档验证。

## 验证

- Python 编译：通过。
- 架构、Model、Controller、Service、Repository、真实本地 FTP 和本地 SMB 集成测试：通过。
- 完整自动化回归：`78 passed, 5 subtests passed`。
- `git diff --check`：通过。
- Windows Qt 桌面渲染：通过；中文、三栏布局、状态卡、日志区和滚动区域均正常。
- 托盘回归：创建成功，窗口关闭前可见，生命周期退出后不可见。
- GUI 证据：[MVC_PHASE8_GUI_SMOKE.png](MVC_PHASE8_GUI_SMOKE.png)。

## 风险与发布注意事项

- 上传 QThread 在安全停止超过 3 秒后仍保留强制终止兜底，以避免应用无限挂起；生产网络长期阻塞时需要继续关注日志。
- 测试使用本地目录和回环 FTP 服务，不替代发布现场对真实 SMB 权限、防火墙、TLS 证书和远程 FTP 的连通性检查。
