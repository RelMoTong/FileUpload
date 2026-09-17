from copy import deepcopy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ConfigManager
from src.models import (
    AppState,
    ApplicationSettings,
    DuplicateStrategy,
    NetworkStatus,
    UploadProtocol,
    UploadStatus,
    UserRole,
)


def test_default_config_round_trip_is_lossless() -> None:
    original = ConfigManager.get_default_config()

    restored = ApplicationSettings.from_config(original).to_config()

    assert restored == original


def test_config_round_trip_preserves_unknown_top_level_and_ftp_fields() -> None:
    original = ConfigManager.get_default_config()
    original["future_top_level"] = {"enabled": True}
    original["ftp_server"]["future_server_option"] = "server-value"
    original["ftp_client"]["future_client_option"] = "client-value"

    restored = ApplicationSettings.from_config(original).to_config()

    assert restored == original


def test_config_models_do_not_share_mutable_defaults() -> None:
    first = ApplicationSettings.from_config(ConfigManager.get_default_config())
    second = ApplicationSettings.from_config(ConfigManager.get_default_config())

    first.cleanup.auto_delete_folders.append("D:/images")
    first.auth.users["admin"] = "hash"
    first.ftp.server.extra["custom"] = []

    assert second.cleanup.auto_delete_folders == []
    assert second.auth.users == {}
    assert second.ftp.server.extra == {}


def test_model_changes_serialize_to_existing_external_values() -> None:
    settings = ApplicationSettings.from_config(ConfigManager.get_default_config())
    settings.upload.upload_protocol = UploadProtocol.BOTH
    settings.upload.current_protocol = UploadProtocol.FTP_CLIENT
    settings.upload.duplicate_strategy = DuplicateStrategy.RENAME
    settings.upload.file_upload_delay_seconds = 2.5

    serialized = settings.to_config()

    assert serialized["upload_protocol"] == "both"
    assert serialized["current_protocol"] == "ftp_client"
    assert serialized["duplicate_strategy"] == "rename"
    assert serialized["file_upload_delay_seconds"] == 2.5


def test_invalid_enum_values_fall_back_without_affecting_input() -> None:
    original = ConfigManager.get_default_config()
    original["upload_protocol"] = "invalid"
    original["duplicate_strategy"] = "invalid"
    snapshot = deepcopy(original)

    settings = ApplicationSettings.from_config(original)

    assert settings.upload.upload_protocol is UploadProtocol.SMB
    assert settings.upload.duplicate_strategy is DuplicateStrategy.ASK
    assert original == snapshot


def test_app_state_has_explicit_enum_backed_defaults() -> None:
    state = AppState()

    assert state.role is UserRole.GUEST
    assert state.upload_status is UploadStatus.STOPPED
    assert state.network_status is NetworkStatus.UNKNOWN
    assert not state.is_running
    assert not state.is_paused

    state.upload_status = UploadStatus.PAUSED
    assert state.is_running
    assert state.is_paused
