# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件（版本由 src.__version__ 提供）
断点续传 + 中英文切换版本
"""

import os
import sys

block_cipher = None

# 项目根目录
project_root = os.path.dirname(os.path.abspath(SPEC))
# 确保项目根目录可导入
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src import __version__ as VERSION

a = Analysis(
    ['pyqt_app.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('version.txt', '.'),
        ('config.json', '.'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'src.core',
        'src.core.utils',
        'src.core.permissions',
        'src.core.resume_manager',
        'src.core.i18n',
        'src.config',
        'src.ui',
        'src.ui.main_window',
        'src.ui.widgets',
        'src.workers',
        'src.workers.upload_worker',
        'src.protocols',
        'src.protocols.ftp',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=f'ImageUploadTool_v{VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/logo.ico' if os.path.exists('assets/logo.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=f'ImageUploadTool_v{VERSION}',
)
