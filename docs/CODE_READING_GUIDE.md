# 代码阅读路径指引（ImageUploadTool v3.5.1）

> 目标：用最短路径建立对整套代码的心智模型，知道「程序怎么跑起来 → 一次上传怎么走完 → 状态怎么落盘 → 出问题去哪儿查」。

---

## 0. 一句话架构结论

项目是**严格分层 MVC**：`src/main.py` 是生产代码中**唯一的组合根**，View 只认「Gateway 协议」接口，Controller 不碰任何 IO，Service 承担业务与线程生命周期，Repository 独占持久化边界。运行期持久化只用 **JSON/JSONL + 日志 + Windows 注册表**，不存在 SQLite/数据库链路。

数据流两个方向：

```
【下行请求】 用户操作 → View → Controller(Gateway) → Service → Repository / Worker / Protocol
【上行事件】 Worker/Service 事件 → Controller 更新 Model → View.render_*()
```

---

## 1. 目录职责总表

`src/` 共约 **19,700 行 / 60 个模块**。

| 目录 | 行数占比 | 职责 | 关键文件（行数） |
|---|---|---|---|
| `src/` 根 | 736 | 组合根 + 旧配置兼容层 | `main.py` (475)、`config.py` (261) |
| `src/models/` | ~1,100 | 纯数据：dataclass / Enum / 请求 / 结果 / 运行状态 | `__init__.py` (87, 全量导出)、`upload_task.py` (81)、`app_state.py` (59)、`settings.py` (52) |
| `src/controllers/` | ~1,100 | 协调 View 意图、调 Service、维护 Model 状态 | `lifecycle_controller.py` (381)、`upload_controller.py` (216) |
| `src/services/` | ~2,300 | 业务规则 + Qt 线程/Worker 生命周期 | `cleanup_service.py` (896)、`ftp_service.py` (412)、`upload_service.py` (395) |
| `src/repositories/` | ~440 | 唯一持久化边界：JSON、日志、清理审计、启动项 | `pending_archive_repository.py` (163)、`runtime_repository.py` (127) |
| `src/ui/` | ~6,900 | 输入收集、展示、事件转发（最大的层） | `main_window.py` (3728)、`disk_cleanup_dialog.py` (2079)、`panels.py` (718) |
| `src/workers/` | 2,466 | 上传主循环与线程池（**业务核心所在**） | `upload_worker.py` (2455) |
| `src/protocols/` | 1,245 | FTP/FTPS 客户端与服务端实现 | `ftp.py` (1234) |
| `src/core/` | ~2,400 | 通用基础设施：原子存储、续传、身份、i18n | `i18n.py` (1462)、`resume_manager.py` (546) |

辅助目录：

- 历史测试源码已从精简仓库移除；需要回归时从删除测试前的提交临时提取到隔离目录，绝不放回仓库。
- `tools/` — 打包与验收工具：`build_v351_release_candidate.py`、`validate_v351_field_acceptance.py`、`release_soak_test.py`。
- `docs/` — 架构与验收文档，**`MVC_ARCHITECTURE.md` 是权威架构说明**。
- `release_candidate/`、`dist/`、`build/` — 产物目录，**阅读代码时直接跳过**。

---

## 2. 依赖规则（读代码时的「红线」）

| 层 | 允许依赖 | 明确禁止 |
|---|---|---|
| View (`ui/`) | Model、View 本地声明的 Gateway Protocol | 导入 Controller / Service / Repository / Worker / Protocol 的**实现** |
| Model (`models/`) | 仅标准库 | 依赖 Qt View、Controller、UI |
| Controller | Model、抽象 Protocol | 具体 UI/Service/Repo/Worker/Protocol；**不执行任何文件、注册表、网络 IO** |
| Service | Repository、Worker、Protocol | 直接操作 View |
| Repository | 文件/日志/注册表 API | 业务规则 |

> 关键设计：`src/ui/main_window.py` 顶部定义了 6 个 `Protocol`（`SettingsGateway` / `AuthGateway` / `FTPGateway` / `UploadGateway` / `CleanupGateway` / `RuntimeGateway` / `LifecycleGateway`）。**这就是 View 与 Controller 之间的契约**，读 View 时先读这些协议定义，比读 3728 行实现高效得多。

---

## 3. 逐步阅读清单

### 阶段 1 — 入口与装配（约 700 行，1 小时）

