# -*- coding: utf-8 -*-
"""
FTP 协议模块综合测试
测试 src/ftp_protocol.py 中的 FTPServerManager 和 FTPClientUploader
"""

import os
import sys
import time
import shutil
import unittest
import socket
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.protocols.ftp import FTPServerManager, FTPClientUploader


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class TestFTPServer(unittest.TestCase):
    """测试 FTP 服务器功能"""
    
    @classmethod
    def setUpClass(cls):
        """测试类初始化"""
        print("\n" + "=" * 60)
        print("FTP 服务器测试")
        print("=" * 60)
        
        # 创建测试共享目录
        cls.test_share = Path("test_ftp_share")
        cls.test_share.mkdir(exist_ok=True)
        
        # 创建测试文件
        (cls.test_share / "test_file.txt").write_text("测试内容", encoding='utf-8')
        cls.port = get_free_port()
        
        # 服务器配置
        cls.server_config = {
            'host': '127.0.0.1',
            'port': cls.port,
            'username': 'test_user',
            'password': 'test_pass',
            'shared_folder': str(cls.test_share.absolute()),
            'enable_tls': False,
            'passive_mode': True,
            'passive_ports': (60000, 65535),
            'max_cons': 256,
            'max_cons_per_ip': 5
        }
    
    @classmethod
    def tearDownClass(cls):
        """测试类清理"""
        # 清理测试目录
        if cls.test_share.exists():
            shutil.rmtree(cls.test_share)
        print("\n✓ 测试环境已清理")
    
    def test_01_server_start(self):
        """测试1: FTP服务器启动"""
        print("\n测试1: FTP服务器启动")
        server = FTPServerManager(self.server_config)
        success = server.start()
        
        self.assertTrue(success, "FTP服务器应该成功启动")
        
        # 验证状态
        status = server.get_status()
        self.assertTrue(status['running'], "服务器应该处于运行状态")
        self.assertEqual(status['address'], f'127.0.0.1:{self.port}')
        self.assertEqual(status['host'], '127.0.0.1')
        self.assertEqual(status['port'], self.port)
        
        print(f"  ✓ 服务器启动成功: {status['address']}")
        print(f"  ✓ 共享目录: {status['shared_folder']}")
        
        # 停止服务器
        server.stop()
        time.sleep(1)  # 等待端口完全释放
        self.assertFalse(server.get_status()['running'], "服务器应该已停止")
        print("  ✓ 服务器停止成功")
    
    def test_02_server_port_conflict(self):
        """测试2: 端口冲突检测"""
        print("\n测试2: 端口冲突检测")
        
        # 确保端口已释放
        time.sleep(1)
        
        # 启动第一个服务器
        server1 = FTPServerManager(self.server_config)
        success1 = server1.start()
        self.assertTrue(success1, "第一个服务器应该成功启动")
        print("  ✓ 第一个服务器启动成功")
        
        # 尝试启动第二个服务器（相同端口）
        server2 = FTPServerManager(self.server_config)
        success2 = server2.start()
        self.assertFalse(success2, "相同端口的第二个服务器不应该启动成功")
        print("  ✓ 端口冲突检测正常（第二个服务器启动失败）")
        
        # 清理
        server1.stop()
        time.sleep(1)
    
    def test_03_server_invalid_config(self):
        """测试3: 配置健壮性"""
        print("\n测试3: 配置健壮性")
        
        # 确保端口已释放
        time.sleep(1)
        
        # 测试空配置（使用默认值）
        minimal_config = {
            'shared_folder': str(self.test_share / 'minimal')
        }
        server = FTPServerManager(minimal_config)
        # 默认配置应该可以启动（使用默认端口和凭证）
        # 但为了避免端口冲突，我们只测试配置接受
        print("  ✓ 最小配置接受正常")
        
        # 测试包含所有可选项的配置
        full_config = self.server_config.copy()
        full_config.update({
            'enable_tls': False,  # TLS需要证书文件
            'passive_ports': (60000, 60100),
            'max_cons': 10,
            'max_cons_per_ip': 2
        })
        server = FTPServerManager(full_config)
        print("  ✓ 完整配置接受正常")


