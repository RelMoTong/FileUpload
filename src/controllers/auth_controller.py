"""Controller for authentication state, password changes and permissions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol

from src.models import (
    AuthModel,
    ControlPermissions,
    LoginResult,
    PasswordChangeResult,
    PermissionContext,
    UserRole,
)
class CredentialSettings(Protocol):
    last_error: str

    def load_raw(self) -> Dict[str, Any]:
        ...

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        ...


class AuthBusinessService(Protocol):
    def default_password_roles(self, model: AuthModel) -> List[UserRole]: ...
    def load_users(self, model: AuthModel, users: Any) -> None: ...
    def authenticate(
        self, model: AuthModel, role: UserRole, password: str
    ) -> LoginResult: ...
    def logout(self, model: AuthModel) -> None: ...
    def change_password(
        self,
        model: AuthModel,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeResult: ...
    def compute_permissions(
        self, role: UserRole, context: PermissionContext
    ) -> ControlPermissions: ...
    def disk_cleanup_block_reason(self, role: UserRole) -> str: ...


class AuthController:
    """Maintain the single authentication state used by the application."""

    def __init__(
        self,
        service: AuthBusinessService,
        settings: Optional[CredentialSettings] = None,
        *,
        model: AuthModel,
    ) -> None:
        self._service = service
        self._settings = settings
        self._model = model

    @property
    def current_role(self) -> UserRole:
        return self._model.current_role

    @property
    def default_password_roles(self) -> List[UserRole]:
        return self._service.default_password_roles(self._model)

    def load_users(self, users: Any) -> None:
        self._service.load_users(self._model, users)

    def set_current_role(self, role: UserRole) -> None:
        self._model.current_role = role

    def login(self, role: UserRole, password: str) -> LoginResult:
        previous_users = deepcopy(self._model.users)
        result = self._service.authenticate(self._model, role, password)
        if not result.success or not result.credential_upgraded or self._settings is None:
            return result
        try:
            config = self._settings.load_raw()
            config["users"] = self._model.to_mapping()
            if not self._settings.save_raw(config, preserve_users=False):
                raise OSError(self._settings.last_error or "凭据迁移写入失败")
        except Exception as exc:
            self._model.users = previous_users
            self._model.current_role = UserRole.GUEST
            self._model.password_change_required = False
            return LoginResult(False, role=role, error=str(exc))
        return result

    def logout(self) -> None:
        self._service.logout(self._model)

    def change_password(
        self,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeResult:
        previous_users = deepcopy(self._model.users)
        result = self._service.change_password(
            self._model,
            target_role,
            old_password,
            new_password,
            confirm_password,
        )
        if not result.success or self._settings is None:
            return result

        try:
            config = self._settings.load_raw()
            config["users"] = self._model.to_mapping()
            if not self._settings.save_raw(config, preserve_users=False):
                self._model.users = previous_users
                return PasswordChangeResult(
                    False,
                    target_role,
                    self._settings.last_error or "写入配置文件失败",
                )
        except Exception as exc:
            self._model.users = previous_users
            return PasswordChangeResult(False, target_role, str(exc))
        return result

    def compute_permissions(self, context: PermissionContext) -> ControlPermissions:
        permissions = self._service.compute_permissions(self.current_role, context)
        if not self._model.password_change_required:
            return permissions
        values = permissions.to_mapping()
        for name in values:
            values[name] = False
        values.update(
            btn_more=True,
            ftp_config_widget=True,
            ftp_server_collapsible=True,
            menu_change_password=True,
            menu_logout=True,
            menu_language=True,
        )
        return ControlPermissions(**values)

    @property
    def password_change_required(self) -> bool:
        return self._model.password_change_required

    def can_manage_disk_cleanup(self) -> bool:
        return self.current_role is UserRole.ADMIN

    def is_authenticated(self) -> bool:
        return self.current_role in {UserRole.USER, UserRole.ADMIN}

    def can_manage_ftp(self) -> bool:
        return self.current_role is UserRole.ADMIN

    def password_change_block_reason(self) -> str:
        if self.current_role is UserRole.GUEST:
            return "请先登录"
        return ""

    def disk_cleanup_block_reason(self) -> str:
        return self._service.disk_cleanup_block_reason(self.current_role)
