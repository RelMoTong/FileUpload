#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
图片上传软件测试程序
测试主要功能是否正常工作
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
import unittest

# 将main.py的目录添加到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class TestImageUploader(unittest.TestCase):
    """图片上传软件测试类"""
    
    def setUp(self):
        """设置测试环境"""
        self.test_dir = Path(tempfile.mkdtemp())
        self.source_dir = self.test_dir / "source"
        self.target_dir = self.test_dir / "target"
        self.backup_dir = self.test_dir / "backup"
        
        # 创建测试目录
        self.source_dir.mkdir()
        self.target_dir.mkdir()
        self.backup_dir.mkdir()
        
        # 创建测试图片文件
        self.test_image = self.source_dir / "test_image.jpg"
        with open(self.test_image, 'wb') as f:
            f.write(b'test image data' * 1000)  # 创建一个小的测试文件
            
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
        
    def test_folder_creation(self):
        """测试文件夹创建功能"""
        self.assertTrue(self.source_dir.exists())
        self.assertTrue(self.target_dir.exists())
        self.assertTrue(self.backup_dir.exists())
        
    def test_image_file_detection(self):
        """测试图片文件检测功能"""
        # 导入主程序类
        try:
            from main import ImageUploaderApp
            app = ImageUploaderApp()
            app.source_folder.set(str(self.source_dir))
            
            # 获取图片文件
            image_files = app.get_image_files()
            self.assertEqual(len(image_files), 1)
            self.assertTrue(str(self.test_image) in image_files)
            
        except ImportError as e:
            print(f"无法导入主程序: {e}")
            self.skipTest("主程序导入失败")
            
    def test_file_copy_functionality(self):
        """测试文件复制功能"""
        target_file = self.target_dir / "test_image.jpg"
        backup_file = self.backup_dir / "test_image.jpg"
        
        # 模拟文件上传过程
        try:
            # 复制到目标文件夹
            shutil.copy2(self.test_image, target_file)
            self.assertTrue(target_file.exists())
            
            # 移动到备份文件夹
            shutil.move(self.test_image, backup_file)
            self.assertTrue(backup_file.exists())
            self.assertFalse(self.test_image.exists())
            
        except Exception as e:
            self.fail(f"文件操作失败: {e}")
            
    def test_file_integrity_check(self):
        """测试文件完整性检查"""
        target_file = self.target_dir / "test_image.jpg"
        
        try:
            from main import ImageUploaderApp
            app = ImageUploaderApp()
            
            # 复制文件
            shutil.copy2(self.test_image, target_file)
            
            # 检查完整性
            result = app.verify_file_integrity(str(self.test_image), str(target_file))
            self.assertTrue(result)
            
        except ImportError:
            # 手动检查文件大小
            shutil.copy2(self.test_image, target_file)
            original_size = os.path.getsize(self.test_image)
            copied_size = os.path.getsize(target_file)
            self.assertEqual(original_size, copied_size)


def run_basic_tests():
    """运行基本功能测试"""
    print("="*50)
    print("图片异步上传软件 - 功能测试")
    print("="*50)
    
    # 测试Python环境
    print(f"Python版本: {sys.version}")
    print(f"当前工作目录: {os.getcwd()}")
    
    # 测试必需模块
    required_modules = ['tkinter', 'threading', 'shutil', 'logging', 'json']
    print("\n检查必需模块:")
    for module in required_modules:
        try:
            __import__(module)
            print(f"✓ {module}")
        except ImportError:
            print(f"✗ {module} - 缺少此模块")
            
    # 测试文件操作
    print("\n测试基本文件操作:")
    test_dir = Path(tempfile.mkdtemp())
    try:
        # 创建测试文件
        test_file = test_dir / "test.txt"
        test_file.write_text("测试内容")
        print("✓ 文件创建")
        
        # 复制文件
        copy_file = test_dir / "test_copy.txt"
        shutil.copy2(test_file, copy_file)
        print("✓ 文件复制")
        
        # 移动文件
        move_file = test_dir / "test_moved.txt"
        shutil.move(copy_file, move_file)
        print("✓ 文件移动")
        
        print("✓ 所有基本功能测试通过")
        
    except Exception as e:
        print(f"✗ 文件操作测试失败: {e}")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        
    print("\n" + "="*50)

    # 运行单元测试
    print("运行详细单元测试:")
    unittest.main(argv=[''], exit=False, verbosity=2)


if __name__ == "__main__":
    run_basic_tests()