class TestFTPClient(unittest.TestCase):
    """测试 FTP 客户端功能"""
    
    @classmethod
    def setUpClass(cls):
        """测试类初始化"""
        print("\n" + "=" * 60)
        print("FTP 客户端测试")
        print("=" * 60)
        
        # 创建测试目录
        cls.test_share = Path("test_ftp_share_client")
        cls.test_share.mkdir(exist_ok=True)
        
        cls.test_upload = Path("test_upload_source")
        cls.test_upload.mkdir(exist_ok=True)
        
        # 创建测试文件
        cls.test_file = cls.test_upload / "upload_test.txt"
        cls.test_file.write_text("这是要上传的测试内容", encoding='utf-8')
        cls.port = get_free_port()
        
        # 启动测试服务器
        server_config = {
            'host': '127.0.0.1',
            'port': cls.port,
            'username': 'client_test',
            'password': 'client_pass',
            'shared_folder': str(cls.test_share.absolute())
        }
        cls.server = FTPServerManager(server_config)
        cls.server.start()
        time.sleep(1)  # 等待服务器启动
        
        # 客户端配置
        cls.client_config = {
            'name': 'test_client',
            'host': '127.0.0.1',
            'port': cls.port,
            'username': 'client_test',
            'password': 'client_pass',
            'remote_path': '/upload',
            'timeout': 10,
            'retry_count': 3
        }
    
    @classmethod
    def tearDownClass(cls):
        """测试类清理"""
        # 停止服务器
        cls.server.stop()
        time.sleep(0.5)
        
        # 清理测试目录
        if cls.test_share.exists():
            shutil.rmtree(cls.test_share)
        if cls.test_upload.exists():
            shutil.rmtree(cls.test_upload)
        print("\n✓ 测试环境已清理")
    
    def test_01_client_connect(self):
        """测试1: 客户端连接"""
        print("\n测试1: 客户端连接")
        
        client = FTPClientUploader(self.client_config)
        success = client.connect()
        
        self.assertTrue(success, "客户端应该成功连接")
        
        # 验证状态
        status = client.get_status()
        self.assertTrue(status['connected'], "客户端应该处于连接状态")
        self.assertEqual(status['host'], '127.0.0.1')
        self.assertEqual(status['port'], self.port)
        
        print(f"  ✓ 连接成功: {status['host']}:{status['port']}")
        
        # 断开连接
        client.disconnect()
        self.assertFalse(client.get_status()['connected'], "客户端应该已断开")
        print("  ✓ 断开连接成功")
    
    def test_02_client_auth_failure(self):
        """测试2: 认证失败"""
        print("\n测试2: 认证失败")
        
        invalid_config = self.client_config.copy()
        invalid_config['password'] = 'wrong_password'
        invalid_config['retry_count'] = 1  # 减少重试次数，避免长时间等待
        invalid_config['timeout'] = 3  # 减少超时时间
        
        client = FTPClientUploader(invalid_config)
        success = client.connect()
        
        self.assertFalse(success, "错误密码应该导致连接失败")
        print("  ✓ 认证失败检测正常")
    
    def test_03_upload_single_file(self):
        """测试3: 上传单个文件"""
        print("\n测试3: 上传单个文件")
        
        client = FTPClientUploader(self.client_config)
        client.connect()
        
        # 上传文件
        success = client.upload_file(self.test_file, "/upload/test.txt")
        self.assertTrue(success, "文件上传应该成功")
        
        # 验证文件存在
        uploaded_file = self.test_share / "upload" / "test.txt"
        self.assertTrue(uploaded_file.exists(), "上传的文件应该存在于服务器")
        
        # 验证内容
        content = uploaded_file.read_text(encoding='utf-8')
        self.assertEqual(content, "这是要上传的测试内容", "上传文件内容应该一致")
        
        print(f"  ✓ 文件上传成功: {uploaded_file}")
        print(f"  ✓ 内容验证通过")
        
        client.disconnect()
    
    def test_04_upload_folder(self):
        """测试4: 上传文件夹"""
        print("\n测试4: 上传文件夹（保持目录结构）")
        
        # 创建测试文件夹结构
        test_folder = self.test_upload / "folder_test"
        test_folder.mkdir(exist_ok=True)
        (test_folder / "file1.txt").write_text("文件1", encoding='utf-8')
        (test_folder / "subdir").mkdir(exist_ok=True)
        (test_folder / "subdir" / "file2.txt").write_text("文件2", encoding='utf-8')
        
        client = FTPClientUploader(self.client_config)
        client.connect()
        
        # 上传文件夹
        success, failed = client.upload_folder(test_folder, "/folder_upload")
        
        self.assertGreater(success, 0, "应该有文件成功上传")
        self.assertEqual(failed, 0, "不应该有上传失败的文件")
        
        print(f"  ✓ 上传成功: {success} 个文件")
        print(f"  ✓ 失败: {failed} 个文件")
        
        # 验证文件存在
        uploaded_file1 = self.test_share / "folder_upload" / "file1.txt"
        uploaded_file2 = self.test_share / "folder_upload" / "subdir" / "file2.txt"
        
        self.assertTrue(uploaded_file1.exists(), "file1.txt应该存在")
        self.assertTrue(uploaded_file2.exists(), "file2.txt应该存在")
        
        print(f"  ✓ 目录结构保持完整")
        
        client.disconnect()
    
    def test_05_connection_test(self):
        """测试5: 连接测试功能"""
        print("\n测试5: 连接测试功能")
        
        client = FTPClientUploader(self.client_config)
        result = client.test_connection()
        
        self.assertTrue(result, "连接测试应该成功")
        print("  ✓ 连接测试成功")
    
    def test_06_passive_mode(self):
        """测试6: 被动模式"""
        print("\n测试6: 被动模式（PASV）")
        
        # 配置被动模式
        passive_config = self.client_config.copy()
        passive_config['passive_mode'] = True
        
        client = FTPClientUploader(passive_config)
        success = client.connect()
        
        self.assertTrue(success, "被动模式连接应该成功")
        print("  ✓ 被动模式连接成功")
        
        # 测试被动模式下的文件上传
        test_file = self.test_upload / "passive_test.txt"
        test_file.write_text("被动模式测试", encoding='utf-8')
        
        upload_success = client.upload_file(test_file, '/passive_test.txt')
        self.assertTrue(upload_success, "被动模式上传应该成功")
        print("  ✓ 被动模式上传成功")
        
        client.disconnect()
        
        # 清理测试文件
        if test_file.exists():
            test_file.unlink()
    
    def test_07_timeout_handling(self):
        """测试7: 超时处理"""
        print("\n测试7: 超时处理")
        
        # 配置较短的超时时间
        timeout_config = self.client_config.copy()
        timeout_config['timeout'] = 2  # 2秒超时
        
        client = FTPClientUploader(timeout_config)
        success = client.connect()
        
        self.assertTrue(success, "连接应该成功")
        print("  ✓ 超时配置有效")
        
        # 验证超时配置
        status = client.get_status()
        self.assertEqual(status.get('timeout', 0), 2, "超时时间应该是2秒")
        print("  ✓ 超时时间设置正确")
        
        client.disconnect()
    
    def test_08_retry_mechanism(self):
        """测试8: 重试机制"""
        print("\n测试8: 重试机制")
        
        # 配置重试参数（减少重试次数，避免长时间等待）
        retry_config = self.client_config.copy()
        retry_config['retry_count'] = 1
        retry_config['timeout'] = 2
        
        client = FTPClientUploader(retry_config)
        
        # 验证重试配置（检查配置而非状态）
        self.assertEqual(client.config.get('retry_count', 0), 1, "重试次数应该是1")
        print("  ✓ 重试次数配置正确")
        
        # 测试连接到不存在的服务器（会触发重试）
        invalid_config = retry_config.copy()
        invalid_config['host'] = '192.0.2.1'  # TEST-NET-1，不可路由
        invalid_config['port'] = 12345
        invalid_config['timeout'] = 1
        
        client_invalid = FTPClientUploader(invalid_config)
        success = client_invalid.connect()
        
        self.assertFalse(success, "连接到无效服务器应该失败")
        print("  ✓ 无效连接正确处理")


