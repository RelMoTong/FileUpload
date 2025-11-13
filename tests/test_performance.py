# -*- coding: utf-8 -*-
"""
v2.1 性能基准测试
测试FTP上传速度、并发能力、资源占用等性能指标
"""

import os
import sys
import time
import psutil
import threading
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ftp_protocol import FTPServerManager, FTPClientUploader


def print_header(title):
    """打印测试标题"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_metric(name, value, unit="", status=""):
    """打印性能指标"""
    symbol = "✓" if status == "pass" else "⚠" if status == "warn" else "✗" if status == "fail" else "→"
    print(f"  {symbol} {name}: {value} {unit}")


def get_process_memory():
    """获取当前进程内存占用（MB）"""
    process = psutil.Process()
    return process.memory_info().rss / 1024 / 1024


def get_process_cpu():
    """获取当前进程CPU占用（%）"""
    process = psutil.Process()
    return process.cpu_percent(interval=1.0)


def create_test_file(file_path, size_mb):
    """创建指定大小的测试文件"""
    with open(file_path, 'wb') as f:
        f.write(os.urandom(int(size_mb * 1024 * 1024)))


def test_performance_1_startup_time():
    """
    性能测试1: 启动时间
    目标: ≤ 3秒
    """
    print_header("性能测试1: 软件启动时间")
    
    try:
        # 记录启动前内存
        mem_before = get_process_memory()
        print_metric("启动前内存", f"{mem_before:.2f}", "MB")
        
        # 模拟软件启动（加载FTP模块）
        start_time = time.time()
        
        # 导入模块
        from src.ftp_protocol import FTPServerManager, FTPClientUploader
        
        # 创建配置
        server_config = {
            'host': '127.0.0.1',
            'port': 3201,
            'username': 'perf_test',
            'password': 'perf_test',
            'shared_folder': str(Path('test_perf_share').absolute())
        }
        
        client_config = {
            'name': 'perf_client',
            'host': '127.0.0.1',
            'port': 3201,
            'username': 'perf_test',
            'password': 'perf_test',
            'remote_path': '/upload'
        }
        
        # 初始化管理器
        server = FTPServerManager(server_config)
        client = FTPClientUploader(client_config)
        
        end_time = time.time()
        startup_time = end_time - start_time
        
        # 记录启动后内存
        mem_after = get_process_memory()
        mem_increase = mem_after - mem_before
        
        print_metric("启动时间", f"{startup_time:.3f}", "秒", 
                    "pass" if startup_time <= 3.0 else "fail")
        print_metric("内存增加", f"{mem_increase:.2f}", "MB")
        print_metric("启动后内存", f"{mem_after:.2f}", "MB")
        
        # 清理
        if server:
            try:
                server.stop()
            except:
                pass
        
        return startup_time <= 3.0
        
    except Exception as e:
        print_metric("测试失败", str(e), "", "fail")
        return False


def test_performance_2_ftp_upload_speed():
    """
    性能测试2: FTP上传速度
    目标: ≥ 2 MB/s（千兆网络）
    """
    print_header("性能测试2: FTP上传速度测试")
    
    server = None
    client = None
    
    try:
        # 创建测试环境
        share_dir = Path("test_perf_share").absolute()
        upload_dir = Path("test_perf_upload").absolute()
        share_dir.mkdir(exist_ok=True)
        upload_dir.mkdir(exist_ok=True)
        
        # 创建测试文件（1MB, 5MB, 10MB）
        test_files = []
        file_sizes = [1, 5, 10]  # MB
        
        print("\n  准备测试文件...")
        for size in file_sizes:
            file_path = upload_dir / f"test_{size}mb.bin"
            print_metric(f"创建 {size}MB 文件", str(file_path))
            create_test_file(file_path, size)
            test_files.append((file_path, size))
        
        # 启动FTP服务器
        server_config = {
            'host': '127.0.0.1',
            'port': 3202,
            'username': 'speed_test',
            'password': 'speed_test',
            'shared_folder': str(share_dir)
        }
        
        server = FTPServerManager(server_config)
        if not server.start():
            print_metric("FTP服务器启动失败", "", "", "fail")
            return False
        
        time.sleep(1)
        print_metric("FTP服务器启动", "127.0.0.1:3202", "", "pass")
        
        # 创建FTP客户端
        client_config = {
            'name': 'speed_test_client',
            'host': '127.0.0.1',
            'port': 3202,
            'username': 'speed_test',
            'password': 'speed_test',
            'remote_path': '/uploads',
            'timeout': 60
        }
        
        client = FTPClientUploader(client_config)
        if not client.connect():
            print_metric("FTP客户端连接失败", "", "", "fail")
            return False
        
        print_metric("FTP客户端连接", "成功", "", "pass")
        
        # 测试上传速度
        speeds = []
        print("\n  开始上传测试...")
        
        for file_path, size_mb in test_files:
            start_time = time.time()
            
            success = client.upload_file(file_path, f'/uploads/{file_path.name}')
            
            end_time = time.time()
            duration = end_time - start_time
            
            if success and duration > 0:
                speed_mbps = size_mb / duration
                speeds.append(speed_mbps)
                
                status = "pass" if speed_mbps >= 2.0 else "warn"
                print_metric(f"{size_mb}MB 文件上传", 
                           f"{speed_mbps:.2f} MB/s ({duration:.2f}秒)", 
                           "", status)
            else:
                print_metric(f"{size_mb}MB 文件上传", "失败", "", "fail")
        
        # 计算平均速度
        if speeds:
            avg_speed = sum(speeds) / len(speeds)
            print_metric("平均上传速度", f"{avg_speed:.2f}", "MB/s", 
                        "pass" if avg_speed >= 2.0 else "fail")
            
            passed = avg_speed >= 2.0
        else:
            passed = False
        
        # 清理
        client.disconnect()
        server.stop()
        time.sleep(1)
        
        import shutil
        if share_dir.exists():
            shutil.rmtree(share_dir)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        
        return passed
        
    except Exception as e:
        print_metric("测试失败", str(e), "", "fail")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if client:
            try:
                client.disconnect()
            except:
                pass
        if server:
            try:
                server.stop()
            except:
                pass


def test_performance_3_concurrent_clients():
    """
    性能测试3: 并发客户端上传
    目标: 支持5个客户端同时上传
    """
    print_header("性能测试3: 并发客户端上传测试")
    
    server = None
    clients = []
    
    try:
        # 创建测试环境
        share_dir = Path("test_perf_concurrent").absolute()
        upload_dirs = []
        share_dir.mkdir(exist_ok=True)
        
        # 为每个客户端创建上传目录和测试文件
        num_clients = 5
        file_size_mb = 2  # 每个文件2MB
        
        print(f"\n  准备{num_clients}个客户端的测试环境...")
        
        for i in range(num_clients):
            upload_dir = Path(f"test_perf_upload_{i}").absolute()
            upload_dir.mkdir(exist_ok=True)
            upload_dirs.append(upload_dir)
            
            # 创建测试文件
            test_file = upload_dir / f"client_{i}_test.bin"
            create_test_file(test_file, file_size_mb)
            print_metric(f"客户端{i+1} 测试文件", str(test_file))
        
        # 启动FTP服务器
        server_config = {
            'host': '127.0.0.1',
            'port': 3203,
            'username': 'concurrent',
            'password': 'concurrent',
            'shared_folder': str(share_dir),
            'max_cons': 256,
            'max_cons_per_ip': 10  # 允许同IP多连接
        }
        
        server = FTPServerManager(server_config)
        if not server.start():
            print_metric("FTP服务器启动失败", "", "", "fail")
            return False
        
        time.sleep(1)
        print_metric("FTP服务器启动", "127.0.0.1:3203", "", "pass")
        
        # 并发上传测试
        results = []
        errors = []
        lock = threading.Lock()
        
        def upload_worker(client_id, upload_dir):
            """工作线程：连接并上传文件"""
            try:
                # 创建客户端
                client_config = {
                    'name': f'client_{client_id}',
                    'host': '127.0.0.1',
                    'port': 3203,
                    'username': 'concurrent',
                    'password': 'concurrent',
                    'remote_path': f'/client_{client_id}',
                    'timeout': 30
                }
                
                client = FTPClientUploader(client_config)
                clients.append(client)
                
                # 连接
                if not client.connect():
                    with lock:
                        errors.append(f"客户端{client_id}连接失败")
                    return
                
                # 上传文件
                test_file = upload_dir / f"client_{client_id}_test.bin"
                start_time = time.time()
                
                success = client.upload_file(test_file, f'/client_{client_id}/test.bin')
                
                end_time = time.time()
                duration = end_time - start_time
                
                with lock:
                    results.append({
                        'client_id': client_id,
                        'success': success,
                        'duration': duration
                    })
                
                client.disconnect()
                
            except Exception as e:
                with lock:
                    errors.append(f"客户端{client_id}异常: {e}")
        
        # 启动所有客户端线程
        print(f"\n  启动{num_clients}个并发客户端...")
        threads = []
        
        start_time = time.time()
        
        for i in range(num_clients):
            thread = threading.Thread(
                target=upload_worker,
                args=(i, upload_dirs[i]),
                daemon=True
            )
            thread.start()
            threads.append(thread)
        
        # 等待所有线程完成
        for thread in threads:
            thread.join(timeout=60)
        
        end_time = time.time()
        total_duration = end_time - start_time
        
        # 分析结果
        print("\n  并发上传结果:")
        successful = sum(1 for r in results if r['success'])
        
        print_metric("总耗时", f"{total_duration:.2f}", "秒")
        print_metric("成功上传", f"{successful}/{num_clients}", "")
        
        if errors:
            print("\n  错误信息:")
            for error in errors:
                print_metric("错误", error, "", "fail")
        
        for result in results:
            status = "pass" if result['success'] else "fail"
            print_metric(f"客户端{result['client_id']}", 
                        f"{'成功' if result['success'] else '失败'} ({result['duration']:.2f}秒)",
                        "", status)
        
        passed = successful >= num_clients
        
        # 清理
        server.stop()
        time.sleep(1)
        
        import shutil
        if share_dir.exists():
            shutil.rmtree(share_dir)
        for upload_dir in upload_dirs:
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
        
        return passed
        
    except Exception as e:
        print_metric("测试失败", str(e), "", "fail")
        import traceback
        traceback.print_exc()
        return False
    finally:
        for client in clients:
            try:
                client.disconnect()
            except:
                pass
        if server:
            try:
                server.stop()
            except:
                pass


def test_performance_4_memory_usage():
    """
    性能测试4: 内存占用
    目标: ≤ 300 MB（运行状态）
    """
    print_header("性能测试4: 内存占用测试")
    
    server = None
    client = None
    
    try:
        # 记录基准内存
        baseline_memory = get_process_memory()
        print_metric("基准内存", f"{baseline_memory:.2f}", "MB")
        
        # 创建测试环境
        share_dir = Path("test_perf_memory").absolute()
        upload_dir = Path("test_perf_memory_upload").absolute()
        share_dir.mkdir(exist_ok=True)
        upload_dir.mkdir(exist_ok=True)
        
        # 启动FTP服务器
        server_config = {
            'host': '127.0.0.1',
            'port': 3204,
            'username': 'memory_test',
            'password': 'memory_test',
            'shared_folder': str(share_dir)
        }
        
        server = FTPServerManager(server_config)
        server.start()
        time.sleep(1)
        
        mem_after_server = get_process_memory()
        server_mem = mem_after_server - baseline_memory
        print_metric("FTP服务器启动后", f"{mem_after_server:.2f}", "MB")
        print_metric("服务器内存增加", f"{server_mem:.2f}", "MB")
        
        # 创建客户端并连接
        client_config = {
            'name': 'memory_test_client',
            'host': '127.0.0.1',
            'port': 3204,
            'username': 'memory_test',
            'password': 'memory_test',
            'remote_path': '/uploads'
        }
        
        client = FTPClientUploader(client_config)
        client.connect()
        time.sleep(0.5)
        
        mem_after_client = get_process_memory()
        client_mem = mem_after_client - mem_after_server
        print_metric("客户端连接后", f"{mem_after_client:.2f}", "MB")
        print_metric("客户端内存增加", f"{client_mem:.2f}", "MB")
        
        # 上传文件测试内存
        test_file = upload_dir / "memory_test.bin"
        create_test_file(test_file, 10)  # 10MB文件
        
        client.upload_file(test_file, '/uploads/memory_test.bin')
        time.sleep(1)
        
        mem_after_upload = get_process_memory()
        upload_mem = mem_after_upload - mem_after_client
        print_metric("上传10MB后", f"{mem_after_upload:.2f}", "MB")
        print_metric("上传内存增加", f"{upload_mem:.2f}", "MB")
        
        # 总内存占用
        total_memory = mem_after_upload
        print_metric("总内存占用", f"{total_memory:.2f}", "MB",
                    "pass" if total_memory <= 300 else "fail")
        
        passed = total_memory <= 300
        
        # 清理
        client.disconnect()
        server.stop()
        time.sleep(1)
        
        import shutil
        if share_dir.exists():
            shutil.rmtree(share_dir)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        
        return passed
        
    except Exception as e:
        print_metric("测试失败", str(e), "", "fail")
        return False
    finally:
        if client:
            try:
                client.disconnect()
            except:
                pass
        if server:
            try:
                server.stop()
            except:
                pass


def test_performance_5_cpu_usage():
    """
    性能测试5: CPU占用
    目标: ≤ 15%（上传中）
    """
    print_header("性能测试5: CPU占用测试")
    
    server = None
    client = None
    
    try:
        # 创建测试环境
        share_dir = Path("test_perf_cpu").absolute()
        upload_dir = Path("test_perf_cpu_upload").absolute()
        share_dir.mkdir(exist_ok=True)
        upload_dir.mkdir(exist_ok=True)
        
        # 记录空闲时CPU
        cpu_idle = get_process_cpu()
        print_metric("空闲时CPU", f"{cpu_idle:.1f}", "%")
        
        # 启动FTP服务器
        server_config = {
            'host': '127.0.0.1',
            'port': 3205,
            'username': 'cpu_test',
            'password': 'cpu_test',
            'shared_folder': str(share_dir)
        }
        
        server = FTPServerManager(server_config)
        server.start()
        time.sleep(1)
        
        cpu_with_server = get_process_cpu()
        print_metric("服务器运行时CPU", f"{cpu_with_server:.1f}", "%")
        
        # 创建客户端
        client_config = {
            'name': 'cpu_test_client',
            'host': '127.0.0.1',
            'port': 3205,
            'username': 'cpu_test',
            'password': 'cpu_test',
            'remote_path': '/uploads'
        }
        
        client = FTPClientUploader(client_config)
        client.connect()
        
        # 创建较大的测试文件
        test_file = upload_dir / "cpu_test.bin"
        create_test_file(test_file, 20)  # 20MB
        
        # 上传时监控CPU
        cpu_samples = []
        
        def monitor_cpu():
            """监控CPU使用率"""
            for _ in range(5):  # 采样5次
                cpu_samples.append(get_process_cpu())
                time.sleep(0.5)
        
        # 启动监控线程
        monitor_thread = threading.Thread(target=monitor_cpu, daemon=True)
        monitor_thread.start()
        
        # 开始上传
        client.upload_file(test_file, '/uploads/cpu_test.bin')
        
        # 等待监控完成
        monitor_thread.join(timeout=10)
        
        # 分析CPU使用率
        if cpu_samples:
            avg_cpu = sum(cpu_samples) / len(cpu_samples)
            max_cpu = max(cpu_samples)
            
            print_metric("上传时平均CPU", f"{avg_cpu:.1f}", "%",
                        "pass" if avg_cpu <= 15 else "fail")
            print_metric("上传时峰值CPU", f"{max_cpu:.1f}", "%")
            
            passed = avg_cpu <= 15
        else:
            print_metric("CPU采样失败", "", "", "fail")
            passed = False
        
        # 清理
        client.disconnect()
        server.stop()
        time.sleep(1)
        
        import shutil
        if share_dir.exists():
            shutil.rmtree(share_dir)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        
        return passed
        
    except Exception as e:
        print_metric("测试失败", str(e), "", "fail")
        return False
    finally:
        if client:
            try:
                client.disconnect()
            except:
                pass
        if server:
            try:
                server.stop()
            except:
                pass


def generate_performance_report(results):
    """生成性能测试报告"""
    print("\n")
    print("=" * 80)
    print("  性能测试报告")
    print("=" * 80)
    
    total = len(results)
    passed = sum(1 for r in results.values() if r)
    
    print(f"\n  测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Python版本: {sys.version.split()[0]}")
    print(f"  操作系统: {os.name}")
    
    print("\n  测试结果汇总:")
    for test_name, result in results.items():
        symbol = "✓" if result else "✗"
        print(f"    {symbol} {test_name}")
    
    print(f"\n  总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n  🎉 所有性能测试通过！")
        return 0
    else:
        print(f"\n  ⚠️  {total - passed} 个测试未通过")
        return 1


def main():
    """运行所有性能测试"""
    print("\n")
    print("*" * 80)
    print("*" + " " * 78 + "*")
    print("*" + "  v2.1 性能基准测试".center(76) + "*")
    print("*" + " " * 78 + "*")
    print("*" * 80)
    
    results = {}
    
    # 运行所有测试
    print("\n⚡ 开始性能测试...")
    
    results['1. 启动时间 (≤3秒)'] = test_performance_1_startup_time()
    results['2. FTP上传速度 (≥2MB/s)'] = test_performance_2_ftp_upload_speed()
    results['3. 并发客户端 (5个)'] = test_performance_3_concurrent_clients()
    results['4. 内存占用 (≤300MB)'] = test_performance_4_memory_usage()
    results['5. CPU占用 (≤15%)'] = test_performance_5_cpu_usage()
    
    # 生成报告
    return generate_performance_report(results)


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
