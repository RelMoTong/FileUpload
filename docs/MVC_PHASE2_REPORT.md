# MVC 重构阶段 2 完成报告

## 修改文件

- `src/repositories/config_repository.py`：新增配置持久化适配器。
- `src/controllers/settings_controller.py`：新增配置控制器和Repository抽象协议。
- `src/config.py`：增加明确的保存错误状态和可控的用户字段保留策略。
- `src/main.py`：在应用组合根创建并注入配置Repository与Controller。
- `src/ui/main_window.py`：移除对 `ConfigManager` 的直接依赖，配置读写统一经过抽象配置网关。
- `tests/test_settings_controller.py`：新增Repository、Controller、用户字段保存和兼容性测试。
- `tests/test_architecture_boundaries.py`：从UI历史允许列表中删除 `src.config`。

## 修改原因

配置文件属于持久化基础设施，不应由View直接打开、解析或写入。本阶段将这项职责迁移到Repository，并由Controller协调配置模型与持久化。

## 行为保持

- `config.json` 路径和JSON格式不变。
- 首次加载仍会生成默认配置。
- 常规保存仍可以保留原有用户密码。
- 修改密码和清理配置的原有完整写回路径仍可替换 `users` 字段。
- 未知配置字段会通过 `ApplicationSettings` 往返保留。
- 无Controller的界面测试使用纯Model默认值，不会写入真实配置文件。

## 测试结果

- 配置、Model与架构专项测试：`13 passed`。
- 完整测试：`45 passed, 5 subtests passed`。

## 风险

- `MainWindow` 目前仍负责从控件收集配置和把配置渲染到控件，这是View的合理职责；路径与FTP校验会在对应Controller阶段继续迁移。
- `ConfigManager` 为保持兼容仍然存在，现仅由Repository使用。

## 遗留问题

- 登录和权限规则仍在View中。
- FTP、上传和清理Controller尚未建立。

## 下一阶段

执行阶段 3：建立认证Model/Service/Controller，把密码校验、角色状态和权限计算移出View。