| 顺序 | 文件 | 读什么 | 为什么 |
|---|---|---|---|
| 1.1 | `README.md` | 版本状态、能力边界、现场约束 | 先知道这版是 NO-GO 候选版，避免误以为是稳定版 |
| 1.2 | `docs/MVC_ARCHITECTURE.md` | 分层规则、启动/退出顺序、扩展规则 | **全文精读**，是整个仓库的骨架说明 |
| 1.3 | `src/main.py` | `main()` 的函数体 | 看对象图装配顺序：Repo → Service → Model → Controller → MainWindow |
| 1.4 | `src/main.py` | `check_dependencies()`、`wakeup_existing_instance()` | 单实例机制（QLocalSocket + QSharedMemory）与启动前置检查 |
| 1.5 | `src/main.py` | `run_packaged_release_probe()` | 可先跳过，只在打包 smoke 时执行；知道它存在即可 |
| 1.6 | `src/core/utils.py` | `get_app_dir` / `get_resource_path` / `get_app_version` | 所有路径与版本号的唯一来源 |

**读完应能回答**：程序起来做了哪 8 件事？配置目录在哪？为什么必须有 `IMAGE_UPLOAD_SMOKE_TEST=1` 才会跑探针？

---

### 阶段 2 — 数据契约（约 700 行，0.5 小时）

先读数据，再读流程，后面所有代码都会变得「有类型」。

| 顺序 | 文件 | 读什么 |
|---|---|---|
| 2.1 | `src/models/__init__.py` | 全量导出清单 —— 一份天然的「业务名词表」 |
| 2.2 | `src/models/app_state.py` | `UploadStatus` / `NetworkStatus` / `UploadProtocol` / `DuplicateStrategy` / `UserRole` 五个枚举 |
| 2.3 | `src/models/upload_task.py` | `UploadTaskRequest`(28 字段)、`UploadCommandResult`、`UploadRuntimeState` |
| 2.4 | `src/models/settings.py` | `ApplicationSettings` 聚合结构 + `from_config`/`to_config` 无损转换 |
| 2.5 | `src/models/_conversion.py`、`src/models/stability.py` | 未知字段保留策略、**稳定性功能冻结**（去重/自动清理默认关闭） |
| 2.6 | `src/models/cleanup_task.py`、`ftp_settings.py`、`auth_model.py` | 其余三条业务线的请求/结果模型 |

**读完应能回答**：一次上传任务需要哪些参数？「功能冻结」在哪里生效、为什么去重默认关闭？

---

### 阶段 3 — 基础设施层 Core（约 800 行，1 小时）

这一层是「无数据库」方案的真正底子，**建议逐个精读，都是小文件、高复用**。

| 顺序 | 文件 | 行数 | 核心机制 |
|---|---|---|---|
| 3.1 | `src/core/atomic_json_store.py` | 205 | 写临时文件 → fsync → 原子替换 → 保留 `.bak` → 损坏文件隔离。所有 JSON 状态的地基 |
| 3.2 | `src/core/file_identity.py` | 105 | **文件代际身份**（路径 + 大小 + mtime + file_id），解决「同名文件换了内容」的误判 |
| 3.3 | `src/core/pause_state.py` | 41 | 可组合暂停：`{manual, network, disk, stopping}` 四个原因互不覆盖 |
| 3.4 | `src/core/file_task_registry.py` | 161 | `FileTaskState` 十态机，防止扫描器/重试器/归档线程并发抢占同一代际 |
| 3.5 | `src/core/safe_deletion.py` | 126 | **fail-closed 删除策略**：只允许进回收站，回收站不可用就安全停止，绝不降级为永久删除 |
| 3.6 | `src/core/resume_manager.py` | 546 | `ResumeManager` + `ResumableFileUploader`，断点续传记录与孤儿 `.part` 清理 |
| 3.7 | `src/core/session_dedup_cache.py` | 37 | 会话级去重缓存 |
| 3.8 | `src/core/i18n.py` | 1462 | 体量大但结构极简单，**只查 `TRANSLATIONS` 与 `t()` 用法，不要通读** |

**读完应能回答**：进程被强杀后状态如何恢复？为什么删除操作不会误删？

---

### 阶段 4 — 三条业务主链路（约 4,000 行，3–4 小时）

按「先看浅层接口、再钻深层实现」的节奏读。

#### 4A. 上传链路（最重要，占全项目核心复杂度）

