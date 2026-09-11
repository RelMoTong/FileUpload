# MVC 重构阶段 4 完成报告

## 修改文件

- `src/models/ftp_settings.py`：新增 FTP 校验、操作结果和服务器事件模型。
- `src/services/ftp_service.py`：封装 FTP 具体协议类，集中配置校验、连接测试和服务生命周期。
- `src/controllers/ftp_controller.py`：协调 FTP 服务、启动来源、事件转换和界面通知。
- `src/repositories/ftp_event_log_repository.py`：持久化独立 FTP 服务器审计日志。
- `src/main.py`：在组合根创建并注入 FTP Controller、Service 和 Repository。
- `src/ui/main_window.py`：移除 FTP 具体类、业务校验、启停编排、事件格式化和日志持久化，仅收集表单并渲染结果。
- `tests/test_ftp_mvc.py`：新增无需 `QApplication` 的 FTP 校验、测试、生命周期、事件和关闭测试。
- `tests/test_ftp_server_logging.py`：改为直接验证 FTP 日志 Repository。
- `tests/test_architecture_boundaries.py`：移除 View 对 FTP 协议层的遗留依赖许可。

## 修改原因

FTP 配置规则、连接测试、具体协议对象和服务器生命周期属于业务与基础设施职责。将它们从 View 中移出后，界面只负责读取控件值、调用 Controller，并显示返回的状态、错误和日志。

## 行为保持

- FTP 服务端配置仍校验主机、端口、账号、密码、共享目录和被动端口范围。
- FTP 客户端仍校验主机、端口、账号、密码和远程路径，并在测试后断开连接。
- 独立启动和上传任务启动的服务器保持不同所有权；停止上传不会停止独立启动的服务器。
- 服务器事件仍显示相同的连接、登录、上传和错误信息，并写入每日独立日志。
- 关闭窗口时统一停止 FTP 资源。

## 验收结果

- `MainWindow` 不再导入或实例化 `FTPProtocolManager`、`FTPServerManager`、`FTPClientUploader`。
- FTP Service 和 Controller 可通过纯 Fake 对象测试，不需要创建主窗口或真实网络连接。
- 架构边界测试已取消 FTP 遗留白名单，防止后续重新引入具体协议依赖。

## 测试结果

- FTP 与架构专项测试：`9 passed`。
- 完整测试：`58 passed, 5 subtests passed`。

## 风险

- 实际 FTP 网络、端口占用、防火墙与 TLS 行为仍依赖运行环境，本阶段使用 Fake 对象验证编排，未占用真实端口。
- FTP 客户端在上传 Worker 内的传输执行仍保留现状，将在阶段 5 随上传生命周期一起迁移。

## 遗留问题

- `MainWindow` 仍直接导入和创建 `UploadWorker`。
- 上传线程生命周期和上传状态转换尚未由 Controller 管理。
- 磁盘扫描与清理逻辑仍在 View 中。

## 下一阶段

执行阶段 5：建立上传 Controller/Service，迁移 Worker 创建、线程生命周期和上传状态转换。
