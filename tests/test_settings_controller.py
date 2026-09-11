import json
import sys
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ConfigManager
from src.controllers import SettingsController
from src.models import ApplicationSettings, UploadProtocol
from src.repositories import ConfigRepository


def test_repository_loads_defaults_as_typed_settings(tmp_path: Path) -> None:
    repository = ConfigRepository(tmp_path / "config.json")

    settings = repository.load()

    assert isinstance(settings, ApplicationSettings)
    assert settings.to_config() == ConfigManager.get_default_config()
    assert repository.config_path.exists()


def test_controller_saves_typed_settings_without_losing_unknown_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    repository = ConfigRepository(config_path)
    controller = SettingsController(repository)
    raw = ConfigManager.get_default_config()
    raw["future_option"] = {"value": 7}
    raw["ftp_server"]["future_ftp_option"] = "kept"
    settings = ApplicationSettings.from_config(raw)
    settings.upload.upload_protocol = UploadProtocol.BOTH

    assert controller.save(settings, preserve_users=False)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["upload_protocol"] == "both"
    assert saved["future_option"] == {"value": 7}
    assert saved["ftp_server"]["future_ftp_option"] == "kept"


def test_config_manager_can_preserve_or_replace_users(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path)
    original = ConfigManager.get_default_config()
    original["users"] = {"admin": "old"}
    assert manager.save(original, preserve_users=False)

    replacement = ConfigManager.get_default_config()
    replacement["users"] = {"admin": "new"}
    assert manager.save(replacement)
    preserved = json.loads(config_path.read_text(encoding="utf-8"))
    assert preserved["users"] == {"admin": "old"}

    assert manager.save(replacement, preserve_users=False)
    replaced = json.loads(config_path.read_text(encoding="utf-8"))
    assert replaced["users"] == {"admin": "new"}


def test_atomic_save_failure_keeps_previous_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path)
    original = ConfigManager.get_default_config()
    original["upload_interval"] = 11
    assert manager.save(original, preserve_users=False)
    previous_bytes = config_path.read_bytes()
    changed = dict(original, upload_interval=99)

    with mock.patch(
        "src.config.os.replace", side_effect=PermissionError("replace denied")
    ):
        assert not manager.save(changed, preserve_users=False)

    assert "replace denied" in manager.last_error
    assert config_path.read_bytes() == previous_bytes
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_corrupt_config_is_backed_up_without_overwriting_original(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    broken = b'{"ftp_client": '
    config_path.write_bytes(broken)
    repository = ConfigRepository(config_path)

    loaded = repository.load_raw()

    assert loaded == ConfigManager.get_default_config()
    assert config_path.read_bytes() == broken
    backups = list(tmp_path.glob("config.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == broken
    assert "原文件未覆盖" in repository.last_error
    assert str(backups[0]) in repository.last_error


def test_config_round_trip_keeps_users_encrypted_passwords_and_unknown_fields(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path)
    original = ConfigManager.get_default_config()
    original["users"] = {"admin": "pbkdf2-hash"}
    original["future_option"] = {"enabled": True}
    original["ftp_client"]["password_encrypted"] = "encrypted-client-secret"
    original["ftp_client"]["future_ftp_option"] = "kept"
    assert manager.save(original, preserve_users=False)

    loaded = ApplicationSettings.from_config(manager.load()).to_config()
    loaded["upload_interval"] = 44
    assert manager.save(loaded)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert saved["users"] == {"admin": "pbkdf2-hash"}
    assert saved["ftp_client"]["password_encrypted"] == "encrypted-client-secret"
    assert saved["ftp_client"]["future_ftp_option"] == "kept"
    assert saved["future_option"] == {"enabled": True}


class FakeRepository:
    def __init__(self) -> None:
        self.settings = ApplicationSettings()
        self.last_error = ""
        self.saved_settings = None
        self.preserve_users = None

    def load(self) -> ApplicationSettings:
        return self.settings

    def load_raw(self):
        return self.settings.to_config()

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        self.saved_settings = settings
        self.preserve_users = preserve_users
        return True

    def save_raw(self, config, preserve_users: bool = True) -> bool:
        raise AssertionError("SettingsController must persist the typed model")


def test_controller_converts_legacy_view_payload_before_persistence() -> None:
    repository = FakeRepository()
    controller = SettingsController(repository)
    raw = ConfigManager.get_default_config()
    raw["upload_protocol"] = "ftp_client"

    assert controller.save_raw(raw, preserve_users=False)

    assert isinstance(repository.saved_settings, ApplicationSettings)
    assert repository.saved_settings.upload.upload_protocol is UploadProtocol.FTP_CLIENT
    assert repository.preserve_users is False
