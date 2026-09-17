# v3.5.1 无数据库版发布验证报告

## 当前结论

本地代码、独立环境构建、候选包校验和隔离启动门禁已通过。真实 SMB/FTP、现场最大文件、断网/服务端重启、强杀/断电、磁盘压力、24–72 小时长稳、逐文件对账、真实回退演练和双签尚未执行，因此当前结论仍为 **NO-GO**。

## 冻结输入与制品

- Python：3.13.5
- 直接依赖锁：`requirements.lock.txt`
- 独立构建环境：`.p4_02_clean_venv`
- PyInstaller 输出：`dist/ImageUploadTool_v3.5.1`
- 现场候选：`release_candidate/ImageUploadTool_v3.5.1_no_database_release.zip`
- 候选 ZIP 哈希：以同目录 `.zip.sha256` 侧车文件为准
- 候选 EXE SHA-256：`6E67CE0E30F52A191E141465A72F2F802DFE0CA08B00563F607B84010E206535`
- 回退包：`release_candidate/ImageUploadTool_v3.4.2_rollback.zip`
- 回退 ZIP SHA-256：`B2AD7AF275DA55BB59D1719BDB337EBAEC67C0EE36D2EAA0E1DEEC90EB4BDD84`
- 回退 EXE SHA-256：`852182EF323463D713FFAF57B2B19A920E40133B56130D60401A703B16C36073`

回退包基于 Git 最后一个可复现稳定节点 `1322bf3`。仓库没有可重建的 v3.5.0 提交或现成制品；v3.4.2 回退构建仅应用 `docs/V342_ROLLBACK_PACKAGING_PATCH.diff` 中的 Qt/ICU 打包兼容补丁，不修改旧业务代码，且不是无数据库版。

## 2026-09-14 自动化证据

| 检查 | 结果 | 命令/说明 |
| --- | --- | --- |
| 主环境完整回归 | `184 passed, 5 subtests passed` | `pytest -q tests -p no:cacheprovider --basetemp=.pytest_tmp_codex` |
| 独立环境完整回归 | `183 passed, 1 skipped, 5 subtests passed` | 锁文件从零安装；跳过项为当前 Windows 权限不允许创建符号链接 |
| 100 次并发认领 | 通过 | 状态机用例内部执行 100 轮、每轮 8 线程并发，同一文件代际始终只有一次认领成功 |
| 10 万候选流式扫描 | 通过 | 100,000 个候选只保留 100 个最旧批次，未建立数据库或全量索引 |
| 清理安全专项 | 通过 | 回收站失败、永久删除授权/审计、身份变化、磁盘 API 失败、同管线候选、无数据库产物 |
| FTP 提交专项 | 通过 | 临时对象、远端大小、rename、断线、rename/校验失败清理、源文件保护 |
| 原子状态/归档专项 | 通过 | replace 故障恢复旧完整记录、坏文件隔离、记录上限、归档日志失败会话内恢复且不重复上传 |
| UNC/暂停专项 | 通过 | 后台探测超时、代次取消、人工与网络暂停组合不互相覆盖 |
| 重点 Pyright | `0 errors, 0 warnings` | 安全删除、清理服务、稳定门禁和候选/回退/长稳/现场校验工具 |
| 源码编译 | 通过 | `python -m compileall -q src tools tests` |
| 独立环境构建 | 通过 | `.p4_02_clean_venv\Scripts\pyinstaller.exe --clean -y ImageUploadTool.spec` |
| 候选目录/ZIP 二次校验 | 通过 | 清单逐文件哈希、ZIP CRC/成员/哈希、运行产物、数据库路径和 PyInstaller 内嵌模块均通过 |
| 候选隔离 smoke | exit 0；9/9 通过 | 打包依赖、资源/语言/版本、JSON 状态、待归档日志、FTPS 控制/数据握手和线程释放；无数据库文件与 `startup-error.log` |
| 回退包启动预检 | 通过 | 独立副本隐藏运行 5 秒仍存活，无 `startup-error.log`；该结果不替代现场回退演练 |
| 现场证据校验器 | 默认 `NO-GO` | 缺真实端点、附件、24 小时长稳、守恒对账、回退与双签时拒绝放行 |

## 无数据库门禁

- `src/` 无 `sqlite3`、`cleanup_index`、`dedup_index`、`.db`、`.sqlite3` 或 SQLAlchemy 生产命中。
- `ImageUploadTool.spec` 只在 `excludes` 中列出 `sqlite3`、`_sqlite3`、`sqlalchemy`，用于阻止打包。
- 候选包目录、ZIP 成员和 PyInstaller 内嵌模块扫描均无数据库命中。
- 全新候选目录隔离启动后只生成配置、日志和 JSON/JSONL 状态，不生成数据库文件。
- 智能去重保持关闭；自动清理使用即时流式扫描和有界最旧批次，不依赖跨运行索引。

## 权限与自动清理范围

- 管理员和普通用户默认口令登录不再强制改密，权限只由角色与运行状态决定。
- 改密的唯一持久化真源是 `AuthController`；写入后读回校验，普通设置保存保留磁盘中的用户凭据。
- 自动清理旧冻结门禁已删除，仅管理员可启用；保存后定时器生效，上传运行期间暂停。
- 自动清理只允许回收站。回收站、磁盘 API、审计或身份校验失败时安全停止，绝不改为永久删除。
- Windows 本地回收站通常不立即释放原磁盘空间；空间未增加时程序停止并记录“回收站未释放空间”。自动清空回收站和自动永久删除不在本候选范围内。

## 现场待测

实际步骤见 `docs/V351_MANUAL_TEST_CHECKLIST.md`，机器数据模板见 `docs/field_evidence.template.json`。现场完成后执行：

```powershell
python field_tools\validate_v351_field_acceptance.py `
  --evidence .\field_evidence.json `
  --candidate-zip .\ImageUploadTool_v3.5.1_no_database_release.zip `
  --output .\field_evidence_validation.json
```

只有输出 `GO` 且开发、现场负责人共同签署后，才能把候选改为正式发布。任何误报成功、重复上传、错移/误删、状态损坏、磁盘检查失败或未解释账目差额都必须立即停机并保持 `NO-GO`。
