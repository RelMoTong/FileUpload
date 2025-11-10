# -*- coding: utf-8 -*-
"""
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
from typing import Optional, Callable, Tuple, Dict, List, Union, cast
from queue import Queue

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
        
        # 确保共享目录存在
        shared_folder = Path(config.get('shared_folder', 'D:/FTP_Share'))
        shared_folder.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"FTP 服务器管理器初始化: {config.get('host')}:{config.get('port')}")
    
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
            
            # 创建处理器
            if self.config.get('enable_tls', False):
                # FTPS 处理器
                if TLS_FTPHandler is None:
                    logger.error("当前 pyftpdlib 版本不支持 FTPS，请升级或禁用 TLS")
                    return False
                handler = TLS_FTPHandler
                handler.certfile = self.config.get('cert_file', 'cert.pem')
                handler.keyfile = self.config.get('key_file', 'key.pem')
                handler.tls_control_required = True
                handler.tls_data_required = True
                logger.info("使用 FTPS (TLS/SSL) 加密")
            else:
                # 普通 FTP 处理器
                handler = FTPHandler
                logger.info("使用普通 FTP 协议（无加密）")
            
            handler.authorizer = authorizer
            
            # 设置被动模式端口范围
            passive_ports = self.config.get('passive_ports', (60000, 65535))
            handler.passive_ports = range(passive_ports[0], passive_ports[1] + 1)  # type: ignore
            
            # 设置 banner
            handler.banner = "图片异步上传工具 v2.0 FTP 服务器"
            
            # 设置超时
            handler.timeout = 300  # 5分钟超时
            
            # 创建服务器
            host = self.config.get('host', '0.0.0.0')
            port = self.config.get('port', 21)
            
            self.server = FTPServer((host, port), handler)
            
            # 设置连接限制
            self.server.max_cons = self.config.get('max_cons', 256)
            self.server.max_cons_per_ip = self.config.get('max_cons_per_ip', 5)
            
            logger.info(f"FTP 服务器配置完成: {host}:{port}")
            logger.info(f"共享目录: {shared_folder}")
            logger.info(f"被动端口范围: {passive_ports[0]}-{passive_ports[1]}")
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
            return True
            
        except PermissionError as e:
            logger.error(f"权限错误：{e}。端口 < 1024 需要管理员权限")
            return False
        except OSError as e:
            if "Address already in use" in str(e) or "10048" in str(e):
                logger.error(f"端口被占用：{self.config.get('port')}。请更换端口或关闭占用端口的程序")
            else:
                logger.error(f"启动 FTP 服务器失败：{e}")
            return False
        except Exception as e:
            logger.error(f"启动 FTP 服务器失败：{e}")
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
            connection_count = len(self.server._map) if hasattr(self.server, '_map') else 0
        except:
            pass
        
        return {
            'running': True,
            'connections': connection_count,
            'address': f"{self.config.get('host')}:{self.config.get('port')}",
            'shared_folder': self.config.get('shared_folder'),
            'tls_enabled': self.config.get('enable_tls', False),
            'max_connections': self.config.get('max_cons', 256),
            'max_connections_per_ip': self.config.get('max_cons_per_ip', 5)
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
        self._lock = threading.Lock()
        
        logger.info(f"FTP 客户端初始化: {config.get('name', 'Unknown')} -> {config.get('host')}")
    
    def connect(self) -> bool:
        """
        连接到 FTP 服务器
        
        Returns:
            bool: 连接是否成功
        """
        with self._lock:
            if self.connected:
                logger.warning("已连接到 FTP 服务器")
                return True
            
            retry_count = self.config.get('retry_count', 3)
            
            for attempt in range(retry_count):
                try:
                    logger.info(f"连接 FTP 服务器 (尝试 {attempt + 1}/{retry_count})...")
                    
                    # 创建 FTP 对象
                    if self.config.get('enable_tls', False):
                        # FTPS 连接
                        self.ftp = FTP_TLS()
                        logger.info("使用 FTPS (TLS/SSL) 连接")
                    else:
                        # 普通 FTP 连接
                        self.ftp = FTP()
                        logger.info("使用普通 FTP 连接")
                    
                    # 连接
                    host = str(self.config.get('host', ''))
                    self.ftp.connect(
                        host=host,
                        port=self.config.get('port', 21),
                        timeout=self.config.get('timeout', 30)
                    )
                    
                    # 登录
                    username = str(self.config.get('username', ''))
                    password = str(self.config.get('password', ''))
                    self.ftp.login(
                        user=username,
                        passwd=password
                    )
                    
                    # FTPS 启用数据连接加密
                    if self.config.get('enable_tls', False) and isinstance(self.ftp, FTP_TLS):
                        self.ftp.prot_p()
                    
                    # 设置被动/主动模式
                    if self.config.get('passive_mode', True):
                        self.ftp.set_pasv(True)
                        logger.info("使用被动模式")
                    else:
                        self.ftp.set_pasv(False)
                        logger.info("使用主动模式")
                    
                    # 设置编码
                    self.ftp.encoding = 'utf-8'
                    
                    self.connected = True
                    logger.info(f"✓ 已连接到 FTP 服务器：{self.config.get('host')}")
                    return True
                    
                except Exception as e:
                    logger.error(f"连接失败 (尝试 {attempt + 1}/{retry_count})：{e}")
                    
                    if self.ftp:
                        try:
                            self.ftp.close()
                        except:
                            pass
                        self.ftp = None
                    
                    if attempt < retry_count - 1:
                        wait_time = (attempt + 1) * 5  # 5秒, 10秒, 15秒
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                    else:
                        logger.error("连接 FTP 服务器失败，已达最大重试次数")
            
            self.connected = False
            return False
    
    def disconnect(self) -> bool:
        """
        断开 FTP 连接
        
        Returns:
            bool: 断开是否成功
        """
        with self._lock:
            if not self.connected:
                logger.warning("未连接到 FTP 服务器")
                return False
            
            try:
                if self.ftp:
                    self.ftp.quit()
                    self.ftp = None
                
                self.connected = False
                logger.info("✓ 已断开 FTP 连接")
                return True
                
            except Exception as e:
                logger.error(f"断开 FTP 连接失败：{e}")
                
                # 强制关闭
                try:
                    if self.ftp:
                        self.ftp.close()
                        self.ftp = None
                except:
                    pass
                
                self.connected = False
                return False
    
    def upload_file(
        self,
        local_path: Path,
        remote_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        上传单个文件
        
        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径（可选，默认使用配置中的路径）
            progress_callback: 进度回调函数 callback(uploaded_bytes, total_bytes)
        
        Returns:
            bool: 上传是否成功
        """
        if not self.connected:
            logger.error("未连接到 FTP 服务器")
            return False
        
        try:
            local_file = Path(local_path)
            if not local_file.exists():
                logger.error(f"文件不存在：{local_path}")
                return False
            
            # 确定远程路径
            if remote_path is None:
                base_remote = self.config.get('remote_path', '/')
                remote_path = f"{base_remote}/{local_file.name}"
            
            # 确保远程目录存在
            remote_dir = os.path.dirname(remote_path).replace('\\', '/')
            self._ensure_remote_dir(remote_dir)
            
            # 获取文件大小
            file_size = local_file.stat().st_size
            uploaded_bytes = 0
            
            # 定义进度回调
            def callback(block):
                nonlocal uploaded_bytes
                uploaded_bytes += len(block)
                if progress_callback:
                    progress_callback(uploaded_bytes, file_size)
            
            # 上传文件（二进制模式）
            if self.ftp:
                with open(local_file, 'rb') as f:
                    self.ftp.storbinary(f'STOR {remote_path}', f, callback=callback)
            
            logger.info(f"✓ 文件上传成功：{local_file.name} → {remote_path} ({file_size} 字节)")
            return True
            
        except error_perm as e:
            logger.error(f"权限错误，上传失败：{e}")
            return False
        except Exception as e:
            logger.error(f"上传文件失败：{e}")
            return False
    
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
    
    def _ensure_remote_dir(self, remote_dir: str):
        """
        确保远程目录存在
        
        Args:
            remote_dir: 远程目录路径
        """
        if not remote_dir or remote_dir == '/' or remote_dir == '.':
            return
        
        # 标准化路径
        remote_dir = remote_dir.replace('\\', '/').strip('/')
        
        if not remote_dir:
            return
        
        if not self.ftp:
            return
            
        try:
            # 尝试切换到目录
            current = self.ftp.pwd()
            try:
                self.ftp.cwd(remote_dir)
                self.ftp.cwd(current)  # 切换回原目录
                return  # 目录存在
            except:
                pass  # 目录不存在，需要创建
            
            # 递归创建目录
            parts = remote_dir.split('/')
            current_path = ''
            for part in parts:
                if not part:
                    continue
                current_path += f'/{part}'
                try:
                    if self.ftp:
                        self.ftp.mkd(current_path)
                    logger.debug(f"创建目录：{current_path}")
                except error_perm:
                    pass  # 目录可能已存在
                except Exception as e:
                    logger.debug(f"创建目录失败 {current_path}：{e}")
            
        except Exception as e:
            logger.warning(f"确保远程目录存在时出错：{e}")
    
    def test_connection(self) -> bool:
        """
        测试连接
        
        Returns:
            bool: 连接测试是否成功
        """
        try:
            if self.connect():
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
            except:
                pass


