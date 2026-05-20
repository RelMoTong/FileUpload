# Version 3.3.2 - 生命周期稳定性与 FTP 测试修复

## 发布日期
2026-05-19

## 适用场景
本版本面向现场使用中的稳定性修复，重点降低网络异常、FTP 连接取消、窗口退出和独立 FTP 服务运行时的隐藏卡死或资源泄漏风险。

## 主要变更
- 新版本首次启动时，如果当前目录没有 `config.json`，会自动从同级旧版本 `ImageUploadTool_v*` 目录迁移配置，减少现场更新后的重复配置工作。
- 网络路径检测兜底逻辑恢复为可终止的子进程超时方案，避免不可达 UNC/映射盘路径让网络监控线程长期阻塞。
- 上传任务停止流程改为有界等待，窗口关闭时不再对文件操作线程池执行无界 `shutdown(wait=True)`。
- FTP 客户端增加连接中和断开请求状态，防止并发连接、停止后又变成已连接，以及临时 FTP 会话泄漏。
- 独立 FTP 服务在真正退出程序时显式停止；最小化到托盘时仍保持后台运行。
- FTP 基础测试和综合测试改用动态端口，补充生命周期回归测试，避免固定端口冲突导致误报。

## 构建产物
- 目录：`dist/ImageUploadTool_v3.3.2/`
- 压缩包：`dist/ImageUploadTool_v3.3.2_win64.zip`
- 可执行文件：`ImageUploadTool_v3.3.2.exe`

## 验证
- `pytest -q`
- `pytest -q tests/test_config_manager.py`
- `git -c safe.directory=E:/Python/文件上传 diff --check`
- `python -m py_compile src\workers\upload_worker.py src\protocols\ftp.py src\ui\main_window.py src\config.py src\core\utils.py`

## 测试结果
- 完整测试：`100 passed, 1 skipped, 17 warnings`
- warnings 来自既有测试风格：部分脚本式测试返回 `bool`，以及一个带 `__init__` 的测试类未被 pytest 收集；本版本不把这些 warning 作为发布阻塞项。
