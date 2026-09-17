# 新手阅读指南

## 1. 软件用途与当前版本限制

这是一个 Windows 桌面图片异步上传工具：支持 SMB、FTP/FTPS 上传、上传成功后的备份/删除归档、断电后待归档恢复，以及管理员可控的磁盘清理。当前是 v3.5.1 无数据库候选版：运行期仅使用 JSON/JSONL、日志和 Windows 注册表，真实 SMB/FTP、断电恢复及 24–72 小时长稳完成前不得替换现场正式版。

## 2. 目录结构与启动入口

```text
src/main.py                 组合根与启动入口
src/models/                 枚举、请求、结果、运行状态
src/controllers/            View 意图、状态转换和生命周期协调
src/services/               业务规则、QThread/线程池生命周期
src/repositories/           JSON、JSONL、日志和注册表持久化边界
src/workers/upload_worker.py 上传会话、重试、归档后台线程
src/protocols/ftp.py        FTP/FTPS 服务端与客户端原子提交
src/core/                   文件身份、原子 JSON、暂停、续传和安全删除
src/ui/                     Qt 界面、Gateway 协议和对话框
docs/                       架构、运维与发布验证资料
tools/                      打包与现场验收工具
```

源码入口是 `python -m src.main`。`main()` 的顺序是：检查依赖 → 加载 Qt → 创建应用 → 单实例检查 → 装配 MVC → 可选发布冒烟探针 → 显示窗口 → Qt 事件循环。

## 3. 推荐阅读顺序

1. 先读根 `README.md`，确认候选版限制和现场安全边界。
2. 读 `docs/MVC_ARCHITECTURE.md`，掌握依赖红线和退出顺序。
3. 读 `src/main.py`，理解对象由哪里创建、怎样注入。
4. 读 `src/models/`，尤其是上传状态、上传请求和配置聚合模型。
5. 读 `src/core/file_identity.py`、`atomic_json_store.py`、`safe_deletion.py`。
6. 沿上传链阅读 `upload_controller.py` → `upload_service.py` → `upload_worker.py` → `pending_archive_repository.py`。
7. 再读 FTP、清理、认证和退出链路；UI 大文件按 Gateway 和事件入口定点阅读。

## 4. 文件上传完整流程

```text
界面收集配置
  → UploadController 校验状态
  → UploadService 创建 QThread/UploadWorker
  → Worker 初始化会话、恢复待归档记录、启动归档线程
  → 处理暂停、网络、磁盘与重试队列
  → 流式扫描源目录，首个候选文件等待稳定窗口
  → 冻结 FileIdentity（路径、大小、mtime、file id）
  → SMB 或 FTP/FTPS 上传
  → 上传成功先写 pending_archives.json
  → 归档线程再次复核文件身份
  → 备份/删除源文件，移除待归档记录
```

`upload_worker.py` 很大，不建议从第一行顺读；优先跳到 `start()`、`_run()`、重试队列、协议分发和归档恢复相关函数。

## 5. FTP 原子提交与失败重试

```text
连接已确认
  → 生成唯一 .part-随机串 临时远端路径
  → STOR 上传临时对象
  → 检查连接代际仍有效
  → 读取远端 SIZE，必须等于本地字节数
  → RNFR/RNTO rename 为最终路径
  → 返回 confirmed

任一步失败 → 返回失败状态 → 尽力删除临时对象 → Worker 按既有重试规则排队
```

最终文件名只在大小确认和 rename 均成功后可见；失败不能被记为上传成功。

## 6. 上传成功后的状态记录与归档

待归档记录保存在应用目录的 `data/pending_archives.json`，归档持久化失败记录在 `data/pending_archive_failures.json`。记录包含目标路径、归档模式及文件身份，防止“同路径新文件”被旧任务删除。恢复时先加载记录、再复核身份；不匹配时安全跳过而不是删除。

## 7. 磁盘检测与自动清理

```text
磁盘使用率达到 trigger_percent
  → 校验管理员权限、目录同卷且不嵌套、回收站可用
  → 有界扫描全局最旧修改时间候选
  → 删除前比较 size / mtime_ns / file id / 创建时间
  → 仅移入回收站
  → 重新读取同卷可用空间
  → 达到 target_percent、候选耗尽或安全停止
  → 写 JSONL 审计汇总
```

