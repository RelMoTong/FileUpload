"""Authentication state and permission render models."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from .app_state import UserRole


@dataclass
class AuthModel:
    """Controller-owned authentication state and persisted credential hashes."""

    users: Dict[str, Any] = field(default_factory=dict)
    current_role: UserRole = UserRole.GUEST
    password_change_required: bool = False

    @classmethod
    def from_mapping(cls, data: Any) -> "AuthModel":
        if not isinstance(data, Mapping):
            return cls()
        return cls(users=deepcopy(dict(data)))

    def to_mapping(self) -> Dict[str, Any]:
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
