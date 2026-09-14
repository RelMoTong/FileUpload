"""Controller for loading and saving application settings."""

from __future__ import annotations

from typing import Any, Dict, Protocol
import sys

from src.core import protect_secret, unprotect_secret
from src.models import ApplicationSettings


class SettingsRepository(Protocol):
    last_error: str

    def load(self) -> ApplicationSettings:
        ...

    def load_raw(self) -> Dict[str, Any]:
        ...

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        ...

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        ...


class SettingsController:
    """Coordinate configuration models and their persistence boundary."""

    def __init__(self, repository: SettingsRepository):
        self._repository = repository
        self.last_error = ""

    @property
    def config_exists(self) -> bool:
        config_path = getattr(self._repository, "config_path", None)
        return bool(config_path and config_path.exists())

    def load(self) -> ApplicationSettings:
        settings = self._repository.load()
        self.last_error = self._repository.last_error
        return settings

    def load_settings(self) -> ApplicationSettings:
        """Typed alias used by views; raw payloads stay at the repository edge."""
        return self.load()

    def load_raw(self) -> Dict[str, Any]:
        config = self._repository.load_raw()
        self.last_error = self._repository.last_error
        return config

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        success = self._repository.save(settings, preserve_users=preserve_users)
        self.last_error = self._repository.last_error
        return success

    @staticmethod
    def decode_ftp_password(section: Dict[str, Any], default: str = "") -> str:
        """Decode protected credentials while retaining legacy plaintext support."""
        encrypted = str(section.get("password_encrypted", "") or "").strip()
        if encrypted:
            decrypted = unprotect_secret(encrypted)
            if decrypted:
                return decrypted
        return str(section.get("password", default) or "")

    @staticmethod
    def encode_ftp_password(password: str, label: str) -> tuple[str, str]:
        """Return legacy plaintext/protected fields for persisted FTP settings."""
        password = password.strip()
        if not password:
            return "", ""
        encrypted = protect_secret(password)
        if sys.platform == "win32" and (not encrypted or encrypted == password):
            raise RuntimeError(f"{label}密码加密失败")
        return "", encrypted

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        settings = ApplicationSettings.from_config(config)
        success = self._repository.save(settings, preserve_users=preserve_users)
        self.last_error = self._repository.last_error
        return success
