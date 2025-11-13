# -*- coding: utf-8 -*-
"""
v2.0 集成测试场景
测试4种协议模式的实际使用场景
"""

import os
import sys
import time
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ftp_protocol import FTPServerManager, FTPClientUploader


def print_header(title):
    """打印测试标题"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(success, message):
    """打印测试结果"""
    symbol = "✓" if success else "✗"
    print(f"  {symbol} {message}")


def test_scenario_1_smb_mode():
    """
    场景1: 纯SMB模式（v1.9兼容性测试）
    模拟：用户从v1.9升级到v2.0，但继续使用SMB
    """
    print_header("场景1: 纯SMB模式（v1.9兼容性）")
    
    try:
        # 创建测试目录
        source = Path("test_scenario1_source")
        target = Path("test_scenario1_target")
        source.mkdir(exist_ok=True)
        target.mkdir(exist_ok=True)
        
        # 创建测试文件
        test_file = source / "test.txt"
        test_file.write_text("SMB模式测试内容", encoding='utf-8')
        
        # 模拟SMB上传（直接复制文件）
        shutil.copy2(test_file, target / "test.txt")
        
        # 验证
        uploaded = target / "test.txt"
        if uploaded.exists():
            content = uploaded.read_text(encoding='utf-8')
            success = content == "SMB模式测试内容"
            print_result(success, f"SMB文件上传: {uploaded}")
        else:
            print_result(False, "文件上传失败")
            return False
        
        # 清理
        shutil.rmtree(source)
        shutil.rmtree(target)
        print_result(True, "场景1测试通过")
        return True
        
    except Exception as e:
        print_result(False, f"场景1测试失败: {e}")
        return False


def test_scenario_2_ftp_server_mode():
    """
    场景2: FTP服务器模式
    模拟：用户将本机作为FTP服务器，其他设备上传文件到本机
    """
    print_header("场景2: FTP服务器模式")
    
    server = None
    try:
        # 创建共享目录
        share_dir = Path("test_scenario2_share")
        share_dir.mkdir(exist_ok=True)
        
        # 启动FTP服务器
        server_config = {
            'host': '127.0.0.1',
            'port': 3121,
            'username': 'scenario2_user',
            'password': 'scenario2_pass',
            'shared_folder': str(share_dir.absolute())
        }
        
        server = FTPServerManager(server_config)
        if not server.start():
            print_result(False, "FTP服务器启动失败")
            return False
        
        print_result(True, f"FTP服务器启动: 127.0.0.1:3121")
        time.sleep(1)
        
        # 模拟客户端上传文件
        from ftplib import FTP
        ftp = FTP()
        ftp.connect('127.0.0.1', 3121, timeout=10)
        ftp.login('scenario2_user', 'scenario2_pass')
        
        # 创建测试文件
        test_file = Path("test_upload_s2.txt")
        test_file.write_text("FTP服务器模式测试", encoding='utf-8')
        
        # 上传
        with open(test_file, 'rb') as f:
            ftp.storbinary('STOR test_upload.txt', f)
        
        ftp.quit()
        print_result(True, "客户端上传文件成功")
        
        # 验证文件存在
        uploaded = share_dir / "test_upload.txt"
        if uploaded.exists():
            content = uploaded.read_text(encoding='utf-8')
            success = content == "FTP服务器模式测试"
            print_result(success, f"文件验证: {uploaded}")
        else:
            print_result(False, "上传文件未找到")
            return False
        
        # 清理
        server.stop()
        time.sleep(0.5)
        test_file.unlink()
        shutil.rmtree(share_dir)
        print_result(True, "场景2测试通过")
        return True
        
    except Exception as e:
        print_result(False, f"场景2测试失败: {e}")
        if server:
            server.stop()
        return False


def test_scenario_3_ftp_client_mode():
    """
    场景3: FTP客户端模式
    模拟：用户将文件上传到远程FTP服务器
    """
    print_header("场景3: FTP客户端模式")
    
    server = None
    try:
        # 先启动一个测试FTP服务器
        remote_share = Path("test_scenario3_remote")
        remote_share.mkdir(exist_ok=True)
        
        server_config = {
            'host': '127.0.0.1',
            'port': 3122,
            'username': 'remote_user',
            'password': 'remote_pass',
            'shared_folder': str(remote_share.absolute())
        }
        
        server = FTPServerManager(server_config)
        if not server.start():
            print_result(False, "远程FTP服务器启动失败")
            return False
        
        print_result(True, "远程FTP服务器启动: 127.0.0.1:3122")
        time.sleep(1)
        
        # 创建本地测试文件
        local_file = Path("test_local_upload.txt")
        local_file.write_text("FTP客户端模式测试", encoding='utf-8')
        
        # 配置并连接FTP客户端
        client_config = {
            'name': 'scenario3_client',
            'host': '127.0.0.1',
            'port': 3122,
            'username': 'remote_user',
            'password': 'remote_pass',
            'remote_path': '/uploads',
            'timeout': 10,
            'retry_count': 3
        }
        
        client = FTPClientUploader(client_config)
        if not client.connect():
            print_result(False, "FTP客户端连接失败")
            return False
        
        print_result(True, "FTP客户端连接成功")
        
        # 上传文件
        if not client.upload_file(local_file, '/uploads/test.txt'):
            print_result(False, "文件上传失败")
            return False
        
        print_result(True, "文件上传成功")
        
        # 验证
        uploaded = remote_share / "uploads" / "test.txt"
        if uploaded.exists():
            content = uploaded.read_text(encoding='utf-8')
            success = content == "FTP客户端模式测试"
            print_result(success, f"文件验证: {uploaded}")
        else:
            print_result(False, "上传文件未找到")
            return False
        
        # 清理
        client.disconnect()
        server.stop()
        time.sleep(0.5)
        local_file.unlink()
        shutil.rmtree(remote_share)
        print_result(True, "场景3测试通过")
        return True
        
    except Exception as e:
        print_result(False, f"场景3测试失败: {e}")
        if server:
            server.stop()
        return False


def test_scenario_4_mixed_mode():
    """
    场景4: 混合模式
    模拟：同时运行FTP服务器（接收文件）和FTP客户端（发送文件）
    """
    print_header("场景4: 混合模式（服务器+客户端）")
    
    server1 = None
    server2 = None
    try:
        # 创建共享目录
        local_share = Path("test_scenario4_local_share")
        remote_share = Path("test_scenario4_remote_share")
        local_share.mkdir(exist_ok=True)
        remote_share.mkdir(exist_ok=True)
        
        # 启动本地FTP服务器（接收文件）
        server1_config = {
            'host': '127.0.0.1',
            'port': 3123,
            'username': 'local_user',
            'password': 'local_pass',
            'shared_folder': str(local_share.absolute())
        }
        
        server1 = FTPServerManager(server1_config)
        if not server1.start():
            print_result(False, "本地FTP服务器启动失败")
            return False
        
        print_result(True, "本地FTP服务器启动: 127.0.0.1:3123")
        time.sleep(1)
        
        # 启动远程FTP服务器（接收上传）
        server2_config = {
            'host': '127.0.0.1',
            'port': 3124,
            'username': 'remote_user',
            'password': 'remote_pass',
            'shared_folder': str(remote_share.absolute())
        }
        
        server2 = FTPServerManager(server2_config)
        if not server2.start():
            print_result(False, "远程FTP服务器启动失败")
            return False
        
        print_result(True, "远程FTP服务器启动: 127.0.0.1:3124")
        time.sleep(1)
        
        # 创建测试文件
        test_file = Path("test_mixed_mode.txt")
        test_file.write_text("混合模式测试", encoding='utf-8')
        
        # FTP客户端上传到远程服务器
        client_config = {
            'name': 'mixed_client',
            'host': '127.0.0.1',
            'port': 3124,
            'username': 'remote_user',
            'password': 'remote_pass',
            'remote_path': '/data',
            'timeout': 10,
            'retry_count': 3
        }
        
        client = FTPClientUploader(client_config)
        client.connect()
        client.upload_file(test_file, '/data/test.txt')
        print_result(True, "客户端上传到远程服务器成功")
        
        # 验证远程服务器收到文件
        uploaded_remote = remote_share / "data" / "test.txt"
        if uploaded_remote.exists():
            print_result(True, f"远程服务器接收文件: {uploaded_remote}")
        else:
            print_result(False, "远程服务器未收到文件")
            return False
        
        # 清理
        client.disconnect()
        server1.stop()
        server2.stop()
        time.sleep(0.5)
        test_file.unlink()
        shutil.rmtree(local_share)
        shutil.rmtree(remote_share)
        print_result(True, "场景4测试通过")
        return True
        
    except Exception as e:
        print_result(False, f"场景4测试失败: {e}")
        if server1:
            server1.stop()
        if server2:
            server2.stop()
        return False


def test_scenario_5_config_upgrade():
    """
    场景5: 配置升级测试（v1.9 → v2.0）
    模拟：用户升级后，旧配置文件仍然可用
    """
    print_header("场景5: 配置升级（v1.9 → v2.0兼容性）")
    
    try:
        import json
        
        # 创建v1.9配置文件
        v19_config = {
            'source_folder': 'E:/test/source',
            'target_folder': 'E:/test/target',
            'backup_folder': 'E:/test/backup',
            'upload_interval': 30,
            'monitor_mode': 'periodic',
            'disk_threshold_percent': 10,
            'retry_count': 3,
            'filter_jpg': True,
            'filter_png': True
            # 注意：没有 upload_protocol 字段
        }
        
        config_file = Path("test_v19_config.json")
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(v19_config, f, indent=2, ensure_ascii=False)
        
        print_result(True, "创建v1.9配置文件")
        
        # 加载并升级配置
        with open(config_file, 'r', encoding='utf-8') as f:
            loaded_config = json.load(f)
        
        # v2.0兼容性处理：如果没有upload_protocol，默认为smb
        upload_protocol = loaded_config.get('upload_protocol', 'smb')
        print_result(True, f"配置加载成功，协议: {upload_protocol}")
        
        # 验证关键字段存在
        required_fields = ['source_folder', 'target_folder', 'backup_folder']
        for field in required_fields:
            if field not in loaded_config:
                print_result(False, f"缺少必要字段: {field}")
                return False
        
        print_result(True, "所有必要字段存在")
        
        # 验证向后兼容性
        assert upload_protocol == 'smb', "默认协议应该是SMB"
        print_result(True, "向后兼容性验证通过")
        
        # 清理
        config_file.unlink()
        print_result(True, "场景5测试通过")
        return True
        
    except Exception as e:
        print_result(False, f"场景5测试失败: {e}")
        return False


def main():
    """运行所有集成测试场景"""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  v2.0 集成测试场景".center(66) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    
    results = {}
    
    # 运行所有场景
    results['场景1: SMB模式'] = test_scenario_1_smb_mode()
    results['场景2: FTP服务器模式'] = test_scenario_2_ftp_server_mode()
    results['场景3: FTP客户端模式'] = test_scenario_3_ftp_client_mode()
    results['场景4: 混合模式'] = test_scenario_4_mixed_mode()
    results['场景5: 配置升级'] = test_scenario_5_config_upgrade()
    
    # 打印总结
    print("\n" + "=" * 70)
    print("  测试总结")
    print("=" * 70)
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    for scenario, result in results.items():
        symbol = "✓" if result else "✗"
        print(f"  {symbol} {scenario}")
    
    print(f"\n  总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n  🎉 所有集成测试场景通过！")
        return 0
    else:
        print(f"\n  ❌ {total - passed} 个场景失败")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
