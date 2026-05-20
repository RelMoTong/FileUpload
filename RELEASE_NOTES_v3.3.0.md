# Version 3.3.0 - 磁盘空间告警与自动清理日志增强

## 发布日期
2026-04-29

## 适用场景
本版本面向客户现场实施，重点增强磁盘爆满、自动清理失败、FTP 远端空间不足等故障场景下的日志可诊断性。

## 主要变更
- SMB 上传写入失败时，如果错误指向磁盘空间不足，会记录 `DISK_FULL`，包含文件名、目标路径、剩余空间百分比和剩余容量。
- FTP 客户端保留最近一次错误，远端返回空间不足、配额不足或 `552` 类错误时，会在上传日志中记录 `FTP_DISK_FULL`。
- 自动清理删除失败时，会记录失败文件路径和异常信息，方便定位文件占用、权限不足或回收站不可用。
- 自动清理执行完成后，如果释放空间不足以达到目标阈值，会记录仍需释放的空间。
- 保留 v3.2.1 的自动清理保存保护：启用自动清理时，未勾选任何可见清理目录则禁止保存历史隐藏目录，降低误清理风险。

## 行为说明
- 自动清理支持多目录配置，可覆盖多个磁盘，但只清理配置中明确列出的目录。
- 自动清理候选文件仍按文件修改时间从旧到新选择，不按 Windows 创建时间排序。
- 上传线程只负责检测磁盘不足并触发清理信号；实际删除由主窗口统一自动清理任务执行。

## 构建产物
- 目录：`dist/ImageUploadTool_v3.3.0/`
- 压缩包：`dist/ImageUploadTool_v3.3.0_win64.zip`
- 可执行文件：`ImageUploadTool_v3.3.0.exe`

## 验证
- `python -m py_compile src\workers\upload_worker.py src\ui\main_window.py src\protocols\ftp.py`
- `python -m unittest tests.test_auto_cleanup_selection tests.test_auto_cleanup_execution tests.test_auto_cleanup_edge_cases`
