# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec.
Version is sourced from src.__version__.
"""
import os
import sys

block_cipher = None

project_root = os.path.dirname(os.path.abspath(SPEC))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src import __version__ as VERSION

a = Analysis(
    ['src/main.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        ('assets', 'assets'),
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
    console=False,
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
