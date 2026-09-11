# 图片异步上传工具 MVC 重构实施计划

## 1. 文档目的

本文档用于指导当前图片异步上传工具逐步重构为职责边界清晰的 MVC 架构。

本次重构遵循以下原则：

- 保留 PySide6、现有界面风格、业务规则和数据格式。
- 保留现有 `workers`、`protocols`、`core` 等模块，在此基础上调整职责。
- 不一次性重写整个项目，按阶段进行小范围、可测试、可回退的迁移。
- 每完成一个阶段，都必须运行测试并确认现有功能没有退化。
- 未经明确确认，不修改配置字段、密码规则、上传规则和磁盘清理规则。

## 2. 当前架构判断

当前程序采用“模块化分层 + Qt 事件驱动”架构，并局部使用了观察者、单例和外观等设计模式。

程序已经把代码拆分到 `ui`、`workers`、`protocols`、`core` 等目录，但 `MainWindow` 仍同时承担以下职责：

- 创建和更新界面。
- 保存大量运行状态。
- 校验和保存配置。
- 管理用户登录与权限。
- 启动、暂停和停止上传任务。
- 创建和管理后台线程。
- 启停 FTP 服务以及测试 FTP 连接。
- 执行磁盘扫描和自动清理。
- 记录日志、托盘通知和程序退出清理。

因此，当前程序只有部分 MVC 思想，还不是严格的 MVC 架构。

## 3. 目标架构

建议采用“严格 MVC 主结构 + Service/Repository 作为 Model 内部协作者”。

```text
src/
├─ main.py                         # 组合根：创建并连接 MVC
├─ models/
│  ├─ app_state.py                 # 全局运行状态
│  ├─ upload_settings.py           # 上传配置数据模型
│  ├─ ftp_settings.py              # FTP配置数据模型
│  ├─ cleanup_settings.py          # 清理配置数据模型
│  └─ auth_model.py                # 用户、角色和权限模型
├─ controllers/
│  ├─ main_controller.py           # 主流程协调
│  ├─ upload_controller.py         # 上传启停、暂停和进度
│  ├─ ftp_controller.py            # FTP服务和连接测试
│  ├─ cleanup_controller.py        # 磁盘扫描与清理
│  ├─ auth_controller.py           # 登录、退出和权限
│  └─ settings_controller.py       # 配置加载与保存
├─ ui/                             # View层
│  ├─ main_window.py
│  ├─ dialogs/
│  │  ├─ login_dialog.py
│  │  ├─ password_dialog.py
│  │  └─ cleanup_dialog.py
│  └─ widgets.py
├─ services/                       # Model侧业务服务
│  ├─ upload_service.py
│  ├─ ftp_service.py
│  ├─ cleanup_service.py
│  ├─ auth_service.py
│  └─ notification_service.py
├─ repositories/                   # Model侧持久化
│  ├─ config_repository.py
│  ├─ resume_repository.py
│  └─ log_repository.py
├─ workers/                        # 保留后台线程实现
├─ protocols/                      # 保留FTP等协议实现
└─ core/                           # 通用工具和国际化
```

该目录是目标结构，不要求一次性全部创建。只有迁移到对应功能时，才新增相应文件。

## 4. MVC 职责边界

| 层 | 允许承担的职责 | 禁止承担的职责 |
|---|---|---|
| Model | 状态、配置、校验、上传规则、FTP、文件系统、持久化 | 创建窗口、弹窗或直接操作控件 |
| View | 创建控件、展示状态、读取输入、发出用户事件 | 启动 Worker、保存配置、连接 FTP、执行业务判断 |
| Controller | 接收 View 事件、调用 Model、把结果传递给 View | 直接实现 FTP 传输、文件删除等底层操作 |
| `main.py` | 创建并注入 Model、View、Controller | 编写具体业务流程 |

依赖方向必须保持为：

```text
用户操作
   ↓
View ──用户事件──> Controller ──业务请求──> Model / Service
  ↑                                      │
  └──────────渲染指令和状态结果───────────┘
```

附加依赖规则：