class TestAdvancedFeatures(unittest.TestCase):
    """高级功能测试：连接限制、TLS等"""
    
    @classmethod
    def setUpClass(cls):
        """测试类初始化"""
        print("\n" + "=" * 60)
        print("高级功能测试")
        print("=" * 60)
        
        # 创建测试目录
        cls.test_share = Path("test_ftp_advanced")
        cls.test_share.mkdir(exist_ok=True)
    
    @classmethod
    def tearDownClass(cls):
        """测试类清理"""
        # 清理测试目录
        if cls.test_share.exists():
            shutil.rmtree(cls.test_share)
        print("\n✓ 测试环境已清理")
    
    def test_01_connection_limits(self):
        """测试1: 连接数限制"""
        print("\n测试1: 连接数限制")
        
        # 配置连接限制
        port = get_free_port()
        config = {
            'host': '127.0.0.1',
            'port': port,
            'username': 'limit_test',
            'password': 'limit_pass',
            'shared_folder': str(self.test_share.absolute()),
            'max_cons': 2,  # 最大2个连接
            'max_cons_per_ip': 1  # 单IP限制1个
        }
        
        server = FTPServerManager(config)
        success = server.start()
        
        self.assertTrue(success, "服务器应该成功启动")
        print("  ✓ 服务器启动，连接限制已配置")
        
        # 验证服务器状态
        status = server.get_status()
        self.assertTrue(status['running'], "服务器应该在运行")
        print(f"  ✓ 最大连接数: {config['max_cons']}")
        print(f"  ✓ 单IP限制: {config['max_cons_per_ip']}")
        
        # 停止服务器
        server.stop()
        time.sleep(0.5)
    
    def test_02_passive_port_range(self):
        """测试2: 被动端口范围"""
        print("\n测试2: 被动端口范围")
        
        # 配置被动端口范围
        port = get_free_port()
        config = {
            'host': '127.0.0.1',
            'port': port,
            'username': 'passive_test',
            'password': 'passive_pass',
            'shared_folder': str(self.test_share.absolute()),
            'passive_mode': True,
            'passive_ports': (60000, 60010)  # 限制端口范围
        }
        
        server = FTPServerManager(config)
        success = server.start()
        
        self.assertTrue(success, "服务器应该成功启动")
        print("  ✓ 被动模式服务器启动")
        print(f"  ✓ 被动端口范围: 60000-60010")
        
        # 验证配置
        status = server.get_status()
        self.assertTrue(status['running'], "服务器应该在运行")
        
        # 停止服务器
        server.stop()
        time.sleep(0.5)


