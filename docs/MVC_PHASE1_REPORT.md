# MVC 重构阶段 1 完成报告

## 修改文件

- `src/models/`：新增运行状态、枚举、上传配置、FTP配置、清理配置、认证配置及聚合配置模型。
- `tests/test_mvc_models.py`：新增配置往返、未知字段保留、可变默认值隔离和枚举测试。
- `tests/test_architecture_boundaries.py`：阶段 0 已建立的依赖守卫同时验证 Model 不依赖 Qt View。

## 修改原因

在迁移 Controller 和持久化之前，先建立不依赖 Qt 的统一数据结构，避免继续使用散落的字符串、布尔值和嵌套字典传递状态。

## 兼容性

- 默认配置经过 `ApplicationSettings.from_config(...).to_config()` 后与原字典完全相同。
- 未识别的顶层字段、FTP服务端字段和FTP客户端字段会原样保留。
- 对外序列化继续使用 `smb`、`ftp_client`、`both`、`skip`、`rename`、`overwrite`、`ask` 等原字符串。
- 本阶段尚未把新模型接入运行流程，因此没有改变现有业务行为。

## 测试结果

- Model与架构专项测试：`9 passed`。
- 完整测试：`41 passed, 5 subtests passed`。

## 风险

- 当前 `MainWindow` 仍使用原始字典和字符串状态；阶段 2 起逐步接入新模型。
- 配置中的历史扩展字段虽然能够保留，但只有迁移对应功能后才会获得强类型访问。

## 遗留问题

- 配置持久化仍由 View 直接调用。
- 登录、FTP、上传和清理逻辑尚未迁移到 Controller/Service。

## 下一阶段

执行阶段 2：新增 `ConfigRepository` 和 `SettingsController`，把配置文件读写从 `MainWindow` 移出。