- `src/ui` 不得导入 `src/workers`、`src.protocols` 或配置持久化实现。
- Model 层不得导入 `QtWidgets`。
- View 不直接持有和管理 `UploadWorker`、`FTPProtocolManager`。
- Controller 不直接执行文件复制、删除、网络连接等底层操作。
- `main.py` 是唯一负责组装 MVC 依赖的位置。

## 5. 核心接口规划

### 5.1 View 接口

View 只暴露语义化事件、表单读取和界面渲染方法。

```python
class MainView:
    start_requested
    pause_requested
    stop_requested
    save_requested

    def read_upload_form(self) -> UploadSettings:
        ...

    def render_app_state(self, state: AppState) -> None:
        ...

    def show_error(self, message: str) -> None:
        ...

    def append_log(self, message: str) -> None:
        ...
```

### 5.2 Controller 接口

Controller 接收 View 事件并协调业务服务。

```python
class UploadController:
    def handle_start(self) -> None:
        settings = self.view.read_upload_form()
        result = self.upload_service.start(settings)
        self.view.render_app_state(result.state)
```

### 5.3 Model 数据对象

建议使用 `dataclass` 和枚举管理数据，减少散落的字典、布尔值和字符串状态。

```python
@dataclass
class AppState:
    role: UserRole
    upload_status: UploadStatus
    network_status: NetworkStatus
    uploaded: int
    failed: int
    skipped: int
```

优先把以下字符串转换为枚举：

- 用户角色：`guest`、`user`、`admin`。
- 上传状态：`stopped`、`running`、`paused`。
- 网络状态：`unknown`、`good`、`unstable`、`disconnected`。
- 上传协议：`smb`、`ftp_client`、`both`。
- 重复文件策略：`skip`、`rename`、`overwrite`、`ask`。

配置文件的字符串值暂时保持兼容，只在程序内部转换为枚举。

## 6. 分阶段实施计划

### 阶段 0：建立重构保护基线

状态：`已完成`

目标：确保后续重构能够及时发现行为变化。

任务：

- [x] 记录当前所有测试的基线结果。
- [x] 记录应用启动、上传、暂停、停止、退出的人工测试步骤。
- [x] 记录 SMB、FTP客户端、FTP服务器和双写模式的预期行为。
- [x] 记录配置文件当前字段和默认值。
- [x] 列出 `MainWindow` 的职责和对应方法。
- [x] 增加基础架构依赖检查方案。

验收标准：

- 当前自动化测试全部通过。
- 关键业务流程有明确的回归测试清单。
- 本阶段不改变任何业务行为。

### 阶段 1：建立 Model 数据结构

状态：`已完成`

目标：建立统一、可测试的数据和状态表达方式。

任务：

- [x] 创建 `models` 包。
- [x] 创建 `AppState`。
- [x] 创建 `UploadSettings`。
- [x] 创建 `FTPServerSettings` 和 `FTPClientSettings`。
- [x] 创建 `CleanupSettings`。
- [x] 创建角色、上传状态、网络状态、协议和重复策略枚举。
- [x] 实现配置字典与数据模型之间的转换。
- [x] 保持现有配置文件格式不变。

验收标准：

- 数据模型不依赖 `QtWidgets`。
- 数据模型可以独立进行单元测试。
- 旧配置可以正常加载。
- 保存后的字段与当前版本兼容。

### 阶段 2：迁移配置管理

状态：`已完成`

目标：将配置读取、校验、转换和保存从 `MainWindow` 移出。

任务：

- [x] 创建 `ConfigRepository`，包装现有 `ConfigManager`。
- [x] 创建 `SettingsController`。
- [x] 将配置加载流程迁移到 Controller。
- [x] 将配置保存流程迁移到 Controller。
- [x] 将配置字典转换为 Model。
- [x] View 只负责读取和显示表单内容。
- [x] 保留现有密码字段保护行为。

验收标准：

- `MainWindow` 不直接实例化 `ConfigManager`。
- 配置加载和保存可以通过 Fake View 测试。
- 配置文件格式和业务默认值没有变化。

### 阶段 3：迁移登录与权限

状态：`已完成`

目标：把身份认证、角色状态和权限判断从 View 移出。

任务：

- [x] 创建 `AuthModel` 和 `AuthService`。
- [x] 创建 `AuthController`。
- [x] 迁移密码校验和修改逻辑。
- [x] 迁移角色切换和退出逻辑。
- [x] 把控件启用状态转换为可测试的权限渲染模型。
- [x] View 只显示登录对话框和权限状态。

