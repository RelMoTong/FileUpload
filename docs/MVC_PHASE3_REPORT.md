# MVC 重构阶段 3 完成报告

## 修改文件

- `src/models/auth_model.py`：扩展认证状态、登录结果、密码修改结果、权限上下文和权限渲染模型。
- `src/services/auth_service.py`：新增纯Python密码哈希、认证、密码强度和权限规则。
- `src/controllers/auth_controller.py`：新增登录、退出、改密、持久化回滚和权限协调逻辑。
- `src/main.py`：在组合根创建并注入 `AuthController`。
- `src/ui/main_window.py`：移除密码哈希、凭据保存和角色授权计算，只收集输入并渲染结果。
- `tests/test_auth_mvc.py`：新增无需 `QApplication` 的认证、改密、回滚和权限测试。
- `tests/test_more_menu_permissions.py`、`tests/test_responsive_layout.py`：通过组合依赖创建主窗口。

## 修改原因

身份认证和权限属于业务规则，不能由View根据字符串角色和密码哈希自行判断。本阶段建立Controller持有的唯一角色状态，并把所有安全规则迁移到纯业务Service。

## 行为保持

- 默认用户密码仍为 `123` 的SHA-256哈希。
- 默认管理员密码仍为 `Tops123` 的SHA-256哈希。
- 密码最小长度、简单密码和字符类别规则不变。
- 只有管理员可以修改用户或管理员密码。
- 密码写入失败时会回滚内存凭据，避免内存和配置文件不一致。
- 访客、普通用户和管理员的控件权限保持原有结果。
- View中剩余的角色比较仅用于角色文案和提示渲染，不再用于业务授权。

## 测试结果

- 认证、权限、UI与架构专项测试：`21 passed, 5 subtests passed`。
- 完整测试：`53 passed, 5 subtests passed`。

## 风险

- `current_role`暂时保留为MainWindow兼容属性，但实际读写已经委托给 `AuthController`，Controller是唯一状态来源。
- FTP按钮的授权已经由认证Controller判断，FTP具体业务将在阶段4迁移。

## 遗留问题

- FTP服务与连接测试仍由View直接协调具体协议类。
- 上传和磁盘清理生命周期尚未迁移。

## 下一阶段

执行阶段4：建立 `FTPService` 和 `FTPController`，移除View对FTP具体类的直接依赖。

