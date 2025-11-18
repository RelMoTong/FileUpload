# -*- coding: utf-8 -*-
"""
权限管理器 - 集中管理角色权限控制

基于角色（Role）和状态（State）的权限矩阵：
- 角色: guest, user, admin
- 状态: idle, running, paused

设计原则：
1. 所有权限检查集中在这一个类
2. 避免在UI代码中分散的 if 判断
3. 权限矩阵一目了然，易于维护
4. 支持细粒度的功能权限控制
"""
from typing import Literal, Dict, Set
from enum import Enum


# 类型定义
Role = Literal['guest', 'user', 'admin']


class UploadState(Enum):
    """上传状态枚举"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class Permission(Enum):
    """权限枚举 - 定义所有可控制的权限"""
    # 路径编辑
    EDIT_SOURCE_PATH = "edit_source_path"
    EDIT_TARGET_PATH = "edit_target_path"
    EDIT_BACKUP_PATH = "edit_backup_path"
    BROWSE_FOLDER = "browse_folder"
    
    # 配置管理
    EDIT_CONFIG = "edit_config"
    SAVE_CONFIG = "save_config"
    LOAD_CONFIG = "load_config"
    
    # 协议设置
    CHANGE_PROTOCOL = "change_protocol"
    EDIT_FTP_CONFIG = "edit_ftp_config"
    
    # 上传控制
    START_UPLOAD = "start_upload"
    PAUSE_UPLOAD = "pause_upload"
    STOP_UPLOAD = "stop_upload"
    RETRY_FAILED = "retry_failed"
    
    # 高级功能
    DISK_CLEANUP = "disk_cleanup"
    EDIT_CLEANUP_RULE = "edit_cleanup_rule"
    EXECUTE_CLEANUP = "execute_cleanup"
    
    # 智能去重
    ENABLE_DEDUP = "enable_dedup"
    CLEAR_DEDUP_CACHE = "clear_dedup_cache"
    
    # 系统管理
    CHANGE_PASSWORD = "change_password"
    VIEW_LOGS = "view_logs"
    EXPORT_LOGS = "export_logs"
    
    # 托盘通知
    CONFIGURE_TRAY_NOTIFICATION = "configure_tray_notification"


class PermissionManager:
    """权限管理器 - 基于权限矩阵"""
    
    # 权限矩阵：定义每个角色在每种状态下的权限
    PERMISSION_MATRIX: Dict[Role, Dict[UploadState, Set[Permission]]] = {
        'guest': {
            UploadState.IDLE: {
                Permission.VIEW_LOGS,
            },
            UploadState.RUNNING: set(),
            UploadState.PAUSED: set(),
        },
        'user': {
            UploadState.IDLE: {
                Permission.EDIT_SOURCE_PATH,
                Permission.EDIT_TARGET_PATH,
                Permission.EDIT_BACKUP_PATH,
                Permission.BROWSE_FOLDER,
                Permission.EDIT_CONFIG,
                Permission.SAVE_CONFIG,
                Permission.LOAD_CONFIG,
                Permission.CHANGE_PROTOCOL,
                Permission.EDIT_FTP_CONFIG,
                Permission.START_UPLOAD,
                Permission.ENABLE_DEDUP,
                Permission.VIEW_LOGS,
                Permission.EXPORT_LOGS,
                Permission.CONFIGURE_TRAY_NOTIFICATION,
            },
            UploadState.RUNNING: {
                Permission.PAUSE_UPLOAD,
                Permission.STOP_UPLOAD,
                Permission.VIEW_LOGS,
            },
            UploadState.PAUSED: {
                Permission.START_UPLOAD,
                Permission.STOP_UPLOAD,
                Permission.VIEW_LOGS,
            },
        },
        'admin': {
            UploadState.IDLE: {
                Permission.EDIT_SOURCE_PATH,
                Permission.EDIT_TARGET_PATH,
                Permission.EDIT_BACKUP_PATH,
                Permission.BROWSE_FOLDER,
                Permission.EDIT_CONFIG,
                Permission.SAVE_CONFIG,
                Permission.LOAD_CONFIG,
                Permission.CHANGE_PROTOCOL,
                Permission.EDIT_FTP_CONFIG,
                Permission.START_UPLOAD,
                Permission.DISK_CLEANUP,
                Permission.EDIT_CLEANUP_RULE,
                Permission.EXECUTE_CLEANUP,
                Permission.ENABLE_DEDUP,
                Permission.CLEAR_DEDUP_CACHE,
                Permission.CHANGE_PASSWORD,
                Permission.VIEW_LOGS,
                Permission.EXPORT_LOGS,
                Permission.CONFIGURE_TRAY_NOTIFICATION,
            },
            UploadState.RUNNING: {
                Permission.PAUSE_UPLOAD,
                Permission.STOP_UPLOAD,
                Permission.VIEW_LOGS,
                Permission.EXPORT_LOGS,
            },
            UploadState.PAUSED: {
                Permission.START_UPLOAD,
                Permission.STOP_UPLOAD,
                Permission.RETRY_FAILED,
                Permission.VIEW_LOGS,
                Permission.EXPORT_LOGS,
            },
        },
    }
    
    def __init__(self):
        self.current_role: Role = 'guest'
        self.current_state: UploadState = UploadState.IDLE
    
    # ============ 状态管理 ============
    
    def set_role(self, role: Role):
        """设置当前角色"""
        self.current_role = role
    
    def set_state(self, state: UploadState):
        """设置上传状态"""
        self.current_state = state
    
    def set_running(self, running: bool):
        """设置运行状态（兼容旧接口）"""
        if running:
            self.current_state = UploadState.RUNNING
        else:
            self.current_state = UploadState.IDLE
    
    @property
    def is_running(self) -> bool:
        """是否正在运行（兼容旧接口）"""
        return self.current_state == UploadState.RUNNING
    
    # ============ 核心权限检查 ============
    
    def has_permission(self, permission: Permission) -> bool:
        """检查是否具有指定权限
        
        Args:
            permission: 要检查的权限
        
        Returns:
            True: 有权限
            False: 无权限
        """
        role_permissions = self.PERMISSION_MATRIX.get(self.current_role, {})
        state_permissions = role_permissions.get(self.current_state, set())
        return permission in state_permissions
    
    def has_any_permission(self, *permissions: Permission) -> bool:
        """检查是否具有任意一个权限"""
        return any(self.has_permission(p) for p in permissions)
    
    def has_all_permissions(self, *permissions: Permission) -> bool:
        """检查是否具有所有权限"""
        return all(self.has_permission(p) for p in permissions)
    
    # ============ 便捷权限检查方法 ============
    
    def can_edit_paths(self) -> bool:
        """是否可以编辑路径"""
        return self.has_permission(Permission.EDIT_SOURCE_PATH)
    
    def can_edit_config(self) -> bool:
        """是否可以编辑配置"""
        return self.has_permission(Permission.EDIT_CONFIG)
    
    def can_save_config(self) -> bool:
        """是否可以保存配置"""
        return self.has_permission(Permission.SAVE_CONFIG)
    
    def can_change_protocol(self) -> bool:
        """是否可以更改协议"""
        return self.has_permission(Permission.CHANGE_PROTOCOL)
    
    def can_browse_folder(self) -> bool:
        """是否可以浏览文件夹"""
        return self.has_permission(Permission.BROWSE_FOLDER)
    
    def can_start_upload(self) -> bool:
        """是否可以开始上传"""
        return self.has_permission(Permission.START_UPLOAD)
    
    def can_pause_upload(self) -> bool:
        """是否可以暂停上传"""
        return self.has_permission(Permission.PAUSE_UPLOAD)
    
    def can_stop_upload(self) -> bool:
        """是否可以停止上传"""
        return self.has_permission(Permission.STOP_UPLOAD)
    
    def can_retry_failed(self) -> bool:
        """是否可以重试失败文件"""
        return self.has_permission(Permission.RETRY_FAILED)
    
    def can_disk_cleanup(self) -> bool:
        """是否可以执行磁盘清理"""
        return self.has_permission(Permission.DISK_CLEANUP)
    
    def can_edit_cleanup_rule(self) -> bool:
        """是否可以编辑清理规则"""
        return self.has_permission(Permission.EDIT_CLEANUP_RULE)
    
    def can_change_password(self) -> bool:
        """是否可以修改密码"""
        return self.has_permission(Permission.CHANGE_PASSWORD)
    
    def can_configure_tray_notification(self) -> bool:
        """是否可以配置托盘通知"""
        return self.has_permission(Permission.CONFIGURE_TRAY_NOTIFICATION)
    
    # ============ 角色查询 ============
    
    def is_guest(self) -> bool:
        """是否是访客"""
        return self.current_role == 'guest'
    
    def is_user_or_admin(self) -> bool:
        """是否是用户或管理员"""
        return self.current_role in ('user', 'admin')
    
    def is_admin(self) -> bool:
        """是否是管理员"""
        return self.current_role == 'admin'
    
    # ============ 批量UI控件状态更新 ============
    
    def get_widget_enabled_state(self, permission: Permission) -> bool:
        """获取控件启用状态（用于直接设置widget.setEnabled()）"""
        return self.has_permission(permission)
    
    def get_permissions_for_role(self, role: Role, state: UploadState) -> Set[Permission]:
        """获取指定角色和状态下的所有权限（用于调试）"""
        return self.PERMISSION_MATRIX.get(role, {}).get(state, set())
    
    def get_permissions_summary(self) -> dict:
        """获取权限摘要（用于调试/日志）"""
        return {
            'role': self.current_role,
            'state': self.current_state.value,
            'permissions': [p.value for p in self.get_current_permissions()],
            'can_edit_paths': self.can_edit_paths(),
            'can_edit_config': self.can_edit_config(),
            'can_save_config': self.can_save_config(),
            'can_change_protocol': self.can_change_protocol(),
            'can_start_upload': self.can_start_upload(),
            'can_pause_upload': self.can_pause_upload(),
            'can_stop_upload': self.can_stop_upload(),
            'can_disk_cleanup': self.can_disk_cleanup(),
        }
    
    def get_current_permissions(self) -> Set[Permission]:
        """获取当前所有权限"""
        return self.PERMISSION_MATRIX.get(self.current_role, {}).get(self.current_state, set())
