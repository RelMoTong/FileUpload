# -*- coding: utf-8 -*-
"""
配置管理器 - 负责配置加载/保存、版本升级兼容
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigManager:
    """配置管理器"""
    
    CONFIG_VERSION = "2.2.0"
    
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self.config_path = app_dir / 'config.json'
        self.config: Dict[str, Any] = {}
        
    def load(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not self.config_path.exists():
            return self._get_default_config()
            
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            # 升级配置到当前版本
            cfg = self._upgrade_config(cfg)
            self.config = cfg
            return cfg
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return self._get_default_config()
    
    def save(self, config: Dict[str, Any]) -> bool:
        """保存配置文件"""
        try:
            # 确保配置版本号
            config['config_version'] = self.CONFIG_VERSION
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            self.config = config.copy()
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            'config_version': self.CONFIG_VERSION,
            'source_folder': '',
            'target_folder': '',
            'backup_folder': '',
            'enable_backup': True,
            'upload_interval': 30,
            'monitor_mode': 'periodic',
            'disk_threshold_percent': 10,
            'retry_count': 3,
            'disk_check_interval': 5,
            'filter_jpg': True,
            'filter_png': True,
            'filter_bmp': True,
            'filter_tiff': True,
            'filter_gif': True,
            'filter_raw': True,
            'auto_start_windows': False,
            'auto_run_on_startup': False,
            'enable_deduplication': False,
            'hash_algorithm': 'md5',
            'duplicate_strategy': 'ask',
            'network_check_interval': 10,
            'network_auto_pause': True,
            'network_auto_resume': True,
            'enable_auto_delete': False,
            'auto_delete_folder': '',
            'auto_delete_threshold': 80,
            'auto_delete_keep_days': 10,
            'auto_delete_check_interval': 300,
            'upload_protocol': 'smb',
            'current_protocol': 'smb',
            'ftp_server': {
                'host': '0.0.0.0',
                'port': 2121,
                'username': 'upload_user',
                'password': 'upload_pass',
                'shared_folder': '',
                'enable_passive': True,
                'passive_ports_start': 60000,
                'passive_ports_end': 65535,
                'enable_tls': False,
                'max_connections': 256,
                'max_connections_per_ip': 5,
            },
            'ftp_client': {
                'host': '',
                'port': 21,
                'username': '',
                'password': '',
                'remote_path': '/upload',
                'timeout': 30,
                'retry_count': 3,
                'passive_mode': True,
                'enable_tls': False,
            },
            # v2.2.0 新增
            'enable_notifications': True,  # 是否启用托盘通知
            'notification_level': 'all',  # all/important/errors
            'log_rotation_size_mb': 10,  # 日志文件大小限制(MB)
            'log_retention_days': 30,  # 日志保留天数
            'users': {},
        }
    
    def _upgrade_config(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """配置版本升级"""
        version = cfg.get('config_version', '1.0.0')
        
        # 从1.x升级到2.0.0
        if version < '2.0.0':
            cfg.setdefault('upload_protocol', 'smb')
            cfg.setdefault('ftp_server', {})
            cfg.setdefault('ftp_client', {})
        
        # 从2.0.x升级到2.1.0
        if version < '2.1.0':
            cfg.setdefault('enable_auto_delete', False)
            cfg.setdefault('auto_delete_folder', '')
            cfg.setdefault('auto_delete_threshold', 80)
            cfg.setdefault('auto_delete_keep_days', 10)
            cfg.setdefault('auto_delete_check_interval', 300)
        
        # 从2.1.x升级到2.2.0
        if version < '2.2.0':
            cfg.setdefault('current_protocol', cfg.get('upload_protocol', 'smb'))
            cfg.setdefault('enable_notifications', True)
            cfg.setdefault('notification_level', 'all')
            cfg.setdefault('log_rotation_size_mb', 10)
            cfg.setdefault('log_retention_days', 30)
        
        # 确保所有必需字段存在
        default_cfg = self._get_default_config()
        for key, value in default_cfg.items():
            if key not in cfg:
                cfg[key] = value
        
        cfg['config_version'] = self.CONFIG_VERSION
        return cfg
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any):
        """设置配置值"""
        self.config[key] = value
