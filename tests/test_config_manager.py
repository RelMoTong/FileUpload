"""
配置管理器测试
测试配置加载、升级、备份功能
"""

import sys
import unittest
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_manager import ConfigManager, DEFAULT_CONFIG_V20


class TestConfigManager(unittest.TestCase):
    """测试配置管理器"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.temp_dir / 'config.json'
    
    def tearDown(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_default_config(self):
        """测试加载默认配置（文件不存在）"""
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        self.assertEqual(config['config_version'], '2.0')
        self.assertEqual(config['protocol_type'], 'smb')
        self.assertIn('ftp_client', config)
        self.assertIn('ftp_server', config)
        
        # 配置文件应该被创建
        self.assertTrue(self.config_path.exists())
    
    def test_upgrade_v19_config(self):
        """测试从 v1.9 升级到 v2.0"""
        # 创建 v1.9 配置
        old_config = {
            "source_folder": "D:/test/source",
            "target_folder": "D:/test/target",
            "backup_folder": "D:/test/backup",
            "upload_interval": 30,
            "enable_deduplication": True,
            "hash_algorithm": "md5"
        }
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(old_config, f)
        
        # 加载并升级
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        # 版本应该是 2.0
        self.assertEqual(config['config_version'], '2.0')
        
        # 旧配置应该被保留
        self.assertEqual(config['source_folder'], 'D:/test/source')
        self.assertEqual(config['target_folder'], 'D:/test/target')
        self.assertEqual(config['enable_deduplication'], True)
        
        # 新字段应该存在
        self.assertIn('protocol_type', config)
        self.assertIn('ftp_client', config)
        self.assertIn('ftp_server', config)
        
        # 备份文件应该被创建
        backup_file = self.temp_dir / 'config_backup_v1.9.json'
        self.assertTrue(backup_file.exists())
    
    def test_merge_new_fields(self):
        """测试合并新字段"""
        # 创建不完整的 v2.0 配置
        partial_config = {
            "config_version": "2.0",
            "protocol_type": "ftp",
            "source_folder": "D:/test"
        }
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(partial_config, f)
        
        # 加载
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        # 缺失的字段应该被补全
        self.assertIn('ftp_client', config)
        self.assertIn('multi_source_paths', config)
        self.assertEqual(config['protocol_type'], 'ftp')  # 保留用户设置
    
    def test_get_protocol_config(self):
        """测试获取协议配置"""
        manager = ConfigManager(self.config_path)
        manager.load()
        
        # 测试 SMB 配置
        manager.set('protocol_type', 'smb')
        manager.set('target_folder', 'D:/test/target')
        smb_config = manager.get_protocol_config()
        self.assertEqual(smb_config['target_folder'], 'D:/test/target')
        
        # 测试 FTP 配置
        manager.set('protocol_type', 'ftp')
        manager.config['ftp_client']['host'] = 'ftp.example.com'
        ftp_config = manager.get_protocol_config()
        self.assertEqual(ftp_config['host'], 'ftp.example.com')
        self.assertEqual(ftp_config['protocol_type'], 'ftp')
    
    def test_save_config(self):
        """测试保存配置"""
        manager = ConfigManager(self.config_path)
        manager.load()
        
        manager.set('protocol_type', 'ftps')
        manager.set('source_folder', 'D:/new/source')
        manager.save()
        
        # 重新加载验证
        manager2 = ConfigManager(self.config_path)
        config2 = manager2.load()
        
        self.assertEqual(config2['protocol_type'], 'ftps')
        self.assertEqual(config2['source_folder'], 'D:/new/source')


if __name__ == '__main__':
    print("运行配置管理器测试...\n")
    unittest.main(verbosity=2)
