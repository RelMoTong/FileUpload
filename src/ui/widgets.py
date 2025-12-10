"""自定义 UI 控件模块

包含：
- Toast: 通知提示组件
- ChipWidget: 数据卡片组件
- CollapsibleBox: 可折叠容器组件
- DiskCleanupDialog: 磁盘清理对话框
"""

import os
from typing import Optional, List, Tuple, Dict, Any, TYPE_CHECKING, Protocol

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    QtEnum = Qt
except ImportError:
    from PyQt5 import QtWidgets, QtCore, QtGui  # type: ignore[import-not-found]
    from PyQt5.QtCore import Qt  # type: ignore[import-not-found]
    QtEnum = QtCore.Qt

# 类型检查时的协议定义
if TYPE_CHECKING:
    class MainWindowProtocol(Protocol):
        """MainWindow 的类型协议（用于类型检查）"""
        bak_edit: QtWidgets.QLineEdit
        tgt_edit: QtWidgets.QLineEdit
        auto_delete_folder: str
        enable_auto_delete: bool
        auto_delete_threshold: int
        auto_delete_keep_days: int
        auto_delete_check_interval: int
        
        def _save_config(self) -> None: ...


class Toast(QtWidgets.QWidget):  # type: ignore[misc]
    """Toast 通知组件
    
    用于显示临时通知消息，支持不同类型的提示样式。
    
    Args:
        parent: 父窗口
        message: 提示消息
        kind: 提示类型 ('info', 'success', 'warning', 'danger')
        duration_ms: 显示时长（毫秒）
    
    Note: 使用 type: ignore[misc] 是因为 Qt 模块在 try-except 中动态导入，
    Pylance 无法在静态分析时确定基类有效性，但运行时完全正确。
    """
    def __init__(
        self,
        parent: QtWidgets.QWidget,
        message: str,
        kind: str = 'info',
        duration_ms: int = 2500
    ):
        super().__init__(parent)
        wt = getattr(QtEnum, 'WindowType', QtEnum)
        wa = getattr(QtEnum, 'WidgetAttribute', QtEnum)
        self.setWindowFlags(
            getattr(wt, 'FramelessWindowHint')
            | getattr(wt, 'Tool')
            | getattr(wt, 'WindowStaysOnTopHint')
        )
        self.setAttribute(getattr(wa, 'WA_TranslucentBackground'))
        colors = {
            'info':    ("#E0F2FE", "#039CA1"),
            'success': ("#DCFCE7", "#166534"),
            'warning': ("#FEF9C3", "#A16207"),
            'danger':  ("#FEE2E2", "#B91C1C"),
        }
        bg, fg = colors.get(kind, colors['info'])
        layout = QtWidgets.QHBoxLayout(self)
        frame = QtWidgets.QFrame(self)
        frame.setStyleSheet(f"QFrame{{background:{bg}; border:1px solid rgba(0,0,0,0.06); border-radius:8px;}}")
        inner = QtWidgets.QHBoxLayout(frame)
        label = QtWidgets.QLabel(message)
        label.setStyleSheet(f"color:{fg}; padding:8px 12px; font-size:11pt;")
        inner.addWidget(label)
        layout.addWidget(frame)
        self.adjustSize()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(duration_ms)

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        """显示事件，自动定位到父窗口右上角"""
        if self.parent():
            p = self.parent()
            geo = p.geometry()
            self.adjustSize()
            x = geo.x() + geo.width() - self.width() - 16
            y = geo.y() + 80
            self.move(x, y)
        return super().showEvent(e)


