#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试模块化架构导入"""
import sys

print("=" * 60)
print("模块化架构导入测试")
print("=" * 60)

try:
    print("\n[1/7] 测试 src.core.utils...")
    from src.core.utils import get_app_dir, get_app_version
    print(f"  ✓ 成功 - 版本: {get_app_version()}")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[2/7] 测试 src.core.permissions...")
    from src.core.permissions import PermissionManager
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[3/7] 测试 src.config...")
    from src.config import ConfigManager
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[4/7] 测试 src.protocols.ftp...")
    from src.protocols.ftp import FTPProtocolManager
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[5/7] 测试 src.ui.widgets...")
    from src.ui.widgets import Toast, ChipWidget, DiskCleanupDialog
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[6/7] 测试 src.workers.upload_worker...")
    from src.workers.upload_worker import UploadWorker
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

try:
    print("\n[7/7] 测试 src.ui.main_window...")
    from src.ui.main_window import MainWindow
    print("  ✓ 成功")
except Exception as e:
    print(f"  ✗ 失败: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✓ 所有模块导入测试通过！")
print("=" * 60)

# 显示模块化架构统计
print("\n模块化架构统计:")
print("  - src/core/utils.py")
print("  - src/core/permissions.py")
print("  - src/config.py")
print("  - src/protocols/ftp.py")
print("  - src/ui/widgets.py")
print("  - src/ui/main_window.py (4017 行)")
print("  - src/workers/upload_worker.py")
print("  - src/main.py (新程序入口)")
