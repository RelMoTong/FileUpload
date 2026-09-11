# MVC 重构阶段 7 完成报告

## 结果

`MainWindow` 已从集中式巨型 View 拆分为独立对话框和面板，并移除了日志文件、磁盘空间和 Windows 启动项等基础设施操作。主窗口现在主要负责窗口布局、读取控件输入、转发用户意图和调用 `render_*()` 更新界面。

## View 拆分

- `src/ui/dialogs/login_dialog.py`：登录输入和 `login_requested` 语义事件。
- `src/ui/dialogs/change_password_dialog.py`：改密输入和 `change_requested` 语义事件。
- `src/ui/dialogs/disk_cleanup_dialog.py`：磁盘清理表单、列表、确认和进度渲染。
- `src/ui/panels.py`：上传目录、上传设置、运行状态和运行日志面板。
- `src/ui/widgets.py`：只保留通用 `Toast`、`ChipWidget` 和 `CollapsibleBox` 控件。

## 迁出的基础设施职责

- `DailyLogRepository`：初始化和追加每日上传日志。
- `WindowsStartupRepository`：读写和删除 Windows Run 启动项。
- `RuntimeService`：启动项版本修复、路径校验、网络盘识别和磁盘剩余空间查询。
- `RuntimeController`：异步日志、磁盘查询、启动项命令和退出时执行器回收。
- `SettingsController`：FTP 密码兼容读取和受保护字段编码。

## 语义事件和渲染入口

- 上传按钮使用 `_request_start_upload()`、`_toggle_upload_pause()` 和 `_request_stop_upload()`。
- 配置保存使用 `_request_save_settings()`，日志清理使用 `clear_log_view()`。
- Controller 上传事件统一进入 `render_upload_stats()`、`render_upload_progress()`、`render_file_progress()`、`render_network_status()`、`render_upload_finished()` 和 `render_upload_error()`。
- 权限、状态、协议和磁盘显示分别收敛到 `render_permissions()`、`render_status_pill()`、`render_protocol_status()` 和 `render_disk_space()`。

## 架构约束

- `MainWindow` 不再导入 `os`、`shutil`、`winreg`、`json`、`ctypes`、`socket`、`tempfile` 等基础设施模块。
- `MainWindow` 不调用 `open()`、`Path.open()`、`Path.mkdir()`、`Path.read_text()` 或 `Path.write_text()`。
- UI 仍只依赖模型和本地 Gateway 协议，不导入具体 Controller、Service、Repository 或 Worker。
- 登录、改密和四个上传面板均有独立实例化测试。

## 行为保持

- 三栏布局、可拖动分隔条、统一主题和低分辨率响应式尺寸保持不变。
- 登录、权限菜单、FTP server-only、上传控制、磁盘清理和托盘入口保持原有调用链。
- 日志仍按日期写入，磁盘查询仍异步执行，启动项仍保留高版本保护和命令引号修复。

## 验证

- 对话框和面板独立测试：`4 passed`。
- Runtime 与架构专项测试：`18 passed, 5 subtests passed`。
- Python 模块编译：通过。
- 完整回归：`72 passed, 5 subtests passed`。

## 风险与后续

- 为兼容现有菜单与测试，`_update_ui_permissions()`、`_update_status_pill()` 和 `_update_protocol_status()` 暂时保留为 `render_*()` 的薄别名；阶段 8 将通过最终架构检查决定是否删除兼容入口。
- 上传设置面板继续把控件引用暴露给主窗口，以保持既有配置加载和输入收集行为；它不执行持久化或网络操作。
- 阶段 8 将收口组合根、依赖方向、静态架构检查、文档和最终回归。
