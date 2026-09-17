# -*- coding: utf-8 -*-
"""
配置管理模块

负责配置文件的加载、保存和默认值生成
"""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from typing import Dict, Any, Optional

from src.models.stability import apply_stability_feature_freeze
from src.core.atomic_json_store import AtomicJsonStore
from src.models._conversion import SHARED_RETIRED_CONFIG_KEYS


class ConfigManager:
    """配置管理器"""
    
    DEFAULT_CONFIG = {
        'source_folder': '',
        'target_folder': '',
        'backup_folder': '',
        'enable_backup': True,
        'upload_interval': 30,
        'file_upload_delay_seconds': 1.5,
        'disk_threshold_percent': 10,
        'retry_count': 3,
        'disk_check_interval': 5,
        # 文件过滤
        'filter_jpg': True,
        'filter_png': True,
        'filter_bmp': True,
        'filter_gif': True,
        'filter_raw': True,
        # 自动启动
        'auto_start_windows': False,
        'auto_run_on_startup': False,
        # 托盘通知
        'show_notifications': True,
        # 速率限制
        'limit_upload_rate': False,
        'max_upload_rate_mbps': 10.0,
        # 去重
        'enable_deduplication': False,
        'hash_algorithm': 'md5',
        'duplicate_strategy': 'ask',
        # 网络监控
        'network_check_interval': 10,
        'network_auto_pause': True,
        'network_auto_resume': True,
        # 自动删除
        'enable_auto_delete': False,
        'auto_delete_folder': '',
        'auto_delete_folders': [],
        'auto_delete_threshold': 80,
        'auto_delete_target_percent': 40,
        'auto_delete_check_interval': 300,
        'auto_delete_formats': [],
        'auto_delete_use_trash': True,
        # 协议配置
        'upload_protocol': 'smb',  # 上传协议: smb, ftp_client, both
        'current_protocol': 'smb',
        'enable_ftp_server': False,  # v3.1.0: FTP服务器独立开关
        # v3.0.2 新增：语言设置
        'language': 'zh_CN',
        # FTP 服务器配置
        'ftp_server': {
            'host': '0.0.0.0',
            'port': 2121,
            'username': 'upload_user',
            'password': '',
            'password_encrypted': '',
            'shared_folder': '',
            'enable_passive': True,
            'passive_ports_start': 60000,
            'passive_ports_end': 65535,
            'enable_tls': False,
            'cert_file': '',
            'key_file': '',
            'max_connections': 256,
            'max_connections_per_ip': 5,
        },
        # FTP 客户端配置
        'ftp_client': {
            'host': '',
            'port': 21,
            'username': '',
            'password': '',
            'password_encrypted': '',
            'remote_path': '/upload',
            'timeout': 30,
            'retry_count': 3,
            'passive_mode': True,
            'enable_tls': False,
        },
        # 用户账户
        'users': {},
    }

    # 旧版本曾写入这些字段，但当前已被固定策略替代或不再生效。
    # 保存时会主动移除它们，同时保留未来版本可能新增的未知字段。
    RETIRED_CONFIG_KEYS = SHARED_RETIRED_CONFIG_KEYS
    
    def __init__(self, config_path: Path):
        """初始化配置管理器和同目录的原子 JSON 存储。

        用途：绑定应用配置文件及其安全读写机制。
        输入：配置文件路径【config_path】。
        输出：已建立内存缓存和原子存储对象的管理器实例。
        关键步骤：保存路径、初始化空缓存，并配置同盘原子替换函数。
        风险点：路径应指向应用可写目录；实际 I/O 延后到【load】或【save】执行。
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self.last_error = ''
        self._store = AtomicJsonStore(
            self.config_path,
            wrap_envelope=False,
            replace_func=lambda source, target: os.replace(source, target),
        )

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """递归合并默认配置与用户配置，返回互不共享引用的新字典。

        用途：为旧版配置补齐新默认字段，同时保留用户已经设置的值。
        输入：默认配置【base】与优先级更高的覆盖配置【override】。
        输出：合并后的独立配置字典。
        关键步骤：同名字典字段继续递归合并，其他字段深拷贝覆盖。
        风险点：不可原地修改默认配置，否则后续加载会继承前一份用户配置的值。
        """
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ConfigManager._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @classmethod
    def _without_retired_keys(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """复制配置并移除已废弃字段，避免旧字段在每次保存后继续传播。"""
        return {
            key: copy.deepcopy(value)
            for key, value in config.items()
            if key not in cls.RETIRED_CONFIG_KEYS
        }
    
    def load(self) -> Dict[str, Any]:
        """加载并规范化配置文件；故障时保留原文件并返回内存默认配置。

        用途：建立配置读取、旧版兼容、功能冻结和损坏恢复的唯一入口。
        输入：构造时保存的【config_path】，无需额外参数。
        输出：调用方可修改而不会污染内部缓存的配置副本。
        关键步骤：首启创建默认配置；正常路径移除废弃字段并深度合并；失败时备份损坏文件。
        风险点：读取或解析失败时绝不覆盖原始配置，避免将现场配置不可恢复地丢失。
        """
        # 每次加载先清除上一轮错误，避免界面展示已经解决的旧问题。
        self.last_error = ''
        if not self.config_path.exists():
            # 首次运行：从默认值生成配置文件，后续仍通过同一读取路径取得副本。
            self._config = copy.deepcopy(self.DEFAULT_CONFIG)
            self.save(self._config)
            return copy.deepcopy(self._config)
        
        try:
            loaded_config = self._store.read(default=None)
            if not isinstance(loaded_config, dict):
                raise ValueError("配置文件必须是 JSON 对象")
            
            # 先移除废弃字段，再深度合并默认值以兼容旧版本缺少的新字段。
            loaded_config = self._without_retired_keys(loaded_config)
            merged_config = apply_stability_feature_freeze(
                self._deep_merge(self.DEFAULT_CONFIG, loaded_config)
            )
            self._config = merged_config
            # 配置结构升级、冻结开关修正后立即原子写回，保持下次启动结果一致。
            if merged_config != loaded_config:
                self.save(merged_config)
            return copy.deepcopy(self._config)
        except Exception as e:
            # 解析失败时绝不覆盖原文件；先尝试备份，再让程序使用内存默认值启动。
            backup_path: Optional[Path] = None
            backup_error = ""
            try:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup_path = self.config_path.with_name(
                    f"{self.config_path.name}.corrupt-{timestamp}.bak"
                )
                shutil.copy2(self.config_path, backup_path)
            except Exception as backup_exc:
                backup_error = f"；损坏配置备份失败: {backup_exc}"
            backup_text = f"；备份: {backup_path}" if backup_path else ""
            self.last_error = (
                f"配置文件解析失败，原文件未覆盖{backup_text}: "
                f"{type(e).__name__}: {e}{backup_error}"
            )
            print(self.last_error)
            self._config = copy.deepcopy(self.DEFAULT_CONFIG)
            return copy.deepcopy(self._config)
    
    def save(self, config: Dict[str, Any], preserve_users: bool = True) -> bool:
        """以原子方式保存配置，并默认保留磁盘上最新的用户凭据。

        用途：作为配置写入的唯一入口，保证兼容字段、冻结开关和凭据策略一致。
        输入：待保存字典【config】；【preserve_users】控制是否保留已有用户凭据。
        输出：成功返回【True】，失败返回【False】并写入【last_error】。
        关键步骤：规范化候选配置、合并未知字段和用户凭据、再交由原子存储写盘。
        风险点：常规保存不能覆盖改密流程刚写入的凭据；任何写入失败都必须保留旧文件。
        """
        # 每次保存独立记录错误，供 Controller 和界面准确提示本次结果。
        self.last_error = ''
        try:
            # 1. 应用功能冻结并剔除废弃字段，形成本次准备写入的候选配置。
            payload = apply_stability_feature_freeze(
                self._without_retired_keys(config)
            )
            # 2. 合并磁盘上的未知字段，避免当前版本保存时误删未来版本配置。
            old_cfg: Dict[str, Any] = {}
            if self.config_path.exists():
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        old_cfg = json.load(f)
                    if isinstance(old_cfg, dict):
                        payload = apply_stability_feature_freeze(
                            self._deep_merge(
                                self._without_retired_keys(old_cfg), payload
                            )
                        )
                    else:
                        old_cfg = {}
                    # 3. 常规保存不覆盖最新凭据；改密流程会显式传入 False。
                    if preserve_users:
                        payload['users'] = old_cfg.get('users', {})
                except Exception:
                    pass

            # 4. AtomicJsonStore 负责临时写入、备份和原子替换。
            if not self._store.write(payload):
                raise OSError(self._store.last_error)

            self._config = copy.deepcopy(payload)
            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"配置保存失败: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """从内存配置读取单项；键不存在时返回调用方给定的默认值。"""
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """仅更新内存配置；调用方需要显式调用【save】才会写入磁盘。"""
        self._config[key] = value
    
    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """返回默认配置的深拷贝，防止调用方污染类级默认值。"""
        return copy.deepcopy(ConfigManager.DEFAULT_CONFIG)
