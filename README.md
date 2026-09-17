# 图片异步上传工具 v3.5.1（无数据库候选版）

当前版本已完成开发机自动化、独立虚拟环境构建和隔离打包 smoke，但在真实 SMB/FTP、断电恢复和 24–72 小时长稳完成前仍为 **NO-GO**。只允许有人值守测试，不得直接替换现场正式版本。

## 当前能力

- SMB、FTP/FTPS 客户端上传和内置 FTP/FTPS 服务端。
- 上传与归档分离；文件代际身份、任务状态机和原子 JSON 状态保护源文件。
- FTP 先上传唯一临时对象，校验远端大小后 rename 为最终名；失败不误报成功。
- 无 SQLite/数据库生产链路；运行状态只使用有界 JSON/JSONL、日志和 Windows 注册表。
- 管理员与普通用户权限；默认口令可直接登录，仅提示风险，不强制改密。
- 管理员可启用无数据库自动清理；智能去重仍强制关闭。

## 发布包

- 候选包：`release_candidate/ImageUploadTool_v3.5.1_no_database_release.zip`
- 候选哈希：以同目录 `.zip.sha256` 侧车文件为准。
- 可执行文件：`ImageUploadTool_v3.5.1.exe`，无需在被测机器安装 Python。
- 回退包：`release_candidate/ImageUploadTool_v3.4.2_rollback.zip`。它基于最后一个可复现稳定提交 `1322bf3`，只用于紧急独立回退，不属于无数据库版。

解压必须使用全新目录，不得覆盖旧版、现场 `config.json`、日志、状态目录或任何源文件。首次启动前候选目录内不得有 `config.json`、`logs/`、`data/`、`resume_data/`。

## 登录与密码

- 管理员默认口令：`Tops123`
- 普通用户默认口令：`123`
- 默认口令登录后业务控件按角色立即启用；不会强制弹出改密框。
- 改密由权限控制器直接持久化并读回校验；普通配置保存不会覆盖新密码。
- 密码使用带独立盐的 PBKDF2 哈希保存；旧 SHA-256 哈希在成功登录后安全升级。

现场仍应由管理员妥善更换并保管口令。忘记密码时不要直接编辑哈希；先备份 `config.json`，再按现场变更流程恢复默认账号或重新配置。

## 自动磁盘清理

- 仅管理员可配置和启用；上传任务运行时暂停定时清理。
- 所有清理目录必须存在、位于同一磁盘，且不得与源、目标、备份目录嵌套。
- 按配置格式和修改时间全局最旧优先，每批有界扫描，每个文件操作前复核身份。
- 自动模式只允许移入回收站；回收站不可用、审计失败、磁盘 API 失败或文件已变化时安全停止，绝不降级为永久删除。
- Windows 本地回收站通常仍占用原磁盘空间。若可用空间没有增加，程序会停止并记录“回收站未释放空间”；当前版本不会自动清空回收站或自动永久删除。

## 开发与验证

```powershell
python -m venv .p4_02_clean_venv
.\.p4_02_clean_venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.p4_02_clean_venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider --basetemp=.pytest_tmp_clean
.\.p4_02_clean_venv\Scripts\pyinstaller.exe --clean -y ImageUploadTool.spec
.\.p4_02_clean_venv\Scripts\python.exe tools\build_v351_release_candidate.py --force
.\.p4_02_clean_venv\Scripts\python.exe tools\build_v351_release_candidate.py --verify-only
```

源码入口：`python -m src.main`。程序入口和组合根是 `src/main.py`；模型、控制器、服务、仓储、UI、Worker 和协议层的边界见 `docs/MVC_ARCHITECTURE.md`。

## 现场必须完成

- 默认口令、改密落盘、退出重登和业务按钮权限。
- 真实 SMB/FTP/FTPS、最大现场文件、双协议部分失败和服务端重启。
- 同路径多代际、断网恢复、人工暂停、强杀/机器重启。
- 自动清理磁盘压力、回收站失败、路径失效和退出取消。
- 至少 24 小时长稳、逐文件对账、独立回退演练和双负责人签署。

按 [现场手工测试清单](docs/V351_MANUAL_TEST_CHECKLIST.md) 执行，并从 `docs/field_evidence.template.json` 创建现场证据。机器校验输出 `GO` 前不得放行。

## 文档

- [运维手册](docs/V351_OPERATIONS_MANUAL.md)
- [发布验证报告](docs/V351_RELEASE_VERIFICATION.md)
- [现场验收表](docs/V351_FIELD_ACCEPTANCE.md)
- [现场证据格式](docs/V351_FIELD_EVIDENCE_SCHEMA.md)
- [回退说明](docs/V351_ROLLBACK_README.md)
- [无数据库改造实施方案](v3.5.1_无数据库稳定性改造实施方案.html)
