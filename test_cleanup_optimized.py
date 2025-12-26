"""
文件清理对话框优化版本测试脚本

测试内容：
1. 延迟加载高级页
2. 简化的自动清理配置
3. 预设下拉格式选择
4. 统一的视觉样式
5. 无emoji的专业界面
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.ui.widgets import DiskCleanupDialog


class TestWindow(QtWidgets.QMainWindow):  # type: ignore[misc]
    """测试用主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("文件清理工具优化测试")
        self.resize(400, 300)
        
        # 模拟配置
        self.bak_edit = QtWidgets.QLineEdit(r"C:\Backup")
        self.tgt_edit = QtWidgets.QLineEdit(r"C:\Target")
        self.auto_delete_folder = r"C:\Monitor"
        self.enable_auto_delete = False
        self.auto_delete_threshold = 80
        self.auto_delete_keep_days = 10
        self.auto_delete_check_interval = 300
        
        # 创建按钮
        btn = QtWidgets.QPushButton("打开文件清理对话框")
        btn.clicked.connect(self.open_dialog)
        
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        
        layout.addWidget(QtWidgets.QLabel("优化内容测试："))
        layout.addWidget(QtWidgets.QLabel("✓ 延迟加载高级页（首次切换时才创建）"))
        layout.addWidget(QtWidgets.QLabel("✓ 自动清理配置简化为按钮（弹出独立窗口）"))
        layout.addWidget(QtWidgets.QLabel("✓ 格式选择改为预设下拉+可选展开"))
        layout.addWidget(QtWidgets.QLabel("✓ 统一样式表，卡片化设计"))
        layout.addWidget(QtWidgets.QLabel("✓ 移除所有emoji，使用纯文本"))
        layout.addWidget(QtWidgets.QLabel("✓ 删除按钮使用克制的红色描边样式"))
        layout.addStretch()
        layout.addWidget(btn)
        
        self.setCentralWidget(central)
    
    def open_dialog(self):
        """打开清理对话框"""
        dialog = DiskCleanupDialog(self)
        
        print("=" * 60)
        print("测试要点：")
        print("1. 启动时只加载基础页，切换到高级页时才创建控件")
        print("2. 基础页中格式选择使用下拉框，点击'展开格式详情'查看详细")
        print("3. 高级页中自动清理配置显示为卡片，点击'配置...'弹出详细窗口")
        print("4. 所有GroupBox改为轻量化卡片，统一视觉风格")
        print("5. 界面无emoji，使用纯文本和按钮")
        print("6. 删除按钮为红色描边样式（非实心红色）")
        print("=" * 60)
        
        dialog.exec()


def main():
    """主函数"""
    app = QtWidgets.QApplication(sys.argv)
    
    # 设置应用样式
    app.setStyle("Fusion")
    
    window = TestWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
