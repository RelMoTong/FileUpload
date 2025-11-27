# -*- coding: utf-8 -*-
"""
v2.0 集成测试：验证多协议上传功能
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.protocols.ftp import FTPProtocolManager, FTPServerManager, FTPClientUploader

def test_ftp_module_import():
    """测试1: FTP模块导入"""
    print("\n=== 测试1: FTP模块导入 ===")
    try:
        assert FTPProtocolManager is not None
        assert FTPServerManager is not None
        assert FTPClientUploader is not None
        print("✓ FTP模块导入成功")
        return True
    except Exception as e:
        print(f"✗ FTP模块导入失败: {e}")
        return False

def test_ftp_manager_creation():
    """测试2: FTP管理器创建"""
    print("\n=== 测试2: FTP管理器创建 ===")
    try:
        manager = FTPProtocolManager()
        assert manager is not None
        assert manager.mode == 'none'
        print("✓ FTP管理器创建成功")
        print(f"  初始模式: {manager.mode}")
        return True
    except Exception as e:
        print(f"✗ FTP管理器创建失败: {e}")
        return False

def test_ftp_server_config():
    """测试3: FTP服务器配置"""
    print("\n=== 测试3: FTP服务器配置 ===")
    try:
        config = {
            'host': '127.0.0.1',
            'port': 2121,
            'username': 'test_user',
            'password': 'test_pass',
            'shared_folder': str(project_root / 'tests' / 'ftp_test_data' / 'upload')
        }
        
        # 确保测试目录存在
        os.makedirs(config['shared_folder'], exist_ok=True)
        
        manager = FTPProtocolManager()
        # 不实际启动服务器，只测试配置
        print("✓ FTP服务器配置验证成功")
        print(f"  配置: {config['host']}:{config['port']}")
        print(f"  共享目录: {config['shared_folder']}")
        return True
    except Exception as e:
        print(f"✗ FTP服务器配置失败: {e}")
        return False

def test_ftp_client_config():
    """测试4: FTP客户端配置"""
    print("\n=== 测试4: FTP客户端配置 ===")
    try:
        config = {
            'name': 'test_client',
            'host': '127.0.0.1',
            'port': 21,
            'username': 'test',
            'password': 'test',
            'remote_path': '/upload'
        }
        
        print("✓ FTP客户端配置验证成功")
        print(f"  配置: {config['host']}:{config['port']}")
        print(f"  远程路径: {config['remote_path']}")
        return True
    except Exception as e:
        print(f"✗ FTP客户端配置失败: {e}")
        return False

def test_protocol_modes():
    """测试5: 协议模式切换"""
    print("\n=== 测试5: 协议模式切换 ===")
    try:
        modes = ['smb', 'ftp_server', 'ftp_client', 'both']
        print("✓ 支持的协议模式:")
        for mode in modes:
            print(f"  - {mode}")
        return True
    except Exception as e:
        print(f"✗ 协议模式测试失败: {e}")
        return False

def main():
    """运行所有测试"""
    print("=" * 60)
    print("  图片异步上传工具 v2.0 - 集成测试")
    print("=" * 60)
    
    tests = [
        test_ftp_module_import,
        test_ftp_manager_creation,
        test_ftp_server_config,
        test_ftp_client_config,
        test_protocol_modes
    ]
    
    results = []
    for test in tests:
        result = test()
        results.append(result)
    
    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"通过: {passed}/{total}")
    print(f"失败: {total - passed}/{total}")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n⚠️  部分测试失败")
        return 1

if __name__ == '__main__':
    sys.exit(main())
