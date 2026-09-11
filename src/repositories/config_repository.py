"""Configuration persistence adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.config import ConfigManager
from src.models import ApplicationSettings


class ConfigRepository:
    """Load and save typed settings through the existing ConfigManager."""

    def __init__(self, config_path: Path):
        self._manager = ConfigManager(config_path)
        self.last_error = ""

    @property
    def config_path(self) -> Path:
        return self._manager.config_path

    def load(self) -> ApplicationSettings:
        raw = self.load_raw()
        return ApplicationSettings.from_config(raw)

    def load_raw(self) -> Dict[str, Any]:
        raw = self._manager.load()
        self.last_error = self._manager.last_error
        return raw

    def save(self, settings: ApplicationSettings, preserve_users: bool = True) -> bool:
        return self.save_raw(settings.to_config(), preserve_users=preserve_users)

    def save_raw(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        success = self._manager.save(config, preserve_users=preserve_users)
        self.last_error = self._manager.last_error
        return success

