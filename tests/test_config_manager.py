# -*- coding: utf-8 -*-
"""
配置管理器测试
测试配置加载、保存功能

v3.0.1 更新：适配新的模块化架构
"""

import sys
import unittest
import tempfile
import json
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import ConfigManager


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
        
        # 应该返回默认配置
        self.assertEqual(config['source_folder'], '')
        self.assertEqual(config['upload_interval'], 30)
        self.assertTrue(config['enable_backup'])
        self.assertIn('ftp_client', config)
        self.assertIn('ftp_server', config)
    
    def test_load_existing_config(self):
        """测试加载已存在的配置文件"""
        # 创建测试配置
        test_config = {
            "source_folder": "D:/test/source",
            "target_folder": "D:/test/target",
            "upload_interval": 60
        }
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(test_config, f)
        
        # 加载
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        # 用户设置应该被保留
        self.assertEqual(config['source_folder'], 'D:/test/source')
        self.assertEqual(config['target_folder'], 'D:/test/target')
        self.assertEqual(config['upload_interval'], 60)
        
        # 默认字段应该被补全
        self.assertIn('ftp_client', config)
        self.assertIn('ftp_server', config)
    
    def test_save_config(self):
        """测试保存配置"""
        manager = ConfigManager(self.config_path)
        manager.load()
        
        # 修改配置
        config = manager._config.copy()
        config['source_folder'] = 'D:/new/source'
        config['upload_interval'] = 45
        
        # 保存
        result = manager.save(config)
        self.assertTrue(result)
        
        # 重新加载验证
        manager2 = ConfigManager(self.config_path)
        config2 = manager2.load()
        
        self.assertEqual(config2['source_folder'], 'D:/new/source')
        self.assertEqual(config2['upload_interval'], 45)
    
    def test_get_set_methods(self):
        """测试 get/set 方法"""
        manager = ConfigManager(self.config_path)
        manager.load()
        
        # 测试 set
        manager.set('source_folder', 'D:/test')
        
        # 测试 get
        self.assertEqual(manager.get('source_folder'), 'D:/test')
        self.assertIsNone(manager.get('non_existent_key'))
        self.assertEqual(manager.get('non_existent_key', 'default'), 'default')
    
    def test_get_default_config(self):
        """测试获取默认配置"""
        default = ConfigManager.get_default_config()
        
        self.assertIsInstance(default, dict)
        self.assertIn('source_folder', default)
        self.assertIn('ftp_server', default)
        self.assertIn('ftp_client', default)
    
    def test_ftp_server_config(self):
        """测试 FTP 服务器配置"""
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        ftp_server = config['ftp_server']
        self.assertEqual(ftp_server['host'], '0.0.0.0')
        self.assertEqual(ftp_server['port'], 2121)
        self.assertEqual(ftp_server['username'], 'upload_user')
        self.assertTrue(ftp_server['enable_passive'])
    
    def test_ftp_client_config(self):
        """测试 FTP 客户端配置"""
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        ftp_client = config['ftp_client']
        self.assertEqual(ftp_client['port'], 21)
        self.assertEqual(ftp_client['remote_path'], '/upload')
        self.assertEqual(ftp_client['timeout'], 30)
        self.assertTrue(ftp_client['passive_mode'])
    
    def test_config_merge(self):
        """测试配置合并（不完整配置补全）"""
        # 创建不完整的配置
        partial_config = {
            "source_folder": "D:/test",
            "ftp_server": {
                "port": 3000  # 只设置端口
            }
        }
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(partial_config, f)
        
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        # 用户设置应该被保留
        self.assertEqual(config['source_folder'], 'D:/test')
        
        # 默认字段应该被补全
        self.assertIn('target_folder', config)
        self.assertIn('enable_backup', config)


if __name__ == '__main__':
    print("运行配置管理器测试...\n")
    unittest.main(verbosity=2)
