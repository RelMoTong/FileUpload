# -*- coding: utf-8 -*-
"""
单实例管理器 - 防止程序多开（跨平台）

使用 QLocalServer/QLocalSocket 实现：
- 跨平台支持（Windows/Linux/macOS）
- 新实例可以激活已运行的窗口
- 自动清理资源
"""
import sys
from pathlib import Path
from typing import Optional, Callable

try:
    from PySide6 import QtCore, QtNetwork
    Signal = QtCore.Signal
except ImportError:
    try:
        from PyQt5 import QtCore, QtNetwork  # type: ignore
        Signal = QtCore.pyqtSignal  # type: ignore
    except ImportError:
        raise ImportError("Neither PySide6 nor PyQt5 is installed")


class SingleInstanceManager(QtCore.QObject):  # type: ignore
    """单实例管理器
    
    特性：
    1. 防止程序多开
    2. 新实例发送激活信号给旧实例
    3. 旧实例接收信号并显示窗口
    """
    
    # 信号：请求激活窗口
    activate_requested = Signal()
    
    def __init__(self, app_name: str = "ImageUploadTool", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.server_name = f"{app_name}_SingleInstance"
        self.server: Optional[QtNetwork.QLocalServer] = None
        self.socket: Optional[QtNetwork.QLocalSocket] = None
        self._is_primary = False
    
    def is_already_running(self) -> bool:
        """检查是否已有实例在运行
        
        Returns:
            True: 已有实例运行（当前是次要实例）
            False: 这是第一个实例（当前是主实例）
        """
        # 尝试连接到已存在的服务器
        self.socket = QtNetwork.QLocalSocket()
        self.socket.connectToServer(self.server_name)
        
        # 如果能连接成功，说明已有实例运行
        if self.socket.waitForConnected(500):
            self._is_primary = False
            return True
        
        # 无法连接，创建新服务器
        self.server = QtNetwork.QLocalServer()
        
        # 移除旧的服务器（如果存在）
        QtNetwork.QLocalServer.removeServer(self.server_name)
        
        # 开始监听
        if not self.server.listen(self.server_name):
            print(f"⚠️ 无法启动单实例服务: {self.server.errorString()}")
            self._is_primary = False
            return True  # 保守处理，认为已有实例
        
        # 连接新连接信号
        self.server.newConnection.connect(self._on_new_connection)
        
        self._is_primary = True
        return False
    
    @property
    def is_primary_instance(self) -> bool:
        """是否为主实例"""
        return self._is_primary
    
    def activate_existing_instance(self):
        """激活已存在的实例（从次要实例调用）"""
        if not self.socket:
            return
        
        # 兼容 Qt5/Qt6 的状态枚举
        connected_state = getattr(QtNetwork.QLocalSocket, 'ConnectedState', 
                                 getattr(QtNetwork.QLocalSocket.LocalSocketState, 'ConnectedState', 3))
        
        if self.socket.state() == connected_state:  # type: ignore[arg-type]
            # 发送激活命令
            self.socket.write(b"ACTIVATE")
            self.socket.flush()
            self.socket.waitForBytesWritten(1000)
            self.socket.disconnectFromServer()
    
    def _on_new_connection(self):
        """处理新连接（主实例接收到次要实例的连接）"""
        if not self.server:
            return
        
        client_socket = self.server.nextPendingConnection()
        if not client_socket:
            return
        
        # 读取命令
        if client_socket.waitForReadyRead(1000):
            data = client_socket.readAll().data()
            
            if data == b"ACTIVATE":
                # 发射激活信号
                self.activate_requested.emit()
        
        # 关闭连接
        client_socket.disconnectFromServer()
        client_socket.deleteLater()
    
    def cleanup(self):
        """清理资源"""
        if self.server:
            self.server.close()
            self.server = None
        
        if self.socket:
            self.socket.close()
            self.socket = None
    
    def __del__(self):
        """析构函数"""
        self.cleanup()
