"""
文件名：src/models/auth_model.py
文件作用：纯数据模型与业务契约模块“auth_model”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

认证状态与界面权限渲染模型。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from .app_state import UserRole


@dataclass
class AuthModel:
    """由控制器管理的认证状态和可持久化凭据摘要。

    用途：把用户凭据与当前登录角色封装为独立模型。
    输入：配置中的用户映射或控制器更新后的角色状态。
    输出：可安全交给配置层保存的用户映射。
    关键步骤：读写映射时均深拷贝，隔离调用方对内部状态的修改。
    风险点：这里只保存摘要及认证状态，明文密码处理必须留在认证服务中。
    """

    users: Dict[str, Any] = field(default_factory=dict)
    current_role: UserRole = UserRole.GUEST
    password_change_required: bool = False

    @classmethod
    def from_mapping(cls, data: Any) -> "AuthModel":
        """从可能不可信的配置值恢复认证模型；非映射值按空模型处理。"""
        if not isinstance(data, Mapping):
            return cls()
        return cls(users=deepcopy(dict(data)))

    def to_mapping(self) -> Dict[str, Any]:
        """返回用户凭据的独立副本，供配置序列化层写入。"""
        return deepcopy(self.users)


@dataclass(frozen=True)
class PermissionContext:
    is_running: bool = False
    enable_backup: bool = True
    protocol_uses_ftp: bool = False
    ftp_server_enabled: bool = False
    ftp_server_running: bool = False
    dedup_enabled: bool = False
    rate_limit_enabled: bool = False


@dataclass(frozen=True)
class ControlPermissions:
    """按当前角色和运行状态计算后交给界面的控件可用性集合。"""
    btn_choose_src: bool
    btn_choose_tgt: bool
    btn_choose_bak: bool
    src_edit_readonly: bool
    tgt_edit_readonly: bool
    bak_edit_readonly: bool
    combo_protocol: bool
    cb_enable_backup: bool
    btn_save: bool
    upload_settings: bool
    file_filters: bool
    startup_settings: bool
    notification_settings: bool
    cb_limit_rate: bool
    spin_max_rate: bool
    cb_dedup_enable: bool
    combo_hash: bool
    combo_strategy: bool
    network_settings: bool
    filter_collapsible: bool
    adv_collapsible: bool
    cb_enable_ftp_server: bool
    ftp_config_widget: bool
    ftp_client_collapsible: bool
    ftp_server_collapsible: bool
    ftp_server_controls: bool
    btn_toggle_ftp_server: bool
    ftp_client_controls: bool
    btn_start: bool
    btn_pause: bool
    btn_stop: bool
    btn_more: bool
    menu_clear_logs: bool
    menu_disk_cleanup: bool
    menu_login: bool
    menu_change_password: bool
    menu_logout: bool
    menu_language: bool

    def to_mapping(self) -> Dict[str, bool]:
        """转换为控件名到可用状态的映射，便于批量渲染。"""
        return dict(self.__dict__)


@dataclass(frozen=True)
class LoginResult:
    success: bool
    role: UserRole = UserRole.GUEST
    uses_default_password: bool = False
    credential_upgraded: bool = False
    error: str = ""


@dataclass(frozen=True)
class PasswordChangeResult:
    success: bool
    target_role: UserRole = UserRole.GUEST
    error: str = ""
