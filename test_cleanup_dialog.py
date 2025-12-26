"""测试文件清理对话框的升级功能"""

import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.ui.widgets import DiskCleanupDialog


class MockMainWindow(QtWidgets.QWidget):  # type: ignore[misc]
    """模拟主窗口"""
    def __init__(self):
        super().__init__()
        self.bak_edit = QtWidgets.QLineEdit()
        self.bak_edit.setText("C:/TestBackup")
        
        self.tgt_edit = QtWidgets.QLineEdit()
        self.tgt_edit.setText("C:/TestTarget")
        
        self.auto_delete_folder = "C:/TestMonitor"
        self.enable_auto_delete = False
        self.auto_delete_threshold = 80
        self.auto_delete_keep_days = 10
        self.auto_delete_check_interval = 300
    
    def _save_config(self):
        print("配置已保存（模拟）")


def main():
    """运行测试"""
    app = QtWidgets.QApplication(sys.argv)
    
    # 创建模拟主窗口
    mock_window = MockMainWindow()
    
    # 创建清理对话框
    dialog = DiskCleanupDialog(mock_window)
    
    # 显示对话框
    dialog.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