验收标准：

- View 不计算角色权限。
- 权限规则可以在没有 `QApplication` 的情况下测试。
- 普通用户、管理员和访客的行为保持不变。

### 阶段 4：迁移 FTP 管理

状态：`已完成`

目标：将 FTP 配置、连接测试、启停和状态管理移出 View。

任务：

- [x] 创建 `FTPService`，包装 `FTPProtocolManager`。
- [x] 创建 `FTPController`。
- [x] 迁移 FTP 服务端配置校验。
- [x] 迁移 FTP 客户端连接测试。
- [x] 迁移 FTP 服务启动和停止流程。
- [x] 将 FTP 事件转换为 Controller 可处理的业务事件。
- [x] View 只显示 FTP 状态、错误和日志。

验收标准：

- `MainWindow` 不直接实例化 `FTPProtocolManager`、`FTPServerManager` 或 `FTPClientUploader`。
- FTP 服务可以在不创建主窗口的情况下测试。
- 服务端、客户端和双写模式保持原有行为。

### 阶段 5：迁移上传流程

状态：`已完成`

目标：使上传任务的创建、线程生命周期和状态转换完全由 Controller/Service 管理。

目标流程：

```text
View 发出开始事件
→ UploadController 校验请求
→ UploadService 创建 UploadWorker
→ Worker 发出进度和状态事件
→ UploadController 接收事件
→ View 渲染最新状态
```

任务：

- [x] 创建 `UploadService`。
- [x] 创建 `UploadController`。
- [x] 将 `UploadWorker` 创建逻辑移出 `MainWindow`。
- [x] 将 `QThread` 生命周期管理移出 `MainWindow`。
- [x] 迁移开始、暂停、恢复和停止流程。
- [x] 迁移进度、速率、错误和完成事件处理。
- [x] 建立明确的上传状态转换规则。
- [x] 防止 Controller、View 和 Worker 分别保存冲突状态。

验收标准：

- View 不持有 `UploadWorker` 和上传线程。
- Controller 可以使用 Fake Service 测试状态转换。
- 上传、暂停、恢复、停止和退出行为保持不变。
- Worker 仍然只通过信号或回调传递结果，不直接操作 UI。

### 阶段 6：迁移磁盘扫描和清理

状态：`已完成`

目标：把扫描、候选文件选择、删除和自动清理策略移出 View。

任务：

- [x] 创建 `CleanupService`。
- [x] 创建 `CleanupController`。
- [x] 迁移目录校验和磁盘分组规则。
- [x] 迁移清理候选文件选择规则。
- [x] 迁移扫描和删除 Worker 生命周期管理。
- [x] 迁移自动清理触发和取消逻辑。
- [x] 迁移清理审计日志逻辑。
- [x] 清理对话框只保留输入、列表展示、确认和进度显示。

验收标准：

- View 不直接扫描或删除文件。
- 清理策略可以独立进行单元测试。
- 回收站、永久删除、取消和审计行为保持不变。

### 阶段 7：拆分和精简 View

状态：`已完成`

目标：使 `MainWindow` 成为真正的 View。

任务：

- [x] 拆分登录对话框。
- [x] 拆分密码修改对话框。
- [x] 拆分磁盘清理对话框。
- [x] 拆分上传设置、状态和日志面板。
- [x] 保留统一主题和响应式布局。
- [x] 将按钮处理函数改为语义化事件。
- [x] 将状态刷新统一为 `render_*()` 方法。
- [x] 删除已经迁移到 Controller/Service 的业务方法。

最终 `MainWindow` 只保留：

- 控件创建和布局。
- 用户输入读取。
- Qt界面事件转发。
- `render_*()` 界面更新方法。
- 必要的窗口显示和关闭事件。

验收标准：

- `MainWindow` 不包含网络、文件操作和业务规则。
- `MainWindow` 不直接保存持久化配置。
- 视图组件可以独立进行界面测试。
- 现有界面布局和交互没有非预期变化。

### 阶段 8：收口组合根和架构检查

状态：`已完成`

目标：统一依赖注入，防止以后重新产生跨层调用。

