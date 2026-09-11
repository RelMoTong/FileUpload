"""Pure authentication and authorization business rules."""

from __future__ import annotations

import hashlib
import base64
import hmac
import os
from typing import Any, Mapping

from src.models import (
    AuthModel,
    ControlPermissions,
    LoginResult,
    PasswordChangeResult,
    PermissionContext,
    UserRole,
)


DEFAULT_USER_PASSWORD_HASH = hashlib.sha256("123".encode("utf-8")).hexdigest()
DEFAULT_ADMIN_PASSWORD_HASH = hashlib.sha256("Tops123".encode("utf-8")).hexdigest()
PBKDF2_ITERATIONS = 600_000


class AuthService:
    """Own password hashing, authentication and permission policy."""

    SIMPLE_PASSWORDS = {"123", "123456", "password", "upload_pass", "Tops123"}

    @staticmethod
    def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return "pbkdf2_sha256${}${}${}".format(
            iterations,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    @staticmethod
    def legacy_hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    @classmethod
    def verify_password(cls, password: str, encoded: str) -> tuple[bool, bool]:
        """Return (matches, needs_legacy_upgrade)."""
        if encoded.startswith("pbkdf2_sha256$"):
            try:
                _, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
                iterations = int(raw_iterations)
                if iterations < 100_000:
                    return False, False
                salt = base64.b64decode(raw_salt, validate=True)
                expected = base64.b64decode(raw_digest, validate=True)
                actual = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, iterations
                )
                return hmac.compare_digest(actual, expected), False
            except (ValueError, TypeError):
                return False, False
        legacy = cls.legacy_hash_password(password)
        return hmac.compare_digest(legacy, encoded), True

    @staticmethod
    def _default_password(role: UserRole) -> str:
        if role is UserRole.USER:
            return "123"
        if role is UserRole.ADMIN:
            return "Tops123"
        return ""

    @staticmethod
    def _default_hash(role: UserRole) -> str:
        if role is UserRole.USER:
            return DEFAULT_USER_PASSWORD_HASH
        if role is UserRole.ADMIN:
            return DEFAULT_ADMIN_PASSWORD_HASH
        return ""

    def load_users(self, model: AuthModel, users: Any) -> None:
        model.users = dict(users) if isinstance(users, Mapping) else {}

    def password_hash(self, model: AuthModel, role: UserRole) -> str:
        stored = model.users.get(role.value)
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        return self._default_hash(role)

    def default_password_roles(self, model: AuthModel) -> list[UserRole]:
        roles: list[UserRole] = []
        for role in (UserRole.USER, UserRole.ADMIN):
            matches, _ = self.verify_password(
                self._default_password(role), self.password_hash(model, role)
            )
            if matches:
                roles.append(role)
        return roles

    def authenticate(self, model: AuthModel, role: UserRole, password: str) -> LoginResult:
        if role not in {UserRole.USER, UserRole.ADMIN}:
            return LoginResult(False, error="不支持的登录角色")
        if not password:
            return LoginResult(False, role=role, error="请输入密码")
        expected = self.password_hash(model, role)
        matches, needs_upgrade = self.verify_password(password, expected)
        if not matches:
            return LoginResult(False, role=role, error="密码错误")
        default_password = password == self._default_password(role)
        if needs_upgrade:
            model.users[role.value] = self.hash_password(password)
        model.current_role = role
        model.password_change_required = default_password
        return LoginResult(
            True,
            role=role,
            uses_default_password=default_password,
            credential_upgraded=needs_upgrade,
        )

    @staticmethod
    def logout(model: AuthModel) -> None:
        model.current_role = UserRole.GUEST
        model.password_change_required = False

    def validate_new_password(self, old_password: str, new_password: str) -> str:
        if len(new_password) < 8:
            return "新密码至少需要 8 位"
        if new_password == old_password:
            return "新密码不能与原密码相同"
        if new_password in self.SIMPLE_PASSWORDS:
            return "新密码过于简单，请使用更安全的密码"

        categories = sum(
            (
                any(ch.islower() for ch in new_password),
                any(ch.isupper() for ch in new_password),
                any(ch.isdigit() for ch in new_password),
                any(not ch.isalnum() for ch in new_password),
            )
        )
        if categories < 2:
            return "新密码至少包含两种字符类型"
        return ""

    def change_password(
        self,
        model: AuthModel,
        target_role: UserRole,
        old_password: str,
        new_password: str,
        confirm_password: str,
    ) -> PasswordChangeResult:
        if model.current_role is UserRole.GUEST:
            return PasswordChangeResult(False, target_role, "请先登录")
        if model.current_role is UserRole.USER and target_role is not UserRole.USER:
            return PasswordChangeResult(False, target_role, "普通用户只能修改自己的密码")
        if not old_password or not new_password or not confirm_password:
            return PasswordChangeResult(False, target_role, "请填写所有字段")
        if new_password != confirm_password:
            return PasswordChangeResult(False, target_role, "两次输入的新密码不一致")
        password_error = self.validate_new_password(old_password, new_password)
        if password_error:
            return PasswordChangeResult(False, target_role, password_error)
        verification_role = (
            UserRole.ADMIN if model.current_role is UserRole.ADMIN else UserRole.USER
        )
        old_matches, _ = self.verify_password(
            old_password, self.password_hash(model, verification_role)
        )
        if not old_matches:
            error = "管理员密码错误" if target_role is UserRole.USER else "原密码错误"
            return PasswordChangeResult(False, target_role, error)
        if target_role not in {UserRole.USER, UserRole.ADMIN}:
            return PasswordChangeResult(False, target_role, "不支持的密码修改对象")

        model.users[target_role.value] = self.hash_password(new_password)
        if target_role is model.current_role:
            model.password_change_required = False
        return PasswordChangeResult(True, target_role)

    @staticmethod
    def compute_permissions(role: UserRole, context: PermissionContext) -> ControlPermissions:
        is_user_or_admin = role in {UserRole.USER, UserRole.ADMIN}
        is_admin = role is UserRole.ADMIN
        is_guest = role is UserRole.GUEST
        can_edit_config = is_user_or_admin and not context.is_running
        can_edit_ftp_server = (
            is_admin and not context.is_running and not context.ftp_server_running
        )
        can_toggle_ftp_server = is_admin and (
            context.ftp_server_running
            or (not context.is_running and context.ftp_server_enabled)
        )

        return ControlPermissions(
            btn_choose_src=can_edit_config,
            btn_choose_tgt=can_edit_config,
            btn_choose_bak=can_edit_config and context.enable_backup,
            src_edit_readonly=not can_edit_config,
            tgt_edit_readonly=not can_edit_config,
            bak_edit_readonly=not (can_edit_config and context.enable_backup),
            combo_protocol=can_edit_config,
            cb_enable_backup=can_edit_config,
            btn_save=can_edit_config,
            upload_settings=can_edit_config,
            file_filters=can_edit_config,
            startup_settings=can_edit_config,
            notification_settings=can_edit_config,
            cb_limit_rate=can_edit_config,
            spin_max_rate=can_edit_config and context.rate_limit_enabled,
            cb_dedup_enable=can_edit_config,
            combo_hash=can_edit_config and context.dedup_enabled,
            combo_strategy=can_edit_config and context.dedup_enabled,
            network_settings=can_edit_config,
            filter_collapsible=can_edit_config,
            adv_collapsible=can_edit_config,
            cb_enable_ftp_server=can_edit_ftp_server,
            ftp_config_widget=True,
            ftp_client_collapsible=can_edit_config and context.protocol_uses_ftp,
            ftp_server_collapsible=True,
            ftp_server_controls=can_edit_ftp_server and context.ftp_server_enabled,
            btn_toggle_ftp_server=can_toggle_ftp_server,
            ftp_client_controls=can_edit_config and context.protocol_uses_ftp,
            btn_start=is_user_or_admin and not context.is_running,
            btn_pause=is_user_or_admin and context.is_running,
            btn_stop=is_user_or_admin and context.is_running,
            btn_more=True,
            menu_clear_logs=is_user_or_admin,
            menu_disk_cleanup=is_admin,
            menu_login=is_guest,
            menu_change_password=is_user_or_admin,
            menu_logout=is_user_or_admin,
            menu_language=is_user_or_admin,
        )

    @staticmethod
    def disk_cleanup_block_reason(role: UserRole) -> str:
        if role is UserRole.GUEST:
            return "请先登录后再使用磁盘清理功能"
        if role is UserRole.USER:
            return "普通用户无权限使用磁盘清理，请切换管理员登录"
        return ""
