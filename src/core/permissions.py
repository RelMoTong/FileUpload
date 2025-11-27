# -*- coding: utf-8 -*-
"""
权限管理模块

负责用户角色权限计算和验证
"""
from typing import Dict, Tuple, Optional
import hashlib


class PermissionManager:
    """权限管理器"""
    
    # 角色定义
    ROLE_GUEST = 'guest'
    ROLE_USER = 'user'
    ROLE_ADMIN = 'admin'
    
    # 默认密码
    DEFAULT_USER_PASSWORD = '123'
    DEFAULT_ADMIN_PASSWORD = 'Tops123'
    
    def __init__(self):
        self.current_role = self.ROLE_GUEST
        self.users: Dict[str, Dict[str, str]] = {}
    
    def set_users(self, users: Dict[str, Dict[str, str]]) -> None:
        """设置用户列表
        
        Args:
            users: 用户字典，格式 {'user': {'password': 'xxx'}, 'admin': {'password': 'xxx'}}
        """
        self.users = users
    
    def verify_password(self, role: str, password: str) -> bool:
        """验证密码
        
        Args:
            role: 用户角色
            password: 密码
            
        Returns:
            密码是否正确
        """
        # 检查是否有存储的密码
        if role in self.users and 'password' in self.users[role]:
            stored_password = self.users[role]['password']
            # 支持加密密码（以 hash: 开头）和明文密码
            if stored_password.startswith('hash:'):
                return stored_password == f"hash:{PermissionManager._hash_password(password)}"
            else:
                return stored_password == password
        
        # 使用默认密码
        if role == self.ROLE_USER:
            return password == self.DEFAULT_USER_PASSWORD
        elif role == self.ROLE_ADMIN:
            return password == self.DEFAULT_ADMIN_PASSWORD
        
        return False
    
    @staticmethod
    def _hash_password(password: str) -> str:
        """密码哈希
        
        Args:
            password: 明文密码
            
        Returns:
            哈希后的密码
        """
        return hashlib.sha256(password.encode()).hexdigest()
    
    def login(self, role: str, password: str) -> Tuple[bool, str]:
        """用户登录
        
        Args:
            role: 用户角色
            password: 密码
            
        Returns:
            (是否成功, 消息)
        """
        if not self.verify_password(role, password):
            return False, "密码错误"
        
        self.current_role = role
        role_name = self._get_role_name(role)
        return True, f"登录成功，当前角色：{role_name}"
    
    def logout(self) -> None:
        """退出登录"""
        self.current_role = self.ROLE_GUEST
    
    def get_current_role(self) -> str:
        """获取当前角色
        
        Returns:
            当前角色
        """
        return self.current_role
    
    def is_guest(self) -> bool:
        """是否是访客"""
        return self.current_role == self.ROLE_GUEST
    
    def is_user(self) -> bool:
        """是否是普通用户"""
        return self.current_role == self.ROLE_USER
    
    def is_admin(self) -> bool:
        """是否是管理员"""
        return self.current_role == self.ROLE_ADMIN
    
    def has_permission(self, action: str) -> bool:
        """检查是否有权限执行某个操作
        
        Args:
            action: 操作名称
            
        Returns:
            是否有权限
        """
        # 定义权限矩阵
        permissions = {
            'start': [self.ROLE_USER, self.ROLE_ADMIN],
            'stop': [self.ROLE_USER, self.ROLE_ADMIN],
            'pause': [self.ROLE_USER, self.ROLE_ADMIN],
            'resume': [self.ROLE_USER, self.ROLE_ADMIN],
            'save_config': [self.ROLE_USER, self.ROLE_ADMIN],
            'change_protocol': [self.ROLE_ADMIN],
            'manage_users': [self.ROLE_ADMIN],
            'view_stats': [self.ROLE_USER, self.ROLE_ADMIN, self.ROLE_GUEST],
        }
        
        allowed_roles = permissions.get(action, [])
        return self.current_role in allowed_roles
    
    @staticmethod
    def _get_role_name(role: str) -> str:
        """获取角色显示名称
        
        Args:
            role: 角色代码
            
        Returns:
            角色名称
        """
        role_names = {
            'guest': '访客',
            'user': '普通用户',
            'admin': '管理员'
        }
        return role_names.get(role, '未知')
    
    def get_role_display(self) -> Tuple[str, str, str]:
        """获取角色显示信息
        
        Returns:
            (图标, 文本, 样式)
        """
        displays = {
            'guest': ("🔒 未登录", "#FFF3E0", "#E67E22"),
            'user': ("👤 普通用户", "#E8F5E9", "#2E7D32"),
            'admin': ("👑 管理员", "#F3E5F5", "#7B1FA2"),
        }
        text, bg, fg = displays.get(self.current_role, displays['guest'])
        style = f"background:{bg}; color:{fg}; padding:6px 12px; border-radius:6px; font-weight:700;"
        return text, style, bg