class ChipWidget(QtWidgets.QFrame):  # type: ignore[misc]
    """数据卡片组件
    
    用于展示键值对信息，带有彩色背景和标题。
    
    Args:
        title: 标题文本
        val: 值文本
        bg: 背景颜色
        fg: 前景颜色（文字颜色）
        parent: 父窗口
    """
    value_label: QtWidgets.QLabel
    title_label: QtWidgets.QLabel
    
    def __init__(
        self,
        title: str,
        val: str,
        bg: str,
        fg: str,
        parent: Optional[QtWidgets.QWidget] = None
    ):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{bg}; border-radius:8px; padding:2px;}} "
            f"QLabel{{color:{fg};}}"
        )
        vv = QtWidgets.QVBoxLayout(self)
        vv.setSpacing(4)  # 增加标题和值之间的间距
        vv.setContentsMargins(10, 8, 10, 8)  # 增加内边距
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("font-size:9.5pt; padding-top:2px;")
        self.value_label = QtWidgets.QLabel(val)
        self.value_label.setStyleSheet("font-weight:700; font-size:11.5pt; padding-bottom:2px;")
        vv.addWidget(self.title_label)
        vv.addWidget(self.value_label)
    
    def setValue(self, text: str) -> None:
        """更新卡片的值文本
        
        Args:
            text: 新的值文本
        """
        self.value_label.setText(text)


class CollapsibleBox(QtWidgets.QWidget):  # type: ignore[misc]
    """可折叠容器组件
    
    提供可展开/折叠的内容区域，用于节省界面空间。
    
    Args:
        title: 标题文本
        parent: 父窗口
    
    Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
    """
    def __init__(self, title: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setStyleSheet("QToolButton { border: none; font-weight: 700; }")
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        
        self.content_area = QtWidgets.QWidget()
        self.content_area.setVisible(False)
        self.content_layout = QtWidgets.QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(20, 8, 8, 8)
        
        self.toggle_button.toggled.connect(self._on_toggle)
        
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.toggle_button)
        main_layout.addWidget(self.content_area)
    
    def _on_toggle(self, checked: bool) -> None:
        """处理展开/折叠切换"""
        self.toggle_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        self.content_area.setVisible(checked)
    
    def setContentLayout(self, layout: QtWidgets.QLayout) -> None:
        """设置内容布局
        
        Args:
            layout: 要设置的布局
        """
        # 清除旧布局
        old_layout = self.content_area.layout()
        if old_layout is not None:
            QtWidgets.QWidget().setLayout(old_layout)
        self.content_area.setLayout(layout)
        layout.setContentsMargins(20, 8, 8, 8)
    
    def addWidget(self, widget: QtWidgets.QWidget) -> None:
        """添加 widget 到内容区域
        
        Args:
            widget: 要添加的 widget
        """
        self.content_layout.addWidget(widget)
    
    def addLayout(self, layout: QtWidgets.QLayout) -> None:
        """添加 layout 到内容区域
        
        Args:
            layout: 要添加的 layout
        """
        self.content_layout.addLayout(layout)
    
    def setTitle(self, title: str) -> None:
        """设置标题文本（用于多语言切换）
        
        Args:
            title: 新的标题文本
        """
        self.toggle_button.setText(title)


