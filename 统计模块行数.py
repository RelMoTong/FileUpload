#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统计模块化架构的文件行数"""
from pathlib import Path

files = [
    'src/ui/main_window.py',
    'src/ui/widgets.py',
    'src/workers/upload_worker.py',
    'src/protocols/ftp.py',
    'src/core/utils.py',
    'src/core/permissions.py',
    'src/config.py',
    'src/main.py',
    'pyqt_app.py'
]

print("=" * 70)
print("模块化架构文件统计")
print("=" * 70)

total_src = 0
total_all = 0

for file_path in files:
    p = Path(file_path)
    if p.exists():
        with open(p, encoding='utf-8') as f:
            lines = len(f.readlines())
        total_all += lines
        if file_path.startswith('src/'):
            total_src += lines
        print(f"{file_path:40s} {lines:6d} 行")
    else:
        print(f"{file_path:40s} [不存在]")

print("=" * 70)
print(f"{'src/ 模块总计:':40s} {total_src:6d} 行")
print(f"{'pyqt_app.py (兼容层):':40s} {total_all - total_src:6d} 行")
print(f"{'总计:':40s} {total_all:6d} 行")
print("=" * 70)
