# -*- coding: utf-8 -*-
"""
FTP 功能基础测试脚本
测试 pyftpdlib 库是否能正常工作
"""

import os
import threading
import time
from pathlib import Path
from ftplib import FTP
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer


def test_ftp_server():
    """测试 FTP 服务器"""
    print("=" * 60)
    print("测试 1: FTP 服务器启动")
    print("=" * 60)
    
    # 创建测试共享目录
    test_dir = Path("test_ftp_share")
    test_dir.mkdir(exist_ok=True)
    
    # 创建测试文件
    test_file = test_dir / "test.txt"
    test_file.write_text("这是一个测试文件", encoding='utf-8')
    
    print(f"✓ 创建测试目录: {test_dir.absolute()}")
    print(f"✓ 创建测试文件: {test_file.name}")
    
    # 配置 FTP 服务器
    try:
        authorizer = DummyAuthorizer()
        authorizer.add_user(
            username="test_user",
            password="test_pass",
            homedir=str(test_dir.absolute()),
            perm="elradfmwMT"
        )
        
        handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "测试 FTP 服务器"
        
        # 使用非标准端口避免权限问题
        server = FTPServer(("127.0.0.1", 2121), handler)
        
        print("✓ FTP 服务器配置完成")
        print(f"  地址: 127.0.0.1:2121")
        print(f"  用户: test_user")
        print(f"  密码: test_pass")
        print(f"  目录: {test_dir.absolute()}")
        
        # 在后台线程启动服务器
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        
        print("✓ FTP 服务器已启动（后台线程）")
        
        # 等待服务器完全启动
        time.sleep(1)
        
        return server, test_dir
        
    except Exception as e:
        print(f"✗ FTP 服务器启动失败: {e}")
        return None, test_dir


