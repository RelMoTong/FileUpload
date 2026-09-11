"""Aggregate configuration model with lossless dictionary conversion."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from ._conversion import unknown_fields
from .auth_model import AuthModel
from .cleanup_settings import CleanupSettings
from .ftp_settings import FTPSettings
from .upload_settings import UploadSettings


@dataclass
class ApplicationSettings:
    upload: UploadSettings = field(default_factory=UploadSettings)
    cleanup: CleanupSettings = field(default_factory=CleanupSettings)
    ftp: FTPSettings = field(default_factory=FTPSettings)
    auth: AuthModel = field(default_factory=AuthModel)
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ApplicationSettings":
        known_fields = (
            *UploadSettings.CONFIG_KEYS,
            *UploadSettings.RETIRED_CONFIG_KEYS,
            *CleanupSettings.CONFIG_KEYS,
            "enable_ftp_server",
            "ftp_server",
            "ftp_client",
            "users",
        )
        return cls(
            upload=UploadSettings.from_mapping(config),
            cleanup=CleanupSettings.from_mapping(config),
            ftp=FTPSettings.from_mapping(config),
            auth=AuthModel.from_mapping(config.get("users")),
            extra=unknown_fields(config, known_fields),
        )

    def to_config(self) -> Dict[str, Any]:
        result = deepcopy(self.extra)
        result.update(self.upload.to_mapping())
        result.update(self.cleanup.to_mapping())
        result.update(self.ftp.to_mapping())
        result["users"] = self.auth.to_mapping()
        return result
