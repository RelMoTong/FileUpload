import sys
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ConfigManager
from src.controllers import AuthController
from src.models import AuthModel, PermissionContext, UserRole
from src.services import AuthService


def test_default_credentials_authenticate_without_qapplication() -> None:
    controller = AuthController(AuthService(), model=AuthModel())

    user_result = controller.login(UserRole.USER, "123")
    assert user_result.success
    assert user_result.uses_default_password
    assert controller.current_role is UserRole.USER

    controller.logout()
    admin_result = controller.login(UserRole.ADMIN, "Tops123")
    assert admin_result.success
    assert admin_result.uses_default_password
    assert controller.current_role is UserRole.ADMIN


def test_invalid_credentials_do_not_change_role() -> None:
    controller = AuthController(AuthService(), model=AuthModel())

    result = controller.login(UserRole.ADMIN, "wrong")

    assert not result.success
    assert controller.current_role is UserRole.GUEST


def test_password_strength_rules_match_existing_behavior() -> None:
    service = AuthService()

    assert service.validate_new_password("old", "short") == "新密码至少需要 8 位"
    assert service.validate_new_password("SamePass1", "SamePass1") == "新密码不能与原密码相同"
    assert service.validate_new_password("old", "password") == "新密码过于简单，请使用更安全的密码"
    assert service.validate_new_password("old", "abcdefgh") == "新密码至少包含两种字符类型"
    assert service.validate_new_password("old", "NewPass123!") == ""


class FakeSettings:
    def __init__(self) -> None:
        self.config = ConfigManager.get_default_config()
        self.last_error = ""
        self.fail_save = False

    def load_raw(self):
        return deepcopy(self.config)

    def save_raw(self, config, preserve_users: bool = True) -> bool:
        if self.fail_save:
            self.last_error = "simulated save failure"
            return False
        self.config = deepcopy(config)
        return True


def test_admin_can_change_user_password_and_persist_hash() -> None:
    settings = FakeSettings()
    controller = AuthController(AuthService(), settings, model=AuthModel())
    assert controller.login(UserRole.ADMIN, "Tops123").success

    result = controller.change_password(
        UserRole.USER,
        "Tops123",
        "NewUser123!",
        "NewUser123!",
    )

    assert result.success
    stored = settings.config["users"]["user"]
    assert stored.startswith("pbkdf2_sha256$")
    assert AuthService.verify_password("NewUser123!", stored) == (True, False)
    controller.logout()
    assert controller.login(UserRole.USER, "NewUser123!").success


def test_non_admin_cannot_change_password() -> None:
    controller = AuthController(AuthService(), model=AuthModel())
    assert controller.login(UserRole.USER, "123").success

    result = controller.change_password(
        UserRole.USER,
        "123",
        "NewUser123!",
        "NewUser123!",
    )

    assert result.success
    controller.logout()
    assert controller.login(UserRole.USER, "NewUser123!").success


def test_failed_password_persistence_rolls_back_credentials() -> None:
    settings = FakeSettings()
    settings.config["users"]["admin"] = AuthService.hash_password("Tops123")
    settings.config["users"]["user"] = AuthService.hash_password("123")
    settings.fail_save = True
    controller = AuthController(AuthService(), settings, model=AuthModel())
    controller.load_users(settings.config["users"])
    assert controller.login(UserRole.ADMIN, "Tops123").success

    result = controller.change_password(
        UserRole.USER,
        "Tops123",
        "NewUser123!",
        "NewUser123!",
    )

    assert not result.success
    assert result.error == "simulated save failure"
    controller.logout()
    assert controller.login(UserRole.USER, "123").success


def test_pbkdf2_uses_independent_salts_and_rejects_corrupt_values() -> None:
    first = AuthService.hash_password("StrongPass123!")
    second = AuthService.hash_password("StrongPass123!")

    assert first != second
    assert AuthService.verify_password("StrongPass123!", first) == (True, False)
    assert AuthService.verify_password("wrong", first) == (False, False)
    assert AuthService.verify_password("StrongPass123!", "pbkdf2_sha256$bad") == (
        False,
        False,
    )


