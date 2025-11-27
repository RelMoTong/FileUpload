# FTP协议 API 文档 v2.1

本文档详细说明 v2.1 版本新增的 FTP 协议相关 API，包括服务器模式和客户端模式的使用方法。

## 目录

- [概述](#概述)
- [FTP服务器模式](#ftp服务器模式)
  - [FTPServerManager 类](#ftpservermanager-类)
  - [配置参数](#服务器配置参数)
  - [API 方法](#服务器-api-方法)
  - [使用示例](#服务器使用示例)
- [FTP客户端模式](#ftp客户端模式)
  - [FTPClientUploader 类](#ftpclientuploader-类)
  - [配置参数](#客户端配置参数)
  - [API 方法](#客户端-api-方法)
  - [使用示例](#客户端使用示例)
- [混合模式](#混合模式)
- [错误处理](#错误处理)
- [性能优化建议](#性能优化建议)

---

## 概述

v2.1 版本在原有 SMB 协议基础上新增了 FTP 协议支持，提供三种工作模式：

1. **FTP服务器模式**: 本机作为 FTP 服务器，接收其他设备上传的文件
2. **FTP客户端模式**: 连接到远程 FTP 服务器，上传本地文件
3. **混合模式**: 同时运行服务器和客户端，实现双向传输

**核心模块**:
- `src/ftp_protocol.py`: FTP 协议核心实现
- `src/ftp_ui_component.py`: FTP UI 组件（配置面板）

---

## FTP服务器模式

### FTPServerManager 类

**文件路径**: `src/ftp_protocol.py`

FTPServerManager 管理本机的 FTP 服务器，负责接收客户端连接并保存上传的文件。

#### 类定义

```python
class FTPServerManager:
    """FTP服务器管理器"""
    
    def __init__(self, 
                 host: str = "0.0.0.0",
                 port: int = 21,
                 username: str = "user",
                 password: str = "password",
                 upload_dir: str = "./uploads",
                 passive_ports: tuple = (50000, 50100),
                 enable_tls: bool = False,
                 max_cons: int = 256,
                 max_cons_per_ip: int = 5):
        """
        初始化FTP服务器管理器
        
        Args:
            host: 监听地址（0.0.0.0表示所有网卡）
            port: FTP端口（默认21）
            username: 登录用户名
            password: 登录密码
            upload_dir: 文件上传保存目录
            passive_ports: 被动模式端口范围（start, end）
            enable_tls: 是否启用TLS/SSL加密
            max_cons: 最大并发连接数
            max_cons_per_ip: 单个IP最大连接数
        """
```

### 服务器配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `host` | str | "0.0.0.0" | 监听地址（0.0.0.0=所有网卡） |
| `port` | int | 21 | FTP端口（需要管理员权限） |
| `username` | str | "user" | 登录用户名 |
| `password` | str | "password" | 登录密码 |
| `upload_dir` | str | "./uploads" | 文件保存目录 |
| `passive_ports` | tuple | (50000, 50100) | 被动模式端口范围 |
| `enable_tls` | bool | False | 是否启用TLS/SSL |
| `max_cons` | int | 256 | 最大并发连接数 |
| `max_cons_per_ip` | int | 5 | 单IP最大连接数 |

**端口说明**:
- 端口 < 1024 需要管理员权限
- 推荐使用 2121 或其他高位端口避免权限问题
- NAT 环境必须开启被动模式并配置端口范围

### 服务器 API 方法

#### start()

启动 FTP 服务器（非阻塞）

```python
def start(self) -> bool:
    """
    启动FTP服务器
    
    Returns:
        bool: 启动成功返回True，失败返回False
        
    Raises:
        OSError: 端口被占用
        PermissionError: 权限不足（端口<1024）
    """
```

**使用示例**:
```python
server = FTPServerManager(host="0.0.0.0", port=2121)
if server.start():
    print("FTP服务器启动成功")
else:
    print("FTP服务器启动失败")
```

#### stop()

停止 FTP 服务器

```python
def stop(self) -> None:
    """
    停止FTP服务器
    
    说明:
        - 断开所有客户端连接
        - 关闭监听端口
        - 清理资源
    """
```

**使用示例**:
```python
server.stop()
print("FTP服务器已停止")
```

#### is_running()

检查服务器运行状态

```python
def is_running(self) -> bool:
    """
    检查服务器是否正在运行
    
    Returns:
        bool: 运行中返回True，否则返回False
    """
```

**使用示例**:
```python
if server.is_running():
    print("服务器运行中")
else:
    print("服务器已停止")
```

#### get_local_ip()

获取本机局域网 IP 地址

```python
@staticmethod
def get_local_ip() -> str:
    """
    获取本机局域网IP地址
    
    Returns:
        str: IP地址（如"192.168.1.100"）
             失败时返回"127.0.0.1"
    """
```

**使用示例**:
```python
ip = FTPServerManager.get_local_ip()
print(f"本机IP: {ip}")
```

### 服务器使用示例

#### 基础用法

```python
from src.protocols.ftp import FTPServerManager

# 1. 创建服务器实例
server = FTPServerManager(
    host="0.0.0.0",          # 监听所有网卡
    port=2121,               # 使用2121端口（避免权限问题）
    username="upload_user",  # 用户名
    password="secure_pass",  # 密码
    upload_dir="D:/uploads"  # 文件保存目录
)

# 2. 启动服务器
if server.start():
    print("FTP服务器启动成功")
    print(f"服务器地址: {FTPServerManager.get_local_ip()}:2121")
    print(f"用户名: upload_user")
    print(f"密码: secure_pass")
else:
    print("FTP服务器启动失败，请检查端口是否被占用")

# 3. 运行一段时间...
import time
time.sleep(3600)  # 运行1小时

# 4. 停止服务器
server.stop()
print("服务器已停止")
```

#### 高级配置（被动模式 + TLS）

```python
# NAT环境 + 加密传输
server = FTPServerManager(
    host="0.0.0.0",
    port=2121,
    username="secure_user",
    password="StrongPassword123!",
    upload_dir="D:/ftp_uploads",
    passive_ports=(50000, 50100),  # 被动模式端口范围（需在路由器/防火墙开放）
    enable_tls=True,               # 启用TLS/SSL加密
    max_cons=10,                   # 最多10个并发连接
    max_cons_per_ip=2              # 单IP最多2个连接
)

if server.start():
    print("安全FTP服务器启动成功（TLS加密）")
    print("客户端需使用FTPS协议连接")
```

#### 错误处理

```python
import logging

try:
    server = FTPServerManager(port=21)  # 尝试使用21端口
    server.start()
except PermissionError as e:
    logging.error(f"权限不足，请使用管理员权限运行或更换端口: {e}")
except OSError as e:
    if "Address already in use" in str(e):
        logging.error("端口21已被占用，请更换端口或关闭其他FTP服务")
    else:
        logging.error(f"服务器启动失败: {e}")
except Exception as e:
    logging.error(f"未知错误: {e}")
```

---

## FTP客户端模式

### FTPClientUploader 类

**文件路径**: `src/ftp_protocol.py`

FTPClientUploader 连接到远程 FTP 服务器，上传本地文件夹中的文件。

#### 类定义

```python
class FTPClientUploader:
    """FTP客户端上传器"""
    
    def __init__(self,
                 ftp_host: str,
                 ftp_port: int = 21,
                 ftp_user: str = "user",
                 ftp_pass: str = "password",
                 source_folder: str = "./source",
                 target_path: str = "/upload",
                 backup_folder: str = "./backup",
                 timeout: int = 10,
                 retry_times: int = 3,
                 passive_mode: bool = True,
                 enable_tls: bool = False,
                 log_callback: callable = None):
        """
        初始化FTP客户端上传器
        
        Args:
            ftp_host: FTP服务器地址
            ftp_port: FTP服务器端口
            ftp_user: 登录用户名
            ftp_pass: 登录密码
            source_folder: 本地源文件夹
            target_path: 服务器目标路径
            backup_folder: 本地备份文件夹
            timeout: 连接超时（秒）
            retry_times: 失败重试次数
            passive_mode: 是否使用被动模式
            enable_tls: 是否启用TLS/SSL
            log_callback: 日志回调函数 func(msg: str)
        """
```

### 客户端配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ftp_host` | str | - | FTP服务器地址（必填） |
| `ftp_port` | int | 21 | FTP服务器端口 |
| `ftp_user` | str | "user" | 登录用户名 |
| `ftp_pass` | str | "password" | 登录密码 |
| `source_folder` | str | "./source" | 本地源文件夹 |
| `target_path` | str | "/upload" | 服务器目标路径 |
| `backup_folder` | str | "./backup" | 本地备份文件夹 |
| `timeout` | int | 10 | 连接超时（秒） |
| `retry_times` | int | 3 | 失败重试次数 |
| `passive_mode` | bool | True | 被动模式（NAT推荐） |
| `enable_tls` | bool | False | 启用TLS/SSL |
| `log_callback` | callable | None | 日志回调函数 |

### 客户端 API 方法

#### test_connection()

测试 FTP 服务器连接

```python
def test_connection(self) -> tuple[bool, str]:
    """
    测试FTP服务器连接
    
    Returns:
        tuple[bool, str]: (是否成功, 消息)
        
    示例:
        success, msg = uploader.test_connection()
        if success:
            print("连接成功")
        else:
            print(f"连接失败: {msg}")
    """
```

#### start_monitoring()

启动文件监控和上传（异步）

```python
def start_monitoring(self, interval: int = 10) -> None:
    """
    启动文件监控和上传
    
    Args:
        interval: 监控间隔（秒）
        
    说明:
        - 在独立线程中运行，不阻塞主线程
        - 每隔interval秒检查源文件夹
        - 自动上传新文件并移动到备份文件夹
    """
```

#### stop_monitoring()

停止文件监控

```python
def stop_monitoring(self) -> None:
    """
    停止文件监控
    
    说明:
        - 等待当前上传完成
        - 停止监控线程
        - 不影响已上传的文件
    """
```

#### upload_file()

上传单个文件

```python
def upload_file(self, local_path: str, remote_path: str = None) -> bool:
    """
    上传单个文件到FTP服务器
    
    Args:
        local_path: 本地文件路径
        remote_path: 服务器路径（None则使用默认target_path）
        
    Returns:
        bool: 上传成功返回True，失败返回False
    """
```

### 客户端使用示例

#### 基础用法

```python
from src.protocols.ftp import FTPClientUploader

# 1. 创建客户端实例
uploader = FTPClientUploader(
    ftp_host="192.168.1.100",     # FTP服务器地址
    ftp_port=2121,                # FTP服务器端口
    ftp_user="upload_user",       # 用户名
    ftp_pass="secure_pass",       # 密码
    source_folder="D:/photos",    # 本地待上传文件夹
    target_path="/uploads",       # 服务器目标路径
    backup_folder="D:/backup"     # 本地备份文件夹
)

# 2. 测试连接
success, msg = uploader.test_connection()
if success:
    print("连接测试成功")
else:
    print(f"连接测试失败: {msg}")
    exit(1)

# 3. 启动监控上传（每10秒检查一次）
uploader.start_monitoring(interval=10)
print("文件监控已启动")

# 4. 运行一段时间...
import time
time.sleep(3600)  # 运行1小时

# 5. 停止监控
uploader.stop_monitoring()
print("监控已停止")
```

#### 带日志回调

```python
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)

def log_handler(msg: str):
    """日志回调函数"""
    logging.info(msg)

# 创建带日志的客户端
uploader = FTPClientUploader(
    ftp_host="ftp.example.com",
    ftp_port=21,
    ftp_user="user",
    ftp_pass="pass",
    source_folder="./source",
    target_path="/remote",
    backup_folder="./backup",
    log_callback=log_handler  # 设置日志回调
)

uploader.start_monitoring(interval=30)  # 每30秒检查一次
```

#### NAT环境配置

```python
# NAT环境（如家庭路由器、公司防火墙）
uploader = FTPClientUploader(
    ftp_host="ftp.remote-server.com",
    ftp_port=21,
    ftp_user="remote_user",
    ftp_pass="remote_pass",
    source_folder="D:/local_files",
    target_path="/remote_uploads",
    backup_folder="D:/backup",
    passive_mode=True,  # 启用被动模式（NAT穿透）
    timeout=30,         # 增加超时（公网较慢）
    retry_times=5       # 增加重试次数
)
```

#### 安全连接（TLS/SSL）

```python
# 使用加密连接（FTPS）
uploader = FTPClientUploader(
    ftp_host="secure-ftp.example.com",
    ftp_port=990,          # FTPS常用端口
    ftp_user="secure_user",
    ftp_pass="StrongPass123!",
    source_folder="D:/sensitive_data",
    target_path="/secure_uploads",
    backup_folder="D:/backup",
    enable_tls=True,       # 启用TLS/SSL
    passive_mode=True
)
```

#### 上传单个文件

```python
# 上传指定文件
uploader = FTPClientUploader(
    ftp_host="192.168.1.100",
    ftp_port=2121,
    ftp_user="user",
    ftp_pass="pass"
)

# 测试连接
success, msg = uploader.test_connection()
if not success:
    print(f"连接失败: {msg}")
    exit(1)

# 上传单个文件
success = uploader.upload_file(
    local_path="D:/report.pdf",
    remote_path="/reports/2025/report.pdf"
)

if success:
    print("文件上传成功")
else:
    print("文件上传失败")
```

---

## 混合模式

混合模式同时运行 FTP 服务器和客户端，实现双向传输。

### 使用场景

1. **中转站**: 接收设备A上传的文件，同时转发到设备C
2. **双向同步**: 本机既上传文件到远程，也接收其他设备的文件
3. **多点采集**: 多个采集设备上传到本机，本机汇总后上传到中心服务器

### 示例代码

```python
from src.protocols.ftp import FTPServerManager, FTPClientUploader

# 1. 启动FTP服务器（接收其他设备上传）
server = FTPServerManager(
    host="0.0.0.0",
    port=2121,
    username="local_server",
    password="server_pass",
    upload_dir="D:/incoming_files"  # 接收文件保存目录
)

if server.start():
    print("FTP服务器启动成功")
    print(f"其他设备可连接: {FTPServerManager.get_local_ip()}:2121")
else:
    print("FTP服务器启动失败")
    exit(1)

# 2. 启动FTP客户端（上传到远程服务器）
client = FTPClientUploader(
    ftp_host="remote-server.com",
    ftp_port=21,
    ftp_user="remote_user",
    ftp_pass="remote_pass",
    source_folder="D:/outgoing_files",  # 待上传文件夹
    target_path="/remote_uploads",
    backup_folder="D:/sent_files"       # 已上传文件备份
)

# 测试远程连接
success, msg = client.test_connection()
if not success:
    print(f"远程连接失败: {msg}")
    server.stop()
    exit(1)

# 启动客户端监控
client.start_monitoring(interval=60)  # 每分钟上传一次
print("FTP客户端监控已启动")

print("\n混合模式运行中...")
print("- 本机FTP服务器: 接收其他设备上传 -> D:/incoming_files")
print("- 本机FTP客户端: 上传 D:/outgoing_files -> remote-server.com")

# 运行一段时间
import time
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    print("\n停止混合模式...")
    client.stop_monitoring()
    server.stop()
    print("已停止")
```

### 注意事项

- 服务器和客户端使用不同的目录，避免循环上传
- 服务器端口不要与客户端连接的端口冲突
- 监控间隔根据文件数量和大小调整
- 确保磁盘空间充足

---

## 错误处理

### 常见异常

#### 连接异常

```python
from ftplib import error_perm, error_temp

try:
    uploader = FTPClientUploader(ftp_host="192.168.1.100", ...)
    success, msg = uploader.test_connection()
    if not success:
        print(f"连接失败: {msg}")
except error_perm as e:
    # 权限错误（用户名/密码错误）
    print(f"登录失败，请检查用户名和密码: {e}")
except error_temp as e:
    # 临时错误（服务器繁忙）
    print(f"服务器暂时不可用: {e}")
except TimeoutError as e:
    # 连接超时
    print(f"连接超时，请检查网络和服务器地址: {e}")
except OSError as e:
    # 网络错误
    print(f"网络错误: {e}")
```

#### 文件上传异常

```python
try:
    success = uploader.upload_file("D:/large_file.zip")
    if not success:
        print("文件上传失败")
except FileNotFoundError as e:
    print(f"文件不存在: {e}")
except PermissionError as e:
    print(f"文件权限不足: {e}")
except Exception as e:
    print(f"上传错误: {e}")
```

#### 服务器启动异常

```python
try:
    server = FTPServerManager(port=21)
    server.start()
except PermissionError as e:
    print("端口21需要管理员权限，请使用端口2121或以管理员身份运行")
except OSError as e:
    if "Address already in use" in str(e):
        print("端口被占用，请检查是否有其他FTP服务运行")
    else:
        print(f"服务器启动失败: {e}")
```

### 错误码说明

FTP 标准错误码（RFC 959）：

| 错误码 | 说明 |
|--------|------|
| 421 | 服务不可用，连接关闭 |
| 425 | 无法打开数据连接 |
| 426 | 连接关闭，传输中止 |
| 450 | 文件不可用（正在使用） |
| 451 | 操作中止（本地错误） |
| 452 | 磁盘空间不足 |
| 500 | 语法错误，命令无法识别 |
| 501 | 参数语法错误 |
| 502 | 命令未实现 |
| 503 | 命令顺序错误 |
| 530 | 未登录 |
| 550 | 文件不可用（权限拒绝） |
| 551 | 页面类型未知 |
| 552 | 超出存储空间限制 |
| 553 | 文件名不合法 |

---

## 性能优化建议

### 服务器端优化

1. **端口选择**
   - 使用高位端口（如2121）避免权限问题
   - 被动模式端口范围不要太大（100个端口足够）

2. **连接限制**
   - 根据实际需求设置 `max_cons` 和 `max_cons_per_ip`
   - 防止恶意连接消耗资源

3. **磁盘IO**
   - 上传目录使用高速磁盘（SSD）
   - 避免频繁的小文件写入

4. **网络配置**
   - 千兆网络: 可支持 5+ 并发客户端
   - 百兆网络: 建议 2-3 个并发
   - 公网: 根据带宽限制连接数

### 客户端端优化

1. **监控间隔**
   - 文件少: 10-30秒
   - 文件多: 60-300秒
   - 避免过于频繁的检查

2. **超时设置**
   - 局域网: 10秒
   - 公网: 30-60秒
   - 大文件: 适当增加

3. **重试策略**
   - 局域网: 3次
   - 公网: 5-10次
   - 间隔递增避免服务器压力

4. **被动模式**
   - NAT环境: 必须开启
   - 直连: 可选（主动模式更快）

### 性能基准（v2.1实测）

基于 `tests/test_performance.py` 的测试结果：

| 场景 | 性能 |
|------|------|
| 启动时间 | < 1毫秒 |
| 内存占用 | 28 MB |
| CPU占用 | < 1% |
| 本地上传速度 | 203 MB/s（回环） |
| 千兆网上传 | 110-120 MB/s |
| 百兆网上传 | 11-12 MB/s |
| 并发客户端 | 5个（无压力） |

详细测试报告: [v2.1_性能测试报告.md](v2.1_性能测试报告.md)

---

## 附录

### 参考资料

- [RFC 959 - FTP协议标准](https://tools.ietf.org/html/rfc959)
- [RFC 2228 - FTP安全扩展](https://tools.ietf.org/html/rfc2228)
- [Python ftplib 文档](https://docs.python.org/3/library/ftplib.html)
- [pyftpdlib 文档](https://pyftpdlib.readthedocs.io/)

### 版本历史

- **v2.1 (2025-11-13)**: 首次发布 FTP 协议支持
  - 新增 FTP 服务器模式
  - 新增 FTP 客户端模式
  - 新增混合模式
  - 性能测试全部通过

### 许可证

本文档和相关代码遵循项目主许可证。

---

**文档版本**: v2.1.0  
**最后更新**: 2025-11-13  
**维护者**: 开发团队