class DiskCleanupDialog(QtWidgets.QDialog):  # type: ignore[misc]
    """磁盘清理对话框
    
    支持选择文件夹路径和文件格式进行磁盘清理。
    整合自动清理配置功能。
    
    Args:
        parent: 父窗口（MainWindow 实例）
    
    Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
    """
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("💿 磁盘清理工具")
        self.setModal(True)
        self.resize(500, 500)  # 增加高度以容纳自动清理配置
        
        # 保存父窗口引用，使用 Any 类型避免 Pylance 误报
        self.parent_window: Any = parent  # type: ignore[assignment]
        self.files_to_delete: List[Tuple[str, int]] = []  # 待删除的文件列表
        
        self._build_ui()
    
    def _build_ui(self) -> None:
        """构建 UI"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题说明
        title_label = QtWidgets.QLabel("选择要清理的文件夹和文件类型")
        title_label.setStyleSheet("font-size: 13pt; font-weight: 700; color: #1976D2;")
        layout.addWidget(title_label)
        
        desc_label = QtWidgets.QLabel(
            "⚠️ 警告：删除的文件将无法恢复！请确认后再执行清理操作。"
        )
        desc_label.setStyleSheet("color: #D32F2F; padding: 8px; background: #FFEBEE; border-radius: 6px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # 文件夹选择区域
        folder_group = self._create_folder_selection_group()
        layout.addWidget(folder_group)
        
        # 文件格式选择区域
        format_group = self._create_format_selection_group()
        layout.addWidget(format_group)
        
        # 自动清理配置区域
        auto_group = self._create_auto_cleanup_group()
        layout.addWidget(auto_group)
        
        # 扫描结果区域
        result_group = self._create_result_group()
        layout.addWidget(result_group)
        
        # 按钮区域
        button_layout = self._create_button_layout()
        layout.addLayout(button_layout)
    
    def _create_folder_selection_group(self) -> QtWidgets.QGroupBox:
        """创建文件夹选择区域"""
        folder_group = QtWidgets.QGroupBox("📁 选择清理目标")
        folder_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
            "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
        )
        folder_layout = QtWidgets.QVBoxLayout(folder_group)
        folder_layout.setSpacing(12)
        
        # 从父窗口的输入框读取路径配置（实时读取最新值）
        backup_path = self.parent_window.bak_edit.text() if self.parent_window and hasattr(self.parent_window, 'bak_edit') else ""
        target_path = self.parent_window.tgt_edit.text() if self.parent_window and hasattr(self.parent_window, 'tgt_edit') else ""
        monitor_path = self.parent_window.auto_delete_folder if self.parent_window and hasattr(self.parent_window, 'auto_delete_folder') else ""
        
        # 备份文件夹
        self.cb_backup = QtWidgets.QCheckBox(f"🗂️ 备份文件夹")
        self.cb_backup.setChecked(True)
        if backup_path:
            self.cb_backup.setText(f"🗂️ 备份文件夹: {backup_path}")
            self.cb_backup.setToolTip(backup_path)
        else:
            self.cb_backup.setEnabled(False)
            self.cb_backup.setText("🗂️ 备份文件夹 (未配置)")
        folder_layout.addWidget(self.cb_backup)
        
        # 目标文件夹
        self.cb_target = QtWidgets.QCheckBox(f"📤 目标文件夹 (服务器)")
        if target_path:
            self.cb_target.setText(f"📤 目标文件夹: {target_path}")
            self.cb_target.setToolTip(target_path)
        else:
            self.cb_target.setEnabled(False)
            self.cb_target.setText("📤 目标文件夹 (未配置)")
        folder_layout.addWidget(self.cb_target)
        
        # 监控文件夹（带输入功能）
        self.cb_monitor = QtWidgets.QCheckBox("🔍 监控文件夹")
        folder_layout.addWidget(self.cb_monitor)
        
        monitor_row = QtWidgets.QHBoxLayout()
        monitor_row.setContentsMargins(30, 0, 0, 0)
        self.edit_monitor = QtWidgets.QLineEdit(monitor_path)
        self.edit_monitor.setPlaceholderText("选择监控文件夹路径...")
        btn_monitor = QtWidgets.QPushButton("浏览")
        btn_monitor.setProperty("class", "Secondary")
        btn_monitor.clicked.connect(self._choose_monitor)
        monitor_row.addWidget(self.edit_monitor, 1)
        monitor_row.addWidget(btn_monitor)
        folder_layout.addLayout(monitor_row)
        
        # 自定义文件夹（保留输入功能）
        self.cb_custom = QtWidgets.QCheckBox("📂 自定义文件夹")
        folder_layout.addWidget(self.cb_custom)
        
        custom_row = QtWidgets.QHBoxLayout()
        custom_row.setContentsMargins(30, 0, 0, 0)
        self.edit_custom = QtWidgets.QLineEdit()
        self.edit_custom.setPlaceholderText("选择自定义文件夹路径...")
        btn_custom = QtWidgets.QPushButton("浏览")
        btn_custom.setProperty("class", "Secondary")
        btn_custom.clicked.connect(self._choose_custom)
        custom_row.addWidget(self.edit_custom, 1)
        custom_row.addWidget(btn_custom)
        folder_layout.addLayout(custom_row)
        
        return folder_group
    
    def _create_format_selection_group(self) -> QtWidgets.QGroupBox:
        """创建文件格式选择区域"""
        format_group = QtWidgets.QGroupBox("📋 选择文件格式")
        format_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
            "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
        )
        format_layout = QtWidgets.QVBoxLayout(format_group)
        format_layout.setSpacing(10)
        
        # 快速选择按钮
        quick_row = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton("全选")
        btn_all.setProperty("class", "Secondary")
        btn_all.clicked.connect(self._select_all_formats)
        btn_none = QtWidgets.QPushButton("取消全选")
        btn_none.setProperty("class", "Secondary")
        btn_none.clicked.connect(self._select_no_formats)
        btn_image = QtWidgets.QPushButton("仅图片")
        btn_image.setProperty("class", "Secondary")
        btn_image.clicked.connect(self._select_image_formats)
        quick_row.addWidget(btn_all)
        quick_row.addWidget(btn_none)
        quick_row.addWidget(btn_image)
        quick_row.addStretch()
        format_layout.addLayout(quick_row)
        
        # 文件格式复选框 - 网格布局
        formats_grid = QtWidgets.QGridLayout()
        formats_grid.setSpacing(8)
        
        self.format_checkboxes: Dict[str, QtWidgets.QCheckBox] = {}
        formats = [
            ('.jpg', '图片'),
            ('.jpeg', '图片'),
            ('.png', '图片'),
            ('.bmp', '图片'),
            ('.gif', '图片'),
            ('.tiff', '图片'),
            ('.tif', '图片'),
            ('.raw', '图片'),
            ('.pdf', '文档'),
            ('.doc', '文档'),
            ('.docx', '文档'),
            ('.txt', '文本'),
            ('.log', '日志'),
            ('.zip', '压缩'),
            ('.rar', '压缩'),
            ('.tmp', '临时'),
        ]
        
        for idx, (ext, category) in enumerate(formats):
            cb = QtWidgets.QCheckBox(f"{ext} ({category})")
            cb.setChecked(ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.raw'])  # 默认选中图片
            self.format_checkboxes[ext] = cb
            row = idx // 4
            col = idx % 4
            formats_grid.addWidget(cb, row, col)
        
        format_layout.addLayout(formats_grid)
        
        # 自定义格式
        custom_format_row = QtWidgets.QHBoxLayout()
        custom_format_label = QtWidgets.QLabel("自定义格式:")
        self.edit_custom_format = QtWidgets.QLineEdit()
        self.edit_custom_format.setPlaceholderText("例如: .bak 或 .old (以点开头)")
        custom_format_row.addWidget(custom_format_label)
        custom_format_row.addWidget(self.edit_custom_format, 1)
        format_layout.addLayout(custom_format_row)
        
        return format_group
    
    def _create_auto_cleanup_group(self) -> QtWidgets.QGroupBox:
        """创建自动清理配置区域"""
        auto_group = QtWidgets.QGroupBox("⚙️ 自动清理配置")
        auto_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 2px solid #FFA726; "
            "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
        )
        auto_layout = QtWidgets.QVBoxLayout(auto_group)
        auto_layout.setSpacing(10)
        
        # 启用自动清理
        self.cb_enable_auto = QtWidgets.QCheckBox("⏰ 启用自动清理")
        auto_enabled = self.parent_window.enable_auto_delete if self.parent_window and hasattr(self.parent_window, 'enable_auto_delete') else False
        self.cb_enable_auto.setChecked(auto_enabled)
        self.cb_enable_auto.toggled.connect(self._on_auto_clean_toggled)
        auto_layout.addWidget(self.cb_enable_auto)
        
        # 配置参数
        config_grid = QtWidgets.QGridLayout()
        config_grid.setSpacing(10)
        
        # 磁盘阈值
        threshold_label = QtWidgets.QLabel("磁盘阈值:")
        self.spin_threshold = QtWidgets.QSpinBox()
        self.spin_threshold.setRange(50, 95)
        auto_threshold = self.parent_window.auto_delete_threshold if self.parent_window and hasattr(self.parent_window, 'auto_delete_threshold') else 80
        self.spin_threshold.setValue(auto_threshold)
        self.spin_threshold.setSuffix(" %")
        self.spin_threshold.setToolTip("磁盘使用率达到此值时自动清理")
        self.spin_threshold.setEnabled(auto_enabled)
        config_grid.addWidget(threshold_label, 0, 0)
        config_grid.addWidget(self.spin_threshold, 0, 1)
        
        # 保留天数
        days_label = QtWidgets.QLabel("保留天数:")
        self.spin_keep_days = QtWidgets.QSpinBox()
        self.spin_keep_days.setRange(1, 365)
        auto_days = self.parent_window.auto_delete_keep_days if self.parent_window and hasattr(self.parent_window, 'auto_delete_keep_days') else 10
        self.spin_keep_days.setValue(auto_days)
        self.spin_keep_days.setSuffix(" 天")
        self.spin_keep_days.setToolTip("只删除超过此天数的文件")
        self.spin_keep_days.setEnabled(auto_enabled)
        config_grid.addWidget(days_label, 0, 2)
        config_grid.addWidget(self.spin_keep_days, 0, 3)
        
        # 检查间隔
        interval_label = QtWidgets.QLabel("检查间隔:")
        self.spin_check_interval = QtWidgets.QSpinBox()
        self.spin_check_interval.setRange(60, 3600)
        auto_interval = self.parent_window.auto_delete_check_interval if self.parent_window and hasattr(self.parent_window, 'auto_delete_check_interval') else 300
        self.spin_check_interval.setValue(auto_interval)
        self.spin_check_interval.setSuffix(" 秒")
        self.spin_check_interval.setToolTip("自动检查的时间间隔")
        self.spin_check_interval.setEnabled(auto_enabled)
        config_grid.addWidget(interval_label, 1, 0)
        config_grid.addWidget(self.spin_check_interval, 1, 1)
        
        auto_layout.addLayout(config_grid)
        
        # 说明文本
        auto_hint = QtWidgets.QLabel(
            "💡 启用后，程序会定期检查磁盘空间，当达到阈值时自动删除超过保留期限的文件"
        )
        auto_hint.setStyleSheet("color: #757575; font-size: 9px; padding: 8px;")
        auto_hint.setWordWrap(True)
        auto_layout.addWidget(auto_hint)
        
        # 保存配置按钮
        btn_save_auto = QtWidgets.QPushButton("💾 保存自动清理配置")
        btn_save_auto.setProperty("class", "Secondary")
        btn_save_auto.clicked.connect(self._save_auto_config)
        auto_layout.addWidget(btn_save_auto)
        
        return auto_group
    
    def _create_result_group(self) -> QtWidgets.QGroupBox:
        """创建扫描结果区域"""
        result_group = QtWidgets.QGroupBox("📊 扫描结果")
        result_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 2px solid #64B5F6; "
            "border-radius: 8px; margin-top: 10px; padding-top: 15px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
        )
        result_layout = QtWidgets.QVBoxLayout(result_group)
        
        self.result_text = QtWidgets.QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(120)
        self.result_text.setPlainText("点击 '扫描文件' 开始查找可清理的文件...")
        result_layout.addWidget(self.result_text)
        
        return result_group
    
    def _create_button_layout(self) -> QtWidgets.QHBoxLayout:
        """创建按钮布局"""
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(12)
        
        self.btn_scan = QtWidgets.QPushButton("🔍 扫描文件")
        self.btn_scan.setProperty("class", "Primary")
        self.btn_scan.setMinimumHeight(40)
        self.btn_scan.clicked.connect(self._scan_files)
        
        self.btn_delete = QtWidgets.QPushButton("🗑️ 执行清理")
        self.btn_delete.setProperty("class", "Danger")
        self.btn_delete.setMinimumHeight(40)
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete_files)
        
        btn_close = QtWidgets.QPushButton("❌ 关闭")
        btn_close.setProperty("class", "Secondary")
        btn_close.setMinimumHeight(40)
        btn_close.clicked.connect(self.reject)
        
        button_layout.addWidget(self.btn_scan)
        button_layout.addWidget(self.btn_delete)
        button_layout.addStretch()
        button_layout.addWidget(btn_close)
        
        return button_layout
    
    # 事件处理方法
    
    def _choose_custom(self) -> None:
        """选择自定义文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择自定义文件夹")
        if path:
            self.edit_custom.setText(path)
    
    def _choose_monitor(self) -> None:
        """选择监控文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择监控文件夹")
        if path:
            self.edit_monitor.setText(path)
    
    def _select_all_formats(self) -> None:
        """全选所有文件格式"""
        for cb in self.format_checkboxes.values():
            cb.setChecked(True)
    
    def _select_no_formats(self) -> None:
        """取消选择所有文件格式"""
        for cb in self.format_checkboxes.values():
            cb.setChecked(False)
    
    def _select_image_formats(self) -> None:
        """仅选择图片格式"""
        image_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.raw']
        for ext, cb in self.format_checkboxes.items():
            cb.setChecked(ext in image_formats)
    
    def _on_auto_clean_toggled(self, checked: bool) -> None:
        """自动清理开关切换"""
        self.spin_threshold.setEnabled(checked)
        self.spin_keep_days.setEnabled(checked)
        self.spin_check_interval.setEnabled(checked)
    
    def _save_auto_config(self) -> None:
        """保存自动清理配置到父窗口"""
        if not self.parent_window:
            return
        
        try:
            # 更新父窗口的自动删除配置
            self.parent_window.enable_auto_delete = self.cb_enable_auto.isChecked()
            self.parent_window.auto_delete_folder = self.edit_monitor.text().strip()  # 保存监控文件夹路径
            self.parent_window.auto_delete_threshold = self.spin_threshold.value()
            self.parent_window.auto_delete_keep_days = self.spin_keep_days.value()
            self.parent_window.auto_delete_check_interval = self.spin_check_interval.value()
            
            # 保存配置到文件
            self.parent_window._save_config()
            
            # 显示成功消息
            QtWidgets.QMessageBox.information(
                self,
                "✅ 配置已保存",
                f"自动清理配置已成功保存！\n\n"
                f"启用状态: {'是' if self.cb_enable_auto.isChecked() else '否'}\n"
                f"监控文件夹: {self.edit_monitor.text().strip() or '未设置'}\n"
                f"磁盘阈值: {self.spin_threshold.value()}%\n"
                f"保留天数: {self.spin_keep_days.value()}天\n"
                f"检查间隔: {self.spin_check_interval.value()}秒"
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "❌ 保存失败",
                f"保存配置时出错：{e}"
            )
    
    def _scan_files(self) -> None:
        """扫描符合条件的文件"""
        self.files_to_delete = []
        self.result_text.clear()
        
        # 获取要扫描的文件夹（从父窗口输入框读取最新路径）
        folders_to_scan = []
        if self.cb_backup.isChecked() and self.parent_window and hasattr(self.parent_window, 'bak_edit'):
            backup_path = self.parent_window.bak_edit.text().strip()
            if backup_path:
                folders_to_scan.append(backup_path)
        if self.cb_target.isChecked() and self.parent_window and hasattr(self.parent_window, 'tgt_edit'):
            target_path = self.parent_window.tgt_edit.text().strip()
            if target_path:
                folders_to_scan.append(target_path)
        if self.cb_monitor.isChecked() and self.edit_monitor.text().strip():
            monitor_path = self.edit_monitor.text().strip()
            if monitor_path:
                folders_to_scan.append(monitor_path)
        if self.cb_custom.isChecked() and self.edit_custom.text().strip():
            folders_to_scan.append(self.edit_custom.text().strip())
        
        if not folders_to_scan:
            self.result_text.setPlainText("❌ 错误：请至少选择一个文件夹！")
            return
        
        # 获取要扫描的文件格式
        formats_to_scan = []
        for ext, cb in self.format_checkboxes.items():
            if cb.isChecked():
                formats_to_scan.append(ext.lower())
        
        # 添加自定义格式
        custom_format = self.edit_custom_format.text().strip()
        if custom_format:
            if not custom_format.startswith('.'):
                custom_format = '.' + custom_format
            formats_to_scan.append(custom_format.lower())
        
        if not formats_to_scan:
            self.result_text.setPlainText("❌ 错误：请至少选择一个文件格式！")
            return
        
        # 开始扫描
        self.result_text.appendPlainText("🔍 开始扫描...\n")
        self.result_text.appendPlainText(f"扫描目录: {len(folders_to_scan)} 个")
        self.result_text.appendPlainText(f"文件格式: {', '.join(formats_to_scan)}\n")
        
        total_size = 0
        for folder in folders_to_scan:
            if not os.path.exists(folder):
                self.result_text.appendPlainText(f"⚠️ 跳过不存在的路径: {folder}")
                continue
            
            self.result_text.appendPlainText(f"\n📁 扫描: {folder}")
            folder_count = 0
            folder_size = 0
            
            try:
                for root, dirs, files in os.walk(folder):
                    for file in files:
                        file_lower = file.lower()
                        if any(file_lower.endswith(ext) for ext in formats_to_scan):
                            file_path = os.path.join(root, file)
                            try:
                                file_size = os.path.getsize(file_path)
                                self.files_to_delete.append((file_path, file_size))
                                folder_count += 1
                                folder_size += file_size
                            except Exception as e:
                                self.result_text.appendPlainText(f"  ⚠️ 无法访问: {file} ({e})")
                
                self.result_text.appendPlainText(
                    f"  找到 {folder_count} 个文件，"
                    f"共 {folder_size / (1024*1024):.2f} MB"
                )
                total_size += folder_size
            except Exception as e:
                self.result_text.appendPlainText(f"  ❌ 扫描失败: {e}")
        
        # 显示汇总
        self.result_text.appendPlainText("\n" + "="*50)
        self.result_text.appendPlainText(
            f"📊 扫描完成！共找到 {len(self.files_to_delete)} 个文件"
        )
        self.result_text.appendPlainText(
            f"💾 总大小: {total_size / (1024*1024):.2f} MB "
            f"({total_size / (1024*1024*1024):.3f} GB)"
        )
        
        # 启用删除按钮
        self.btn_delete.setEnabled(len(self.files_to_delete) > 0)
    
    def _delete_files(self) -> None:
        """删除扫描到的文件"""
        if not self.files_to_delete:
            return
        
        # 确认对话框
        total_size = sum(size for _, size in self.files_to_delete)
        reply = QtWidgets.QMessageBox.warning(
            self,
            "⚠️ 确认删除",
            f"确定要删除 {len(self.files_to_delete)} 个文件吗？\n\n"
            f"总大小: {total_size / (1024*1024):.2f} MB\n\n"
            f"⚠️ 警告：此操作不可恢复！",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        
        # 执行删除
        self.result_text.appendPlainText("\n" + "="*50)
        self.result_text.appendPlainText("🗑️ 开始删除文件...\n")
        
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        
        for file_path, file_size in self.files_to_delete:
            try:
                os.remove(file_path)
                deleted_count += 1
                deleted_size += file_size
            except Exception as e:
                failed_count += 1
                self.result_text.appendPlainText(f"❌ 删除失败: {file_path}\n   错误: {e}")
        
        # 显示结果
        self.result_text.appendPlainText("\n" + "="*50)
        self.result_text.appendPlainText("✅ 清理完成！\n")
        self.result_text.appendPlainText(f"成功删除: {deleted_count} 个文件")
        self.result_text.appendPlainText(
            f"释放空间: {deleted_size / (1024*1024):.2f} MB "
            f"({deleted_size / (1024*1024*1024):.3f} GB)"
        )
        if failed_count > 0:
            self.result_text.appendPlainText(f"删除失败: {failed_count} 个文件")
        
        # 清空待删除列表并禁用删除按钮
        self.files_to_delete = []
        self.btn_delete.setEnabled(False)
        
        # 显示成功消息
        QtWidgets.QMessageBox.information(
            self,
            "✅ 清理完成",
            f"成功删除 {deleted_count} 个文件\n"
            f"释放空间 {deleted_size / (1024*1024):.2f} MB"
        )
