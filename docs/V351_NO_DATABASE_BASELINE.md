# v3.5.1 无数据库稳定性改造基线

## 基线身份

- 建立日期：2026-09-11
- 改造分支：`codex/v3.5.1-no-database-stability`
- 改造前 Git 基点：`77b63c8094ea7bd63ed26fd01cc34220260bdd05`
- 应用版本：`3.5.1`
- 实施主清单：`v3.5.1_无数据库稳定性改造实施方案.html`

当前工作树包含此前尚未提交的 v3.5.1、MVC 分层、清理、FTP、测试和发布审查成果。它们作为用户既有工作整体保留，并在本分支的首个本地提交中固化；本次改造不回滚或覆盖这些成果。

以下目录属于测试或构建运行产物，不纳入基线提交：`build/`、`dist/`、`.pytest*`、`.test_runtime/`、`.review_v351/`、`.release-*`、`_codex_tmp_*`、`codex_pytest_*`、`logs/`、`__pycache__/`。

## 实施前验证

- `pytest -q tests -p no:cacheprovider --basetemp="$env:TEMP\image-upload-v351-no-db-plan-audit"`
  - 结果：`159 passed, 5 subtests passed in 26.08s`
- `pyright`
  - 结果：`0 errors, 0 warnings, 0 informations`
- `git diff --check`
  - 结果：无空白错误；仅有 Git 的 LF/CRLF 工作区提示。

## 已确认的冻结原因

- `ConfigManager.DEFAULT_CONFIG`、`UploadSettings` 与 `CleanupSettings` 的去重、自动清理默认值已为关闭。
- 现场 `config.json` 仍启用了自动清理，必须在 GATE-00 中强制关闭。
- 当前组合根仍构造 SQLite 清理索引，发布探针仍创建 SQLite 清理和去重文件。
- 在 P2-01 完成前，自动清理和跨文件持久化去重不得从 UI、配置迁移或运行请求重新启用。

## 临时现场运行限制

- 仅允许有人值守试运行。
- 优先使用 SMB 单通道。
- 必须开启备份。
- 自动清理与跨文件去重保持关闭。
- 源目录、目标目录、备份目录必须完全分离。
- 出现误报成功、重复上传、错移、误删或状态损坏时立即停止运行并保留日志及状态文件。

## 回退方法

GATE-00 完成后创建本地标签 `v3.5.1-pre-no-db-baseline` 指向首个基线提交。后续任务均在该提交之后进行；需要回退时，新建分支指向该标签进行恢复和核对。禁止使用 `git reset --hard` 覆盖现场或用户工作，运行配置和源文件须先独立备份。
