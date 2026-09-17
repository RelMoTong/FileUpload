"""
文件名：src/controllers/auth_controller.py
文件作用：控制器层的“auth_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。

Controller for authentication state, password changes and permissions.
"""

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
        """作用：执行“load_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        """作用：执行“save_raw”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        ...


class AuthBusinessService(Protocol):
    def default_password_roles(self, model: AuthModel) -> List[UserRole]:
        """协议占位：声明“default_password_roles”的最小调用约定，由实现方提供既有行为。"""
        ...
    def load_users(self, model: AuthModel, users: Any) -> None:
        """协议占位：声明“load_users”的最小调用约定，由实现方提供既有行为。"""
        ...
    def authenticate(
        self, model: AuthModel, role: UserRole, password: str
    ) -> LoginResult:
        """协议占位：声明“authenticate”的最小调用约定，由实现方提供既有行为。"""
        ...
    def logout(self, model: AuthModel) -> None:
        """协议占位：声明“logout”的最小调用约定，由实现方提供既有行为。"""
        ...
    def change_password(
        self,
        model: AuthModel,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeResult:
        """协议占位：声明“change_password”的最小调用约定，由实现方提供既有行为。"""
        ...
    def compute_permissions(
        self, role: UserRole, context: PermissionContext
    ) -> ControlPermissions:
        """协议占位：声明“compute_permissions”的最小调用约定，由实现方提供既有行为。"""
        ...
    def disk_cleanup_block_reason(self, role: UserRole) -> str:
        """协议占位：声明“disk_cleanup_block_reason”的最小调用约定，由实现方提供既有行为。"""
        ...


class AuthController:
    """Maintain the single authentication state used by the application."""

    def __init__(
        self,
        service: AuthBusinessService,
        settings: Optional[CredentialSettings] = None,
        *,
        model: AuthModel,
    ) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._service = service
        self._settings = settings
        self._model = model

    @property
    def current_role(self) -> UserRole:
        """作用：执行“current_role”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._model.current_role

    @property
    def default_password_roles(self) -> List[UserRole]:
        """作用：执行“default_password_roles”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.default_password_roles(self._model)

    def load_users(self, users: Any) -> None:
        """作用：执行“load_users”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._service.load_users(self._model, users)

    def set_current_role(self, role: UserRole) -> None:
        """作用：执行“set_current_role”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._model.current_role = role

    def login(self, role: UserRole, password: str) -> LoginResult:
        """作用：执行“login”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        previous_users = deepcopy(self._model.users)
        previous_password_change_required = self._model.password_change_required
        result = self._service.authenticate(self._model, role, password)
        if not result.success or not result.credential_upgraded or self._settings is None:
            return result
        try:
            config = self._settings.load_raw()
            config["users"] = self._model.to_mapping()
            if not self._settings.save_raw(config, preserve_users=False):
                raise OSError(self._settings.last_error or "凭据迁移写入失败")
            persisted = self._settings.load_raw().get("users", {})
            if not isinstance(persisted, dict) or (
                persisted.get(role.value) != self._model.users.get(role.value)
            ):
                raise OSError("凭据迁移写入校验失败")
        except Exception as exc:
            self._model.users = previous_users
            self._model.current_role = UserRole.GUEST
            self._model.password_change_required = previous_password_change_required
            return LoginResult(False, role=role, error=str(exc))
        return result

    def logout(self) -> None:
        """作用：执行“logout”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        self._service.logout(self._model)

    def change_password(
        self,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeResult:
        """作用：执行“change_password”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        previous_users = deepcopy(self._model.users)
        previous_password_change_required = self._model.password_change_required
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
                self._model.password_change_required = previous_password_change_required
                return PasswordChangeResult(
                    False,
                    target_role,
                    self._settings.last_error or "写入配置文件失败",
                )
            persisted = self._settings.load_raw().get("users", {})
            if not isinstance(persisted, dict) or (
                persisted.get(target_role.value)
                != self._model.users.get(target_role.value)
            ):
                self._model.users = previous_users
                self._model.password_change_required = previous_password_change_required
                return PasswordChangeResult(
                    False, target_role, "密码写入校验失败，请重试"
                )
        except Exception as exc:
            self._model.users = previous_users
            self._model.password_change_required = previous_password_change_required
            return PasswordChangeResult(False, target_role, str(exc))
        return result

    def compute_permissions(self, context: PermissionContext) -> ControlPermissions:
        # P0-04：默认口令仅作风险提示，权限只由角色与运行态决定。
        """作用：执行“compute_permissions”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.compute_permissions(self.current_role, context)

    @property
    def password_change_required(self) -> bool:
        """作用：执行“password_change_required”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._model.password_change_required

    def can_manage_disk_cleanup(self) -> bool:
        """作用：执行“can_manage_disk_cleanup”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self.current_role is UserRole.ADMIN

    def is_authenticated(self) -> bool:
        """作用：执行“is_authenticated”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self.current_role in {UserRole.USER, UserRole.ADMIN}

    def users_mapping(self) -> Dict[str, Any]:
        """Return a copy of the controller-owned credential source of truth."""
        return self._model.to_mapping()

    def can_manage_ftp(self) -> bool:
        """作用：执行“can_manage_ftp”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self.current_role is UserRole.ADMIN

    def password_change_block_reason(self) -> str:
        """作用：执行“password_change_block_reason”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        if self.current_role is UserRole.GUEST:
            return "请先登录"
        return ""

    def disk_cleanup_block_reason(self) -> str:
        """作用：执行“disk_cleanup_block_reason”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.disk_cleanup_block_reason(self.current_role)
