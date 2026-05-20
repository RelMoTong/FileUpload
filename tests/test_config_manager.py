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
from src.core.utils import migrate_config_from_previous_version


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

    def test_save_preserves_existing_users_when_config_has_no_user_changes(self):
        """普通配置保存不应清空已有用户密码。"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({
                'source_folder': 'D:/old',
                'users': {'user': 'old-user-hash'},
            }, f)

        manager = ConfigManager(self.config_path)
        config = manager.load()
        config['source_folder'] = 'D:/new'
        config['users'] = {}

        self.assertTrue(manager.save(config))

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        self.assertEqual(saved['source_folder'], 'D:/new')
        self.assertEqual(saved['users'], {'user': 'old-user-hash'})

    def test_save_merges_user_updates_with_existing_users(self):
        """保存用户变更时，新用户哈希覆盖同名旧值且保留其他用户。"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump({
                'users': {'user': 'old-user-hash', 'admin': 'old-admin-hash'},
            }, f)

        manager = ConfigManager(self.config_path)
        config = manager.load()
        config['users'] = {'user': 'new-user-hash'}

        self.assertTrue(manager.save(config))

        with open(self.config_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        self.assertEqual(
            saved['users'],
            {'user': 'new-user-hash', 'admin': 'old-admin-hash'},
        )
    
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
        self.assertEqual(ftp_server['password'], '')
        self.assertEqual(ftp_server['password_encrypted'], '')
        self.assertTrue(ftp_server['enable_passive'])
    
    def test_ftp_client_config(self):
        """测试 FTP 客户端配置"""
        manager = ConfigManager(self.config_path)
        config = manager.load()
        
        ftp_client = config['ftp_client']
        self.assertEqual(ftp_client['port'], 21)
        self.assertEqual(ftp_client['password'], '')
        self.assertEqual(ftp_client['password_encrypted'], '')
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

    def test_config_merge_adds_encrypted_password_fields(self):
        """测试旧版 FTP 配置会补全加密字段。"""
        partial_config = {
            "ftp_server": {
                "host": "127.0.0.1",
                "password": "legacy-password",
            },
            "ftp_client": {
                "password": "legacy-client-password",
            }
        }

        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(partial_config, f)

        manager = ConfigManager(self.config_path)
        config = manager.load()

        self.assertIn('password_encrypted', config['ftp_server'])
        self.assertIn('password_encrypted', config['ftp_client'])
        self.assertEqual(config['ftp_server']['password'], 'legacy-password')
        self.assertEqual(config['ftp_client']['password'], 'legacy-client-password')

    def test_migrate_config_from_highest_previous_version(self):
        """打包版首次启动时迁移最高旧版本配置。"""
        old_330 = self.temp_dir / 'ImageUploadTool_v3.3.0'
        old_331 = self.temp_dir / 'ImageUploadTool_v3.3.1'
        current = self.temp_dir / 'ImageUploadTool_v3.3.2'
        for path in (old_330, old_331, current):
            path.mkdir()

        with open(old_330 / 'config.json', 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/old-330'}, f)
        with open(old_331 / 'config.json', 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/old-331'}, f)

        migrated = migrate_config_from_previous_version(current, frozen=True)

        self.assertEqual(migrated, old_331 / 'config.json')
        with open(current / 'config.json', 'r', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['source_folder'], 'D:/old-331')

    def test_migrate_config_does_not_overwrite_current_config(self):
        """当前目录已有配置时不覆盖。"""
        old_dir = self.temp_dir / 'ImageUploadTool_v3.3.1'
        current = self.temp_dir / 'ImageUploadTool_v3.3.2'
        old_dir.mkdir()
        current.mkdir()

        with open(old_dir / 'config.json', 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/old'}, f)
        with open(current / 'config.json', 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/current'}, f)

        migrated = migrate_config_from_previous_version(current, frozen=True)

        self.assertIsNone(migrated)
        with open(current / 'config.json', 'r', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['source_folder'], 'D:/current')

    def test_migrate_config_skips_dirs_without_config(self):
        """同级旧版本没有 config.json 时不迁移。"""
        old_dir = self.temp_dir / 'ImageUploadTool_v3.3.1'
        current = self.temp_dir / 'ImageUploadTool_v3.3.2'
        old_dir.mkdir()
        current.mkdir()

        migrated = migrate_config_from_previous_version(current, frozen=True)

        self.assertIsNone(migrated)
        self.assertFalse((current / 'config.json').exists())

    def test_migrate_config_fallback_to_newest_unparseable_version_dir(self):
        """无法解析版本名时按配置文件修改时间兜底。"""
        older = self.temp_dir / 'ImageUploadTool_v现场A'
        newer = self.temp_dir / 'ImageUploadTool_v现场B'
        current = self.temp_dir / 'ImageUploadTool_v3.3.2'
        for path in (older, newer, current):
            path.mkdir()

        older_cfg = older / 'config.json'
        newer_cfg = newer / 'config.json'
        with open(older_cfg, 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/older'}, f)
        with open(newer_cfg, 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/newer'}, f)
        import os
        os.utime(older_cfg, (1000, 1000))
        os.utime(newer_cfg, (2000, 2000))

        migrated = migrate_config_from_previous_version(current, frozen=True)

        self.assertEqual(migrated, newer_cfg)
        with open(current / 'config.json', 'r', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['source_folder'], 'D:/newer')

    def test_migrate_config_disabled_in_development_mode(self):
        """开发环境不扫描同级旧版本目录。"""
        old_dir = self.temp_dir / 'ImageUploadTool_v3.3.1'
        current = self.temp_dir / 'ImageUploadTool_v3.3.2'
        old_dir.mkdir()
        current.mkdir()
        with open(old_dir / 'config.json', 'w', encoding='utf-8') as f:
            json.dump({'source_folder': 'D:/old'}, f)

        migrated = migrate_config_from_previous_version(current, frozen=False)

        self.assertIsNone(migrated)
        self.assertFalse((current / 'config.json').exists())


if __name__ == '__main__':
    print("运行配置管理器测试...\n")
    unittest.main(verbosity=2)
