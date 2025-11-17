# -*- coding: utf-8 -*-
"""
单实例管理器 - 防止程序多开
"""
import sys
from pathlib import Path

try:
    from PySide6 import QtCore, QtNetwork
except ImportError:
    from PyQt6 import QtCore, QtNetwork


class SingleInstanceManager:
    """单实例管理器"""
    
    def __init__(self, app_name: str = "ImageUploadTool"):
        self.app_name = app_name
        self.server_name = f"{app_name}_SingleInstance"
        self.server = None
        self.socket = None
    
    def is_already_running(self) -> bool:
        """检查是否已有实例在运行"""
        # 尝试连接到已存在的服务器
        self.socket = QtNetwork.QLocalSocket()
        self.socket.connectToServer(self.server_name)
        
        # 如果能连接成功，说明已有实例运行
        if self.socket.waitForConnected(500):
            return True
        
        # 无法连接，创建新服务器
        self.server = QtNetwork.QLocalServer()
        
        # 移除旧的服务器（如果存在）
        QtNetwork.QLocalServer.removeServer(self.server_name)
        
        # 开始监听
        if not self.server.listen(self.server_name):
            print(f"无法启动单实例服务: {self.server.errorString()}")
            return True  # 保守处理，认为已有实例
        
        return False
    
    def activate_existing_instance(self):
        """激活已存在的实例"""
        if self.socket and self.socket.state() == QtNetwork.QLocalSocket.ConnectedState:  # type: ignore
            # 发送激活信号
            self.socket.write(b"ACTIVATE")
            self.socket.waitForBytesWritten(1000)
            self.socket.disconnectFromServer()
    
    def cleanup(self):
        """清理资源"""
        if self.server:
            self.server.close()
        if self.socket:
            self.socket.close()
