# MVC 架构说明

## 1. 架构结论

本项目采用严格的 MVC 主结构，并把 Service、Repository、Worker 和 Protocol 视为 Model 侧的业务及基础设施协作者。`src/main.py` 是生产代码中唯一的组合根。

```text
用户操作
  -> View (src/ui)
  -> Controller (src/controllers)
  -> Model / Service (src/models, src/services)
  -> Repository / Worker / Protocol

后台结果
  -> Service 事件
  -> Controller 状态更新
  -> View render_*()
```

## 2. 最终目录和职责

```text
src/
├── main.py                  # 依赖检查、单实例、对象装配、事件循环
├── models/                  # 纯数据、枚举、请求、结果和运行状态
├── controllers/             # 协调 View 意图、Service 调用和 Model 状态
├── services/                # 业务规则、Worker/QThread 和底层能力封装
├── repositories/            # JSON、日志、清理审计、Windows 启动项
├── ui/
│   ├── main_window.py       # 主 View；输入、展示、事件转发
│   ├── panels.py            # 上传设置、状态和日志面板
│   ├── widgets.py           # 通用控件
│   └── dialogs/             # 登录、改密和磁盘清理对话框
├── workers/                 # 上传 Worker 和内部线程池
├── protocols/               # FTP/FTPS 客户端与服务端实现
├── core/                    # 国际化、通用函数和断点续传
└── config.py                # 旧 JSON 配置格式兼容层
```

运行期持久化仅使用 JSON/JSONL、日志和 Windows 注册表；生产代码不依赖 SQLite 或生成数据库文件。

## 3. 强制依赖规则

- View 只依赖 Model 和 View 本地声明的 Gateway 协议；不导入 Controller、Service、Repository、Worker 或 Protocol 实现。
- Model 不依赖 Qt View、Controller 或 UI。
- Controller 依赖 Model 和抽象协议；不导入具体 UI、Service、Repository、Worker、Protocol，也不执行文件、注册表或网络 IO。
- Service 承担业务规则及后台任务生命周期，并可调用 Repository、Worker 和 Protocol。
- Repository 是持久化边界；文件、日志和 Windows 注册表操作集中在这里。
- 具体 Repository、Service、Model、Controller 和 `MainWindow` 只能在 `src/main.py` 中组成生产对象图。

这些规则在开发环境中由架构边界测试静态检查；精简源代码目录不包含测试套件。

## 4. 组合根与启动顺序

`src.main.main()` 按以下顺序启动：

1. 检查必需和可选依赖。
2. 创建 `QApplication`，执行本地 Socket 与共享内存单实例检查。
3. 创建 Repository。
4. 创建 Service。
5. 创建长期运行的 Model 状态对象。
6. 创建 Controller，并注入 Model、Service 和 Repository Gateway。
7. 创建 `MainWindow`，注入全部 Controller Gateway。
8. 建立单实例本地服务、显示窗口并进入 Qt 事件循环。

`main.py` 只负责创建和连接对象，不实现上传、FTP、清理、认证或持久化业务。

## 5. 退出顺序

`LifecycleController` 保证退出只执行一次，并在单个参与者失败时继续释放其余资源：

1. View 停止界面定时器，禁止产生新的后台请求。
2. `CleanupController` 取消自动清理，等待扫描/删除 QThread，关闭自动清理线程池。
3. `UploadController` 停止 Worker；Worker 关闭文件操作、网络检查线程池和 FTP 客户端；随后等待上传 QThread。
4. `FTPController` 停止独立 FTP 服务端及剩余客户端。
5. `RuntimeController` 等待日志和磁盘查询线程池完成。
6. View 隐藏托盘图标并关闭单实例 `QLocalServer`。

关闭错误记录到日志并汇总到 `LifecycleShutdownResult`，不会跳过后续资源释放。

## 6. 扩展功能的维护规则

1. 先在 `models/` 定义输入、输出和状态，不向模型加入 Qt 控件。
2. 在 Service/Repository 实现业务或 IO，并用 Protocol 向 Controller 暴露最小接口。
3. Controller 只协调请求、状态转换和 View 渲染通知。
4. View 只收集表单、发出语义事件并实现 `render_*()`。
5. 在 `main.py` 注入新增的生产组件。
6. 至少增加 Model/Service 单元测试、Controller Fake 测试和必要的架构边界规则。
7. 运行完整测试与离屏 GUI 烟雾测试后再交付。

## 7. 验证入口

```powershell
python -m compileall -q src
```

完整回归测试和 Qt 离屏测试应在独立开发检出中执行，不随精简源代码目录发布。