class FTPProtocolManager:
    """
    FTP 协议管理器（统一管理服务器和客户端）
    
    功能：
    - 统一管理 FTP 服务器和客户端
    - 支持同时运行服务器 + 多个客户端
    - 模式切换（server / client / both / none）
    - 添加/删除客户端
    - 获取整体状态
    - 全局错误处理
    
    工作模式：
    - 'none': 禁用 FTP（使用 SMB）
    - 'server': 仅 FTP 服务器
    - 'client': 仅 FTP 客户端
    - 'both': 服务器 + 客户端（同时）
    """
    
    def __init__(self):
        """初始化协议管理器"""
        self.server: Optional[FTPServerManager] = None
        self.clients: Dict[str, FTPClientUploader] = {}
        self.mode = 'none'  # 'server', 'client', 'both', 'none'
        self._lock = threading.Lock()
        
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
                    # 更新模式
                    if self.mode == 'client':
                        self.mode = 'both'
                    else:
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
                
                # 更新模式
                if self.mode == 'both':
                    self.mode = 'client'
                else:
                    self.mode = 'none'
                
                logger.info(f"FTP 服务器已停止，当前模式：{self.mode}")
            
            return result
    
    def add_client(self, name: str, config: dict) -> bool:
        """
        添加 FTP 客户端
        
        Args:
            name: 客户端名称
            config: 客户端配置
        
        Returns:
            bool: 添加是否成功
        """
        with self._lock:
            try:
                if name in self.clients:
                    logger.warning(f"客户端已存在：{name}")
                    return False
                
                # 确保配置中有名称
                config['name'] = name
                
                client = FTPClientUploader(config)
                
                if client.connect():
                    self.clients[name] = client
                    
                    # 更新模式
                    if self.mode == 'server':
                        self.mode = 'both'
                    elif self.mode == 'none':
                        self.mode = 'client'
                    
                    logger.info(f"FTP 客户端已添加：{name}，当前模式：{self.mode}")
                    return True
                else:
                    return False
                    
            except Exception as e:
                logger.error(f"添加 FTP 客户端失败：{e}")
                return False
    
    def remove_client(self, name: str) -> bool:
        """
        移除 FTP 客户端
        
        Args:
            name: 客户端名称
        
        Returns:
            bool: 移除是否成功
        """
        with self._lock:
            if name not in self.clients:
                logger.warning(f"客户端不存在：{name}")
                return False
            
            client = self.clients[name]
            client.disconnect()
            del self.clients[name]
            
            # 更新模式
            if not self.clients:
                if self.mode == 'both':
                    self.mode = 'server'
                else:
                    self.mode = 'none'
            
            logger.info(f"FTP 客户端已移除：{name}，当前模式：{self.mode}")
            return True
    
    def get_client(self, name: str) -> Optional[FTPClientUploader]:
        """
        获取指定的客户端
        
        Args:
            name: 客户端名称
        
        Returns:
            FTPClientUploader: 客户端对象，如果不存在返回 None
        """
        return self.clients.get(name)
    
    def upload_file(
        self,
        client_name: str,
        local_path: Path,
        remote_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        通过指定客户端上传文件
        
        Args:
            client_name: 客户端名称
            local_path: 本地文件路径
            remote_path: 远程文件路径
            progress_callback: 进度回调
        
        Returns:
            bool: 上传是否成功
        """
        client = self.get_client(client_name)
        if not client:
            logger.error(f"客户端不存在：{client_name}")
            return False
        
        return client.upload_file(local_path, remote_path, progress_callback)
    
    def upload_folder(
        self,
        client_name: str,
        local_folder: Path,
        remote_base: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[int, int]:
        """
        通过指定客户端上传文件夹
        
        Args:
            client_name: 客户端名称
            local_folder: 本地文件夹路径
            remote_base: 远程基础路径
            progress_callback: 进度回调
        
        Returns:
            tuple: (成功数, 失败数)
        """
        client = self.get_client(client_name)
        if not client:
            logger.error(f"客户端不存在：{client_name}")
            return (0, 0)
        
        return client.upload_folder(local_folder, remote_base, progress_callback)
    
    def get_status(self) -> dict:
        """
        获取整体状态
        
        Returns:
            dict: 整体状态信息
        """
        return {
            'mode': self.mode,
            'server': self.server.get_status() if self.server else None,
            'clients': {name: client.get_status() for name, client in self.clients.items()},
            'client_count': len(self.clients)
        }
    
    def stop_all(self):
        """停止所有服务器和客户端"""
        with self._lock:
            logger.info("正在停止所有 FTP 服务...")
            
            # 停止所有客户端
            for name in list(self.clients.keys()):
                self.remove_client(name)
            
            # 停止服务器
            if self.server:
                self.stop_server()
            
            self.mode = 'none'
            logger.info("✓ 所有 FTP 服务已停止")
    
    def __del__(self):
        """析构函数，确保所有服务被关闭"""
        try:
            self.stop_all()
        except:
            pass


# 模块级别的便捷函数

def create_ftp_server(config: dict) -> FTPServerManager:
    """
    便捷函数：创建 FTP 服务器
    
    Args:
        config: 服务器配置
    
    Returns:
        FTPServerManager: 服务器管理器实例
    """
    return FTPServerManager(config)


def create_ftp_client(config: dict) -> FTPClientUploader:
    """
    便捷函数：创建 FTP 客户端
    
    Args:
        config: 客户端配置
    
    Returns:
        FTPClientUploader: 客户端上传器实例
    """
    return FTPClientUploader(config)


# 示例用法（用于测试）
if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] %(message)s'
    )
    
    print("=" * 60)
    print("FTP 协议模块测试")
    print("=" * 60)
    print()
    
    # 测试 1: FTP 服务器
    print("测试 1: FTP 服务器")
    print("-" * 60)
    
    server_config = {
        'host': '0.0.0.0',
        'port': 2121,
        'username': 'test_user',
        'password': 'test_pass',
        'shared_folder': 'test_ftp_share',
        'enable_tls': False,
        'passive_ports': (60000, 65535),
        'max_cons': 256,
        'max_cons_per_ip': 5,
    }
    
    server = FTPServerManager(server_config)
    if server.start():
        print("✓ FTP 服务器启动成功")
        print(f"状态：{server.get_status()}")
        
        # 测试 2: FTP 客户端
        print()
        print("测试 2: FTP 客户端")
        print("-" * 60)
        
        client_config = {
            'name': '测试客户端',
            'host': '127.0.0.1',
            'port': 2121,
            'username': 'test_user',
            'password': 'test_pass',
            'remote_path': '/upload',
            'enable_tls': False,
            'passive_mode': True,
            'timeout': 30,
            'retry_count': 3,
        }
        
        client = FTPClientUploader(client_config)
        if client.connect():
            print("✓ FTP 客户端连接成功")
            print(f"状态：{client.get_status()}")
            
            # 创建测试文件
            test_file = Path("test_upload.txt")
            test_file.write_text("这是一个测试文件", encoding='utf-8')
            
            # 上传文件
            if client.upload_file(test_file):
                print("✓ 文件上传成功")
            
            # 清理
            test_file.unlink()
            client.disconnect()
        
        # 停止服务器
        server.stop()
    
    print()
    print("=" * 60)
    print("测试完成")
    print("=" * 60)
