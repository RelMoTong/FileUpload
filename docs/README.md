文档概要
========
本仓库仅保留精简文档：阅读本文件了解架构/使用，版本变更见 `docs/CHANGELOG.md`。

项目功能
--------
- GUI 上传工具（PySide6/PyQt5），带单实例守护与依赖检查。
- 磁盘清理对话框：选择文件夹与文件类型，异步扫描/删除，支持回收站删除（send2trash 可选），自动清理阈值配置。
- 国际化：`zh_CN` / `en_US`，文本集中于 `src/core/i18n.py`，UI 使用翻译键获取。
- 打包：PyInstaller 规格文件 `ImageUploadTool_v3.1.1.spec` 读取 `src.__version__`，产物输出到 `dist/ImageUploadTool_v<version>/`。

运行与打包
----------
- 运行：`python -m src.main` 或直接使用打包后的 exe。
- 打包：`pyinstaller ImageUploadTool_v3.1.1.spec --clean`，生成结果位于 `dist/`。

核心模块速览
------------
- `src/main.py`：依赖检查（PySide6/PyQt5 必需，pyftpdlib 可选）、单实例（QLocalSocket + QSharedMemory）、应用启动。
- `src/core/i18n.py`：翻译存储与语言切换，新增文本请添加键值（如 `disk_cleanup_*`）。
- `src/ui/widgets.py`：自定义控件；磁盘清理对话框用 QThread 异步扫描/删除，支持回收站，全面适配 i18n。
- `ImageUploadTool_v3.1.1.spec`：包含资源/配置/版本，显式列出 Qt 与项目模块的 hiddenimports。

国际化提示
----------
- 界面文本请通过 `from src.core.i18n import t/tr` 获取，避免硬编码。
- 为新功能添加翻译时，同时补充 `zh_CN` 与 `en_US`，可复用通用键（如 `unit_day/unit_second`、`word_yes/word_no`）。

清理建议
--------
- 文档仅保留 `docs/README.md`、`docs/CHANGELOG.md`；历史/零散说明已合并删除。
- 打包产物保留最新 `dist/ImageUploadTool_v<version>/`，旧版可归档到仓库外。
