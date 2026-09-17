# -*- coding: utf-8 -*-
"""
文件名：src/protocols/ftp.py
文件作用：网络传输协议实现模块“ftp”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

FTP 原子提交流程图：
服务器配置 → TLS 与被动端口设置 → 建立临时远端对象 → 上传字节流 →
确认当前连接与远端大小 → rename 为最终对象 → 成功；任一步失败 → 删除临时对象并返回失败。

FTP/FTPS 协议处理模块
支持 FTP 服务器和客户端功能

版本: v2.0
日期: 2025-11-10
作者: 开发团队
"""

import os
import threading
import logging
import time
import datetime
import uuid
from pathlib import Path
from ftplib import FTP, FTP_TLS, error_perm
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
try:
    from pyftpdlib.handlers import TLS_FTPHandler
except ImportError:
    # 旧版本的 pyftpdlib 中 TLS_FTPHandler 可能不存在
    TLS_FTPHandler = None  # type: ignore
from pyftpdlib.servers import FTPServer
from typing import Optional, Callable, Tuple, Union

from src.models import FTPOperationResult

# 配置日志
logger = logging.getLogger(__name__)
class FTPServerManager:
    """
    FTP 服务器管理器

    功能：
    - 启动/停止 FTP 服务器
    - 用户认证管理
    - 共享目录配置
    - 被动模式支持
    - FTPS (TLS/SSL) 支持
    - 连接数限制
    - 状态监控
    """

    def __init__(self, config: dict):
        """
        初始化 FTP 服务器

        Args:
            config: 配置字典
                {
                    'host': '0.0.0.0',              # 监听地址
                    'port': 21,                      # 端口
                    'username': 'upload_user',       # FTP 用户名
                    'password': 'upload_pass',       # FTP 密码
                    'shared_folder': 'D:/FTP_Share', # 共享目录
                    'enable_tls': False,             # 是否启用 TLS
                    'cert_file': '',                 # TLS 证书文件
                    'key_file': '',                  # TLS 密钥文件
                    'passive_ports': (60000, 65535), # 被动模式端口范围
                    'max_cons': 256,                 # 最大连接数
                    'max_cons_per_ip': 5,            # 每个IP最大连接数
                }
        """
        self.config = config
        self.server: Optional[FTPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
        self._stop_event = threading.Event()
        self.event_callback: Optional[Callable[[dict], None]] = config.get('event_callback')

        # 确保共享目录存在
        shared_folder = Path(config.get('shared_folder', 'D:/FTP_Share'))
        shared_folder.mkdir(parents=True, exist_ok=True)

        logger.info(f"FTP 服务器管理器初始化: {config.get('host')}:{config.get('port')}")

    def _emit_event(self, event: str, **payload):
        """发送 FTP 服务器事件，供 UI 或日志层记录。"""
        event_data = {
            'event': event,
            'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
            'client_ip': payload.pop('client_ip', ''),
            'username': payload.pop('username', ''),
            'path': payload.pop('path', ''),
            'size': payload.pop('size', ''),
            'message': payload.pop('message', ''),
        }
        event_data.update(payload)
        callback = self.event_callback
        if callback:
            try:
                callback(event_data)
            except Exception as e:
                logger.debug(f"FTP事件回调失败: {type(e).__name__}: {e}")

    def _isolate_incomplete_upload(self, file_path: str) -> str:
        """Move an incomplete task temporary object out of the delivery tree."""
        source = Path(file_path)
        if not source.is_file():
            return ""
        try:
            quarantine_dir = source.parent / ".incomplete"
            quarantine_dir.mkdir(exist_ok=True)
            destination = quarantine_dir / source.name
            if destination.exists():
                destination = quarantine_dir / f"{source.name}.{uuid.uuid4().hex[:8]}"
            os.replace(source, destination)
            return str(destination)
        except OSError as exc:
            logger.warning("FTP 不完整文件隔离失败 %s: %s", source, exc)
            return ""

    def start(self) -> bool:
        """
        启动 FTP 服务器

        Returns:
            bool: 启动是否成功
        """
        if self.is_running:
            logger.warning("FTP 服务器已在运行")
            return False

        try:
            # 创建授权器
            authorizer = DummyAuthorizer()

            # 添加用户（可读写）
            username = self.config.get('username', 'upload_user')
            password = self.config.get('password', 'upload_pass')
            shared_folder = str(self.config.get('shared_folder', 'D:/FTP_Share'))

            authorizer.add_user(
                username=username,
                password=password,
                homedir=shared_folder,
                perm='elradfmwMT'  # 完整权限
            )

            logger.info(f"已添加 FTP 用户: {username}")

            manager = self

            def _file_size(file_path: str) -> int:
                """内部辅助：完成“_file_size”对应的既有局部工作。"""
                try:
                    return os.path.getsize(file_path)
                except OSError:
                    return 0

            class EventHandlerMixin:
                def on_connect(self):
                    """作用：执行“on_connect”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    manager._emit_event(
                        'connect',
                        client_ip=getattr(self, 'remote_ip', ''),
                        message='客户端已连接'
                    )

                def on_disconnect(self):
                    """作用：执行“on_disconnect”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    manager._emit_event(
                        'disconnect',
                        client_ip=getattr(self, 'remote_ip', ''),
                        username=getattr(self, 'username', ''),
                        message='客户端已断开'
                    )

                def on_login(self, username):
                    """作用：执行“on_login”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    manager._emit_event(
                        'login_ok',
                        client_ip=getattr(self, 'remote_ip', ''),
                        username=username,
                        message='登录成功'
                    )

                def on_login_failed(self, username, password):
                    """作用：执行“on_login_failed”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    manager._emit_event(
                        'login_failed',
                        client_ip=getattr(self, 'remote_ip', ''),
                        username=username,
                        message='登录失败'
                    )

                def on_file_received(self, file):
                    """作用：执行“on_file_received”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    manager._emit_event(
                        'upload_ok',
                        client_ip=getattr(self, 'remote_ip', ''),
                        username=getattr(self, 'username', ''),
                        path=str(file),
                        size=_file_size(str(file)),
                        message='文件上传成功'
                    )

                def on_incomplete_file_received(self, file):
                    """作用：执行“on_incomplete_file_received”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    isolated_path = manager._isolate_incomplete_upload(str(file))
                    manager._emit_event(
                        'upload_incomplete',
                        client_ip=getattr(self, 'remote_ip', ''),
                        username=getattr(self, 'username', ''),
                        path=isolated_path or str(file),
                        size=_file_size(isolated_path or str(file)),
                        message='文件上传未完成，已隔离' if isolated_path else '文件上传未完成，隔离失败'
                    )

            class EventFTPHandler(EventHandlerMixin, FTPHandler):
                pass

            if self.config.get('enable_tls', False):
                # FTPS 处理器
                if TLS_FTPHandler is None:
                    logger.error("当前 pyftpdlib 版本不支持 FTPS，请升级或禁用 TLS")
                    self._emit_event('error', message='当前 pyftpdlib 版本不支持 FTPS，请升级或禁用 TLS')
                    return False
                cert_file = str(self.config.get('cert_file', '')).strip()
                key_file = str(self.config.get('key_file', '')).strip()
                if not os.path.isfile(cert_file) or not os.path.isfile(key_file):
                    message = 'FTPS 证书或私钥文件不存在，已拒绝启动'
                    logger.error(message)
                    self._emit_event('error', message=message)
                    return False
                class EventTLSFTPHandler(EventHandlerMixin, TLS_FTPHandler):  # type: ignore[misc, valid-type]
                    pass
                handler = EventTLSFTPHandler
                setattr(handler, 'certfile', cert_file)
                setattr(handler, 'keyfile', key_file)
                handler.tls_control_required = True
                handler.tls_data_required = True
                logger.info("使用 FTPS (TLS/SSL) 加密")
            else:
                # 普通 FTP 处理器
                handler = EventFTPHandler
                logger.info("使用普通 FTP 协议（无加密）")

            handler.authorizer = authorizer

            # 设置被动模式端口范围
            enable_passive = self.config.get('enable_passive', True)
            passive_ports = self.config.get('passive_ports')
            if passive_ports is None:
                passive_start = self.config.get('passive_ports_start', 60000)
                passive_end = self.config.get('passive_ports_end', 65535)
                passive_ports = (passive_start, passive_end)
            if enable_passive and isinstance(passive_ports, (list, tuple)) and len(passive_ports) == 2:
                try:
                    start_port, end_port = int(passive_ports[0]), int(passive_ports[1])
                except Exception:
                    start_port, end_port = 60000, 65535
                passive_ports = (start_port, end_port)
                if start_port <= end_port:
                    handler.passive_ports = range(start_port, end_port + 1)  # type: ignore
                else:
                    handler.passive_ports = range(end_port, start_port + 1)  # type: ignore

            # 设置 banner
            handler.banner = "图片异步上传工具 v2.0 FTP 服务器"

            # 设置超时
            handler.timeout = 300  # 5分钟超时

            # 创建服务器
            host = self.config.get('host', '0.0.0.0')
            port = self.config.get('port', 21)

            self.server = FTPServer((host, port), handler)

            # 设置连接限制
            self.server.max_cons = self.config.get('max_cons', self.config.get('max_connections', 256))
            self.server.max_cons_per_ip = self.config.get('max_cons_per_ip', self.config.get('max_connections_per_ip', 5))

            logger.info(f"FTP 服务器配置完成: {host}:{port}")
            logger.info(f"共享目录: {shared_folder}")
            if enable_passive and isinstance(passive_ports, (list, tuple)) and len(passive_ports) == 2:
                logger.info(f"被动端口范围: {passive_ports[0]}-{passive_ports[1]}")
            else:
                logger.info("被动端口范围: 默认")
            logger.info(f"最大连接数: {self.server.max_cons}")
            logger.info(f"单IP最大连接数: {self.server.max_cons_per_ip}")

            # 在新线程中启动服务器
            self._stop_event.clear()
            self.server_thread = threading.Thread(
                target=self._run_server,
                daemon=True,
                name="FTPServerThread"
            )
            self.server_thread.start()

            # 等待服务器启动
            time.sleep(0.5)

            self.is_running = True
            logger.info("✓ FTP 服务器已启动")
            self._emit_event(
                'server_started',
                message='FTP 服务器已启动',
                path=shared_folder,
                address=f"{host}:{port}"
            )
            return True

        except PermissionError as e:
            logger.error(f"权限错误：{e}。端口 < 1024 需要管理员权限")
            self._emit_event('error', message=f"权限错误：{e}。端口 < 1024 需要管理员权限")
            return False
        except OSError as e:
            if "Address already in use" in str(e) or "10048" in str(e):
                logger.error(f"端口被占用：{self.config.get('port')}。请更换端口或关闭占用端口的程序")
                self._emit_event('error', message=f"端口被占用：{self.config.get('port')}")
            else:
                logger.error(f"启动 FTP 服务器失败：{e}")
                self._emit_event('error', message=f"启动 FTP 服务器失败：{e}")
            return False
        except Exception as e:
            logger.error(f"启动 FTP 服务器失败：{e}")
            self._emit_event('error', message=f"启动 FTP 服务器失败：{e}")
            import traceback
            traceback.print_exc()
            return False

    def _run_server(self):
        """运行 FTP 服务器（在独立线程中）"""
        try:
            logger.info("FTP 服务器线程开始运行")
            if self.server:
                self.server.serve_forever()
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"FTP 服务器运行错误：{e}")
            self.is_running = False
        finally:
            logger.info("FTP 服务器线程已退出")

    def stop(self) -> bool:
        """
        停止 FTP 服务器

        Returns:
            bool: 停止是否成功
        """
        if not self.is_running:
            logger.warning("FTP 服务器未运行")
            return False

        try:
            logger.info("正在停止 FTP 服务器...")
            self._stop_event.set()

            if self.server:
                self.server.close_all()

            self.is_running = False
            logger.info("✓ FTP 服务器已停止")
            self._emit_event('server_stopped', message='FTP 服务器已停止')
            return True

        except Exception as e:
            logger.error(f"停止 FTP 服务器失败：{e}")
            return False

    def get_status(self) -> dict:
        """
        获取服务器状态

        Returns:
            dict: 服务器状态信息
        """
        if not self.is_running or not self.server:
            return {
                'running': False,
                'connections': 0,
                'address': None,
                'shared_folder': None,
                'tls_enabled': False
            }

        # 获取当前连接数
        connection_count = 0
        try:
            # Note: _map 是 asyncore.dispatcher 的内部属性
            connection_count = len(self.server._map) if hasattr(self.server, '_map') else 0  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # _map 属性访问或len()可能失败（预期情况）
            connection_count = 0
        except Exception as e:
            # 意外错误，记录但不影响返回
            print(f"⚠️ FTP状态获取异常: {type(e).__name__}: {str(e)[:100]}")
            connection_count = 0

        return {
            'running': True,
            'connections': connection_count,
            'address': f"{self.config.get('host')}:{self.config.get('port')}",
            'shared_folder': self.config.get('shared_folder'),
            'tls_enabled': self.config.get('enable_tls', False),
            'max_connections': self.config.get('max_cons', self.config.get('max_connections', 256)),
            'max_connections_per_ip': self.config.get('max_cons_per_ip', self.config.get('max_connections_per_ip', 5))
        }

    def __del__(self):
        """析构函数，确保服务器被关闭"""
        if self.is_running:
            self.stop()