def test_legacy_sha256_login_is_transparently_migrated_and_persisted() -> None:
    settings = FakeSettings()
    legacy = AuthService.legacy_hash_password("LegacyPass123!")
    settings.config["users"] = {"admin": legacy}
    controller = AuthController(AuthService(), settings, model=AuthModel())
    controller.load_users(settings.config["users"])

    result = controller.login(UserRole.ADMIN, "LegacyPass123!")

    assert result.success
    assert result.credential_upgraded
    migrated = settings.config["users"]["admin"]
    assert migrated.startswith("pbkdf2_sha256$")
    assert migrated != legacy
    assert AuthService.verify_password("LegacyPass123!", migrated) == (True, False)


def test_default_password_login_is_persisted_but_business_actions_stay_locked() -> None:
    settings = FakeSettings()
    controller = AuthController(AuthService(), settings, model=AuthModel())

    result = controller.login(UserRole.ADMIN, "Tops123")

    assert result.success
    assert result.uses_default_password
    assert controller.password_change_required
    assert settings.config["users"]["admin"].startswith("pbkdf2_sha256$")
    assert UserRole.ADMIN in controller.default_password_roles
    permissions = controller.compute_permissions(PermissionContext())
    assert not permissions.btn_start
    assert not permissions.btn_save
    assert permissions.menu_change_password
    assert permissions.menu_logout

    changed = controller.change_password(
        UserRole.ADMIN, "Tops123", "ChangedAdmin123!", "ChangedAdmin123!"
    )
    assert changed.success
    assert not controller.password_change_required
    assert controller.compute_permissions(PermissionContext()).btn_start


def test_failed_legacy_migration_rejects_login_and_restores_legacy_hash() -> None:
    settings = FakeSettings()
    legacy = AuthService.legacy_hash_password("LegacyPass123!")
    settings.config["users"] = {"admin": legacy}
    settings.fail_save = True
    controller = AuthController(AuthService(), settings, model=AuthModel())
    controller.load_users(settings.config["users"])

    result = controller.login(UserRole.ADMIN, "LegacyPass123!")

    assert not result.success
    assert controller.current_role is UserRole.GUEST
    assert controller._model.users["admin"] == legacy


def test_permission_render_model_covers_all_roles_without_qapplication() -> None:
    service = AuthService()
    model = AuthModel()
    controller = AuthController(service, model=model)

    guest = controller.compute_permissions(PermissionContext())
    assert guest.menu_login
    assert not guest.btn_start
    assert not guest.menu_disk_cleanup

    controller.set_current_role(UserRole.USER)
    user = controller.compute_permissions(PermissionContext(enable_backup=True))
    assert user.btn_start
    assert user.btn_choose_bak
    assert not user.menu_disk_cleanup
    assert not user.cb_enable_ftp_server

    controller.set_current_role(UserRole.ADMIN)
    admin = controller.compute_permissions(
        PermissionContext(
            enable_backup=True,
            ftp_server_enabled=True,
            dedup_enabled=True,
            rate_limit_enabled=True,
        )
    )
    assert admin.menu_disk_cleanup
    assert admin.cb_enable_ftp_server
    assert admin.btn_toggle_ftp_server
    assert admin.combo_hash
    assert admin.spin_max_rate

    running = controller.compute_permissions(
        PermissionContext(is_running=True, ftp_server_enabled=True)
    )
    assert not running.btn_start
    assert running.btn_pause
    assert running.btn_stop
    assert not running.btn_save


def test_disk_cleanup_policy_is_owned_by_auth_controller() -> None:
    controller = AuthController(AuthService(), model=AuthModel())
    assert not controller.can_manage_disk_cleanup()
    assert controller.disk_cleanup_block_reason() == "请先登录后再使用磁盘清理功能"

    controller.set_current_role(UserRole.USER)
    assert not controller.can_manage_disk_cleanup()
    assert "普通用户无权限" in controller.disk_cleanup_block_reason()

    controller.set_current_role(UserRole.ADMIN)
    assert controller.can_manage_disk_cleanup()
    assert controller.disk_cleanup_block_reason() == ""
