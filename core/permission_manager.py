# -*- coding: utf-8 -*-
"""
权限管理器 - 集中管理角色权限控制
"""
from typing import Literal

Role = Literal['guest', 'user', 'admin']


class PermissionManager:
    """权限管理器"""
    
    def __init__(self):
        self.current_role: Role = 'guest'
        self.is_running: bool = False
    
    def set_role(self, role: Role):
        """设置当前角色"""
        self.current_role = role
    
    def set_running(self, running: bool):
        """设置运行状态"""
        self.is_running = running
    
    # ========== 权限判断方法 ==========
    
    def can_edit_paths(self) -> bool:
        """是否可以编辑路径"""
        return self.current_role in ('user', 'admin') and not self.is_running
    
    def can_edit_config(self) -> bool:
        """是否可以编辑配置"""
        return self.current_role in ('user', 'admin') and not self.is_running
    
    def can_save_config(self) -> bool:
        """是否可以保存配置"""
        return self.current_role in ('user', 'admin') and not self.is_running
    
    def can_change_protocol(self) -> bool:
        """是否可以更改协议"""
        return self.current_role in ('user', 'admin') and not self.is_running
    
    def can_browse_folder(self) -> bool:
        """是否可以浏览文件夹"""
        return self.current_role in ('user', 'admin') and not self.is_running
    
    def can_start_upload(self) -> bool:
        """是否可以开始上传"""
        return not self.is_running
    
    def can_pause_upload(self) -> bool:
        """是否可以暂停上传"""
        return self.is_running
    
    def can_stop_upload(self) -> bool:
        """是否可以停止上传"""
        return self.is_running
    
    def can_disk_cleanup(self) -> bool:
        """是否可以执行磁盘清理"""
        return self.current_role == 'admin'
    
    def can_change_password(self) -> bool:
        """是否可以修改密码"""
        return self.current_role == 'admin'
    
    def is_guest(self) -> bool:
        """是否是访客"""
        return self.current_role == 'guest'
    
    def is_user_or_admin(self) -> bool:
        """是否是用户或管理员"""
        return self.current_role in ('user', 'admin')
    
    def is_admin(self) -> bool:
        """是否是管理员"""
        return self.current_role == 'admin'
    
    def get_permissions_summary(self) -> dict:
        """获取权限摘要（用于调试/日志）"""
        return {
            'role': self.current_role,
            'is_running': self.is_running,
            'can_edit_paths': self.can_edit_paths(),
            'can_edit_config': self.can_edit_config(),
            'can_save_config': self.can_save_config(),
            'can_change_protocol': self.can_change_protocol(),
            'can_start_upload': self.can_start_upload(),
            'can_pause_upload': self.can_pause_upload(),
            'can_stop_upload': self.can_stop_upload(),
        }