class FTPClientUploader:
    """
    FTP 客户端上传器

    功能：
    - 连接到 FTP 服务器
    - 用户认证
    - 上传单个文件
    - 上传整个文件夹（保持目录结构）
    - 被动/主动模式切换
    - FTPS 支持
    - 进度回调
    - 连接测试
    - 重试机制
    - 超时处理
    """

    def __init__(self, config: dict):
        """
        初始化 FTP 客户端

        Args:
            config: 配置字典
                {
                    'name': 'FTP客户端1',            # 客户端名称
                    'host': 'ftp.example.com',      # FTP 服务器地址
                    'port': 21,                      # FTP 端口
                    'username': 'upload_user',       # 用户名
                    'password': 'upload_pass',       # 密码
                    'remote_path': '/upload/photos', # 远程目标路径
                    'enable_tls': False,             # 是否启用 FTPS
                    'passive_mode': True,            # 被动模式
                    'timeout': 30,                   # 超时时间（秒）
                    'retry_count': 3,                # 重试次数
                }
        """
        self.config = config
        self.ftp: Optional[Union[FTP, FTP_TLS]] = None
        self.connected = False
        # `_lock` serializes connect and transfer operations.  Connection state
        # has a separate lock because disconnect/cancel must be able to close a
        # blocked socket without waiting for a long-running STOR call.
        self._lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._connection_epoch = 0
        self._cancel_event = threading.Event()

        logger.info(f"FTP 客户端初始化: {config.get('name', 'Unknown')} -> {config.get('host')}")

    def connect(
        self,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        连接到 FTP 服务器

        Returns:
            bool: 连接是否成功
        """
        self._cancel_event.clear()

        def cancelled() -> bool:
            """作用：执行“cancelled”的既有业务或基础设施职责。

            参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件或异常语义。
            执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
            风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
            """
            return self._cancel_event.is_set() or bool(
                cancel_event is not None and cancel_event.is_set()
            )

        def wait_for_retry(seconds: float) -> bool:
            """作用：执行“wait_for_retry”的既有业务或基础设施职责。

            参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件或异常语义。
            执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
            风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
            """
            deadline = time.monotonic() + max(0.0, seconds)
            while not cancelled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                self._cancel_event.wait(min(0.1, remaining))
            return False

        with self._lock:
            with self._connection_lock:
                already_connected = self.connected
            if already_connected:
                logger.warning("已连接到 FTP 服务器")
                return True

            retry_count = max(1, int(self.config.get('retry_count', 3)))

            for attempt in range(retry_count):
                if cancelled():
                    logger.info("FTP 连接已取消")
                    return False
                if progress_callback:
                    try:
                        progress_callback(attempt + 1, retry_count)
                    except Exception:
                        pass
                candidate: Optional[Union[FTP, FTP_TLS]] = None
                try:
                    logger.info(f"连接 FTP 服务器 (尝试 {attempt + 1}/{retry_count})...")

                    # 创建 FTP 对象
                    if self.config.get('enable_tls', False):
                        # FTPS 连接
                        candidate = FTP_TLS()
                        logger.info("使用 FTPS (TLS/SSL) 连接")
                    else:
                        # 普通 FTP 连接
                        candidate = FTP()
                        logger.info("使用普通 FTP 连接")
                    # Publish the candidate before I/O so disconnect() can
                    # actively close a connect/login operation.  The epoch
                    # binds every later upload confirmation to this exact
                    # connection generation.
                    with self._connection_lock:
                        if cancelled():
                            candidate.close()
                            logger.info("FTP 连接在开始前已取消")
                            return False
                        self.ftp = candidate
                        self.connected = False
                        self._connection_epoch += 1

                    # 连接
                    host = str(self.config.get('host', ''))
                    candidate.connect(
                        host=host,
                        port=self.config.get('port', 21),
                        timeout=self.config.get('timeout', 30)
                    )

                    # 登录
                    username = str(self.config.get('username', ''))
                    password = str(self.config.get('password', ''))
                    candidate.login(
                        user=username,
                        passwd=password
                    )

                    # FTPS 启用数据连接加密
                    if self.config.get('enable_tls', False) and isinstance(candidate, FTP_TLS):
                        candidate.prot_p()

                    # 设置被动/主动模式
                    if self.config.get('passive_mode', True):
                        candidate.set_pasv(True)
                        logger.info("使用被动模式")
                    else:
                        candidate.set_pasv(False)
                        logger.info("使用主动模式")

                    # 设置编码
                    candidate.encoding = 'utf-8'

                    with self._connection_lock:
                        is_current_candidate = self.ftp is candidate
                        if not cancelled() and is_current_candidate:
                            self.connected = True
                            logger.info(f"✓ 已连接到 FTP 服务器：{self.config.get('host')}")
                            return True
                    if cancelled() or not is_current_candidate:
                        candidate.close()
                        logger.info("FTP 连接在完成前已取消")
                        return False

                except Exception as e:
                    logger.error(f"连接失败 (尝试 {attempt + 1}/{retry_count})：{e}")

                    if candidate:
                        try:
                            candidate.close()
                        except (OSError, IOError, error_perm):
                            # 连接已关闭或无效，预期情况
                            pass
                        except Exception as e:
                            # 意外的关闭错误
                            logger.debug(f"FTP关闭异常: {type(e).__name__}: {e}")
                    with self._connection_lock:
                        if self.ftp is candidate:
                            self.ftp = None
                            self.connected = False
                            self._connection_epoch += 1
                    if cancelled():
                        logger.info("FTP 连接已取消")
                        return False
                    if attempt < retry_count - 1:
                        wait_time = (attempt + 1) * 5  # 5秒, 10秒
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        if not wait_for_retry(wait_time):
                            logger.info("FTP 重试等待已取消")
                            return False
                    else:
                        logger.error("连接 FTP 服务器失败，已达最大重试次数")

            with self._connection_lock:
                self.connected = False
            return False

    def disconnect(self) -> bool:
        """
        断开 FTP 连接

        Returns:
            bool: 断开是否成功
        """
        self._cancel_event.set()
        with self._connection_lock:
            ftp = self.ftp
            self.ftp = None
            was_active = self.connected or ftp is not None
            self.connected = False
            self._connection_epoch += 1
        if ftp is None:
            if not was_active:
                logger.warning("未连接到 FTP 服务器")
            return was_active
        try:
            # close() 不进行网络往返，可从其他线程打断 connect/login/传输。
            ftp.close()
            logger.info("✓ 已断开 FTP 连接")
            return True
        except Exception as e:
            logger.debug(f"FTP强制关闭异常: {type(e).__name__}: {e}")
            return False

    def cancel(self) -> None:
        """非阻塞请求取消连接、重试等待或当前 FTP 操作。"""
        self.disconnect()

    def upload_file(
        self,
        local_path: Path,
        remote_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        enable_speed_limit: bool = False,
        speed_limit_mbps: int = 10
    ) -> bool:
        """Compatibility wrapper around the structured transfer result."""
        return self.upload_file_result(
            local_path,
            remote_path,
            progress_callback,
            enable_speed_limit,
            speed_limit_mbps,
        ).success

    def upload_file_result(
        self,
        local_path: Path,
        remote_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        enable_speed_limit: bool = False,
        speed_limit_mbps: int = 10,
    ) -> FTPOperationResult:
        """Upload once and report success only after explicit confirmation."""

        def make_result(
            success: bool,
            code: str,
            message: str,
            *,
            command_executed: bool = False,
            normalized_remote: str = "",
            local_size: int = -1,
            uploaded_bytes: int = 0,
            remote_size: Optional[int] = None,
            response: str = "",
        ) -> FTPOperationResult:
            """作用：执行“make_result”的既有业务或基础设施职责。

            参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
            返回结果：沿用当前实现的返回值、事件或异常语义。
            执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
            风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
            """
            return FTPOperationResult(
                success,
                message,
                errors=() if success else (message,),
                status={
                    "code": code,
                    "command_executed": command_executed,
                    "remote_path": normalized_remote,
                    "local_size": local_size,
                    "uploaded_bytes": uploaded_bytes,
                    "remote_size": remote_size,
                    "response": response,
                },
            )

        local_file = Path(local_path)
        if not local_file.is_file():
            message = f"文件不存在：{local_path}"
            logger.error(message)
            return make_result(False, "local_missing", message)

        if remote_path is None:
            base_remote = self.config.get('remote_path', '/')
            remote_path = f"{base_remote}/{local_file.name}"
        normalized_remote = remote_path.replace('\\', '/')
        if len(normalized_remote) > 2 and normalized_remote[1] == ':':
            normalized_remote = normalized_remote[2:]
        if not normalized_remote.startswith('/'):
            normalized_remote = '/' + normalized_remote

        file_size = local_file.stat().st_size
        uploaded_bytes = 0
        command_executed = False
        response_text = ""
        temporary_remote = (
            f"{normalized_remote}.part-{uuid.uuid4().hex[:12]}"
        )
        committed = False
        with self._lock:
            with self._connection_lock:
                ftp = self.ftp
                connection_epoch = self._connection_epoch
                connection_ready = (
                    self.connected and ftp is not None and not self._cancel_event.is_set()
                )
            if not connection_ready or ftp is None:
                message = "FTP 连接不可用，未执行上传命令"
                logger.error(message)
                return make_result(
                    False,
                    "connection_missing",
                    message,
                    normalized_remote=normalized_remote,
                    local_size=file_size,
                )

            try:
                remote_dir = os.path.dirname(normalized_remote)
                if not self._ensure_remote_dir(remote_dir, ftp):
                    message = f"无法确认或创建 FTP 远端目录：{remote_dir}"
                    logger.error(message)
                    return make_result(
                        False,
                        "remote_directory_error",
                        message,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                    )

                speed_limit_bytes_per_sec = (
                    speed_limit_mbps * 1024 * 1024 if enable_speed_limit else 0
                )
                last_chunk_time = time.time()

                def callback(block: bytes) -> None:
                    """作用：执行“callback”的既有业务或基础设施职责。

                    参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
                    返回结果：沿用当前实现的返回值、事件或异常语义。
                    执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
                    风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
                    """
                    nonlocal uploaded_bytes, last_chunk_time
                    chunk_start = last_chunk_time
                    uploaded_bytes += len(block)
                    if progress_callback:
                        progress_callback(uploaded_bytes, file_size)
                    if enable_speed_limit and speed_limit_bytes_per_sec > 0:
                        expected_time = len(block) / speed_limit_bytes_per_sec
                        actual_time = time.time() - chunk_start
                        if actual_time < expected_time:
                            time.sleep(expected_time - actual_time)
                    last_chunk_time = time.time()

                command_executed = True
                with open(local_file, 'rb') as stream:
                    response = ftp.storbinary(
                        f'STOR {temporary_remote}', stream, callback=callback
                    )
                response_text = str(response or "")
                if not response_text.startswith("2"):
                    message = f"FTP STOR 未返回成功响应：{response_text or '空响应'}"
                    logger.error(message)
                    return make_result(
                        False,
                        "response_unconfirmed",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        response=response_text,
                    )
                if uploaded_bytes != file_size:
                    message = (
                        f"FTP 已发送字节数不匹配：本地 {file_size}，已发送 {uploaded_bytes}"
                    )
                    logger.error(message)
                    return make_result(
                        False,
                        "byte_count_mismatch",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        response=response_text,
                    )
                if not self._is_current_connection(ftp, connection_epoch):
                    message = "FTP 连接在上传确认前已断开"
                    logger.error(message)
                    return make_result(
                        False,
                        "connection_lost",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        response=response_text,
                    )
                try:
                    remote_size_value = ftp.size(temporary_remote)
                    remote_size = (
                        int(remote_size_value) if remote_size_value is not None else None
                    )
                except Exception as exc:
                    message = f"FTP 远端大小确认失败：{type(exc).__name__}: {exc}"
                    logger.error(message)
                    return make_result(
                        False,
                        "remote_size_unavailable",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        response=response_text,
                    )
                if remote_size != file_size:
                    message = f"FTP 远端大小不匹配：本地 {file_size}，远端 {remote_size}"
                    logger.error(message)
                    return make_result(
                        False,
                        "remote_size_mismatch",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        remote_size=remote_size,
                        response=response_text,
                    )

                # This locked check is the linearization point for a
                # successful upload.  If disconnect() completed before it,
                # the connection generation no longer matches and this call
                # must fail rather than allow archival of the source file.
                if not self._is_current_connection(ftp, connection_epoch):
                    message = "FTP 连接在远端确认完成前已断开"
                    logger.error(message)
                    return make_result(
                        False,
                        "connection_lost",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        remote_size=remote_size,
                        response=response_text,
                    )

                try:
                    rename_response = ftp.rename(temporary_remote, normalized_remote)
                except Exception as exc:
                    message = f"FTP 最终提交失败：{type(exc).__name__}: {exc}"
                    logger.error(message)
                    return make_result(
                        False,
                        "rename_failed",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        remote_size=remote_size,
                        response=response_text,
                    )
                if not str(rename_response or "").startswith("2"):
                    message = f"FTP 最终提交未确认：{rename_response}"
                    logger.error(message)
                    return make_result(
                        False,
                        "rename_unconfirmed",
                        message,
                        command_executed=True,
                        normalized_remote=normalized_remote,
                        local_size=file_size,
                        uploaded_bytes=uploaded_bytes,
                        remote_size=remote_size,
                        response=str(rename_response),
                    )
                committed = True
                message = (
                    f"文件上传已确认：{local_file.name} → {normalized_remote} "
                    f"({file_size} 字节)"
                )
                logger.info(message)
                return make_result(
                    True,
                    "confirmed",
                    message,
                    command_executed=True,
                    normalized_remote=normalized_remote,
                    local_size=file_size,
                    uploaded_bytes=uploaded_bytes,
                    remote_size=remote_size,
                    response=response_text,
                )
            except error_perm as exc:
                message = f"FTP 权限错误，上传失败：{exc}"
                logger.error(message)
                return make_result(
                    False,
                    "permission_denied",
                    message,
                    command_executed=command_executed,
                    normalized_remote=normalized_remote,
                    local_size=file_size,
                    uploaded_bytes=uploaded_bytes,
                    response=response_text,
                )
            except Exception as exc:
                message = f"FTP 上传失败：{type(exc).__name__}: {exc}"
                logger.error(message)
                return make_result(
                    False,
                    "transfer_error",
                    message,
                    command_executed=command_executed,
                    normalized_remote=normalized_remote,
                    local_size=file_size,
                    uploaded_bytes=uploaded_bytes,
                    response=response_text,
                )
            finally:
                if not committed and command_executed:
                    try:
                        ftp.delete(temporary_remote)
                    except Exception:
                        logger.debug("FTP 临时对象清理失败: %s", temporary_remote)

    def _is_current_connection(
        self,
        ftp: Union[FTP, FTP_TLS],
        connection_epoch: int,
    ) -> bool:
        """Return whether *ftp* is still the live, uncancelled generation."""
        with self._connection_lock:
            return (
                self.ftp is ftp
                and self.connected
                and self._connection_epoch == connection_epoch
                and not self._cancel_event.is_set()
            )

    def upload_folder(
        self,
        local_folder: Path,
        remote_base: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[int, int]:
        """
        上传整个文件夹

        Args:
            local_folder: 本地文件夹路径
            remote_base: 远程基础路径（可选）
            progress_callback: 进度回调函数 callback(current, total, filename)

        Returns:
            tuple: (成功数, 失败数)
        """
        if not self.connected:
            logger.error("未连接到 FTP 服务器")
            return (0, 0)

        local_folder = Path(local_folder)
        if not local_folder.exists():
            logger.error(f"文件夹不存在：{local_folder}")
            return (0, 0)

        # 收集所有文件
        all_files = list(local_folder.rglob('*'))
        all_files = [f for f in all_files if f.is_file()]

        total = len(all_files)
        success = 0
        failed = 0

        # 确定远程基础路径
        if remote_base is None:
            remote_base = self.config.get('remote_path', '/')

        logger.info(f"开始上传文件夹：{local_folder} → {remote_base} (共 {total} 个文件)")

        for i, file_path in enumerate(all_files, 1):
            try:
                # 计算相对路径
                rel_path = file_path.relative_to(local_folder)
                remote_path = f"{remote_base}/{rel_path.as_posix()}"

                # 上传文件
                if self.upload_file(file_path, remote_path):
                    success += 1
                else:
                    failed += 1

                # 调用回调
                if progress_callback:
                    progress_callback(i, total, file_path.name)

            except Exception as e:
                logger.error(f"上传文件失败 {file_path.name}：{e}")
                failed += 1

        logger.info(f"✓ 文件夹上传完成：成功 {success}，失败 {failed}")
        return (success, failed)

    def _ensure_remote_dir(
        self,
        remote_dir: str,
        ftp_connection: Optional[Union[FTP, FTP_TLS]] = None,
    ) -> bool:
        """
        确保远程目录存在

        Args:
            remote_dir: 远程目录路径
        """
        if not remote_dir or remote_dir == '/' or remote_dir == '.':
            return True

        # 标准化路径
        remote_dir = remote_dir.replace('\\', '/').strip('/')

        if not remote_dir:
            return True

        ftp = ftp_connection or self.ftp
        if ftp is None:
            return False

        try:
            # 尝试切换到目录
            current = ftp.pwd()
            try:
                ftp.cwd(remote_dir)
                ftp.cwd(current)  # 切换回原目录
                return True  # 目录存在
            except error_perm:
                # 目录不存在（预期情况），需要创建
                pass
            except Exception as e:
                # 意外的目录检查错误
                logger.debug(f"FTP目录检查异常: {type(e).__name__}: {e}")
                return False  # 出错时不创建

            # 递归创建目录
            parts = remote_dir.split('/')
            current_path = ''
            for part in parts:
                if not part:
                    continue
                current_path += f'/{part}'
                try:
                    ftp.mkd(current_path)
                    logger.debug(f"创建目录：{current_path}")
                except error_perm:
                    pass  # 目录可能已存在
                except Exception as e:
                    logger.debug(f"创建目录失败 {current_path}：{e}")
            return True

        except Exception as e:
            logger.warning(f"确保远程目录存在时出错：{e}")
            return False

    def test_connection(
        self,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        测试连接

        Returns:
            bool: 连接测试是否成功
        """
        try:
            if self.connect(cancel_event, progress_callback):
                if cancel_event is not None and cancel_event.is_set():
                    self.disconnect()
                    return False
                # 测试列出目录
                if self.ftp:
                    self.ftp.nlst()
                self.disconnect()
                return True
            return False
        except Exception as e:
            logger.error(f"连接测试失败：{e}")
            return False

    def get_status(self) -> dict:
        """
        获取客户端状态

        Returns:
            dict: 客户端状态信息
        """
        return {
            'name': self.config.get('name', 'Unknown'),
            'connected': self.connected,
            'host': self.config.get('host'),
            'port': self.config.get('port'),
            'remote_path': self.config.get('remote_path'),
            'tls_enabled': self.config.get('enable_tls', False),
            'passive_mode': self.config.get('passive_mode', True),
            'timeout': self.config.get('timeout', 30)
        }

    def __del__(self):
        """析构函数，确保连接被关闭"""
        if self.connected:
            try:
                self.disconnect()
            except Exception as e:
                # 析构时断开连接失败（可能已断开）
                logger.debug(f"FTPClient析构断开连接异常: {type(e).__name__}: {e}")


class FTPProtocolManager:
    """
    FTP 服务器生命周期管理器。

    功能：
    - 启动和停止内置 FTP 服务器
    - 获取服务器运行状态
    - 统一停止服务并处理生命周期异常

    工作模式：
    - 'none': 禁用 FTP（使用 SMB）
    - 'server': 仅 FTP 服务器
    """

    def __init__(self):
        """初始化协议管理器"""
        self.server: Optional[FTPServerManager] = None
        self.mode = 'none'  # 'server' or 'none'
        self._lock = threading.RLock()  # 使用可重入锁防止stop_all()中的死锁

        logger.info("FTP 协议管理器初始化")

    def start_server(self, config: dict) -> bool:
        """
        启动 FTP 服务器

        Args:
            config: 服务器配置

        Returns:
            bool: 启动是否成功
        """
        with self._lock:
            try:
                if self.server and self.server.is_running:
                    logger.warning("FTP 服务器已在运行")
                    return False

                self.server = FTPServerManager(config)

                if self.server.start():
                    self.mode = 'server'

                    logger.info(f"FTP 服务器已启动，当前模式：{self.mode}")
                    return True
                else:
                    self.server = None
                    return False

            except Exception as e:
                logger.error(f"启动 FTP 服务器失败：{e}")
                self.server = None
                return False

    def stop_server(self) -> bool:
        """
        停止 FTP 服务器

        Returns:
            bool: 停止是否成功
        """
        with self._lock:
            if not self.server:
                logger.warning("FTP 服务器未启动")
                return False

            result = self.server.stop()

            if result:
                self.server = None

                self.mode = 'none'

                logger.info(f"FTP 服务器已停止，当前模式：{self.mode}")

            return result

    def get_status(self) -> dict:
        """
        获取整体状态

        Returns:
            dict: 整体状态信息
        """
        return {
            'mode': self.mode,
            'server': self.server.get_status() if self.server else None,
        }

    def stop_all(self):
        """停止所有服务器和客户端"""
        with self._lock:
            logger.info("正在停止所有 FTP 服务...")

            # 停止服务器
            if self.server:
                self.stop_server()

            self.mode = 'none'
            logger.info("✓ 所有 FTP 服务已停止")

    def __del__(self):
        """析构函数，确保所有服务被关闭"""
        try:
            self.stop_all()
        except Exception as e:
            # 析构时停止服务失败（可能已停止）
            logger.debug(f"FTPManager析构停止服务异常: {type(e).__name__}: {e}")