若回收站未释放同卷空间，程序停止并记录结果；不会退化为永久删除。

## 8. 核心模块关系

| 模块 | 职责 | 主要协作者 |
| --- | --- | --- |
| `main.py` | 唯一组合根 | 全部 Repository、Service、Controller、MainWindow |
| Controller | 协调意图与状态 | Model、抽象 Service/Gateway |
| Service | 业务规则和后台生命周期 | Repository、Worker、Protocol |
| Repository | JSON/日志/注册表 IO | `core.atomic_json_store` |
| Worker | 上传、重试、归档执行 | FTP 客户端、任务注册表、待归档仓储 |
| Protocol | FTP/FTPS 网络边界 | Service、Worker |
| UI | 输入、展示和事件转发 | Controller Gateway 协议 |

## 9. 重要配置、单位与默认值

| 项目 | 单位/含义 | 阅读提示 |
| --- | --- | --- |
| FTP 端口 | TCP 端口，常用默认值 21 | FTPS 还必须校验证书与私钥路径 |
| 被动端口范围 | TCP 起止端口 | 起止值会被规范化为可用范围 |
| 超时 | 秒或毫秒，按参数名区分 | `*_ms` 是毫秒，`timeout`/`*_seconds` 依调用点确认 |
| 文件大小 | 字节；磁盘显示可换算 MB/GB | 上传与 FTP 确认始终用原始字节比较 |
| 磁盘阈值 | 百分比 0–100 | `trigger_percent` 触发，`target_percent` 为清理目标 |
| 重试次数 | 非负整数 | 不改变既有最大次数、退避和状态转换 |
| 路径 | Windows 中文路径允许 | 源、目标、备份和清理根目录存在嵌套时会被拒绝 |

实际默认值以 `models/*_settings.py` 和现有 `config.json` 的兼容读取结果为准；不要手改 JSON 键名。

## 10. 日志、配置和恢复记录

- `config.json`：应用设置与账号哈希；改动前先备份。
- `logs/` 与 `data/`：运行日志、审计与状态记录，均位于应用目录。
- `data/pending_archives.json`：上传成功、尚未归档的恢复记录。
- `resume_data/`：续传状态；属于运行产物，不应提交。
- Windows 注册表：开机启动项由 `runtime_repository.py` 管理。

## 11. 常见异常排查

| 现象 | 优先检查 |
| --- | --- |
| 程序无法启动 | PySide6、控制台依赖提示、`startup-error.log`（打包版） |
| FTP 上传失败 | 主机/端口、TLS 文件、被动端口、防火墙、远端 SIZE 与临时对象日志 |
| 文件未归档 | `pending_archives.json`、源文件身份是否变化、备份路径可写性 |
| 磁盘未释放 | 回收站是否仍占用同一磁盘；程序不会自动清空或永久删除 |
| 无法退出 | 生命周期日志、上传/清理 Worker 是否仍在收尾；不要杀线程替代协作退出 |
| 配置保存后异常 | 先恢复备份的 `config.json`，不要编辑密码哈希或未知 JSON 字段 |

## 12. 不必从头阅读的大文件

- `src/ui/main_window.py`：先读顶部 Gateway Protocol、初始化、事件入口和 `render_*()`。
- `src/ui/dialogs/disk_cleanup_dialog.py`：先读请求收集、扫描/删除事件和自动清理设置。
- `src/workers/upload_worker.py`：按会话、重试、单文件、归档、恢复五段定点阅读。
- `src/core/i18n.py`：只查 `TRANSLATIONS` 与 `t()`，不必通读翻译字典。

## 13. 与现有文档的关系

本指南面向第一次维护项目的人，给出入口、阅读顺序和故障定位。

- `docs/MVC_ARCHITECTURE.md` 是分层规则、组合根和退出约定的权威说明。
- `docs/CODE_READING_GUIDE.md` 是更细的按文件阅读路线；其中测试源码已从精简仓库移除，历史测试仅在隔离验证目录临时提取。
