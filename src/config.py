# -*- coding: utf-8 -*-
"""
配置管理模块

负责配置文件的加载、保存和默认值生成
"""
import copy
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, Iterator, BinaryIO


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
        'auto_delete_folders': [],
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
            'password': '',
            'password_encrypted': '',
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
    
    def __init__(self, config_path: Path):
        """初始化配置管理器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self._save_lock = threading.Lock()

    @contextmanager
    def _locked_config_file(self) -> Iterator[None]:
        """进程内和进程间配置文件锁。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.config_path.with_name(self.config_path.name + '.lock')
        with open(lock_path, 'a+b') as lock_file:
            self._lock_file(lock_file)
            try:
                yield
            finally:
                self._unlock_file(lock_file)

    @staticmethod
    def _lock_file(lock_file: BinaryIO) -> None:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            return

        import fcntl  # type: ignore[import-not-found]
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_file(lock_file: BinaryIO) -> None:
        try:
            if os.name == 'nt':
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                return

            import fcntl  # type: ignore[import-not-found]
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

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
        with self._save_lock, self._locked_config_file():
            try:
                config_to_save = copy.deepcopy(config)
                # 保留现有的用户密码
                if self.config_path.exists():
                    try:
                        with open(self.config_path, 'r', encoding='utf-8') as f:
                            old_cfg = json.load(f)
                            old_users = old_cfg.get('users', {})
                            new_users = config_to_save.get('users', {})
                            if isinstance(old_users, dict):
                                if isinstance(new_users, dict) and new_users:
                                    merged_users = copy.deepcopy(old_users)
                                    merged_users.update(copy.deepcopy(new_users))
                                    config_to_save['users'] = merged_users
                                else:
                                    config_to_save['users'] = copy.deepcopy(old_users)
                    except Exception:
                        pass

                temp_path = self.config_path.with_name(
                    f"{self.config_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                bak_path = Path(str(self.config_path) + '.bak')
                # Bug 9: 替换前备份原文件，replace() 失败时可恢复
                backed_up = False
                if self.config_path.exists():
                    try:
                        import shutil
                        shutil.copy2(self.config_path, bak_path)
                        backed_up = True
                    except Exception:
                        pass
                try:
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        json.dump(config_to_save, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_path, self.config_path)
                    if backed_up:
                        try:
                            bak_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if backed_up and bak_path.exists() and not self.config_path.exists():
                        try:
                            bak_path.replace(self.config_path)
                        except Exception:
                            pass
                    raise

                self._config = copy.deepcopy(config_to_save)
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