| 顺序 | 文件/位置 | 读什么 |
|---|---|---|
| 1 | `src/controllers/upload_controller.py` | `_ALLOWED_TRANSITIONS` 状态机、`start/pause/resume/stop`、`_handle_service_event` |
| 2 | `src/controllers/upload_controller.py` | 顶部 `UploadLifecycleService` Protocol —— Controller 眼中的 Service 长什么样 |
| 3 | `src/services/upload_service.py` | `validate_request` → `start`：Worker 构造、`moveToThread`、11 条 QueuedConnection 信号连线 |
| 4 | `src/services/upload_service.py` | `_UploadEventBridge` —— 后台信号如何跨线程安全回到 UI 线程 |
| 5 | `src/services/upload_service.py` | `shutdown` + `_schedule_release_check` —— 优雅停止与资源回收 |
| 6 | `src/workers/upload_worker.py` | 只看骨架：`__init__` → `start()` → `_run()` → `stop()`，**先不要读文件操作细节** |
| 7 | `src/workers/upload_worker.py` | 核心循环片段：`_get_image_files` 扫描 → `_process_retry_queue` 重试 → `_upload_file_by_protocol` 分发 |
| 8 | `src/workers/upload_worker.py` | 归档子链路：`_archive_worker` → `_queue_archive` → `_process_archive_item` → `_complete_archive_record` |
| 9 | `src/workers/upload_worker.py` | 可靠性细节：`_restore_pending_archives`、`_schedule_archive_persist_retry`、`_safe_net_check` 超时与熔断 |
| 10 | `src/repositories/pending_archive_repository.py` | 归档日志落盘 —— 断电后「哪些文件已上传但还没归档」的唯一依据 |

> ⚠️ `upload_worker.py` 有 2455 行，**禁止从头顺序读**。按上表「骨架 → 循环 → 归档 → 可靠性」四刀切进去。

#### 4B. 清理链路

| 顺序 | 文件 | 读什么 |
|---|---|---|
| 1 | `src/controllers/cleanup_controller.py` (128) | 请求校验、派发、取消 |
| 2 | `src/services/cleanup_service.py` (896) | 模块级函数：`iter_cleanup_candidates`、`oldest_cleanup_candidates`、`sort_cleanup_file_items` |
| 3 | 同上 | `_ScanWorker` / `_DeleteWorker`（QObject + QThread）、`_ManualEventBridge` |
| 4 | 同上 | `validate_cleanup_folder_group` —— 目录不嵌套、同盘约束 |
| 5 | 同上 | `run_auto_cleanup` —— 自动模式只进回收站，空间未释放即停止 |
| 6 | `src/ui/dialogs/disk_cleanup_dialog.py` (2079) | 只在需要改 UI 时读；先读 `panels.py` 更有性价比 |

#### 4C. 认证 / 权限 / FTP 链路（较独立，可最后读）

| 顺序 | 文件 | 读什么 |
|---|---|---|
| 1 | `src/models/auth_model.py` (99) | `ControlPermissions`、`PermissionContext`、`LoginResult` |
| 2 | `src/services/auth_service.py` (250) | PBKDF2 + 独立盐、旧 SHA-256 登录后安全升级 |
| 3 | `src/controllers/auth_controller.py` (174) | 角色切换、`compute_permissions`、`default_password_roles` |
| 4 | `src/services/ftp_service.py` (412) | 服务端/客户端配置校验、异步连通性测试 |
| 5 | `src/protocols/ftp.py` (1234) | `FTPProtocolManager` → `FTPServerManager` → `FTPClientUploader` |
| 6 | `src/protocols/ftp.py` | **重点**：`upload_file_result` —— 先传唯一临时名 → 校验远端大小 → rename 为最终名（失败绝不误报成功） |

---

### 阶段 5 — 界面层（按需，不要求全读）

| 顺序 | 文件/位置 | 读什么 |
|---|---|---|
| 5.1 | `src/ui/main_window.py` L49–212 | **先读 7 个 Gateway 协议** —— 这就是整个 View 的对外接口清单 |
| 5.2 | `src/ui/main_window.py` | `__init__` 注入、`_build_ui` / `_card` / `_control_card` 布局骨架 |
| 5.3 | `src/ui/main_window.py` | `_handle_upload_event` —— 上行事件总入口，对应 Controller 的 `_notify` |
| 5.4 | `src/ui/main_window.py` | `render_permissions` / `render_authenticated_role` —— `render_*()` 命名约定 |
| 5.5 | `src/ui/main_window.py` | `calculate_responsive_metrics` / `_scale_px` / `_font_pt` —— 响应式缩放方案 |
| 5.6 | `src/ui/main_window.py` | `_refresh_ui_texts` + `core/i18n.py` 的 `t()` —— 中英文切换 |
| 5.7 | `src/ui/panels.py`、`widgets.py` | 可复用面板与控件 |
| 5.8 | `src/ui/dialogs/*` | 登录、改密、磁盘清理三个对话框 |

