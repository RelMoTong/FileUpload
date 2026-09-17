# -*- coding: utf-8 -*-
"""Disk cleanup dialog and its presentation-only helper widgets."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from src.core.i18n import t
from src.models import (
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupScanRequest,
    normalize_cleanup_folders,
)
from src.ui.widgets import CollapsibleBox

Signal = QtCore.Signal


def tr(key: str, **kwargs: Any) -> str:
    """Translate a cleanup-view label and apply formatting values."""
    return t(key, key).format(**kwargs)


class CleanupGateway(Protocol):
    @property
    def trash_available(self) -> bool: ...
    @property
    def is_scanning(self) -> bool: ...
    @property
    def is_deleting(self) -> bool: ...
    def set_manual_listener(self, listener: Any) -> None: ...
    def validate_scan_request(self, request: CleanupScanRequest) -> Any: ...
    def start_scan(self, request: CleanupScanRequest) -> Any: ...
    def cancel_scan(self) -> None: ...
    def start_delete(self, request: CleanupDeleteRequest) -> Any: ...
    def close_manual(self) -> None: ...


class CleanupSettingsGateway(Protocol):
    @property
    def cleanup_role(self) -> str: ...
    @property
    def cleanup_settings_error(self) -> str: ...
    def cleanup_settings_snapshot(self) -> Dict[str, Any]: ...
    def save_auto_cleanup_settings(self, config: Dict[str, Any]) -> bool: ...


def calculate_dialog_responsive_metrics(
    available_width: int, available_height: int
) -> Dict[str, int]:
    """Return screen-clamped sizing values for the cleanup dialog."""
    width = max(int(available_width or 0), 800)
    height = max(int(available_height or 0), 600)
    max_width = max(760, int(width * 0.94))
    max_height = max(560, int(height * 0.9))
    return {
        "min_width": min(1100, max(760, int(width * 0.88)), max_width),
        "min_height": min(650, max(540, int(height * 0.82)), max_height),
        "initial_width": min(1300, max(800, int(width * 0.94))),
        "initial_height": min(750, max(580, int(height * 0.9))),
    }


FileItem = CleanupFileItem


class FileListTable(QtWidgets.QTableWidget):  # type: ignore[misc]
    """文件列表表格"""
    PAGE_SIZE = 1000
    check_state_changed = Signal()
    page_changed = Signal(int, int)
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.file_items: List[FileItem] = []
        self.current_page = 0
        self._rendering = False
        self._setup_table()
        self._setup_context_menu()
        self.itemChanged.connect(self._on_item_changed)
    
    def _setup_table(self) -> None:
        """设置表格"""
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["", "文件名", "路径", "大小", "修改时间"])
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        
        # 设置列宽
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 40)
        self.setColumnWidth(1, 200)
        self.setColumnWidth(3, 100)
        self.setColumnWidth(4, 150)
    
    def _setup_context_menu(self) -> None:
        """设置右键菜单"""
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
    
    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        """显示右键菜单"""
        item = self.itemAt(pos)
        if not item:
            return
        
        row = item.row()
        if row >= len(self.file_items):
            return
        
        check_item = self.item(row, 0)
        file_item = check_item.data(Qt.ItemDataRole.UserRole) if check_item else None
        if not isinstance(file_item, CleanupFileItem):
            return
        menu = QtWidgets.QMenu(self)
        
        action_open_folder = menu.addAction("打开所在文件夹")
        action_copy_path = menu.addAction("复制路径")
        menu.addSeparator()
        action_copy_name = menu.addAction("复制文件名")
        
        action = menu.exec(self.mapToGlobal(pos))
        
        if action == action_open_folder:
            self._open_file_location(file_item.path)
        elif action == action_copy_path:
            QtWidgets.QApplication.clipboard().setText(file_item.path)
        elif action == action_copy_name:
            QtWidgets.QApplication.clipboard().setText(file_item.name)
    
    def _open_file_location(self, file_path: str) -> None:
        """打开文件所在文件夹"""
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ['explorer', '/select,', file_path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
            elif platform.system() == "Darwin":  # macOS
                subprocess.run(
                    ['open', '-R', file_path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
            else:  # Linux
                folder = os.path.dirname(file_path)
                subprocess.run(
                    ['xdg-open', folder],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"无法打开文件夹：{e}")
    
    def load_files(self, file_items: List[FileItem]) -> None:
        """加载轻量记录；Qt 控件只渲染当前有界页。"""
        self.file_items = file_items
        self.current_page = 0
        self._render_page()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.file_items) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _render_page(self) -> None:
        self._rendering = True
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        start = self.current_page * self.PAGE_SIZE
        page_items = self.file_items[start : start + self.PAGE_SIZE]
        self.clearContents()
        self.setRowCount(len(page_items))
        for row, file_item in enumerate(page_items):
            check_item = QtWidgets.QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if file_item.checked else Qt.CheckState.Unchecked
            )
            check_item.setData(Qt.ItemDataRole.UserRole, file_item)
            self.setItem(row, 0, check_item)
            
            # 文件名
            self.setItem(row, 1, QtWidgets.QTableWidgetItem(file_item.name))
            
            # 路径
            self.setItem(row, 2, QtWidgets.QTableWidgetItem(file_item.path))
            
            # 大小
            size_text = self._format_size(file_item.size)
            size_item = QtWidgets.QTableWidgetItem(size_text)
            size_item.setData(Qt.ItemDataRole.UserRole, file_item.size)  # 存储原始值用于排序
            self.setItem(row, 3, size_item)
            
            # 修改时间
            mtime_text = datetime.fromtimestamp(file_item.mtime).strftime("%Y-%m-%d %H:%M:%S")
            mtime_item = QtWidgets.QTableWidgetItem(mtime_text)
            mtime_item.setData(Qt.ItemDataRole.UserRole, file_item.mtime)  # 存储原始值用于排序
            self.setItem(row, 4, mtime_item)
        self.setSortingEnabled(sorting)
        self._rendering = False
        self.page_changed.emit(self.current_page + 1, self.page_count)

    def next_page(self) -> None:
        if self.current_page + 1 < self.page_count:
            self.current_page += 1
            self._render_page()

    def previous_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._render_page()
    
    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.1f} {unit}"
            size_float /= 1024.0
        return f"{size_float:.1f} TB"
    
    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._rendering or item.column() != 0:
            return
        file_item = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(file_item, CleanupFileItem):
            file_item.checked = item.checkState() == Qt.CheckState.Checked
        self.check_state_changed.emit()
    
    def get_checked_files(self) -> List[FileItem]:
        """获取已勾选的文件"""
        return [item for item in self.file_items if item.checked]
    
    def select_all(self) -> None:
        """全选"""
        for item in self.file_items:
            item.checked = True
        self._render_page()
        self.check_state_changed.emit()
    
    def select_none(self) -> None:
        """取消全选"""
        for item in self.file_items:
            item.checked = False
        self._render_page()
        self.check_state_changed.emit()

class DiskCleanupDialog(QtWidgets.QDialog):  # type: ignore[misc]
    """文件清理对话框 - 按目录和扩展名清理文件

    本工具用于清理指定目录中的特定格式文件，不是系统级磁盘清理工具。
    支持选择文件夹路径和文件格式进行清理，可查看、筛选和确认删除文件。
    整合自动清理配置功能。
    
    Args:
        parent: 仅用于 Qt 窗口所有权
    
    Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
    """
    
    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        cleanup_controller: Optional[CleanupGateway] = None,
        settings_gateway: Optional[CleanupSettingsGateway] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("文件清理工具 - 按目录和扩展名清理")
        self.setModal(True)

        self.settings_gateway = settings_gateway
        self._settings = self._read_settings_snapshot()
        if cleanup_controller is None:
            raise ValueError("DiskCleanupDialog requires a cleanup controller")
        self.cleanup_controller: CleanupGateway = cleanup_controller
        self.cleanup_controller.set_manual_listener(self._handle_cleanup_event)

        self.all_files: List[FileItem] = []
        self._scanned_folders: Tuple[str, ...] = ()
        self._hidden_auto_cleanup_folders: List[str] = []
        self._folder_rows: List[
            Tuple[
                QtWidgets.QCheckBox,
                QtWidgets.QLineEdit,
                QtWidgets.QPushButton,
                QtWidgets.QPushButton,
                QtWidgets.QPushButton,
            ]
        ] = []
        self.trash_available = self.cleanup_controller.trash_available

        self._advanced_tab_created = False
        self.tab_widget: QtWidgets.QTabWidget

        self._build_ui()
        self._apply_permission_state()
        if not self.trash_available:
            self._append_log_line("回收站不可用，删除会被拒绝；可手动选择永久删除并二次确认。")

    def _read_settings_snapshot(self) -> Dict[str, Any]:
        if self.settings_gateway is None:
            return {}
        try:
            snapshot = self.settings_gateway.cleanup_settings_snapshot()
            return dict(snapshot) if isinstance(snapshot, dict) else {}
        except Exception:
            return {}

    def _can_manage_cleanup(self) -> bool:
        """当前窗口是否允许执行扫描、删除和保存清理配置。"""
        role = self.settings_gateway.cleanup_role if self.settings_gateway else "guest"
        return role == "admin"

    def _get_cleanup_block_reason(self) -> str:
        """返回当前不可操作时的阻止原因。"""
        role = self.settings_gateway.cleanup_role if self.settings_gateway else "guest"
        if role == "guest":
            return "请先登录后再使用磁盘清理功能。"
        if role == "user":
            return "普通用户无权限使用磁盘清理，请切换管理员登录。"
        return ""

    def _ensure_cleanup_permission(self, action: str) -> bool:
        """执行危险操作前做权限校验。"""
        if self._can_manage_cleanup():
            return True
        reason = self._get_cleanup_block_reason() or "当前状态不允许执行该操作。"
        self._append_log_line(f"{action}已阻止：{reason}")
        QtWidgets.QMessageBox.warning(self, "权限不足", reason)
        return False

    def _apply_permission_state(self) -> None:
        """根据主窗口角色和运行状态更新对话框操作权限。"""
        can_manage = self._can_manage_cleanup()
        editable_names = [
            "cb_backup", "cb_target", "cb_monitor", "cb_custom",
            "btn_scan", "btn_auto_config", "btn_delete_dropdown",
            "cb_enable_auto", "btn_save_auto",
        ]
        for name in editable_names:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(can_manage)

        for row in self._folder_rows:
            self._apply_folder_row_state(*row, can_manage=can_manage)

        if hasattr(self, "spin_threshold"):
            auto_enabled = bool(getattr(self, "cb_enable_auto", None) and self.cb_enable_auto.isChecked())
            self.spin_threshold.setEnabled(can_manage and auto_enabled)
        if hasattr(self, "spin_target"):
            auto_enabled = bool(getattr(self, "cb_enable_auto", None) and self.cb_enable_auto.isChecked())
            self.spin_target.setEnabled(can_manage and auto_enabled)
        if hasattr(self, "spin_check_interval"):
            auto_enabled = bool(getattr(self, "cb_enable_auto", None) and self.cb_enable_auto.isChecked())
            self.spin_check_interval.setEnabled(can_manage and auto_enabled)

        for action_name in ("action_trash", "action_permanent"):
            action = getattr(self, action_name, None)
            if action is not None:
                action.setEnabled(can_manage and (action_name != "action_trash" or self.trash_available))

        if hasattr(self, "btn_delete"):
            self.btn_delete.setEnabled(can_manage and bool(self.file_table.get_checked_files()))

        if hasattr(self, "progress_label") and not can_manage:
            self.progress_label.setText(self._get_cleanup_block_reason())

    def _on_file_check_changed(self) -> None:
        """v3.3.0：复选框状态变化时刷新删除按钮"""
        if hasattr(self, 'btn_delete'):
            can_manage = self._can_manage_cleanup()
            self.btn_delete.setEnabled(can_manage and bool(self.file_table.get_checked_files()))

    @staticmethod
    def _format_folder_summary(folders: List[str]) -> str:
        """格式化目录摘要文本。"""
        if not folders:
            return "未设置"
        auto_path_text = "；".join(folders[:2])
        if len(folders) > 2:
            auto_path_text = f"{auto_path_text}..."
        return auto_path_text

    def _get_parent_auto_cleanup_folders(self) -> List[str]:
        """读取当前生效的自动清理目录。"""
        folders = self._get_saved_auto_cleanup_folders()
        if not folders:
            legacy_path = str(self._settings.get("auto_delete_folder", "") or "").strip()
            if legacy_path:
                folders = [legacy_path]
        return folders

    def _refresh_auto_cleanup_card_from_parent(self) -> None:
        """仅根据父窗口已保存状态刷新自动清理摘要。"""
        if hasattr(self, "auto_status_label"):
            self._settings = self._read_settings_snapshot()
            auto_enabled = bool(self._settings.get("enable_auto_delete", False))
            self._update_auto_cleanup_status_summary(auto_enabled)
        if hasattr(self, "auto_path_label"):
            self.auto_path_label.setText(
                f"清理路径: {self._format_folder_summary(self._get_parent_auto_cleanup_folders())}"
            )

    def _apply_unified_stylesheet(self) -> None:
        """应用统一的样式表"""
        stylesheet = """
            QWidget{font-family:'Microsoft YaHei UI', 'Segoe UI'; font-size:11pt; color:#1F2937; background:#E3F2FD;}
            QDialog{background:#E3F2FD;}

            /* 标题层级 */
            QLabel[class="title"]{color:#1976D2; font-weight:800; font-size:14pt;}
            QLabel[class="subtitle"]{color:#6B7280; font-size:10pt; margin-left:10px;}
            QLabel[class="section-title"]{color:#1976D2; font-weight:800; font-size:11pt;}
            QLabel[class="hint"]{color:#757575; font-size:9pt;}

            /* 警告横幅 */
            QLabel[class="warning-banner"]{background:#FFEBEE; color:#B91C1C; padding:10px 12px; font-weight:800; border-radius:10px; border:1px solid #FCA5A5;}
            QLabel[class="info-banner"]{background:#E3F2FD; color:#0D47A1; padding:10px 12px; font-weight:700; border-radius:10px; border:1px solid #90CAF9;}

            /* 卡片样式（蓝色粗边框） */
            QFrame[class="card"]{background:#FFFFFF; border:2px solid #64B5F6; border-radius:10px; padding:10px;}
            QFrame[class="card"][kind="info"]{background:#E3F2FD; border:2px solid #64B5F6;}

            /* Tab */
            QTabWidget::pane{border:2px solid #64B5F6; border-radius:10px; background:#FFFFFF;}
            QTabBar::tab{padding:8px 16px; color:#1F2937;}
            QTabBar::tab:selected{background:#E3F2FD; font-weight:800; border:2px solid #64B5F6; border-bottom:0px; border-top-left-radius:10px; border-top-right-radius:10px;}

            /* 输入控件 */
            QLineEdit{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:6px; padding:6px 8px;}
            QLineEdit:read-only{background:#F3F4F6; color:#6B7280; border:1px solid #D1D5DB;}
            QSpinBox, QComboBox{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:6px; padding:6px 8px;}
            QSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled{background:#F3F4F6; color:#9CA3AF; border:1px solid #D1D5DB;}

            /* 复选框（大尺寸+蓝色描边） */
            QCheckBox{color:#1F2937; spacing:8px;}
            QCheckBox[folderSelector="true"][folderActive="false"]{color:#9CA3AF;}
            QCheckBox[folderSelector="true"][folderActive="true"]{color:#1F2937;}
            QCheckBox:disabled{color:#9CA3AF;}
            QCheckBox::indicator{width:22px; height:22px; background:#FFFFFF; border:2px solid #64B5F6; border-radius:4px;}
            QCheckBox::indicator:disabled{background:#F3F4F6; border:2px solid #D1D5DB;}
            QCheckBox::indicator:checked{background:qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1976D2, stop:1 #2196F3); border:2px solid #1976D2;}
            QCheckBox::indicator:checked:disabled{background:#E0E0E0; border:2px solid #D1D5DB;}

            /* 表格 */
            QTableWidget{background:#FFFFFF; border:2px solid #64B5F6; border-radius:10px; gridline-color:#E5EAF0;}
            QHeaderView::section{background:#F1F5F9; color:#1F2937; border:none; border-bottom:2px solid #64B5F6; padding:8px 10px; font-weight:800;}
            QTableWidget::item:selected{background:#E3F2FD; color:#1F2937;}

            /* 进度条 */
            QProgressBar{border:1px solid #64B5F6; border-radius:6px; background:#EEF2F5; text-align:center; color:#1F2937;}
            QProgressBar::chunk{border-radius:6px; background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4FACFE, stop:1 #00F2FE);}

            /* 按钮 */
            QPushButton{font-size:11pt;}
            QPushButton:disabled{background:#E5E7EB; color:#9CA3AF; border:1px solid #D1D5DB;}
            QPushButton[class="Primary"]{background:#1976D2; color:#FFFFFF; border:none; border-radius:8px; padding:8px 12px; font-weight:800;}
            QPushButton[class="Primary"]:hover{background:#1E88E5;}
            QPushButton[class="Secondary"]{background:#F1F5F9; color:#0F172A; border:1px solid #64B5F6; border-radius:8px; padding:6px 10px;}
            QPushButton[class="Secondary"]:hover{background:#E3F2FD;}
            QPushButton[class="Danger"]{background:#FEE2E2; color:#B91C1C; border:1px solid #FCA5A5; border-radius:8px; padding:6px 10px; font-weight:800;}
            QPushButton[class="Danger"]:hover{background:#FECACA;}
            QPushButton[class="Danger"][split="left"]{border-top-right-radius:0px; border-bottom-right-radius:0px;}
            QPushButton[class="Danger"][split="right"]{border-top-left-radius:0px; border-bottom-left-radius:0px; padding:6px 8px;}

            /* 小型工具按钮（目录行：浏览/打开/复制） */
            QPushButton[variant="tool"]{background:#F1F5F9; color:#0F172A; border:1px solid #64B5F6; border-radius:8px; padding:4px 10px;}
            QPushButton[variant="tool"]:hover{background:#E3F2FD;}
            QPushButton[hasMenu="true"]::menu-indicator{image:none; width:0px;}

            /* Chips（快捷筛选） */
            QPushButton[chip="true"]{background:#FFFFFF; color:#0F172A; border:1px solid #64B5F6; border-radius:999px; padding:4px 10px;}
            QPushButton[chip="true"]:hover{background:#E3F2FD;}
            QPushButton[chip="true"]:checked{background:#E3F2FD; color:#1976D2; font-weight:800; border:2px solid #64B5F6;}

            /* 菜单/提示 */
            QMenu{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:8px; padding:6px;}
            QMenu::item{padding:6px 20px; border-radius:6px;}
            QMenu::item:selected{background:#E3F2FD; color:#1976D2;}
            QToolTip{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:8px; padding:6px 8px;}

            /* 滚动条样式 */
            QScrollBar:vertical{background:#E3F2FD; width:12px; border-radius:6px; margin:0px;}
            QScrollBar::handle:vertical{background:#90CAF9; border-radius:6px; min-height:30px;}
            QScrollBar::handle:vertical:hover{background:#64B5F6;}
            QScrollBar::handle:vertical:pressed{background:#42A5F5;}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{height:0px;}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical{background:transparent;}

            QScrollBar:horizontal{background:#E3F2FD; height:12px; border-radius:6px; margin:0px;}
            QScrollBar::handle:horizontal{background:#90CAF9; border-radius:6px; min-width:30px;}
            QScrollBar::handle:horizontal:hover{background:#64B5F6;}
            QScrollBar::handle:horizontal:pressed{background:#42A5F5;}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal{width:0px;}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal{background:transparent;}
        """
        self.setStyleSheet(stylesheet)
        

    def _build_ui(self) -> None:
        """构建 UI - 左右分割布局"""
        # 应用统一样式表
        self._apply_unified_stylesheet()
        
        # 设置可调整大小的窗口，小屏现场电脑不超过可用屏幕。
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            metrics = calculate_dialog_responsive_metrics(available.width(), available.height())
        else:
            metrics = calculate_dialog_responsive_metrics(1366, 768)
        self.responsive_metrics = metrics
        self.setMinimumSize(metrics["min_width"], metrics["min_height"])
        self.resize(metrics["initial_width"], metrics["initial_height"])
        
        # 主布局
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 标题说明
        title_layout = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel("文件清理工具")
        title_label.setProperty("class", "title")
        title_layout.addWidget(title_label)
        
        subtitle_label = QtWidgets.QLabel("按目录和扩展名清理文件")
        subtitle_label.setProperty("class", "subtitle")
        title_layout.addWidget(subtitle_label)
        title_layout.addStretch()
        main_layout.addLayout(title_layout)

        info_label = QtWidgets.QLabel("说明：本工具仅按目录与扩展名清理文件，不是系统级磁盘清理。请确认路径与删除模式后再操作。")
        info_label.setProperty("class", "info-banner")
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        # 回收站提示（如果不可用）
        if not self.trash_available:
            warning_label = QtWidgets.QLabel("警告：回收站不可用；默认不删除，永久删除需手动选择并二次确认。")
            warning_label.setProperty("class", "warning-banner")
            main_layout.addWidget(warning_label)
        
        # 使用 QSplitter 左右分隔设置区和结果区
        splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：扫描设置区（可滚动）
        settings_widget = self._create_settings_area()
        splitter.addWidget(settings_widget)
        
        # 右侧：结果区
        results_widget = self._create_results_area()
        splitter.addWidget(results_widget)
        
        # 设置分割比例（设置:结果 = 2:3）
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        
        main_layout.addWidget(splitter)
        
        # 底部按钮
        button_layout = self._create_button_layout()
        main_layout.addLayout(button_layout)

    def _append_log_line(self, text: str) -> None:
        if hasattr(self, 'log_view'):
            self.log_view.appendPlainText(text.rstrip())

    def _clear_log(self) -> None:
        if hasattr(self, 'log_view'):
            self.log_view.clear()
    
    def _create_settings_area(self) -> QtWidgets.QWidget:
        """创建设置区域（左侧，使用Tab分隔基础/高级）"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 使用 TabWidget 分隔基础和高级设置
        self.tab_widget = QtWidgets.QTabWidget()
        
        # 基础设置Tab
        basic_tab = self._create_basic_settings_tab()
        self.tab_widget.addTab(basic_tab, "基础设置")
        
        # 高级设置Tab - 延迟加载，先放占位页
        placeholder = QtWidgets.QLabel("高级设置将在首次打开时加载")
        placeholder.setProperty("class", "hint")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tab_widget.addTab(placeholder, "高级设置")
        
        # 连接Tab切换信号
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        
        layout.addWidget(self.tab_widget)
        
        # 底部扫描按钮（固定在左侧底部）
        scan_layout = QtWidgets.QHBoxLayout()
        self.btn_scan = QtWidgets.QPushButton("开始扫描")
        self.btn_scan.setProperty("class", "Primary")
        self.btn_scan.setMinimumHeight(40)
        self.btn_scan.clicked.connect(self._scan_files)
        scan_layout.addWidget(self.btn_scan)
        layout.addLayout(scan_layout)
        
        return widget
    
    def _create_basic_settings_tab(self) -> QtWidgets.QWidget:
        """创建基础设置Tab（文件夹+格式预设）"""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 文件夹选择区域
        folder_group = self._create_folder_selection_group()
        layout.addWidget(folder_group)
        
        # 文件格式预设区域
        format_group = self._create_format_selection_group()
        layout.addWidget(format_group)
        
        layout.addStretch()
        scroll.setWidget(content)
        return scroll
    
    def _create_advanced_settings_tab(self) -> QtWidgets.QWidget:
        """创建高级设置Tab（过滤+自动清理）"""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 过滤条件区域
        filter_group = self._create_filter_group()
        layout.addWidget(filter_group)
        
        # 自定义格式
        custom_format_group = self._create_custom_format_group()
        layout.addWidget(custom_format_group)
        
        # 自动清理配置 - 简化为按钮
        auto_card = self._create_auto_cleanup_card()
        layout.addWidget(auto_card)
        
        layout.addStretch()
        scroll.setWidget(content)
        return scroll
    
    def _create_custom_format_group(self) -> QtWidgets.QFrame:
        """创建自定义格式区域"""
        group = QtWidgets.QFrame()
        group.setProperty("class", "card")
        layout = QtWidgets.QVBoxLayout(group)
        
        title_label = QtWidgets.QLabel("自定义扩展名")
        title_label.setProperty("class", "section-title")
        layout.addWidget(title_label)
        
        hint_label = QtWidgets.QLabel("输入额外的文件扩展名（逗号分隔）")
        hint_label.setProperty("class", "hint")
        hint_label.setToolTip("例如: .bak, .cache, .pyc")
        layout.addWidget(hint_label)
        
        self.edit_custom_format = QtWidgets.QLineEdit()
        self.edit_custom_format.setPlaceholderText("例如: .bak, .cache, .pyc")
        layout.addWidget(self.edit_custom_format)
        
        return group
    
    def _create_results_area(self) -> QtWidgets.QWidget:
        """创建结果区域 - 带摘要条和快捷筛选"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 摘要条
        summary_frame = QtWidgets.QFrame()
        summary_frame.setProperty("class", "card")
        summary_frame.setProperty("kind", "info")
        summary_layout = QtWidgets.QVBoxLayout(summary_frame)
        summary_layout.setSpacing(4)
        summary_layout.setContentsMargins(8, 8, 8, 8)
        
        self.summary_label = QtWidgets.QLabel("扫描条件：未设置")
        self.summary_label.setProperty("class", "hint")
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)
        
        layout.addWidget(summary_frame)
        
        # 标题和进度行
        header_layout = QtWidgets.QHBoxLayout()
        result_title = QtWidgets.QLabel("扫描结果")
        result_title.setProperty("class", "section-title")
        header_layout.addWidget(result_title)
        header_layout.addStretch()
        
        # 进度条（扫描时显示）
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setMaximumHeight(20)
        self.progress_bar.setVisible(False)
        header_layout.addWidget(self.progress_bar)
        
        # 取消扫描按钮
        self.btn_cancel_scan = QtWidgets.QPushButton("取消")
        self.btn_cancel_scan.setProperty("class", "Secondary")
        self.btn_cancel_scan.setMaximumWidth(70)
        self.btn_cancel_scan.setVisible(False)
        self.btn_cancel_scan.clicked.connect(self._cancel_scan)
        header_layout.addWidget(self.btn_cancel_scan)
        
        layout.addLayout(header_layout)
        
        # 进度标签
        self.progress_label = QtWidgets.QLabel("等待扫描…")
        self.progress_label.setProperty("class", "hint")
        layout.addWidget(self.progress_label)

        # 诊断日志
        log_header = QtWidgets.QHBoxLayout()
        log_title = QtWidgets.QLabel("诊断日志")
        log_title.setProperty("class", "section-title")
        log_header.addWidget(log_title)
        log_header.addStretch()
        btn_clear_log = QtWidgets.QPushButton("清空日志")
        btn_clear_log.setProperty("class", "Secondary")
        btn_clear_log.setMaximumWidth(90)
        btn_clear_log.clicked.connect(self._clear_log)
        log_header.addWidget(btn_clear_log)
        layout.addLayout(log_header)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setMaximumHeight(140)
        layout.addWidget(self.log_view)
        
        # 快捷筛选条（chips）
        filter_chip_layout = QtWidgets.QHBoxLayout()
        filter_chip_layout.setSpacing(5)
        
        chip_label = QtWidgets.QLabel("快捷筛选:")
        chip_label.setProperty("class", "hint")
        filter_chip_layout.addWidget(chip_label)
        
        self.chip_show_checked = QtWidgets.QPushButton("仅已选")
        self.chip_show_checked.setCheckable(True)
        self.chip_show_checked.setProperty("chip", True)
        self.chip_show_checked.clicked.connect(self._apply_quick_filters)
        filter_chip_layout.addWidget(self.chip_show_checked)
        
        self.chip_show_large = QtWidgets.QPushButton("大文件(>10MB)")
        self.chip_show_large.setCheckable(True)
        self.chip_show_large.setProperty("chip", True)
        self.chip_show_large.clicked.connect(self._apply_quick_filters)
        filter_chip_layout.addWidget(self.chip_show_large)
        
        self.chip_show_recent = QtWidgets.QPushButton("最近7天")
        self.chip_show_recent.setCheckable(True)
        self.chip_show_recent.setProperty("chip", True)
        self.chip_show_recent.clicked.connect(self._apply_quick_filters)
        filter_chip_layout.addWidget(self.chip_show_recent)
        
        filter_chip_layout.addStretch()
        layout.addLayout(filter_chip_layout)
        
        # 搜索框
        search_layout = QtWidgets.QHBoxLayout()
        search_label = QtWidgets.QLabel("搜索:")
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("输入文件名或路径...")
        self.search_edit.textChanged.connect(self._filter_files)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_edit, 1)
        layout.addLayout(search_layout)
        
        # 文件列表表格
        self.file_table = FileListTable()
        # v3.3.0：复选框变化时刷新删除按钮状态
        self.file_table.check_state_changed.connect(self._on_file_check_changed)
        self.file_table.page_changed.connect(self._on_file_page_changed)
        layout.addWidget(self.file_table)
        
        # 表格操作按钮和统计
        table_actions_layout = QtWidgets.QHBoxLayout()
        btn_select_all = QtWidgets.QPushButton("全选")
        btn_select_all.setProperty("class", "Secondary")
        btn_select_all.clicked.connect(self.file_table.select_all)
        btn_select_none = QtWidgets.QPushButton("取消全选")
        btn_select_none.setProperty("class", "Secondary")
        btn_select_none.clicked.connect(self.file_table.select_none)
        table_actions_layout.addWidget(btn_select_all)
        table_actions_layout.addWidget(btn_select_none)
        self.btn_previous_page = QtWidgets.QPushButton("上一页")
        self.btn_previous_page.setProperty("class", "Secondary")
        self.btn_previous_page.clicked.connect(self.file_table.previous_page)
        self.btn_next_page = QtWidgets.QPushButton("下一页")
        self.btn_next_page.setProperty("class", "Secondary")
        self.btn_next_page.clicked.connect(self.file_table.next_page)
        self.page_label = QtWidgets.QLabel("1 / 1")
        table_actions_layout.addWidget(self.btn_previous_page)
        table_actions_layout.addWidget(self.page_label)
        table_actions_layout.addWidget(self.btn_next_page)
        table_actions_layout.addStretch()
        
        # 统计信息
        self.stats_label = QtWidgets.QLabel("未扫描")
        self.stats_label.setProperty("class", "hint")
        table_actions_layout.addWidget(self.stats_label)
        
        layout.addLayout(table_actions_layout)
        
        return widget

    def _on_file_page_changed(self, page: int, total: int) -> None:
        self.page_label.setText(f"{page} / {total}")
        self.btn_previous_page.setEnabled(page > 1)
        self.btn_next_page.setEnabled(page < total)
        self._filter_files()
    
    def _apply_quick_filters(self) -> None:
        """应用快捷筛选"""
        import time
        cutoff_time_7days = time.time() - (7 * 24 * 3600)
        size_threshold = 10 * 1024 * 1024  # 10MB
        
        show_checked_only = self.chip_show_checked.isChecked()
        show_large_only = self.chip_show_large.isChecked()
        show_recent_only = self.chip_show_recent.isChecked()
        
        for row in range(self.file_table.rowCount()):
            check_item = self.file_table.item(row, 0)
            file_item = (
                check_item.data(Qt.ItemDataRole.UserRole) if check_item else None
            )
            if not isinstance(file_item, CleanupFileItem):
                continue
            show = True
            
            # 检查已选筛选
            if show_checked_only and not file_item.checked:
                show = False
            
            # 检查大文件筛选
            if show_large_only and file_item.size < size_threshold:
                show = False
            
            # 检查最近7天筛选
            if show_recent_only and file_item.mtime < cutoff_time_7days:
                show = False
            
            self.file_table.setRowHidden(row, not show)
    
    def _update_summary(self) -> None:
        """更新摘要条"""
        folders = []
        if hasattr(self, 'cb_backup') and self.cb_backup.isChecked():
            folders.append("备份目录")
        if hasattr(self, 'cb_target') and self.cb_target.isChecked():
            folders.append("目标目录")
        if hasattr(self, 'cb_monitor') and self.cb_monitor.isChecked():
            folders.append("监控目录")
        if hasattr(self, 'cb_custom') and self.cb_custom.isChecked():
            folders.append("自定义目录")
        
        # 统计格式
        format_count = 0
        if hasattr(self, 'format_checkboxes'):
            format_count = sum(1 for cb in self.format_checkboxes.values() if cb.isChecked())
        
        # 过滤条件
        filter_text = ""
        if hasattr(self, 'cb_filter_days') and self.cb_filter_days.isChecked():
            filter_text = f"，仅 {self.spin_filter_days.value()} 天前"
        
        summary_text = f"扫描条件：{len(folders)} 个目录 | {format_count} 种格式{filter_text}"
        if folders:
            summary_text += f" | 目录：{', '.join(folders)}"
        
        self.summary_label.setText(summary_text)
    
    def _create_filter_group(self) -> QtWidgets.QFrame:
        """创建过滤条件区域"""
        filter_group = QtWidgets.QFrame()
        filter_group.setProperty("class", "card")
        filter_layout = QtWidgets.QVBoxLayout(filter_group)
        filter_layout.setSpacing(10)
        
        # 标题
        title_label = QtWidgets.QLabel("过滤条件")
        title_label.setProperty("class", "section-title")
        filter_layout.addWidget(title_label)
        
        # 保留天数过滤
        days_row = QtWidgets.QHBoxLayout()
        self.cb_filter_days = QtWidgets.QCheckBox("仅显示/删除")
        self.cb_filter_days.setToolTip("勾选后仅扫描指定天数前修改的文件")
        self.cb_filter_days.toggled.connect(self._on_filter_days_toggled)
        self.spin_filter_days = QtWidgets.QSpinBox()
        self.spin_filter_days.setRange(1, 365)
        self.spin_filter_days.setValue(10)
        self.spin_filter_days.setSuffix(" 天前的文件")
        self.spin_filter_days.setEnabled(False)
        days_row.addWidget(self.cb_filter_days)
        days_row.addWidget(self.spin_filter_days)
        days_row.addStretch()
        filter_layout.addLayout(days_row)
        
        return filter_group
    
    def _on_tab_changed(self, index: int) -> None:
        """Tab切换处理 - 延迟构建高级页 + 性能优化"""
        # 如果切换到高级页且未创建，则创建
        if index == 1 and not self._advanced_tab_created:
            # 禁用更新减少重排
            self.tab_widget.setUpdatesEnabled(False)
            
            try:
                # 创建高级设置页
                advanced_tab = self._create_advanced_settings_tab()
                self.tab_widget.removeTab(1)  # 移除占位页
                self.tab_widget.insertTab(1, advanced_tab, "高级设置")
                self._advanced_tab_created = True
            finally:
                # 延迟恢复更新，避免抖动
                QtCore.QTimer.singleShot(0, lambda w=self.tab_widget: w.setUpdatesEnabled(True))
    
    def _on_filter_days_toggled(self, checked: bool) -> None:
        """过滤天数复选框切换"""
        self.spin_filter_days.setEnabled(checked)
    
    def _create_folder_selection_group(self) -> QtWidgets.QFrame:
        """创建文件夹选择区域 - 卡片样式"""
        folder_group = QtWidgets.QFrame()
        folder_group.setProperty("class", "card")
        folder_layout = QtWidgets.QVBoxLayout(folder_group)
        folder_layout.setSpacing(8)
        
        # 标题
        title_label = QtWidgets.QLabel("扫描目录")
        title_label.setProperty("class", "section-title")
        folder_layout.addWidget(title_label)
        
        # 从显式设置网关的快照读取路径，不访问父窗口控件。
        backup_path = str(self._settings.get('backup_path', '') or '').strip()
        target_path = str(self._settings.get('target_path', '') or '').strip()
        auto_folders = self._get_saved_auto_cleanup_folders()
        auto_folder_set = set(auto_folders)
        known_paths = {backup_path, target_path}
        extra_paths = [path for path in auto_folders if path not in known_paths]

        monitor_path = ""
        legacy_monitor_path = ""
        legacy_monitor_path = str(
            self._settings.get('auto_delete_folder', '') or ''
        ).strip()

        if legacy_monitor_path and legacy_monitor_path not in known_paths:
            if legacy_monitor_path in extra_paths:
                monitor_path = legacy_monitor_path
                extra_paths = [path for path in extra_paths if path != legacy_monitor_path]
            elif not auto_folders:
                monitor_path = legacy_monitor_path

        if not monitor_path and extra_paths:
            monitor_path = extra_paths.pop(0)

        custom_path = extra_paths[0] if extra_paths else ""
        self._hidden_auto_cleanup_folders = extra_paths[1:] if len(extra_paths) > 1 else []
        backup_checked = backup_path in auto_folder_set if auto_folders else bool(backup_path)
        target_checked = target_path in auto_folder_set if auto_folders else bool(target_path)
        monitor_checked = monitor_path in auto_folder_set if auto_folders else bool(monitor_path)
        custom_checked = custom_path in auto_folder_set if auto_folders else bool(custom_path)
        
        # 备份文件夹行
        self.cb_backup, self.edit_backup, backup_btns = self._create_folder_row("备份目录", backup_path, backup_checked)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_backup, self.edit_backup, backup_btns))
        self.btn_backup_browse = backup_btns[0]
        self.btn_backup_open = backup_btns[1]
        self.btn_backup_copy = backup_btns[2]

        # 目标文件夹行
        self.cb_target, self.edit_target, target_btns = self._create_folder_row("目标目录", target_path, target_checked)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_target, self.edit_target, target_btns))
        self.btn_target_browse = target_btns[0]
        self.btn_target_open = target_btns[1]
        self.btn_target_copy = target_btns[2]

        # 监控文件夹行
        self.cb_monitor, self.edit_monitor, monitor_btns = self._create_folder_row("监控目录", monitor_path, monitor_checked)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_monitor, self.edit_monitor, monitor_btns))
        self.btn_monitor_browse = monitor_btns[0]
        self.btn_monitor_open = monitor_btns[1]
        self.btn_monitor_copy = monitor_btns[2]
        
        # 自定义文件夹行
        self.cb_custom, self.edit_custom, custom_btns = self._create_folder_row("自定义目录", custom_path, custom_checked)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_custom, self.edit_custom, custom_btns))
        self.btn_custom_browse = custom_btns[0]
        self.btn_custom_open = custom_btns[1]
        self.btn_custom_copy = custom_btns[2]

        if self._hidden_auto_cleanup_folders:
            hidden_hint = QtWidgets.QLabel(
                f"另有 {len(self._hidden_auto_cleanup_folders)} 个自动清理目录已保留；当前窗口不会覆盖这些隐藏目录。"
            )
            hidden_hint.setProperty("class", "hint")
            hidden_hint.setWordWrap(True)
            folder_layout.addWidget(hidden_hint)
        
        return folder_group

    def _get_saved_auto_cleanup_folders(self) -> List[str]:
        """读取新版多目录自动清理配置。"""
        raw_folders = self._settings.get("auto_delete_folders", [])
        return normalize_cleanup_folders(
            raw_folders if isinstance(raw_folders, list) else []
        )

    def _collect_selected_folders(self, include_hidden: bool = False) -> List[str]:
        """收集当前勾选的目录，保持顺序并去重。"""
        folders_to_clean: List[str] = []
        folder_rows = [
            ("cb_backup", "edit_backup"),
            ("cb_target", "edit_target"),
            ("cb_monitor", "edit_monitor"),
            ("cb_custom", "edit_custom"),
        ]
        for cb_name, edit_name in folder_rows:
            cb = getattr(self, cb_name, None)
            edit = getattr(self, edit_name, None)
            if cb is None or edit is None or not cb.isChecked():
                continue
            path = edit.text().strip()
            if path and path not in folders_to_clean:
                folders_to_clean.append(path)
        if include_hidden:
            for path in self._hidden_auto_cleanup_folders:
                cleaned = path.strip()
                if cleaned and cleaned not in folders_to_clean:
                    folders_to_clean.append(cleaned)
        return normalize_cleanup_folders(folders_to_clean)

    def _update_folder_action_buttons(
        self,
        cb: QtWidgets.QCheckBox,
        edit: QtWidgets.QLineEdit,
        btn_open: QtWidgets.QPushButton,
        btn_copy: QtWidgets.QPushButton,
    ) -> None:
        """根据勾选和路径内容刷新操作按钮状态。"""
        enabled = cb.isChecked() and bool(edit.text().strip())
        btn_open.setEnabled(enabled)
        btn_copy.setEnabled(enabled)

    def _update_auto_cleanup_path_summary(self, folders_to_clean: Optional[List[str]] = None) -> None:
        """更新自动清理卡片上的路径摘要。"""
        if not hasattr(self, 'auto_path_label'):
            return

        if folders_to_clean is None:
            folders_to_clean = self._collect_selected_folders()
        self.auto_path_label.setText(f"清理路径: {self._format_folder_summary(folders_to_clean)}")

    def _update_auto_cleanup_status_summary(self, enabled: bool) -> None:
        """更新自动清理卡片上的启用状态摘要。"""
        if hasattr(self, 'auto_status_label'):
            status_text = "已启用" if enabled else "未启用"
            self.auto_status_label.setText(f"当前状态: {status_text}")
    
    def _create_folder_row(self, label: str, path: str, checked: bool) -> Tuple[QtWidgets.QCheckBox, QtWidgets.QLineEdit, List[QtWidgets.QPushButton]]:
        """创建单个文件夹选择行的组件"""
        # 复选框 - 始终可用，让用户自行选择是否启用该目录
        cb = QtWidgets.QCheckBox(label)
        cb.setProperty("folderSelector", True)
        cb.setChecked(bool(checked))
        
        # 路径输入框（可直接输入也可浏览选择）
        edit = QtWidgets.QLineEdit(path)
        edit.setProperty("folderSelector", True)
        edit.setPlaceholderText(f"选择{label}或直接输入路径...")
        edit.editingFinished.connect(self._sync_auto_cleanup_folders)
        
        # 按钮组：浏览、打开、复制
        btn_browse = QtWidgets.QPushButton("...")
        btn_browse.setToolTip("浏览选择")
        btn_browse.setMaximumWidth(40)
        btn_browse.setProperty("variant", "tool")
        btn_browse.clicked.connect(lambda: self._browse_folder(edit))
        
        btn_open = QtWidgets.QPushButton("打开")
        btn_open.setToolTip("在文件管理器中打开")
        btn_open.setMaximumWidth(50)
        btn_open.setProperty("variant", "tool")
        btn_open.clicked.connect(lambda: self._open_folder_in_explorer(edit.text()))
        
        btn_copy = QtWidgets.QPushButton("复制")
        btn_copy.setToolTip("复制路径到剪贴板")
        btn_copy.setMaximumWidth(50)
        btn_copy.setProperty("variant", "tool")
        btn_copy.clicked.connect(lambda: self._copy_path(edit.text()))
        edit.textChanged.connect(
            lambda _text, checkbox=cb, line_edit=edit, open_btn=btn_open, copy_btn=btn_copy:
            self._update_folder_action_buttons(checkbox, line_edit, open_btn, copy_btn)
        )

        row = (cb, edit, btn_browse, btn_open, btn_copy)
        self._folder_rows.append(row)
        self._apply_folder_row_state(*row, can_manage=True)
        cb.toggled.connect(
            lambda _checked, folder_row=row: self._on_folder_toggled(*folder_row)
        )
        
        return cb, edit, [btn_browse, btn_open, btn_copy]
    
    def _create_folder_form_row(self, cb: QtWidgets.QCheckBox, edit: QtWidgets.QLineEdit, buttons: List[QtWidgets.QPushButton]) -> QtWidgets.QHBoxLayout:
        """创建表单行布局"""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(5)
        cb.setMinimumWidth(80)
        row.addWidget(cb)
        row.addWidget(edit, 1)
        for btn in buttons:
            row.addWidget(btn)
        return row
    
    @staticmethod
    def _refresh_folder_widget_style(widget: QtWidgets.QWidget) -> None:
        """让动态属性变化立即反映到 Qt 样式表。"""
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _apply_folder_row_state(
        self,
        cb: QtWidgets.QCheckBox,
        edit: QtWidgets.QLineEdit,
        btn_browse: QtWidgets.QPushButton,
        btn_open: QtWidgets.QPushButton,
        btn_copy: QtWidgets.QPushButton,
        *,
        can_manage: Optional[bool] = None,
    ) -> None:
        """统一路径行的明暗样式和可操作状态。"""
        checked = cb.isChecked()
        if can_manage is None:
            can_manage = cb.isEnabled()
        active = bool(can_manage and checked)
        for widget in (cb, edit):
            widget.setProperty("folderActive", checked)
            self._refresh_folder_widget_style(widget)
        edit.setEnabled(active)
        btn_browse.setEnabled(active)
        has_path = bool(edit.text().strip())
        btn_open.setEnabled(active and has_path)
        btn_copy.setEnabled(active and has_path)

    def _on_folder_toggled(
        self,
        cb: QtWidgets.QCheckBox,
        edit: QtWidgets.QLineEdit,
        btn_browse: QtWidgets.QPushButton,
        btn_open: QtWidgets.QPushButton,
        btn_copy: QtWidgets.QPushButton,
    ) -> None:
        """文件夹复选框切换"""
        self._apply_folder_row_state(
            cb,
            edit,
            btn_browse,
            btn_open,
            btn_copy,
        )
        self._sync_auto_cleanup_folders()
    
    def _browse_folder(self, edit: QtWidgets.QLineEdit) -> None:
        """浏览选择文件夹"""
        if not self._ensure_cleanup_permission("编辑清理路径"):
            return
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            edit.setText(path)
            # 更新按钮状态（根据编辑框找到对应的按钮组）
            if edit == self.edit_backup:
                self._update_folder_action_buttons(self.cb_backup, self.edit_backup, self.btn_backup_open, self.btn_backup_copy)
            elif edit == self.edit_target:
                self._update_folder_action_buttons(self.cb_target, self.edit_target, self.btn_target_open, self.btn_target_copy)
            elif edit == self.edit_monitor:
                self._update_folder_action_buttons(self.cb_monitor, self.edit_monitor, self.btn_monitor_open, self.btn_monitor_copy)
            elif edit == self.edit_custom:
                self._update_folder_action_buttons(self.cb_custom, self.edit_custom, self.btn_custom_open, self.btn_custom_copy)
            self._sync_auto_cleanup_folders()
    
    def _open_folder_in_explorer(self, path: str) -> None:
        """在文件管理器中打开文件夹"""
        if not path or not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "错误", "文件夹不存在！")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.run(
                    ['open', path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
            else:
                subprocess.run(
                    ['xdg-open', path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"无法打开文件夹：{e}")
    
    def _copy_path(self, path: str) -> None:
        """复制路径到剪贴板"""
        if path:
            QtWidgets.QApplication.clipboard().setText(path)
            # 可以添加一个短暂的提示
    
    def _create_format_selection_group(self) -> QtWidgets.QFrame:
        """创建文件格式选择区域 - 预设下拉+可选展开"""
        format_group = QtWidgets.QFrame()
        format_group.setProperty("class", "card")
        format_layout = QtWidgets.QVBoxLayout(format_group)
        format_layout.setSpacing(10)
        
        # 标题
        title_label = QtWidgets.QLabel("文件格式")
        title_label.setProperty("class", "section-title")
        format_layout.addWidget(title_label)
        
        # 预设下拉选择器
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("快速预设:"))
        
        self.combo_format_preset = QtWidgets.QComboBox()
        self.combo_format_preset.addItems(["图片格式", "文档格式", "压缩包", "日志文件", "全部格式", "自定义..."])
        self.combo_format_preset.setCurrentIndex(0)  # 默认图片
        self.combo_format_preset.currentIndexChanged.connect(self._on_format_preset_changed)
        preset_row.addWidget(self.combo_format_preset, 1)
        format_layout.addLayout(preset_row)
        
        # 初始化格式checkboxes字典（但不立即创建UI）
        self.format_checkboxes: Dict[str, QtWidgets.QCheckBox] = {}
        
        # 格式定义（内部使用）
        self._format_presets = {
            "图片格式": ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.raw'],
            "文档格式": ['.pdf', '.doc', '.docx', '.txt'],
            "压缩包": ['.zip', '.rar', '.7z', '.tar', '.gz'],
            "日志文件": ['.log', '.tmp'],
            "全部格式": ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.raw',
                        '.pdf', '.doc', '.docx', '.txt', '.zip', '.rar', '.7z', '.tar', '.gz', '.log', '.tmp'],
        }
        
        # 展开自定义选项（可折叠容器）
        self.format_expand_btn = QtWidgets.QPushButton("展开格式详情...")
        self.format_expand_btn.setCheckable(True)
        self.format_expand_btn.clicked.connect(self._toggle_format_details)
        format_layout.addWidget(self.format_expand_btn)
        
        # 详细格式选择区域（默认隐藏）
        self.format_details_widget = QtWidgets.QWidget()
        self.format_details_widget.setVisible(False)
        details_layout = QtWidgets.QVBoxLayout(self.format_details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)
        
        # 分组展示所有格式
        format_groups = {
            "图片": ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.raw'],
            "文档": ['.pdf', '.doc', '.docx', '.txt'],
            "压缩": ['.zip', '.rar', '.7z', '.tar', '.gz'],
            "日志": ['.log', '.tmp'],
        }
        
        for group_name, extensions in format_groups.items():
            group_label = QtWidgets.QLabel(group_name)
            group_label.setStyleSheet("font-weight: 700; color: #616161; font-size: 9pt;")
            details_layout.addWidget(group_label)
            
            group_flow = QtWidgets.QHBoxLayout()
            group_flow.setSpacing(6)
            
            for ext in extensions:
                cb = QtWidgets.QCheckBox(ext)
                cb.setChecked(ext in self._format_presets["图片格式"])  # 默认图片
                self.format_checkboxes[ext] = cb
                group_flow.addWidget(cb)
            
            group_flow.addStretch()
            details_layout.addLayout(group_flow)
        
        format_layout.addWidget(self.format_details_widget)
        
        return format_group
    
    def _create_auto_cleanup_card(self) -> QtWidgets.QFrame:
        """创建自动清理配置卡片（简化版）"""
        card = QtWidgets.QFrame()
        card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background: #F5F5F5;
                border: 1px solid #E0E0E0;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        
        layout = QtWidgets.QVBoxLayout(card)
        layout.setSpacing(8)
        
        # 标题和摘要
        title_label = QtWidgets.QLabel("自动清理配置")
        title_label.setStyleSheet("font-weight: 700; color: #424242;")
        layout.addWidget(title_label)
        
        # 状态摘要
        auto_enabled = bool(self._settings.get('enable_auto_delete', False))
        status_text = "已启用" if auto_enabled else "未启用"
        self.auto_status_label = QtWidgets.QLabel(f"当前状态: {status_text}")
        self.auto_status_label.setStyleSheet("color: #757575; font-size: 9pt;")
        layout.addWidget(self.auto_status_label)

        auto_paths = self._get_parent_auto_cleanup_folders()
        self.auto_path_label = QtWidgets.QLabel(f"清理路径: {self._format_folder_summary(auto_paths)}")
        self.auto_path_label.setStyleSheet("color: #757575; font-size: 9pt;")
        layout.addWidget(self.auto_path_label)
        
        # 配置按钮
        self.btn_auto_config = QtWidgets.QPushButton("配置...")
        self.btn_auto_config.setToolTip("打开自动清理配置窗口")
        self.btn_auto_config.clicked.connect(self._open_auto_cleanup_config)
        layout.addWidget(self.btn_auto_config)
        
        return card
    
    def _create_auto_cleanup_group(self) -> CollapsibleBox:
        """创建自动清理配置区域（可折叠） - 已废弃，保留供独立对话框使用"""
        auto_box = CollapsibleBox("自动清理配置（高级）")
        auto_layout = QtWidgets.QVBoxLayout()
        auto_layout.setSpacing(10)
        auto_layout.setSpacing(10)
        
        # 启用自动清理
        self.cb_enable_auto = QtWidgets.QCheckBox(tr("disk_cleanup_auto_enable"))
        auto_enabled = bool(self._settings.get('enable_auto_delete', False))
        self.cb_enable_auto.setChecked(auto_enabled)
        self.cb_enable_auto.toggled.connect(self._on_auto_clean_toggled)
        auto_layout.addWidget(self.cb_enable_auto)
        
        # 配置参数
        config_grid = QtWidgets.QGridLayout()
        config_grid.setSpacing(10)
        
        # 触发阈值
        threshold_label = QtWidgets.QLabel(tr("disk_cleanup_auto_threshold"))
        self.spin_threshold = QtWidgets.QSpinBox()
        self.spin_threshold.setRange(50, 95)
        auto_threshold = int(self._settings.get('auto_delete_threshold', 80))
        self.spin_threshold.setValue(auto_threshold)
        self.spin_threshold.setSuffix(" %")
        self.spin_threshold.setToolTip(tr("disk_cleanup_auto_threshold_tip"))
        self.spin_threshold.setEnabled(auto_enabled)
        config_grid.addWidget(threshold_label, 0, 0)
        config_grid.addWidget(self.spin_threshold, 0, 1)
        
        # 目标阈值
        target_label = QtWidgets.QLabel(tr("disk_cleanup_auto_target"))
        self.spin_target = QtWidgets.QSpinBox()
        self.spin_target.setRange(10, 90)
        auto_target = int(self._settings.get('auto_delete_target_percent', 40))
        self.spin_target.setValue(auto_target)
        self.spin_target.setSuffix(" %")
        self.spin_target.setToolTip(tr("disk_cleanup_auto_target_tip"))
        self.spin_target.setEnabled(auto_enabled)
        config_grid.addWidget(target_label, 0, 2)
        config_grid.addWidget(self.spin_target, 0, 3)
        
        # 检查间隔
        interval_label = QtWidgets.QLabel(tr("disk_cleanup_auto_interval"))
        self.spin_check_interval = QtWidgets.QSpinBox()
        self.spin_check_interval.setRange(60, 3600)
        auto_interval = int(self._settings.get('auto_delete_check_interval', 300))
        self.spin_check_interval.setValue(auto_interval)
        self.spin_check_interval.setSuffix(" " + tr("unit_second"))
        self.spin_check_interval.setToolTip(tr("disk_cleanup_auto_interval_tip"))
        self.spin_check_interval.setEnabled(auto_enabled)
        config_grid.addWidget(interval_label, 1, 0)
        config_grid.addWidget(self.spin_check_interval, 1, 1)
        
        # 格式过滤
        formats_label = QtWidgets.QLabel("格式过滤")
        self.edit_formats = QtWidgets.QLineEdit()
        auto_formats = self._settings.get('auto_delete_formats', [])
        self.edit_formats.setText(','.join(auto_formats) if auto_formats else '')
        self.edit_formats.setPlaceholderText("留空=不限制格式，例: .jpg,.png,.bmp")
        self.edit_formats.setToolTip("逗号分隔的文件后缀名，留空表示清理所有格式")
        self.edit_formats.setEnabled(auto_enabled)
        config_grid.addWidget(formats_label, 2, 0)
        config_grid.addWidget(self.edit_formats, 2, 1, 1, 3)
        
        auto_layout.addLayout(config_grid)
        
        # v3.3.0：删除模式（回收站/永久删除）
        self.cb_auto_use_trash = QtWidgets.QCheckBox("使用回收站删除（更安全）")
        self.cb_auto_use_trash.setChecked(True)
        self.cb_auto_use_trash.setEnabled(False)
        self.cb_auto_use_trash.setToolTip("自动清理仅允许回收站模式；回收站不可用时自动清理会失败关闭")
        auto_layout.addWidget(self.cb_auto_use_trash)
        
        # 说明文本
        auto_hint = QtWidgets.QLabel(tr("disk_cleanup_auto_hint"))
        auto_hint.setProperty("class", "hint")
        auto_hint.setWordWrap(True)
        auto_layout.addWidget(auto_hint)
        
        # 保存配置按钮
        btn_save_auto = QtWidgets.QPushButton(tr("disk_cleanup_auto_save"))
        btn_save_auto.setProperty("class", "Secondary")
        btn_save_auto.clicked.connect(self._save_auto_config)
        self.btn_save_auto = btn_save_auto
        auto_layout.addWidget(btn_save_auto)
        
        auto_box.setContentLayout(auto_layout)
        self._apply_permission_state()
        return auto_box
    
    def _create_button_layout(self) -> QtWidgets.QHBoxLayout:
        """创建底部按钮布局 - 统一的危险操作样式"""
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(8)
        
        # 左侧：删除操作组（危险操作）
        delete_group = QtWidgets.QHBoxLayout()
        delete_group.setSpacing(5)
        
        # 创建删除按钮（危险操作）
        self.btn_delete = QtWidgets.QPushButton("删除选中文件")
        self.btn_delete.setProperty("class", "Danger")
        self.btn_delete.setProperty("split", "left")
        self.btn_delete.setMinimumHeight(36)
        self.btn_delete.setEnabled(False)
        
        # 删除模式选择
        delete_mode_menu = QtWidgets.QMenu(self)
        self.action_trash = delete_mode_menu.addAction("移入回收站（推荐）")
        self.action_trash.setCheckable(True)
        self.action_trash.setChecked(self.trash_available)
        self.action_trash.setEnabled(self.trash_available)
        
        self.action_permanent = delete_mode_menu.addAction("永久删除")
        self.action_permanent.setCheckable(True)
        self.action_permanent.setChecked(False)
        self._permanent_mode_explicit = False
        
        # 确保只有一个被选中
        self.action_trash.triggered.connect(lambda: self._set_delete_mode(True))
        self.action_permanent.triggered.connect(lambda: self._set_delete_mode(False))
        
        self.btn_delete.clicked.connect(self._delete_files)
        
        self.btn_delete_dropdown = QtWidgets.QPushButton("▼")
        self.btn_delete_dropdown.setMaximumWidth(30)
        self.btn_delete_dropdown.setMinimumHeight(36)
        self.btn_delete_dropdown.setProperty("class", "Danger")
        self.btn_delete_dropdown.setProperty("split", "right")
        self.btn_delete_dropdown.setProperty("hasMenu", True)
        self.btn_delete_dropdown.setMenu(delete_mode_menu)

        delete_group.addWidget(self.btn_delete)
        delete_group.addWidget(self.btn_delete_dropdown)
        button_layout.addLayout(delete_group)
        
        # 显示当前删除模式（灰色小标签）
        self.delete_mode_label = QtWidgets.QLabel("(回收站)" if self.trash_available else "(回收站不可用)")
        self.delete_mode_label.setProperty("class", "hint")
        button_layout.addWidget(self.delete_mode_label)
        
        # 中间：弹性空间
        button_layout.addStretch()
        
        # 右侧：关闭按钮（安全操作）
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.setProperty("class", "Secondary")
        btn_close.setMinimumHeight(36)
        btn_close.setMinimumWidth(100)
        btn_close.clicked.connect(self.reject)
        button_layout.addWidget(btn_close)

        return button_layout
    
    def _set_delete_mode(self, use_trash: bool) -> None:
        """设置删除模式"""
        self.action_trash.setChecked(use_trash)
        self.action_permanent.setChecked(not use_trash)
        self._permanent_mode_explicit = not use_trash
        self.delete_mode_label.setText("(回收站)" if use_trash else "(永久)")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Close the dialog after delegating worker cleanup to the controller."""
        if self.cleanup_controller.is_deleting:
            QtWidgets.QMessageBox.warning(
                self, "正在删除", "正在删除文件，请等待完成后再关闭。"
            )
            event.ignore()
            return
        self.cleanup_controller.close_manual()
        super().closeEvent(event)

    # 事件处理方法
    
    def _choose_custom(self) -> None:
        """选择自定义文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("disk_cleanup_dialog_custom_folder"))
        if path:
            self.edit_custom.setText(path)
            self._sync_auto_cleanup_folders()
    
    def _choose_monitor(self) -> None:
        """选择监控文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("disk_cleanup_dialog_monitor_folder"))
        if path:
            self.edit_monitor.setText(path)
            self._sync_auto_cleanup_folders()

    def _sync_auto_cleanup_folders(self) -> None:
        """同步基础设置中勾选的扫描目录到自动清理监控列表"""
        self._update_auto_cleanup_path_summary()
    
    def _on_format_preset_changed(self, index: int) -> None:
        """预设格式下拉改变"""
        preset_name = self.combo_format_preset.currentText()
        
        if preset_name == "自定义...":
            # 展开详细选项
            self.format_expand_btn.setChecked(True)
            self.format_details_widget.setVisible(True)
            self.format_expand_btn.setText("收起格式详情")
            return
        
        # 应用预设
        if preset_name in self._format_presets:
            selected_formats = set(self._format_presets[preset_name])
            for ext, cb in self.format_checkboxes.items():
                cb.setChecked(ext in selected_formats)
    
    def _toggle_format_details(self, checked: bool) -> None:
        """展开/折叠格式详情"""
        self.format_details_widget.setVisible(checked)
        self.format_expand_btn.setText("收起格式详情" if checked else "展开格式详情...")
    
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
        image_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.raw']
        for ext, cb in self.format_checkboxes.items():
            cb.setChecked(ext in image_formats)
    
    def _open_auto_cleanup_config(self) -> None:
        """打开自动清理配置独立窗口"""
        if not self._ensure_cleanup_permission("打开自动清理配置"):
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("自动清理配置")
        dialog.setModal(True)
        dialog.resize(500, 400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 复用原配置控件
        auto_group = self._create_auto_cleanup_group()
        layout.addWidget(auto_group)
        
        # 底部按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("保存")
        def _on_save_clicked(_dialog: QtWidgets.QDialog = dialog) -> None:
            if self._save_auto_config():
                _dialog.accept()
        btn_save.clicked.connect(_on_save_clicked)
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        dialog.exec()
        self._refresh_auto_cleanup_card_from_parent()
    
    def _on_auto_clean_toggled(self, checked: bool) -> None:
        """自动清理开关切换"""
        self.spin_threshold.setEnabled(checked)
        self.spin_target.setEnabled(checked)
        self.spin_check_interval.setEnabled(checked)
        if hasattr(self, 'edit_formats'):
            self.edit_formats.setEnabled(checked)
        if hasattr(self, 'cb_auto_use_trash'):
            self.cb_auto_use_trash.setEnabled(False)
        self._apply_permission_state()
    
    def _save_auto_config(self) -> bool:
        """通过显式网关保存自动清理配置。"""
        if self.settings_gateway is None:
            return False
        if not self._ensure_cleanup_permission("保存自动清理配置"):
            return False
        
        try:
            folders_to_clean = self._collect_selected_folders(include_hidden=True)

            if self.cb_enable_auto.isChecked():
                if not folders_to_clean:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        "已启用自动清理，但未选择清理目录。"
                    )
                    return False
                if self.spin_target.value() >= self.spin_threshold.value():
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        "目标阈值必须小于触发阈值，请调整配置。",
                    )
                    return False
                previously_enabled = bool(
                    self._settings.get("enable_auto_delete", False)
                )
                if not previously_enabled:
                    confirmation = QtWidgets.QMessageBox.question(
                        self,
                        "确认启用自动清理",
                        "自动清理达到磁盘触发阈值后，会在所选目录中按最旧优先清理，"
                        "且不受“保留天数”限制。\n\n"
                        "请确认清理目录、格式过滤和删除模式均已正确设置。"
                        "是否确认启用？",
                        QtWidgets.QMessageBox.StandardButton.Yes
                        | QtWidgets.QMessageBox.StandardButton.No,
                        QtWidgets.QMessageBox.StandardButton.No,
                    )
                    if confirmation != QtWidgets.QMessageBox.StandardButton.Yes:
                        return False

            # 解析格式过滤输入
            formats_text = self.edit_formats.text().strip() if hasattr(self, 'edit_formats') else ''
            formats_list = [f.strip() for f in formats_text.split(',') if f.strip()] if formats_text else []

            cleanup_config = {
                "enable_auto_delete": self.cb_enable_auto.isChecked(),
                "auto_delete_folders": folders_to_clean,
                "auto_delete_threshold": self.spin_threshold.value(),
                "auto_delete_target_percent": self.spin_target.value(),
                "auto_delete_check_interval": self.spin_check_interval.value(),
                "auto_delete_use_trash": True,
                "auto_delete_formats": formats_list,
            }
            
            save_result = bool(
                self.settings_gateway.save_auto_cleanup_settings(cleanup_config)
            )
            if not save_result:
                error_message = (
                    str(self.settings_gateway.cleanup_settings_error).strip()
                    or "配置文件写入失败。"
                )
                QtWidgets.QMessageBox.warning(
                    self,
                    "保存失败",
                    f"自动清理配置未写入配置文件：\n\n{error_message}",
                )
                return False

            self._settings = self._read_settings_snapshot()
            self._update_auto_cleanup_status_summary(self.cb_enable_auto.isChecked())
            self._refresh_auto_cleanup_card_from_parent()
            
            # 显示成功消息
            enabled_text = tr("word_yes") if self.cb_enable_auto.isChecked() else tr("word_no")
            monitor_text = "；".join(folders_to_clean) if folders_to_clean else tr("disk_cleanup_not_set")
            self._append_log_line(
                f"自动清理配置已保存：启用={enabled_text}，清理路径={monitor_text}，触发={self.spin_threshold.value()}%，目标={self.spin_target.value()}%"
            )
            QtWidgets.QMessageBox.information(
                self,
                tr("disk_cleanup_config_saved_title"),
                tr(
                    "disk_cleanup_config_saved_body",
                    enabled=enabled_text,
                    monitor=monitor_text,
                    threshold=self.spin_threshold.value(),
                    target=self.spin_target.value(),
                    interval=self.spin_check_interval.value(),
                ),
            )
            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                tr("disk_cleanup_config_save_fail_title"),
                tr("disk_cleanup_config_save_fail_body", error=e),
            )
            return False
    
    def _filter_files(self) -> None:
        """根据搜索框过滤文件（与快捷筛选结合）"""
        search_text = self.search_edit.text().lower()
        import time
        cutoff_time_7days = time.time() - (7 * 24 * 3600)
        size_threshold = 10 * 1024 * 1024
        
        show_checked_only = self.chip_show_checked.isChecked()
        show_large_only = self.chip_show_large.isChecked()
        show_recent_only = self.chip_show_recent.isChecked()
        
        for row in range(self.file_table.rowCount()):
            check_item = self.file_table.item(row, 0)
            file_item = (
                check_item.data(Qt.ItemDataRole.UserRole) if check_item else None
            )
            if not isinstance(file_item, CleanupFileItem):
                continue
            name_item = self.file_table.item(row, 1)
            path_item = self.file_table.item(row, 2)
            
            show = True
            
            # 搜索文本匹配
            if search_text and name_item and path_item:
                name_match = search_text in name_item.text().lower()
                path_match = search_text in path_item.text().lower()
                if not (name_match or path_match):
                    show = False
            
            # 快捷筛选
            if show_checked_only and not file_item.checked:
                show = False
            if show_large_only and file_item.size < size_threshold:
                show = False
            if show_recent_only and file_item.mtime < cutoff_time_7days:
                show = False
            
            self.file_table.setRowHidden(row, not show)
    
    def _cancel_scan(self) -> None:
        """取消扫描"""
        self.cleanup_controller.cancel_scan()
        self.btn_cancel_scan.setVisible(False)
        self.progress_bar.setVisible(False)
        self._append_log_line("扫描已取消。")

    def _scan_files(self) -> None:
        """Collect scan input and delegate filesystem work to the controller."""
        if not self._ensure_cleanup_permission("扫描文件"):
            return

        self._update_summary()
        self._clear_log()
        self._append_log_line("准备扫描...")
        self.all_files = []
        self.file_table.load_files([])
        self.stats_label.setText("扫描中...")
        self.progress_label.setText("准备扫描...")

        folders_to_scan = self._collect_selected_folders()

        if not folders_to_scan:
            self._append_log_line("未选择任何文件夹，扫描已取消。")
            QtWidgets.QMessageBox.warning(
                self, "错误", "请至少选择一个文件夹进行扫描！"
            )
            return
        self._scanned_folders = tuple(folders_to_scan)

        formats_to_scan: List[str] = [
            ext.lower() for ext, checkbox in self.format_checkboxes.items()
            if checkbox.isChecked()
        ]
        custom_editor = getattr(self, "edit_custom_format", None)
        custom_format = custom_editor.text().strip() if custom_editor is not None else ""
        for ext in custom_format.split(","):
            ext = ext.strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                formats_to_scan.append(ext.lower())

        if not formats_to_scan:
            self._append_log_line("未选择任何文件格式，扫描已取消。")
            QtWidgets.QMessageBox.warning(
                self, "错误", "请至少选择一种文件格式进行扫描！"
            )
            return

        keep_days = (
            self.spin_filter_days.value()
            if hasattr(self, "cb_filter_days") and self.cb_filter_days.isChecked()
            else 0
        )
        request = CleanupScanRequest(
            tuple(folders_to_scan), tuple(formats_to_scan), keep_days
        )
        validation = self.cleanup_controller.validate_scan_request(request)
        if validation.invalid_reasons:
            self._append_log_line("以下路径不可用，将被跳过：")
            for line in validation.invalid_reasons:
                self._append_log_line(f"  - {line}")
            QtWidgets.QMessageBox.warning(
                self,
                "路径不可用",
                "部分路径不可用，将跳过这些路径：\n\n"
                + "\n".join(validation.invalid_reasons),
            )
        if not validation.is_valid:
            message = "\n".join(validation.errors) or "没有可用的扫描路径"
            QtWidgets.QMessageBox.warning(self, "错误", message)
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.btn_cancel_scan.setVisible(True)
        self.btn_scan.setEnabled(False)
        self.btn_delete.setEnabled(False)
        result = self.cleanup_controller.start_scan(request)
        if not result.success:
            self.btn_scan.setEnabled(self._can_manage_cleanup())
            self.progress_bar.setVisible(False)
            self.btn_cancel_scan.setVisible(False)
            QtWidgets.QMessageBox.warning(
                self, "扫描失败", "\n".join(result.errors) or result.message
            )

    def _handle_cleanup_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        if event_type == "log":
            self._append_log_line(str(event.get("message", "")))
        elif event_type == "scan_progress":
            self._on_scan_progress(
                str(event.get("current_dir", "")),
                int(event.get("file_count", 0)),
                # New workers keep totals in an object payload under the
                # explicit byte key.  Retain the legacy key while older
                # workers are still in use during staged upgrades.
                int(event.get("total_size_bytes", event.get("total_size", 0))),
            )
        elif event_type == "scan_finished":
            self._on_scan_finished(list(event.get("files", [])))
        elif event_type == "delete_progress":
            self._on_delete_progress_value(
                int(event.get("current", 0)), int(event.get("total", 0))
            )
        elif event_type == "delete_finished":
            self._on_delete_finished(
                int(event.get("deleted_count", 0)),
                int(event.get("deleted_size", 0)),
                int(event.get("failed_count", 0)),
                list(event.get("remaining_files", [])),
            )

    def _on_scan_progress(self, current_dir: str, file_count: int, total_size: int) -> None:
        """更新扫描进度"""
        size_mb = total_size / (1024 * 1024)
        # 简化显示路径
        if len(current_dir) > 50:
            current_dir = "..." + current_dir[-47:]
        self.progress_label.setText(f"扫描: {file_count} 文件 | {size_mb:.1f} MB | {current_dir}")
    
    def _delete_files(self) -> None:
        """删除选中的文件（异步线程，支持回收站）"""
        if not self._ensure_cleanup_permission("删除文件"):
            return

        checked_files = self.file_table.get_checked_files()
        if not checked_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有选中任何文件！")
            return

        total_size = sum(f.size for f in checked_files)
        
        # 生成清理清单摘要
        summary = self._generate_delete_summary(checked_files)
        
        # 从下拉菜单获取删除模式
        use_trash = self.action_trash.isChecked()
        if not use_trash and not self._permanent_mode_explicit:
            QtWidgets.QMessageBox.information(
                self,
                "需要显式选择",
                "回收站不可用。请先在删除模式菜单中手动选择“永久删除”。",
            )
            return
        action_text = "移入回收站" if use_trash else "永久删除"
        
        confirm_text = (
            f"【清理清单摘要】\n\n"
            f"{summary}\n\n"
            f"⚠️ 即将{action_text} {len(checked_files)} 个文件，"
            f"共 {total_size / (1024 * 1024):.2f} MB ({total_size / (1024 * 1024 * 1024):.2f} GB)\n\n"
            f"{'🗑️ 文件将移入回收站，可恢复' if use_trash else '⚠️ 文件将被永久删除，无法恢复！'}\n\n"
            f"是否继续？"
        )
        
        reply = QtWidgets.QMessageBox.warning(
            self,
            f"确认{action_text}",
            confirm_text,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        if not use_trash:
            text, ok = QtWidgets.QInputDialog.getText(
                self,
                "永久删除确认",
                "此操作不可恢复。请输入 DELETE 以继续："
            )
            if not ok or text.strip().upper() != "DELETE":
                QtWidgets.QMessageBox.information(self, "已取消", "未通过确认，已取消永久删除。")
                return

        # 显示进度
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(checked_files))
        self.progress_bar.setValue(0)
        
        # 开始删除
        self.btn_delete.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.progress_label.setText(f"正在删除: 0 / {len(checked_files)}")
        self._append_log_line(f"开始{action_text} {len(checked_files)} 个文件。")

        result = self.cleanup_controller.start_delete(
            CleanupDeleteRequest(
                tuple(checked_files),
                use_trash,
                permanent_authorized=not use_trash,
                allowed_roots=self._scanned_folders,
            )
        )
        if not result.success:
            self.progress_bar.setVisible(False)
            self.btn_scan.setEnabled(self._can_manage_cleanup())
            self.btn_delete.setEnabled(bool(checked_files))
            QtWidgets.QMessageBox.warning(self, "删除失败", result.message)

    def _generate_delete_summary(self, files: List[FileItem]) -> str:
        """生成删除摘要（Top 5 最大文件）"""
        sorted_files = sorted(files, key=lambda x: x.size, reverse=True)
        top_files = sorted_files[:5]
        
        summary_lines = ["Top 5 最大文件:"]
        for i, file in enumerate(top_files, 1):
            size_mb = file.size / (1024 * 1024)
            summary_lines.append(f"  {i}. {file.name} ({size_mb:.2f} MB)")
        
        if len(files) > 5:
            summary_lines.append(f"  ... 及其他 {len(files) - 5} 个文件")
        
        return "\n".join(summary_lines)

    def _on_scan_finished(self, files: List[FileItem]) -> None:
        """扫描完成回调"""
        self.all_files = sorted(files, key=lambda x: x.size, reverse=True)
        
        # 隐藏进度条和取消按钮
        self.progress_bar.setVisible(False)
        self.btn_cancel_scan.setVisible(False)
        
        # 加载到表格
        self.file_table.load_files(self.all_files)
        
        # 更新统计
        total_size = sum(f.size for f in self.all_files)
        size_mb = total_size / (1024 * 1024)
        size_gb = total_size / (1024 * 1024 * 1024)
        self.stats_label.setText(
            f"共 {len(self.all_files)} 文件 | {size_mb:.1f} MB ({size_gb:.2f} GB)"
        )
        self.progress_label.setText(f"扫描完成，找到 {len(self.all_files)} 个文件")
        self._append_log_line(f"扫描完成，找到 {len(self.all_files)} 个文件。")

        can_manage = self._can_manage_cleanup()
        self.btn_scan.setEnabled(can_manage)
        self.btn_delete.setEnabled(can_manage and bool(self.file_table.get_checked_files()))

    def _on_delete_progress_value(self, current: int, total: int) -> None:
        """删除进度更新"""
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"正在删除: {current} / {total}")

    def _on_delete_finished(
        self,
        deleted_count: int,
        deleted_size: int,
        failed_count: int,
        remaining_files: List[FileItem],
    ) -> None:
        """Render a completed delete result supplied by the service."""
        self.progress_bar.setVisible(False)
        size_mb = deleted_size / (1024 * 1024)
        size_gb = deleted_size / (1024 * 1024 * 1024)
        result_text = (
            f"删除完成！\n\n"
            f"✅ 成功删除: {deleted_count} 个文件\n"
            f"📦 释放空间: {size_mb:.2f} MB ({size_gb:.2f} GB)\n"
        )
        if failed_count > 0:
            result_text += f"❌ 失败: {failed_count} 个文件\n"
            self._append_log_line(
                f"删除完成，失败 {failed_count} 个文件。建议重新扫描确认。"
            )
        else:
            self._append_log_line("删除完成，未发现失败项。")

        self.all_files = remaining_files
        self.file_table.load_files(self.all_files)
        total_size = sum(item.size for item in self.all_files)
        size_mb_total = total_size / (1024 * 1024)
        size_gb_total = total_size / (1024 * 1024 * 1024)
        self.stats_label.setText(
            f"剩余 {len(self.all_files)} 文件 | "
            f"{size_mb_total:.1f} MB ({size_gb_total:.2f} GB)"
        )
        self.progress_label.setText(f"删除完成，成功 {deleted_count} 个")
        can_manage = self._can_manage_cleanup()
        self.btn_scan.setEnabled(can_manage)
        self.btn_delete.setEnabled(
            can_manage and bool(self.file_table.get_checked_files())
        )
        QtWidgets.QMessageBox.information(self, "删除完成", result_text)
