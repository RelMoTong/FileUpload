# -*- coding: utf-8 -*-
"""
协议客户端 - 封装 SMB/FTP/Both 的具体实现

职责：
1. 统一的文件上传接口
2. 协议特定的连接管理
3. 智能重连机制
4. 错误分类和处理
"""

import os
import shutil
from pathlib import Path
from typing import Optional, Callable, Tuple
from enum import Enum
from ftplib import FTP, error_perm, error_temp


class ProtocolType(Enum):
    """协议类型"""
    SMB = "smb"  # Windows 共享
    FTP = "ftp"  # FTP 协议
    BOTH = "both"  # 双协议（先SMB后FTP）


class ConnectionStatus(Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class ProtocolClient:
    """协议客户端统一接口"""
    
    def __init__(self, protocol_type: ProtocolType):
        self.protocol_type = protocol_type
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._ftp_client: Optional[FTP] = None
        self._connection_failures = 0
        self._max_connection_failures = 3
        
        # FTP 配置
        self._ftp_host: Optional[str] = None
        self._ftp_port: int = 21
        self._ftp_user: Optional[str] = None
        self._ftp_password: Optional[str] = None
        
        # 回调
        self._on_connection_status_changed: Optional[Callable[[ConnectionStatus], None]] = None
    
    # ============ 连接管理 ============
    
    @property
    def connection_status(self) -> ConnectionStatus:
        """获取连接状态"""
        return self._connection_status
    
    def set_connection_status(self, status: ConnectionStatus):
        """设置连接状态"""
        if self._connection_status != status:
            self._connection_status = status
            if self._on_connection_status_changed:
                self._on_connection_status_changed(status)
    
    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connection_status == ConnectionStatus.CONNECTED
    
    def configure_ftp(self, host: str, port: int, user: str, password: str):
        """配置 FTP 连接参数"""
        self._ftp_host = host
        self._ftp_port = port
        self._ftp_user = user
        self._ftp_password = password
    
    def connect(self) -> Tuple[bool, Optional[str]]:
        """建立连接
        
        Returns:
            (success, error_message)
        """
        if self.protocol_type == ProtocolType.SMB:
            # SMB 不需要显式连接，直接返回成功
            self.set_connection_status(ConnectionStatus.CONNECTED)
            return True, None
        
        elif self.protocol_type in (ProtocolType.FTP, ProtocolType.BOTH):
            return self._connect_ftp()
        
        return False, "Unknown protocol type"
    
    def _connect_ftp(self) -> Tuple[bool, Optional[str]]:
        """连接 FTP 服务器"""
        try:
            self.set_connection_status(ConnectionStatus.CONNECTING)
            
            self._ftp_client = FTP()
            self._ftp_client.connect(self._ftp_host, self._ftp_port, timeout=10)
            self._ftp_client.login(self._ftp_user, self._ftp_password)
            
            self.set_connection_status(ConnectionStatus.CONNECTED)
            self._connection_failures = 0
            return True, None
            
        except error_perm as e:
            error_msg = f"FTP权限错误: {str(e)}"
            self.set_connection_status(ConnectionStatus.FAILED)
            self._connection_failures += 1
            return False, error_msg
            
        except error_temp as e:
            error_msg = f"FTP临时错误: {str(e)}"
            self.set_connection_status(ConnectionStatus.FAILED)
            self._connection_failures += 1
            return False, error_msg
            
        except Exception as e:
            error_msg = f"FTP连接失败: {str(e)}"
            self.set_connection_status(ConnectionStatus.FAILED)
            self._connection_failures += 1
            return False, error_msg
    
    def disconnect(self):
        """断开连接"""
        if self._ftp_client:
            try:
                self._ftp_client.quit()
            except Exception:
                try:
                    self._ftp_client.close()
                except Exception:
                    pass
            finally:
                self._ftp_client = None
        
        self.set_connection_status(ConnectionStatus.DISCONNECTED)
    
    def reconnect(self) -> Tuple[bool, Optional[str]]:
        """重新连接"""
        self.set_connection_status(ConnectionStatus.RECONNECTING)
        self.disconnect()
        return self.connect()
    
    def should_reconnect(self) -> bool:
        """是否应该尝试重连"""
        return (self._connection_failures < self._max_connection_failures and 
                self._connection_status == ConnectionStatus.FAILED)
    
    # ============ 文件上传 ============
    
    def upload_file(self, 
                   source_path: str, 
                   target_path: str,
                   progress_callback: Optional[Callable[[int, int], None]] = None) -> Tuple[bool, Optional[str]]:
        """上传文件
        
        Args:
            source_path: 源文件路径
            target_path: 目标路径
            progress_callback: 进度回调 (uploaded_bytes, total_bytes)
        
        Returns:
            (success, error_message)
        """
        if self.protocol_type == ProtocolType.SMB:
            return self._upload_smb(source_path, target_path, progress_callback)
        
        elif self.protocol_type == ProtocolType.FTP:
            return self._upload_ftp(source_path, target_path, progress_callback)
        
        elif self.protocol_type == ProtocolType.BOTH:
            # 先尝试 SMB
            success, error = self._upload_smb(source_path, target_path, progress_callback)
            if success:
                return True, None
            # SMB 失败，尝试 FTP
            return self._upload_ftp(source_path, target_path, progress_callback)
        
        return False, "Unknown protocol type"
    
    def _upload_smb(self, 
                   source_path: str, 
                   target_path: str,
                   progress_callback: Optional[Callable[[int, int], None]] = None) -> Tuple[bool, Optional[str]]:
        """通过 SMB 上传文件（Windows 文件复制）"""
        try:
            # 确保目标目录存在
            target_dir = os.path.dirname(target_path)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            
            # 复制文件（带进度）
            total_size = os.path.getsize(source_path)
            uploaded = 0
            
            with open(source_path, 'rb') as src, open(target_path, 'wb') as dst:
                while True:
                    chunk = src.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    dst.write(chunk)
                    uploaded += len(chunk)
                    if progress_callback:
                        progress_callback(uploaded, total_size)
            
            return True, None
            
        except PermissionError:
            return False, "目标路径无写入权限"
        except OSError as e:
            if e.errno == 28:  # No space left on device
                return False, "目标磁盘空间不足"
            return False, f"文件操作错误: {str(e)}"
        except Exception as e:
            return False, f"SMB上传失败: {str(e)}"
    
    def _upload_ftp(self, 
                   source_path: str, 
                   target_path: str,
                   progress_callback: Optional[Callable[[int, int], None]] = None) -> Tuple[bool, Optional[str]]:
        """通过 FTP 上传文件"""
        if not self.is_connected:
            success, error = self.connect()
            if not success:
                return False, error
        
        try:
            # 确保远程目录存在
            remote_dir = os.path.dirname(target_path).replace('\\', '/')
            self._ensure_ftp_directory(remote_dir)
            
            # 上传文件
            total_size = os.path.getsize(source_path)
            uploaded = 0
            
            def callback(chunk):
                nonlocal uploaded
                uploaded += len(chunk)
                if progress_callback:
                    progress_callback(uploaded, total_size)
            
            with open(source_path, 'rb') as f:
                remote_file = os.path.basename(target_path)
                self._ftp_client.storbinary(f'STOR {remote_file}', f, callback=callback)
            
            return True, None
            
        except error_perm as e:
            return False, f"FTP权限错误: {str(e)}"
        except error_temp as e:
            return False, f"FTP临时错误: {str(e)}"
        except Exception as e:
            return False, f"FTP上传失败: {str(e)}"
    
    def _ensure_ftp_directory(self, remote_dir: str):
        """确保 FTP 远程目录存在"""
        if not remote_dir or remote_dir == '/':
            return
        
        dirs = remote_dir.split('/')
        current = ''
        
        for d in dirs:
            if not d:
                continue
            current += '/' + d
            try:
                self._ftp_client.cwd(current)
            except error_perm:
                try:
                    self._ftp_client.mkd(current)
                    self._ftp_client.cwd(current)
                except Exception:
                    pass
    
    # ============ 目录操作 ============
    
    def create_directory(self, path: str) -> Tuple[bool, Optional[str]]:
        """创建目录"""
        if self.protocol_type == ProtocolType.SMB:
            try:
                os.makedirs(path, exist_ok=True)
                return True, None
            except Exception as e:
                return False, str(e)
        
        elif self.protocol_type == ProtocolType.FTP:
            if not self.is_connected:
                return False, "FTP未连接"
            try:
                self._ensure_ftp_directory(path.replace('\\', '/'))
                return True, None
            except Exception as e:
                return False, str(e)
        
        return False, "Unsupported operation"
    
    # ============ 回调注册 ============
    
    def on_connection_status_changed(self, callback: Callable[[ConnectionStatus], None]):
        """注册连接状态变化回调"""
        self._on_connection_status_changed = callback