def test_ftp_client(test_dir):
    """测试 FTP 客户端"""
    print("\n" + "=" * 60)
    print("测试 2: FTP 客户端连接和上传")
    print("=" * 60)
    
    try:
        # 连接到 FTP 服务器
        ftp = FTP()
        ftp.connect("127.0.0.1", 2121, timeout=10)
        print("✓ 连接到 FTP 服务器")
        
        # 登录
        ftp.login("test_user", "test_pass")
        print("✓ 登录成功")
        
        # 列出文件
        files = ftp.nlst()
        print(f"✓ 列出文件: {files}")
        
        # 创建测试上传文件
        upload_file = Path("test_upload.txt")
        upload_file.write_text("这是要上传的测试文件", encoding='utf-8')
        
        # 上传文件
        with open(upload_file, 'rb') as f:
            ftp.storbinary(f'STOR {upload_file.name}', f)
        print(f"✓ 上传文件: {upload_file.name}")
        
        # 再次列出文件确认
        files = ftp.nlst()
        print(f"✓ 上传后的文件列表: {files}")
        
        # 检查文件是否真的存在于服务器目录
        uploaded_path = test_dir / upload_file.name
        if uploaded_path.exists():
            print(f"✓ 文件确实存在于服务器目录: {uploaded_path}")
        else:
            print(f"✗ 文件不存在于服务器目录: {uploaded_path}")
        
        # 下载文件测试
        download_file = Path("test_download.txt")
        with open(download_file, 'wb') as f:
            ftp.retrbinary(f'RETR {upload_file.name}', f.write)
        print(f"✓ 下载文件: {download_file.name}")
        
        # 验证下载的内容
        downloaded_content = download_file.read_text(encoding='utf-8')
        print(f"✓ 下载的内容: {downloaded_content}")
        
        # 断开连接
        ftp.quit()
        print("✓ 断开 FTP 连接")
        
        # 清理测试文件
        upload_file.unlink()
        download_file.unlink()
        print("✓ 清理临时文件")
        
        return True
        
    except Exception as e:
        print(f"✗ FTP 客户端测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ftp_folder_upload():
    """测试文件夹上传"""
    print("\n" + "=" * 60)
    print("测试 3: 文件夹上传（保持目录结构）")
    print("=" * 60)
    
    try:
        # 创建测试文件夹结构
        test_source = Path("test_source_folder")
        test_source.mkdir(exist_ok=True)
        
        # 创建子目录和文件
        (test_source / "subdir1").mkdir(exist_ok=True)
        (test_source / "subdir2").mkdir(exist_ok=True)
        (test_source / "file1.txt").write_text("文件1", encoding='utf-8')
        (test_source / "subdir1" / "file2.txt").write_text("文件2", encoding='utf-8')
        (test_source / "subdir2" / "file3.txt").write_text("文件3", encoding='utf-8')
        
        print(f"✓ 创建测试文件夹结构: {test_source.absolute()}")
        print("  目录结构:")
        print("  test_source_folder/")
        print("  ├── file1.txt")
        print("  ├── subdir1/")
        print("  │   └── file2.txt")
        print("  └── subdir2/")
        print("      └── file3.txt")
        
        # 连接 FTP
        ftp = FTP()
        ftp.connect("127.0.0.1", 2121, timeout=10)
        ftp.login("test_user", "test_pass")
        print("✓ 连接到 FTP 服务器")
        
        # 上传整个文件夹
        def upload_folder(ftp, local_folder, remote_base="uploaded_folder"):
            """递归上传文件夹"""
            # 先创建基础目录
            try:
                ftp.mkd(remote_base)
                print(f"  ✓ 创建目录: /{remote_base}")
            except:
                pass  # 目录可能已存在
            
            for item in local_folder.iterdir():
                if item.is_file():
                    # 上传文件
                    remote_path = f"{remote_base}/{item.name}"
                    with open(item, 'rb') as f:
                        ftp.storbinary(f'STOR {remote_path}', f)
                    print(f"  ✓ 上传文件: {remote_path}")
                elif item.is_dir():
                    # 创建目录并递归上传
                    remote_dir = f"{remote_base}/{item.name}"
                    try:
                        ftp.mkd(remote_dir)
                        print(f"  ✓ 创建目录: {remote_dir}")
                    except:
                        pass  # 目录可能已存在
                    upload_folder(ftp, item, remote_dir)
        
        upload_folder(ftp, test_source, "uploaded_folder")
        print("✓ 文件夹上传完成")
        
        # 列出上传后的文件
        print("\n上传后的服务器文件结构:")
        def list_files(ftp, path="/"):
            """递归列出文件"""
            try:
                ftp.cwd(path)
                items = ftp.nlst()
                for item in items:
                    if item in ['.', '..']:
                        continue
                    item_path = f"{path}/{item}".replace("//", "/")
                    try:
                        ftp.cwd(item_path)
                        print(f"  📁 {item_path}")
                        list_files(ftp, item_path)
                        ftp.cwd('..')
                    except:
                        print(f"  📄 {item_path}")
            except Exception as e:
                print(f"  列出文件失败: {e}")
        
        list_files(ftp, "uploaded_folder")
        
        ftp.quit()
        print("\n✓ 文件夹上传测试完成")
        
        # 清理测试文件夹
        import shutil
        shutil.rmtree(test_source)
        print("✓ 清理测试文件夹")
        
        return True
        
    except Exception as e:
        print(f"✗ 文件夹上传测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup(server, test_dir):
    """清理测试环境"""
    print("\n" + "=" * 60)
    print("清理测试环境")
    print("=" * 60)
    
    try:
        if server:
            server.close_all()
            print("✓ 关闭 FTP 服务器")
        
        # 清理测试目录
        import shutil
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"✓ 删除测试目录: {test_dir}")
        
    except Exception as e:
        print(f"✗ 清理失败: {e}")


def main():
    """主测试函数"""
    print("\n")
    print("*" * 60)
    print("*" + " " * 58 + "*")
    print("*" + "  FTP 功能基础测试".center(56) + "*")
    print("*" + " " * 58 + "*")
    print("*" * 60)
    print("\n")
    
    # 测试 1: FTP 服务器
    server, test_dir = test_ftp_server()
    
    if server is None:
        print("\n✗ 测试失败：无法启动 FTP 服务器")
        return
    
    # 测试 2: FTP 客户端
    client_success = test_ftp_client(test_dir)
    
    # 测试 3: 文件夹上传
    folder_success = test_ftp_folder_upload()
    
    # 清理
    cleanup(server, test_dir)
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"FTP 服务器启动: ✓")
    print(f"FTP 客户端连接: {'✓' if client_success else '✗'}")
    print(f"文件夹上传:     {'✓' if folder_success else '✗'}")
    
    if client_success and folder_success:
        print("\n🎉 所有测试通过！FTP 功能正常工作。")
        print("\n下一步:")
        print("  1. 查看任务书: docs/开发文档/v2.0_FTP功能设计任务书.md")
        print("  2. 开始开发 FTP 核心模块: ftp_protocol.py")
        print("  3. 集成到主程序: src.main")
    else:
        print("\n❌ 部分测试失败，请检查错误信息。")


if __name__ == "__main__":
    main()