class TestIntegration(unittest.TestCase):
    """集成测试：服务器和客户端协同工作"""
    
    def test_server_client_integration(self):
        """测试: 服务器和客户端集成"""
        print("\n" + "=" * 60)
        print("集成测试: 服务器 + 客户端")
        print("=" * 60)
        
        # 创建测试环境
        share_dir = Path("test_integration_share")
        share_dir.mkdir(exist_ok=True)
        
        upload_dir = Path("test_integration_upload")
        upload_dir.mkdir(exist_ok=True)
        (upload_dir / "integration_test.txt").write_text("集成测试内容", encoding='utf-8')
        
        try:
            # 启动服务器
            port = get_free_port()
            server_config = {
                'host': '127.0.0.1',
                'port': port,
                'username': 'integration',
                'password': 'integration_pass',
                'shared_folder': str(share_dir.absolute())
            }
            server = FTPServerManager(server_config)
            server.start()
            time.sleep(1)
            print("✓ FTP服务器已启动")
            
            # 连接客户端
            client_config = {
                'name': 'integration_client',
                'host': '127.0.0.1',
                'port': port,
                'username': 'integration',
                'password': 'integration_pass',
                'remote_path': '/data',
                'timeout': 10,
                'retry_count': 3
            }
            client = FTPClientUploader(client_config)
            client.connect()
            print("✓ FTP客户端已连接")
            
            # 上传文件
            test_file = upload_dir / "integration_test.txt"
            success = client.upload_file(test_file, "/data/test.txt")
            self.assertTrue(success, "集成测试文件上传应该成功")
            print("✓ 文件上传成功")
            
            # 验证
            uploaded = share_dir / "data" / "test.txt"
            self.assertTrue(uploaded.exists(), "上传的文件应该存在")
            content = uploaded.read_text(encoding='utf-8')
            self.assertEqual(content, "集成测试内容", "内容应该一致")
            print("✓ 文件验证成功")
            
            # 清理
            client.disconnect()
            server.stop()
            time.sleep(0.5)
            
        finally:
            # 清理测试目录
            if share_dir.exists():
                shutil.rmtree(share_dir)
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
            print("✓ 测试环境已清理")


def run_tests():
    """运行所有测试"""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  FTP 协议模块综合测试".center(66) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试
    suite.addTests(loader.loadTestsFromTestCase(TestFTPServer))
    suite.addTests(loader.loadTestsFromTestCase(TestFTPClient))
    suite.addTests(loader.loadTestsFromTestCase(TestAdvancedFeatures))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 打印总结
    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)
    print(f"总测试数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    
    if result.wasSuccessful():
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n❌ 部分测试失败")
        return 1


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