---

### 阶段 6 — 退出与验证（收尾，0.5 小时）

| 顺序 | 文件 | 读什么 |
|---|---|---|
| 6.1 | `src/controllers/lifecycle_controller.py` (381) | `request_shutdown` 的固定顺序：View 停止定时器 → cleanup → upload → ftp → runtime → View 释放 |
| 6.2 | `src/controllers/lifecycle_controller.py` | 参与方失败不中断整体释放；`LifecycleShutdownResult` 汇总 |
| 6.3 | 历史 `test_architecture_boundaries.py` | 隔离提取后可执行的分层依赖静态守卫 |
| 6.4 | 历史上传/生命周期 MVC 测试 | 隔离提取后用 Fake Gateway 理解层间隔离 |
| 6.5 | 历史 `conftest.py` | 隔离提取后的离屏 Qt 测试夹具 |
| 6.6 | `tools/build_v351_release_candidate.py` | 打包、哈希侧车、`--verify-only` 校验流程 |

---

## 4. 快速定位表：想改 X 该看哪儿

| 需求 | 起点 | 波及层 |
|---|---|---|
| 新增一个上传参数 | `models/upload_task.py` → `models/upload_settings.py` | Model → Service 校验 → Worker → UI 表单 |
| 修改上传扫描/过滤规则 | `workers/upload_worker.py` `_get_image_files` / `_should_yield_source_path` | Worker 单点 |
| 改重试策略 | `workers/upload_worker.py` `_handle_upload_failure` / `_process_retry_queue` | Worker 单点 |
| 改删除/归档行为 | `core/safe_deletion.py` + `repositories/pending_archive_repository.py` | Core + Repository（**高风险**） |
| 改暂停/恢复逻辑 | `core/pause_state.py` + Worker `_set_pause_reason` | Core → Controller 状态机 |
| 改磁盘清理排序/阈值 | `services/cleanup_service.py` 模块级纯函数 | Service 单点 |
| 改权限与默认口令 | `models/auth_model.py` + `services/auth_service.py` | Model + Service |
| 改 FTP 上传原子性 | `protocols/ftp.py` `upload_file_result` | Protocol（**高风险**） |
| 改界面文案/布局 | `ui/main_window.py` `_build_ui` / `_refresh_ui_texts` + `core/i18n.py` | View + Core |
| 改退出顺序 | `controllers/lifecycle_controller.py` | Controller（**高风险**） |
| 加新业务线 | 按 `docs/MVC_ARCHITECTURE.md` §6 七步走 | 全链路 |

---

## 5. 阅读时的五个陷阱

1. **不要顺序读 `ui/main_window.py`（3728 行）和 `workers/upload_worker.py`（2455 行）。** 先读网关协议和函数索引，再定点跳转。
2. **不要跳过 `docs/MVC_ARCHITECTURE.md`。** 里面写着哪些 import 是禁止的，违反会被 `test_architecture_boundaries.py` 拦下。
3. **`src/config.py` 是旧 JSON 格式兼容层，不是新架构的一部分。** 新代码的配置入口是 `repositories/config_repository.py` + `models/settings.py`。
4. **`models/stability.py` 会强制冻结功能**（去重、自动清理默认关闭）。看到「明明配了却不生效」先查这里。
5. **执行顺序不可互换**：Worker 停止必须在 cleanup 之后、View 释放之前；改线程相关代码前先读 `LifecycleController` 的顺序约定。

---

## 6. 建议的阅读节奏

| 阶段 | 内容 | 累计投入 | 可验证成果 |
|---|---|---|---|
| 1 | 入口与装配 | 1h | 能画出启动/退出时序图 |
| 2 | 数据契约 | 1.5h | 能列出 `UploadTaskRequest` 全字段含义 |
| 3 | Core 基础设施 | 2.5h | 能解释断电恢复与 fail-closed 删除 |
| 4A | 上传主链路 | 5h | 能画出「文件从扫描到归档」完整事件流 |
| 4B/4C | 清理 + 认证/FTP | 7h | 能定位任一功能到具体函数 |
| 5 | 界面层（按需） | 8h | 能独立新增一个界面控件并接上 Controller |
| 6 | 退出与验证 | 8.5h | 能读懂并运行架构边界测试 |

---

*生成依据：`docs/MVC_ARCHITECTURE.md`、`README.md`、源码静态扫描（60 个模块 / 约 19,700 行）。*
