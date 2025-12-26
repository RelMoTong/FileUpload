# -*- coding: utf-8 -*-
"""
配置管理模块

负责配置文件的加载、保存和默认值生成
"""
import copy
import json
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigManager:
    """配置管理器"""
    
    DEFAULT_CONFIG = {
        'source_folder': '',
        'target_folder': '',
        'backup_folder': '',
        'enable_backup': True,
        'upload_interval': 30,
        'monitor_mode': 'periodic',
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
        'auto_delete_threshold': 80,
        'auto_delete_target_percent': 40,
        'auto_delete_keep_days': 10,
        'auto_delete_check_interval': 300,
        # 协议配置
        'upload_protocol': 'smb',  # 上传协议: smb, ftp_client, both
        'current_protocol': 'smb',
        'enable_ftp_server': False,  # v3.1.0: FTP服务器独立开关
        # v3.0.2 新增：语言设置
        'language': 'zh_CN',
        # v3.0.2 新增：断点续传设置
        'enable_resume': True,
        'resume_min_size_mb': 10,
        # FTP 服务器配置
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
        # FTP 客户端配置
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
        # 用户账户
        'users': {},
    }
    
    def __init__(self, config_path: Path):
        """初始化配置管理器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """递归合并配置，确保保留默认值且不污染全局默认配置。"""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ConfigManager._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    
    def load(self) -> Dict[str, Any]:
        """加载配置文件
        
        Returns:
            配置字典
        """
        if not self.config_path.exists():
            self._config = copy.deepcopy(self.DEFAULT_CONFIG)
            self.save(self._config)
            return copy.deepcopy(self._config)
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
            
            # 合并默认配置和加载的配置（深度合并，保留新增默认值）
            merged_config = self._deep_merge(self.DEFAULT_CONFIG, loaded_config)
            self._config = merged_config
            if merged_config != loaded_config:
                self.save(merged_config)
            return copy.deepcopy(self._config)
        except Exception as e:
            print(f"配置加载失败: {e}")
            self._config = copy.deepcopy(self.DEFAULT_CONFIG)
            self.save(self._config)
            return copy.deepcopy(self._config)
    
    def save(self, config: Dict[str, Any]) -> bool:
        """保存配置文件
        
        Args:
            config: 要保存的配置字典
            
        Returns:
            是否保存成功
        """
        try:
            # 保留现有的用户密码
            if self.config_path.exists():
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        old_cfg = json.load(f)
                        config['users'] = old_cfg.get('users', {})
                except Exception:
                    pass
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            self._config = copy.deepcopy(config)
            return True
        except Exception as e:
            print(f"配置保存失败: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置配置项
        
        Args:
            key: 配置键
            value: 配置值
        """
        self._config[key] = value
    
    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """获取默认配置
        
        Returns:
            默认配置字典
        """
        return copy.deepcopy(ConfigManager.DEFAULT_CONFIG)
