"""支持未知字段无损往返的聚合配置模型。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from ._conversion import unknown_fields
from .auth_model import AuthModel
from .cleanup_settings import CleanupSettings
from .ftp_settings import FTPSettings
from .upload_settings import UploadSettings
from .stability import apply_stability_feature_freeze


@dataclass
class ApplicationSettings:
    """组合上传、清理、FTP 与认证设置的顶层配置对象。

    用途：在配置存储层和各业务配置模型之间提供统一转换边界。
    输入：来自 JSON 的配置映射，或由应用运行时创建的子模型。
    输出：结构化配置对象及可持久化的普通字典。
    关键步骤：识别当前已知字段，将未知字段深拷贝到 ``extra`` 后无损回写。
    风险点：迁移冻结逻辑会主动覆盖被禁止的功能开关，不能绕过此入口直接加载配置。
    """
    upload: UploadSettings = field(default_factory=UploadSettings)
    cleanup: CleanupSettings = field(default_factory=CleanupSettings)
    ftp: FTPSettings = field(default_factory=FTPSettings)
    auth: AuthModel = field(default_factory=AuthModel)
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ApplicationSettings":
        """从原始配置恢复全部子模型，并保留未识别字段。

        用途：兼容旧版和未来版本配置，同时应用当前稳定性冻结规则。
        输入：JSON 解析后的配置映射。
        输出：完整的 ``ApplicationSettings`` 实例。
        关键步骤：先复制并冻结受限开关，再划分已知字段和额外字段。
        风险点：输入映射不可在此方法中原地修改，避免影响配置缓存的其他使用者。
        """
        config = apply_stability_feature_freeze(dict(config))
        known_fields = (
            *UploadSettings.CONFIG_KEYS,
            *UploadSettings.RETIRED_CONFIG_KEYS,
            *CleanupSettings.CONFIG_KEYS,
            *CleanupSettings.RETIRED_CONFIG_KEYS,
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
        """合并各子模型与未知字段，生成可直接写入 JSON 的配置字典。

        用途：保证保存配置时不丢失当前模型尚未消费的字段。
        输入：当前聚合模型状态。
        输出：独立的普通字典。
        关键步骤：深拷贝 ``extra`` 后按子模型顺序覆盖已知字段。
        风险点：导出结果不能复用 ``extra`` 的引用，否则保存前的修改会污染模型。
        """
        result = deepcopy(self.extra)
        result.update(self.upload.to_mapping())
        result.update(self.cleanup.to_mapping())
        result.update(self.ftp.to_mapping())
        result["users"] = self.auth.to_mapping()
        return result