任务：

- [x] 由 `main.py` 创建 Repository、Service、Model、View 和 Controller。
- [x] 明确程序启动和退出顺序。
- [x] 明确所有 Worker、线程池和 FTP 服务的关闭顺序。
- [x] 增加架构依赖测试。
- [x] 增加 Controller 的 Fake View/Fake Service 测试。
- [x] 更新项目结构和维护文档。

验收标准：

- `main.py` 是唯一的 MVC 组合根。
- Model 不依赖 View。
- View 不依赖业务实现。
- Controller 不直接执行底层 IO。
- 所有自动化测试通过。
- 完整人工回归测试通过。

## 7. 每阶段统一执行流程

每个阶段都按照以下步骤执行：

1. 确认本阶段范围和不包含的内容。
2. 检查涉及文件和现有测试。
3. 给出本阶段的详细文件级修改计划。
4. 获得明确确认后再修改代码。
5. 只完成本阶段要求，不提前实施后续阶段。
6. 运行相关单元测试。
7. 运行完整测试集。
8. 必要时执行人工界面回归测试。
9. 汇总变更文件、修改原因、风险和遗留问题。
10. 用户确认后再进入下一阶段。

## 8. 每阶段交付报告模板

```markdown
## 阶段 N 完成报告

### 修改文件

- 文件路径：修改内容

### 修改原因

- 本阶段解决的问题

### 测试结果

- 单元测试：
- 完整测试：
- 人工测试：

### 风险

- 当前仍需关注的风险

### 遗留问题

- 本阶段明确不处理的内容

### 下一阶段

- 下一阶段目标
```

## 9. 主要风险

### 9.1 Qt线程归属

`QObject`、`QThread` 和界面控件存在明确的线程归属。迁移线程生命周期时，必须确保所有 UI 更新仍在主线程执行。

### 9.2 状态重复

当前 View、Worker 和管理器中都保存了一部分运行状态。重构后必须确定单一状态来源，避免同一个状态在多个对象中不一致。

### 9.3 程序退出顺序

退出时需要按顺序停止：

1. 自动清理任务。
2. 上传 Worker。
3. FTP 服务和客户端。
4. 后台线程与线程池。
5. 日志任务。
6. 托盘和本地单实例服务。

### 9.4 配置兼容

引入数据模型和枚举时，不能直接改变现有 JSON 字段及字符串值，必须提供双向转换。

### 9.5 View过度拆分

拆分目标是明确职责，而不是追求文件数量。只在组件具有独立职责、状态或测试价值时拆分。

### 9.6 一次性重写风险

禁止直接替换整个 `MainWindow`。应先建立新层，再逐个迁移功能，确保每次变更都可以独立验证和回退。

## 10. 最终验收标准

- [x] UI层不导入 Worker、协议实现或配置持久化实现。
- [x] Model层不导入 `QtWidgets`。
- [x] Controller只负责协调，不包含底层IO实现。
- [x] Model和Controller可以脱离真实主窗口测试。
- [x] 所有状态由明确的Model统一管理。
- [x] 所有后台任务通过事件通知Controller。
- [x] View只负责输入、展示和界面事件。
- [x] 配置文件向后兼容。
- [x] SMB、FTP客户端、FTP服务器和双写功能保持正常。
- [x] 登录、权限、日志、托盘和自动清理功能保持正常。
- [x] 当前自动化测试全部通过。
- [x] 新增Controller、Model和架构边界测试。
- [x] 项目说明文档与最终目录结构一致。

## 11. 推荐执行顺序

建议严格按以下顺序分段执行：

1. 阶段 0：建立保护基线。
2. 阶段 1：建立 Model 数据结构。
3. 阶段 2：迁移配置管理。
4. 阶段 3：迁移登录与权限。
5. 阶段 4：迁移 FTP 管理。
6. 阶段 5：迁移上传流程。
7. 阶段 6：迁移磁盘扫描和清理。
8. 阶段 7：拆分和精简 View。
9. 阶段 8：收口组合根和架构检查。

开始某个阶段时，建议使用如下指令：

```text
按照 MVC重构实施计划.md 执行阶段 N。先检查当前代码并给出本阶段的文件级修改计划，不要执行后续阶段。
```
