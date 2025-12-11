# -*- coding: utf-8 -*-
"""
图片异步上传工具 - 主程序入口 (模块化版本)

v3.1.1 - 断点续传、中英文切换、配置加载修复
- 使用新的模块化结构
- 保持与 pyqt_app.py 的兼容性
"""
import sys
import os
from pathlib import Path
from typing import List, Tuple

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 切换工作目录到项目根目录（确保配置文件能被正确找到）
os.chdir(project_root)


def check_dependencies() -> Tuple[bool, List[str], List[str]]:
    """检查关键依赖是否已安装
    
    Returns:
        (all_ok, missing_required, missing_optional)
        - all_ok: 必需依赖是否全部满足
        - missing_required: 缺失的必需依赖列表
        - missing_optional: 缺失的可选依赖列表
    """
    missing_required: List[str] = []
    missing_optional: List[str] = []
    
    # 必需依赖
    required_packages = [
        ('PySide6', 'pip install PySide6'),  # 或 PyQt5
    ]
    
    # 可选依赖
    optional_packages = [
        ('pyftpdlib', 'pip install pyftpdlib'),  # FTP 服务器功能
    ]
    
    # 检查必需依赖（PySide6 或 PyQt5 任一即可）
    try:
        import PySide6  # noqa: F401
    except ImportError:
        try:
            import PyQt5  # type: ignore[import-not-found] # noqa: F401
        except ImportError:
            missing_required.append('PySide6 (or PyQt5): pip install PySide6')
    
    # 检查可选依赖
    for pkg_name, install_cmd in optional_packages:
        try:
            __import__(pkg_name)
        except ImportError:
            missing_optional.append(f'{pkg_name}: {install_cmd}')
    
    return len(missing_required) == 0, missing_required, missing_optional


def show_dependency_warning(missing_required: List[str], missing_optional: List[str]) -> None:
    """显示依赖缺失警告"""
    if missing_required:
        print("\n" + "=" * 60)
        print("❌ 缺少必需依赖，程序无法启动：")
        for dep in missing_required:
            print(f"   - {dep}")
        print("=" * 60 + "\n")
    
    if missing_optional:
        print("\n" + "-" * 60)
        print("ℹ️ 缺少可选依赖，部分功能不可用：")
        for dep in missing_optional:
            print(f"   - {dep}")
        print("-" * 60 + "\n")


# 导入核心模块
from src.core import get_app_dir, get_app_version, get_app_title
from src.config import ConfigManager

def check_single_instance(server_name: str) -> bool:
    """检查并尝试唤醒已运行的实例
    
    Args:
        server_name: 服务器名称
        
    Returns:
        True - 是新实例，应该继续启动
        False - 已有实例运行，已发送唤醒消息
    """
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    
    # 尝试连接到已运行的实例
    if socket.waitForConnected(500):  # 等待500ms
        # 连接成功，说明程序已在运行
        # 发送唤醒消息
        socket.write(b"WAKEUP")
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return False  # 不是新实例
    
    # 连接失败，说明没有其他实例在运行
    return True  # 是新实例


def main():
    """主程序入口"""
    # 依赖检查（在导入 Qt 之前，便于提示）
    all_ok, missing_required, missing_optional = check_dependencies()
    if not all_ok:
        show_dependency_warning(missing_required, missing_optional)
        print("程序因缺少必需依赖而无法启动。")
        return 1
    
    if missing_optional:
        show_dependency_warning([], missing_optional)
    
    # 延迟导入 Qt，避免缺依赖时直接 ImportError
    global QtCore, QtWidgets, QLocalServer, QLocalSocket, QT_LIB  # type: ignore
    try:
        from PySide6 import QtCore, QtWidgets  # type: ignore
        from PySide6.QtNetwork import QLocalServer, QLocalSocket  # type: ignore
        QT_LIB = 'PySide6'
    except ImportError:
        try:
            from PyQt5 import QtCore, QtWidgets  # type: ignore[import-not-found]
            from PyQt5.QtNetwork import QLocalServer, QLocalSocket  # type: ignore[import-not-found]
            QT_LIB = 'PyQt5'
        except ImportError:
            show_dependency_warning(["PySide6 (or PyQt5): pip install PySide6"], [])
            return 1

    # 导入主窗口（依赖 Qt）
    from src.ui import MainWindow  # type: ignore

    app = QtWidgets.QApplication(sys.argv)
    
    # 设置应用程序信息
    app.setApplicationName("图片异步上传工具")
    app.setApplicationVersion(get_app_version())
    app.setOrganizationName("RelMoTong")
    
    # 单例检查
    server_name = "ImageUploadTool_SingleInstance_Server"
    if not check_single_instance(server_name):
        # 已有实例运行，已发送唤醒消息，直接退出
        return 0
    
    # 使用共享内存作为辅助锁（防止极端情况下的竞态条件）
    shared_mem = QtCore.QSharedMemory("ImageUploadTool_SingleInstance")
    if not shared_mem.create(1):
        # 极少情况：LocalServer 未响应但共享内存存在
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("程序启动异常")
        msg.setText("检测到程序可能未正常退出")
        msg.setInformativeText("建议：\n1. 检查任务管理器是否有残留进程\n2. 重启计算机后重试")
        msg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        msg.exec() if hasattr(msg, 'exec') else msg.exec_()
        return 1
    
    # 创建主窗口
    window = MainWindow()
    window.show()
    
    # 启动应用程序事件循环
    try:
        return app.exec()  # PySide6 / PyQt6
    except AttributeError:
        return app.exec_()  # PyQt5


if __name__ == '__main__':
    sys.exit(main())
