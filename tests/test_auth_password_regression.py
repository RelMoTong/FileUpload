"""P0-04 regression coverage for password persistence and authority."""

import json
from copy import deepcopy
from pathlib import Path

from src.config import ConfigManager
from src.controllers import AuthController, SettingsController
from src.models import ApplicationSettings, AuthModel, UserRole
from src.repositories import ConfigRepository
from src.services import AuthService


def _make_persisted_default_settings(tmp_path: Path) -> tuple[SettingsController, AuthController]:
    repository = ConfigRepository(tmp_path / "config.json")
    settings = SettingsController(repository)
    default_admin_hash = AuthService.hash_password("Tops123")
    initial = ApplicationSettings.from_config({"users": {"admin": default_admin_hash}})
    assert settings.save(initial, preserve_users=False)
    controller = AuthController(AuthService(), settings, model=AuthModel())
    controller.load_users(settings.load_settings().auth.to_mapping())
    return settings, controller


def test_password_change_survives_subsequent_config_save(tmp_path: Path) -> None:
    settings, controller = _make_persisted_default_settings(tmp_path)
    stale_hash = controller.users_mapping()["admin"]

    assert controller.login(UserRole.ADMIN, "Tops123").success
    assert controller.change_password(
        UserRole.ADMIN, "Tops123", "FieldAdmin123!", "FieldAdmin123!"
    ).success

    stale_payload = ConfigManager.get_default_config()
    stale_payload["source_folder"] = "D:/camera"
    stale_payload["users"] = {"admin": stale_hash}
    assert settings.save(
        ApplicationSettings.from_config(stale_payload), preserve_users=True
    )

    restarted = AuthController(AuthService(), settings, model=AuthModel())
    restarted.load_users(settings.load_settings().auth.to_mapping())
    assert not restarted.login(UserRole.ADMIN, "Tops123").success
    assert restarted.login(UserRole.ADMIN, "FieldAdmin123!").success

    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted["users"]["admin"].startswith("pbkdf2_sha256$")


def test_config_write_preserves_users_when_payload_carries_stale_hash(tmp_path: Path) -> None:
    settings, controller = _make_persisted_default_settings(tmp_path)
    old_hash = controller.users_mapping()["admin"]
    assert controller.login(UserRole.ADMIN, "Tops123").success
    assert controller.change_password(
        UserRole.ADMIN, "Tops123", "FieldAdmin123!", "FieldAdmin123!"
    ).success
    new_hash = controller.users_mapping()["admin"]

    stale = ApplicationSettings.from_config({"users": {"admin": old_hash}})
    assert settings.save(stale, preserve_users=True)

    persisted = settings.load_settings().auth.to_mapping()
    assert persisted["admin"] == new_hash


def test_users_survive_config_write_when_field_omitted(tmp_path: Path) -> None:
    settings, controller = _make_persisted_default_settings(tmp_path)
    expected_users = controller.users_mapping()
    payload = ConfigManager.get_default_config()
    payload.pop("users")

    assert settings.save_raw(payload, preserve_users=True)
    assert settings.load_settings().auth.to_mapping() == expected_users


class _DiscardingPasswordSettings:
    def __init__(self) -> None:
        self.last_error = ""
        self.config = ConfigManager.get_default_config()
        self.config["users"] = {"admin": AuthService.hash_password("Tops123")}

    def load_raw(self) -> dict:
        return deepcopy(self.config)

    def save_raw(self, config: dict, preserve_users: bool = True) -> bool:
        del preserve_users
        updated = deepcopy(config)
        updated["users"] = deepcopy(self.config["users"])
        self.config = updated
        return True


def test_password_change_fails_loudly_when_persisted_hash_mismatch() -> None:
    settings = _DiscardingPasswordSettings()
    controller = AuthController(AuthService(), settings, model=AuthModel())
    controller.load_users(settings.config["users"])
    assert controller.login(UserRole.ADMIN, "Tops123").success

    result = controller.change_password(
        UserRole.ADMIN, "Tops123", "FieldAdmin123!", "FieldAdmin123!"
    )

    assert not result.success
    assert result.error == "密码写入校验失败，请重试"
    controller.logout()
    assert controller.login(UserRole.ADMIN, "Tops123").success


def test_users_mapping_matches_persisted_source_after_change(tmp_path: Path) -> None:
    settings, controller = _make_persisted_default_settings(tmp_path)
    assert controller.login(UserRole.ADMIN, "Tops123").success
    assert controller.change_password(
        UserRole.ADMIN, "Tops123", "FieldAdmin123!", "FieldAdmin123!"
    ).success

    assert controller.users_mapping() == settings.load_settings().auth.to_mapping()
