# MVC 重构阶段 0 基线

## 1. 基线范围

本基线记录 MVC 重构开始前的自动化测试、关键业务流程、配置格式、`MainWindow` 职责以及现有跨层依赖。

阶段 0 只增加文档和架构守卫，不改变程序运行逻辑。

## 2. 自动化测试基线

基线日期：2026-08-07

执行命令：

```powershell
pytest -q --basetemp=<工作区内唯一临时目录> -p no:cacheprovider
```

基线结果：

```text
32 passed, 5 subtests passed
```

说明：沙箱账户不能访问用户系统临时目录，因此测试时必须通过 `--basetemp` 使用工作区内的唯一临时目录。这是测试环境限制，不是业务失败。

## 3. 人工回归测试清单

以下场景需要在带桌面的 Windows 环境中执行。阶段 0 只记录步骤，尚未执行人工回归。

### 3.1 启动与单实例

1. 执行 `python -m src.main`。
2. 确认主窗口可以显示，默认角色为访客。
3. 再次启动程序。
4. 确认没有创建第二个主窗口，已有窗口被唤醒。

### 3.2 配置保存与恢复

1. 以管理员身份登录。
2. 修改源目录、目标目录、备份目录和上传选项。
3. 保存配置并关闭程序。
4. 重新启动程序。
5. 确认保存的配置被恢复，新增默认字段也可以正常补齐。

### 3.3 上传生命周期

1. 配置可访问的源目录和目标目录。
2. 点击开始上传，确认状态变为运行中。
3. 点击暂停，确认当前任务进入暂停状态。
4. 点击恢复，确认任务继续。
5. 点击停止，确认线程停止且界面恢复可操作状态。
6. 上传过程中关闭程序，确认后台任务和线程能够有序退出。

### 3.4 权限

1. 访客状态下确认只有登录操作可用。
2. 普通用户登录后确认可以操作上传，但不能使用管理员专属功能。
3. 管理员登录后确认配置保存、独立 FTP 服务和磁盘清理可用。
4. 退出登录后确认权限立即恢复为访客状态。

### 3.5 磁盘清理

1. 以管理员身份打开磁盘清理窗口。
2. 选择目录和文件格式后执行扫描。
3. 确认扫描结果、文件数量和大小正确。
4. 分别验证回收站和永久删除模式。
5. 取消正在运行的扫描或清理，确认任务安全停止。
6. 确认审计日志记录成功和失败结果。

### 3.6 托盘与退出

1. 最小化程序并确认托盘图标可用。
2. 从托盘执行开始、暂停和停止操作。
3. 从托盘恢复主窗口。
4. 退出程序并确认没有残留后台任务。

## 4. 协议模式预期行为

| 模式 | 预期行为 |
|---|---|
| `smb` | 文件复制到目标共享目录；大文件可以使用断点续传；不要求 FTP 客户端配置 |
| `ftp_client` | 文件上传到远程 FTP 目录；使用 FTP 超时、重试、被动模式和 TLS 配置；不以本地 SMB 目标写入作为成功条件 |
| `both` | 同一文件必须分别完成 SMB 和 FTP 上传；重试时保留各协议已经成功的状态，避免重复上传 |
| 独立 FTP 服务器 | 可以在 SMB 上传模式之外单独启停；使用共享目录接收外部客户端上传；把连接和传输事件发送到日志层 |

所有模式都必须保持现有的过滤、备份、去重、暂停、停止、网络监控、磁盘阈值和错误记录行为。

## 5. 配置字段基线

配置文件继续使用 JSON 字典。重构期间不得直接改变下列键名和外部值。

### 5.1 顶层字段

```text
source_folder
target_folder
backup_folder
enable_backup
upload_interval
monitor_mode
disk_threshold_percent
retry_count
disk_check_interval
filter_jpg
filter_png
filter_bmp
filter_gif
filter_raw
auto_start_windows
auto_run_on_startup
show_notifications
limit_upload_rate
max_upload_rate_mbps
enable_deduplication
hash_algorithm
duplicate_strategy
network_check_interval
network_auto_pause
network_auto_resume
enable_auto_delete
auto_delete_folder
auto_delete_folders
auto_delete_threshold
auto_delete_target_percent
auto_delete_keep_days
auto_delete_check_interval
upload_protocol
current_protocol
enable_ftp_server
language
enable_resume
resume_min_size_mb
ftp_server
ftp_client
users
```

### 5.2 FTP 服务端字段

```text
host
port
username
password
password_encrypted
shared_folder
enable_passive
passive_ports_start
passive_ports_end
enable_tls
max_connections
max_connections_per_ip
```

### 5.3 FTP 客户端字段

```text
host
port
username
password
password_encrypted
remote_path
timeout
retry_count
passive_mode
enable_tls
```

内部可以使用 `dataclass` 和枚举，但保存时必须转换回以上兼容格式。

## 6. MainWindow 职责基线

| 职责 | 当前主要方法 |
|---|---|
| UI创建与主题 | `_build_ui`、`_apply_theme`、各类 `*_card` 方法 |
| 配置加载与保存 | `_load_config`、`_save_config`、`_write_config_payload` |
| 登录与权限 | `_show_login`、`_show_change_password`、`_logout`、`_update_ui_permissions` |
| FTP管理 | `_test_ftp_server_config`、`_toggle_ftp_server_only`、`_test_ftp_client_connection` |
| 上传生命周期 | `_on_start`、`_on_pause_resume`、`_on_stop`、`_on_worker_finished` |
| 上传状态渲染 | `_on_stats`、`_on_progress`、`_on_file_progress`、`_on_network_status` |
| 磁盘清理 | `_show_disk_cleanup`、`_auto_cleanup_task` 及候选文件选择方法 |
| 日志与通知 | `_append_log`、`_write_log_to_file`、`_show_notification` |
| 托盘与退出 | `_init_tray_icon`、`_quit_application`、`closeEvent` |
| 单实例唤醒 | `_setup_single_instance_server`、`_handle_wakeup_request` |

迁移过程中每移走一项职责，都应删除对应的旧跨层依赖并增加 Controller/Service 测试。

## 7. 架构依赖基线

阶段 0 已知的 UI 跨层导入仅允许以下三个历史入口：

```text
src/ui/main_window.py -> src.config
src/ui/main_window.py -> src.protocols.ftp
src/ui/main_window.py -> src.workers.upload_worker
```

`tests/test_architecture_boundaries.py` 会阻止新增 UI 跨层导入。迁移完成后必须逐项删除允许列表，阶段 8 时允许列表必须为空。

同时从阶段 0 开始执行以下规则：

- `src/models` 不得导入 PySide6、PyQt5、`src.ui`、`src.controllers`。
- `src/controllers` 不得导入具体 UI、Worker 或协议实现。
- 新增代码不得扩大现有 UI 跨层依赖集合。

## 8. 阶段 0 验收结论

- 自动化测试基线：已通过。
- 人工回归步骤：已记录，等待有桌面的发布回归环境执行。
- 协议模式预期行为：已记录。
- 配置字段：已记录。
- `MainWindow` 职责：已记录。
- 架构依赖守卫：已建立。
- 业务行为变化：无。

