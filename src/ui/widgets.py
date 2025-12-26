"""自定义 UI 控件模块

包含：
- Toast: 通知提示组件
- ChipWidget: 数据卡片组件
- CollapsibleBox: 可折叠容器组件
- DiskCleanupDialog: 文件清理对话框
"""

import os
import subprocess
import platform
import ctypes
from ctypes import wintypes
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any, TYPE_CHECKING, Protocol

try:
    from send2trash import send2trash  # type: ignore[import-not-found]
except ImportError:
    send2trash = None  # type: ignore[assignment]


def trash_supported() -> bool:
    return send2trash is not None or os.name == 'nt'


def send_to_trash(path: str) -> None:
    if send2trash is not None:
        send2trash(path)  # type: ignore[misc]
        return
    if os.name != 'nt':
        raise RuntimeError("Trash not supported without send2trash")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.UINT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    flags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    from_buf = path + "\0\0"
    op = SHFILEOPSTRUCTW(0, FO_DELETE, from_buf, None, flags, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(rc, "Send to Recycle Bin failed", path)

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    QtEnum = Qt
except ImportError:
    from PyQt5 import QtWidgets, QtCore, QtGui  # type: ignore[import-not-found]
    from PyQt5.QtCore import Qt  # type: ignore[import-not-found]
    QtEnum = QtCore.Qt

# 兼容 PySide / PyQt 的信号定义
Signal = getattr(QtCore, "Signal", None)
if Signal is None:
    Signal = getattr(QtCore, "pyqtSignal")  # type: ignore[attr-defined]

from src.core.i18n import t


def tr(key: str, **kwargs: Any) -> str:
    """便捷翻译并格式化"""
    return t(key, key).format(**kwargs)

# 类型检查时的协议定义
if TYPE_CHECKING:
    class MainWindowProtocol(Protocol):
        """MainWindow 的类型协议（用于类型检查）"""
        bak_edit: QtWidgets.QLineEdit
        tgt_edit: QtWidgets.QLineEdit
        auto_delete_folder: str
        enable_auto_delete: bool
        auto_delete_threshold: int
        auto_delete_target_percent: int
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
    
    def set_expanded(self, expanded: bool) -> None:
        """设置展开/折叠状态 (v3.1.0 新增)
        
        公开方法，用于程序控制折叠框的展开状态。
        
        Args:
            expanded: True 展开, False 折叠
        """
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.blockSignals(False)
        self._on_toggle(expanded)
    
    def is_expanded(self) -> bool:
        """获取当前是否展开 (v3.1.0 新增)
        
        Returns:
            True 如果已展开，否则 False
        """
        return self.toggle_button.isChecked()
    
    def setEnabled(self, enabled: bool) -> None:
        """重写 setEnabled，同时控制折叠按钮 (v3.1.0 增强)
        
        禁用时收起折叠框并禁用按钮，避免"亮着但不可用"的误导。
        
        Args:
            enabled: 是否启用
        """
        super().setEnabled(enabled)
        self.toggle_button.setEnabled(enabled)
        if not enabled:
            # 禁用时强制收起
            self.set_expanded(False)
    
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


class FileItem:
    """文件项数据类"""
    def __init__(self, path: str, size: int, mtime: float):
        self.path = path
        self.size = size
        self.mtime = mtime
        self.name = os.path.basename(path)
        self.checked = True  # 默认勾选


class ScanWorker(QtCore.QObject):  # type: ignore[misc]
    """磁盘扫描线程工作者"""
    progress = Signal(str)
    progress_detail = Signal(str, int, int)  # 当前目录，文件数，累计大小
    finished = Signal(list)  # List[FileItem]
    
    def __init__(self, folders: List[str], formats: List[str], keep_days: int = 0) -> None:
        super().__init__()
        self.folders = folders
        self.formats = formats
        self.keep_days = keep_days
        self._cancelled = False

    def cancel(self) -> None:
        """取消扫描"""
        self._cancelled = True

    def _emit(self, text: str) -> None:
        self.progress.emit(text)

    @QtCore.Slot()
    def run(self) -> None:
        files: List[FileItem] = []
        total_size = 0
        file_count = 0
        
        # 计算时间阈值
        import time
        cutoff_time = time.time() - (self.keep_days * 24 * 3600) if self.keep_days > 0 else 0
        
        self._emit(tr("disk_cleanup_scan_start") + "\n")
        self._emit(tr("disk_cleanup_scan_dirs", count=len(self.folders)))
        self._emit(tr("disk_cleanup_scan_formats", formats=", ".join(self.formats)) + "\n")
        if self.keep_days > 0:
            self._emit(f"仅扫描 {self.keep_days} 天前的文件\n")

        for folder in self.folders:
            if self._cancelled:
                self._emit("\n扫描已取消")
                break
            if not os.path.exists(folder):
                self._emit(tr("disk_cleanup_skip_missing", path=folder))
                continue

            self._emit("\n" + tr("disk_cleanup_scan_folder", folder=folder))
            folder_count = 0
            folder_size = 0

            try:
                for root, dirs, files_list in os.walk(folder):
                    if self._cancelled:
                        break
                    
                    # 发送实时进度
                    self.progress_detail.emit(root, file_count, total_size)
                    
                    for file in files_list:
                        if self._cancelled:
                            break
                        
                        file_lower = file.lower()
                        if any(file_lower.endswith(ext) for ext in self.formats):
                            file_path = os.path.join(root, file)
                            try:
                                file_stat = os.stat(file_path)
                                file_size = file_stat.st_size
                                file_mtime = file_stat.st_mtime
                                
                                # 检查文件修改时间
                                if cutoff_time > 0 and file_mtime > cutoff_time:
                                    continue  # 跳过太新的文件
                                
                                files.append(FileItem(file_path, file_size, file_mtime))
                                folder_count += 1
                                folder_size += file_size
                                file_count += 1
                                total_size += file_size
                            except Exception as e:  # pragma: no cover - OS errors
                                self._emit(tr("disk_cleanup_cannot_access", file=file, error=e))

                self._emit(
                    tr("disk_cleanup_found_folder", count=folder_count, size_mb=folder_size / (1024 * 1024))
                )
            except Exception as e:  # pragma: no cover
                self._emit(tr("disk_cleanup_scan_fail", error=e))

        self.finished.emit(files)


class DeleteWorker(QtCore.QObject):  # type: ignore[misc]
    """删除文件线程工作者"""
    progress = Signal(str)
    progress_value = Signal(int, int)
    finished = Signal(int, int, int)

    def __init__(self, files: List[FileItem], use_trash: bool) -> None:
        super().__init__()
        self.files = files
        self.use_trash = use_trash

    def _emit(self, text: str) -> None:
        self.progress.emit(text)

    @QtCore.Slot()
    def run(self) -> None:
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        total_files = len(self.files)
        use_trash = self.use_trash and trash_supported()
        if self.use_trash and not trash_supported():
            self._emit(tr("disk_cleanup_send2trash_missing"))

        for idx, file_item in enumerate(self.files, start=1):
            try:
                if use_trash:
                    send_to_trash(file_item.path)
                else:
                    os.remove(file_item.path)
                deleted_count += 1
                deleted_size += file_item.size
            except Exception as e:  # pragma: no cover
                failed_count += 1
                self._emit(tr("disk_cleanup_delete_fail", path=file_item.path, error=e))
            finally:
                self.progress_value.emit(idx, total_files)

        self.finished.emit(deleted_count, deleted_size, failed_count)


class FileListTable(QtWidgets.QTableWidget):  # type: ignore[misc]
    """文件列表表格"""
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.file_items: List[FileItem] = []
        self._setup_table()
        self._setup_context_menu()
    
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
        
        file_item = self.file_items[row]
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
                subprocess.run(['explorer', '/select,', file_path], check=False)
            elif platform.system() == "Darwin":  # macOS
                subprocess.run(['open', '-R', file_path], check=False)
            else:  # Linux
                folder = os.path.dirname(file_path)
                subprocess.run(['xdg-open', folder], check=False)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"无法打开文件夹：{e}")
    
    def load_files(self, file_items: List[FileItem]) -> None:
        """加载文件列表"""
        self.file_items = file_items
        self.setRowCount(len(file_items))
        
        for row, file_item in enumerate(file_items):
            # 复选框
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(file_item.checked)
            checkbox.stateChanged.connect(lambda state, r=row: self._on_checkbox_changed(r, state))
            checkbox_widget = QtWidgets.QWidget()
            checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.setCellWidget(row, 0, checkbox_widget)
            
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
    
    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.1f} {unit}"
            size_float /= 1024.0
        return f"{size_float:.1f} TB"
    
    def _on_checkbox_changed(self, row: int, state: int) -> None:
        """复选框状态改变"""
        if row < len(self.file_items):
            self.file_items[row].checked = (state == Qt.CheckState.Checked.value if hasattr(Qt.CheckState, 'Checked') else state == 2)
    
    def get_checked_files(self) -> List[FileItem]:
        """获取已勾选的文件"""
        return [item for item in self.file_items if item.checked]
    
    def select_all(self) -> None:
        """全选"""
        for row in range(self.rowCount()):
            widget = self.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QtWidgets.QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)
    
    def select_none(self) -> None:
        """取消全选"""
        for row in range(self.rowCount()):
            widget = self.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QtWidgets.QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)


class DiskCleanupDialog(QtWidgets.QDialog):  # type: ignore[misc]
    """文件清理对话框 - 按目录和扩展名清理文件

    本工具用于清理指定目录中的特定格式文件，不是系统级磁盘清理工具。
    支持选择文件夹路径和文件格式进行清理，可查看、筛选和确认删除文件。
    整合自动清理配置功能。
    
    Args:
        parent: 父窗口（MainWindow 实例）
    
    Note: type: ignore[misc] - Qt 动态导入导致的 Pylance 误报
    """
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("文件清理工具 - 按目录和扩展名清理")
        self.setModal(True)
        
        # 保存父窗口引用，使用 Any 类型避免 Pylance 误报
        self.parent_window: Any = parent  # type: ignore[assignment]
        self.all_files: List[FileItem] = []  # 所有扫描到的文件
        self.scan_worker: Optional[ScanWorker] = None  # 扫描线程
        
        # 检查回收站可用性
        self.trash_available = trash_supported()
        
        # 延迟加载标志
        self._advanced_tab_created = False
        self.tab_widget: Optional[QtWidgets.QTabWidget] = None
        
        self._build_ui()
        if not self.trash_available:
            self._append_log_line("回收站不可用，删除将为永久删除。")
    
    def _apply_unified_stylesheet(self) -> None:
        """应用统一的样式表"""
        stylesheet = """
            QWidget{font-family:'Segoe UI', 'Microsoft YaHei UI'; font-size:11pt; color:#1F2937; background:#E3F2FD;}
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
        
        # 设置可调整大小的窗口
        self.setMinimumSize(1100, 650)
        self.resize(1300, 750)
        
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
            warning_label = QtWidgets.QLabel("警告：回收站不可用，文件将被永久删除！")
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
        table_actions_layout.addStretch()
        
        # 统计信息
        self.stats_label = QtWidgets.QLabel("未扫描")
        self.stats_label.setProperty("class", "hint")
        table_actions_layout.addWidget(self.stats_label)
        
        layout.addLayout(table_actions_layout)
        
        return widget
    
    def _apply_quick_filters(self) -> None:
        """应用快捷筛选"""
        import time
        cutoff_time_7days = time.time() - (7 * 24 * 3600)
        size_threshold = 10 * 1024 * 1024  # 10MB
        
        show_checked_only = self.chip_show_checked.isChecked()
        show_large_only = self.chip_show_large.isChecked()
        show_recent_only = self.chip_show_recent.isChecked()
        
        for row in range(self.file_table.rowCount()):
            if row >= len(self.file_table.file_items):
                continue
            
            file_item = self.file_table.file_items[row]
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
            if self.tab_widget:
                self.tab_widget.setUpdatesEnabled(False)
            
            try:
                # 创建高级设置页
                advanced_tab = self._create_advanced_settings_tab()
                if self.tab_widget:
                    self.tab_widget.removeTab(1)  # 移除占位页
                    self.tab_widget.insertTab(1, advanced_tab, "高级设置")
                    self._advanced_tab_created = True
            finally:
                # 延迟恢复更新，避免抖动
                if self.tab_widget:
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
        
        # 从父窗口读取路径配置
        backup_path = self.parent_window.bak_edit.text() if self.parent_window and hasattr(self.parent_window, 'bak_edit') else ""
        target_path = self.parent_window.tgt_edit.text() if self.parent_window and hasattr(self.parent_window, 'tgt_edit') else ""
        monitor_path = self.parent_window.auto_delete_folder if self.parent_window and hasattr(self.parent_window, 'auto_delete_folder') else ""
        
        # 备份文件夹行
        self.cb_backup, self.edit_backup, backup_btns = self._create_folder_row("备份目录", backup_path, True)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_backup, self.edit_backup, backup_btns))
        
        # 目标文件夹行
        self.cb_target, self.edit_target, target_btns = self._create_folder_row("目标目录", target_path, False)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_target, self.edit_target, target_btns))
        
        # 监控文件夹行
        self.cb_monitor, self.edit_monitor, monitor_btns = self._create_folder_row("监控目录", monitor_path, False)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_monitor, self.edit_monitor, monitor_btns))
        self.btn_monitor_browse = monitor_btns[0]
        self.btn_monitor_open = monitor_btns[1]
        
        # 自定义文件夹行
        self.cb_custom, self.edit_custom, custom_btns = self._create_folder_row("自定义目录", "", False)
        folder_layout.addLayout(self._create_folder_form_row(self.cb_custom, self.edit_custom, custom_btns))
        self.btn_custom_browse = custom_btns[0]
        self.btn_custom_open = custom_btns[1]
        
        return folder_group
    
    def _create_folder_row(self, label: str, path: str, checked: bool) -> Tuple[QtWidgets.QCheckBox, QtWidgets.QLineEdit, List[QtWidgets.QPushButton]]:
        """创建单个文件夹选择行的组件"""
        # 复选框
        cb = QtWidgets.QCheckBox(label)
        cb.setChecked(checked if path else False)
        cb.setEnabled(bool(path) or label in ["监控目录", "自定义目录"])
        cb.toggled.connect(lambda checked, e=None: self._on_folder_toggled(cb, e))
        
        # 只读路径输入框
        edit = QtWidgets.QLineEdit(path)
        edit.setReadOnly(True)
        edit.setPlaceholderText(f"选择{label}...")
        edit.setEnabled(cb.isChecked())
        
        # 按钮组：浏览、打开、复制
        btn_browse = QtWidgets.QPushButton("...")
        btn_browse.setToolTip("浏览选择")
        btn_browse.setMaximumWidth(40)
        btn_browse.setProperty("variant", "tool")
        btn_browse.setEnabled(cb.isChecked())
        btn_browse.clicked.connect(lambda: self._browse_folder(edit))
        
        btn_open = QtWidgets.QPushButton("打开")
        btn_open.setToolTip("在文件管理器中打开")
        btn_open.setMaximumWidth(50)
        btn_open.setProperty("variant", "tool")
        btn_open.setEnabled(cb.isChecked() and bool(edit.text()))
        btn_open.clicked.connect(lambda: self._open_folder_in_explorer(edit.text()))
        
        btn_copy = QtWidgets.QPushButton("复制")
        btn_copy.setToolTip("复制路径到剪贴板")
        btn_copy.setMaximumWidth(50)
        btn_copy.setProperty("variant", "tool")
        btn_copy.setEnabled(cb.isChecked() and bool(edit.text()))
        btn_copy.clicked.connect(lambda: self._copy_path(edit.text()))
        
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
    
    def _on_folder_toggled(self, cb: QtWidgets.QCheckBox, edit: Optional[QtWidgets.QLineEdit]) -> None:
        """文件夹复选框切换"""
        checked = cb.isChecked()
        # 找到对应的输入框和按钮
        if cb == self.cb_backup:
            self.edit_backup.setEnabled(checked)
        elif cb == self.cb_target:
            self.edit_target.setEnabled(checked)
        elif cb == self.cb_monitor:
            self.edit_monitor.setEnabled(checked)
            self.btn_monitor_browse.setEnabled(checked)
            self.btn_monitor_open.setEnabled(checked and bool(self.edit_monitor.text()))
        elif cb == self.cb_custom:
            self.edit_custom.setEnabled(checked)
            self.btn_custom_browse.setEnabled(checked)
            self.btn_custom_open.setEnabled(checked and bool(self.edit_custom.text()))
    
    def _browse_folder(self, edit: QtWidgets.QLineEdit) -> None:
        """浏览选择文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            edit.setText(path)
            # 更新按钮状态（根据编辑框找到对应的按钮组）
            if edit == self.edit_monitor:
                self.btn_monitor_open.setEnabled(True)
            elif edit == self.edit_custom:
                self.btn_custom_open.setEnabled(True)
    
    def _open_folder_in_explorer(self, path: str) -> None:
        """在文件管理器中打开文件夹"""
        if not path or not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "错误", "文件夹不存在！")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', path], check=False)
            else:
                subprocess.run(['xdg-open', path], check=False)
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
        auto_enabled = self.parent_window.enable_auto_delete if self.parent_window and hasattr(self.parent_window, 'enable_auto_delete') else False
        status_text = "已启用" if auto_enabled else "未启用"
        self.auto_status_label = QtWidgets.QLabel(f"当前状态: {status_text}")
        self.auto_status_label.setStyleSheet("color: #757575; font-size: 9pt;")
        layout.addWidget(self.auto_status_label)

        auto_path = self.parent_window.auto_delete_folder if self.parent_window and hasattr(self.parent_window, 'auto_delete_folder') else ""
        self.auto_path_label = QtWidgets.QLabel(f"监控路径: {auto_path or '未设置'}")
        self.auto_path_label.setStyleSheet("color: #757575; font-size: 9pt;")
        layout.addWidget(self.auto_path_label)
        
        # 配置按钮
        btn_config = QtWidgets.QPushButton("配置...")
        btn_config.setToolTip("打开自动清理配置窗口")
        btn_config.clicked.connect(self._open_auto_cleanup_config)
        layout.addWidget(btn_config)
        
        return card
    
    def _create_auto_cleanup_group(self) -> CollapsibleBox:
        """创建自动清理配置区域（可折叠） - 已废弃，保留供独立对话框使用"""
        auto_box = CollapsibleBox("自动清理配置（高级）")
        auto_layout = QtWidgets.QVBoxLayout()
        auto_layout.setSpacing(10)
        auto_layout.setSpacing(10)
        
        # 启用自动清理
        self.cb_enable_auto = QtWidgets.QCheckBox(tr("disk_cleanup_auto_enable"))
        auto_enabled = self.parent_window.enable_auto_delete if self.parent_window and hasattr(self.parent_window, 'enable_auto_delete') else False
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
        auto_threshold = self.parent_window.auto_delete_threshold if self.parent_window and hasattr(self.parent_window, 'auto_delete_threshold') else 80
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
        auto_target = self.parent_window.auto_delete_target_percent if self.parent_window and hasattr(self.parent_window, 'auto_delete_target_percent') else 40
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
        auto_interval = self.parent_window.auto_delete_check_interval if self.parent_window and hasattr(self.parent_window, 'auto_delete_check_interval') else 300
        self.spin_check_interval.setValue(auto_interval)
        self.spin_check_interval.setSuffix(" " + tr("unit_second"))
        self.spin_check_interval.setToolTip(tr("disk_cleanup_auto_interval_tip"))
        self.spin_check_interval.setEnabled(auto_enabled)
        config_grid.addWidget(interval_label, 1, 0)
        config_grid.addWidget(self.spin_check_interval, 1, 1)
        
        auto_layout.addLayout(config_grid)
        
        # 说明文本
        auto_hint = QtWidgets.QLabel(tr("disk_cleanup_auto_hint"))
        auto_hint.setProperty("class", "hint")
        auto_hint.setWordWrap(True)
        auto_layout.addWidget(auto_hint)
        
        # 保存配置按钮
        btn_save_auto = QtWidgets.QPushButton(tr("disk_cleanup_auto_save"))
        btn_save_auto.setProperty("class", "Secondary")
        btn_save_auto.clicked.connect(self._save_auto_config)
        auto_layout.addWidget(btn_save_auto)
        
        auto_box.setContentLayout(auto_layout)
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
        self.action_permanent.setChecked(not self.trash_available)
        
        # 确保只有一个被选中
        self.action_trash.triggered.connect(lambda: self._set_delete_mode(True))
        self.action_permanent.triggered.connect(lambda: self._set_delete_mode(False))
        
        self.btn_delete.clicked.connect(self._delete_files)
        
        btn_delete_dropdown = QtWidgets.QPushButton("▼")
        btn_delete_dropdown.setMaximumWidth(30)
        btn_delete_dropdown.setMinimumHeight(36)
        btn_delete_dropdown.setProperty("class", "Danger")
        btn_delete_dropdown.setProperty("split", "right")
        btn_delete_dropdown.setProperty("hasMenu", True)
        btn_delete_dropdown.setMenu(delete_mode_menu)
        
        delete_group.addWidget(self.btn_delete)
        delete_group.addWidget(btn_delete_dropdown)
        button_layout.addLayout(delete_group)
        
        # 显示当前删除模式（灰色小标签）
        self.delete_mode_label = QtWidgets.QLabel("(回收站)" if self.trash_available else "(永久)")
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
        self.delete_mode_label.setText("(回收站)" if use_trash else "(永久)")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """关闭对话框时清理后台线程"""
        if hasattr(self, "delete_thread") and self.delete_thread and self.delete_thread.isRunning():
            QtWidgets.QMessageBox.warning(self, "正在删除", "正在删除文件，请等待完成后再关闭。")
            event.ignore()
            return

        try:
            if self.scan_worker:
                self.scan_worker.cancel()
            if hasattr(self, "scan_thread") and self.scan_thread and self.scan_thread.isRunning():
                self.scan_thread.quit()
                self.scan_thread.wait(1000)
        except Exception:
            pass

        super().closeEvent(event)
    
    # 事件处理方法
    
    def _choose_custom(self) -> None:
        """选择自定义文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("disk_cleanup_dialog_custom_folder"))
        if path:
            self.edit_custom.setText(path)
    
    def _choose_monitor(self) -> None:
        """选择监控文件夹"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("disk_cleanup_dialog_monitor_folder"))
        if path:
            self.edit_monitor.setText(path)
    
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
        btn_save.clicked.connect(lambda: [self._save_auto_config(), dialog.accept()])
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        dialog.exec()
        
        # 更新状态标签
        auto_enabled = self.cb_enable_auto.isChecked() if hasattr(self, 'cb_enable_auto') else False
        status_text = "已启用" if auto_enabled else "未启用"
        if hasattr(self, 'auto_status_label'):
            self.auto_status_label.setText(f"当前状态: {status_text}")
        if hasattr(self, 'auto_path_label'):
            self.auto_path_label.setText(f"监控路径: {self.edit_monitor.text().strip() or '未设置'}")
    
    def _on_auto_clean_toggled(self, checked: bool) -> None:
        """自动清理开关切换"""
        self.spin_threshold.setEnabled(checked)
        self.spin_target.setEnabled(checked)
        self.spin_check_interval.setEnabled(checked)
    
    def _save_auto_config(self) -> None:
        """保存自动清理配置到父窗口"""
        if not self.parent_window:
            return
        
        try:
            monitor_path = self.edit_monitor.text().strip()
            if self.cb_enable_auto.isChecked():
                if not monitor_path:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        "已启用自动清理，但未设置监控文件夹路径。"
                    )
                    return
                if not os.path.exists(monitor_path):
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        f"监控文件夹不存在：\n{monitor_path}"
                    )
                    return
                if not os.path.isdir(monitor_path):
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        f"监控路径不是文件夹：\n{monitor_path}"
                    )
                    return
                if not os.access(monitor_path, os.R_OK):
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        f"监控文件夹无读取权限：\n{monitor_path}"
                    )
                    return
                if not os.access(monitor_path, os.W_OK):
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        f"监控文件夹无删除/写入权限：\n{monitor_path}"
                    )
                    return
                if self.spin_target.value() >= self.spin_threshold.value():
                    QtWidgets.QMessageBox.warning(
                        self,
                        "配置无效",
                        "目标阈值必须小于触发阈值，请调整配置。",
                    )
                    return

            # 更新父窗口的自动删除配置
            self.parent_window.enable_auto_delete = self.cb_enable_auto.isChecked()
            self.parent_window.auto_delete_folder = monitor_path  # 保存监控文件夹路径
            self.parent_window.auto_delete_threshold = self.spin_threshold.value()
            self.parent_window.auto_delete_target_percent = self.spin_target.value()
            self.parent_window.auto_delete_check_interval = self.spin_check_interval.value()
            
            # 保存配置到文件
            self.parent_window._save_config()
            
            # 显示成功消息
            enabled_text = tr("word_yes") if self.cb_enable_auto.isChecked() else tr("word_no")
            self._append_log_line(
                f"自动清理配置已保存：启用={enabled_text}，监控={monitor_path or tr('disk_cleanup_not_set')}，触发={self.spin_threshold.value()}%，目标={self.spin_target.value()}%"
            )
            QtWidgets.QMessageBox.information(
                self,
                tr("disk_cleanup_config_saved_title"),
                tr(
                    "disk_cleanup_config_saved_body",
                    enabled=enabled_text,
                    monitor=self.edit_monitor.text().strip() or tr("disk_cleanup_not_set"),
                    threshold=self.spin_threshold.value(),
                    target=self.spin_target.value(),
                    interval=self.spin_check_interval.value(),
                ),
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                tr("disk_cleanup_config_save_fail_title"),
                tr("disk_cleanup_config_save_fail_body", error=e),
            )
    
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
            if row >= len(self.file_table.file_items):
                continue
            
            file_item = self.file_table.file_items[row]
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
        if self.scan_worker:
            self.scan_worker.cancel()
            self.btn_cancel_scan.setVisible(False)
            self.progress_bar.setVisible(False)
            self._append_log_line("扫描已取消。")
    
    def _scan_files(self) -> None:
        """扫描符合条件的文件（异步线程）"""
        # 更新摘要
        self._update_summary()
        self._clear_log()
        self._append_log_line("准备扫描...")
        
        self.all_files = []
        self.file_table.load_files([])
        self.stats_label.setText("扫描中...")
        self.progress_label.setText("准备扫描...")

        folders_to_scan: List[str] = []
        if self.cb_backup.isChecked():
            path = self.edit_backup.text().strip()
            if path:
                folders_to_scan.append(path)
        if self.cb_target.isChecked():
            path = self.edit_target.text().strip()
            if path:
                folders_to_scan.append(path)
        if self.cb_monitor.isChecked():
            path = self.edit_monitor.text().strip()
            if path:
                folders_to_scan.append(path)
        if self.cb_custom.isChecked():
            path = self.edit_custom.text().strip()
            if path:
                folders_to_scan.append(path)

        if not folders_to_scan:
            self._append_log_line("未选择任何文件夹，扫描已取消。")
            QtWidgets.QMessageBox.warning(self, "错误", "请至少选择一个文件夹进行扫描！")
            return

        valid_folders: List[str] = []
        invalid_reasons: List[str] = []
        for path in folders_to_scan:
            if not os.path.exists(path):
                invalid_reasons.append(f"{path}：路径不存在")
                continue
            if not os.path.isdir(path):
                invalid_reasons.append(f"{path}：不是文件夹")
                continue
            if not os.access(path, os.R_OK):
                invalid_reasons.append(f"{path}：无读取权限")
                continue
            valid_folders.append(path)

        if invalid_reasons:
            self._append_log_line("以下路径不可用，将被跳过：")
            for line in invalid_reasons:
                self._append_log_line(f"  - {line}")
            QtWidgets.QMessageBox.warning(
                self,
                "路径不可用",
                "部分路径不可用，将跳过这些路径：\n\n" + "\n".join(invalid_reasons)
            )

        if not valid_folders:
            QtWidgets.QMessageBox.warning(self, "错误", "没有可用的扫描路径，请检查路径与权限。")
            return

        formats_to_scan: List[str] = []
        for ext, cb in self.format_checkboxes.items():
            if cb.isChecked():
                formats_to_scan.append(ext.lower())

        custom_format = self.edit_custom_format.text().strip()
        if custom_format:
            for ext in custom_format.split(','):
                ext = ext.strip()
                if ext and not ext.startswith('.'):
                    ext = '.' + ext
                if ext:
                    formats_to_scan.append(ext.lower())

        if not formats_to_scan:
            self._append_log_line("未选择任何文件格式，扫描已取消。")
            QtWidgets.QMessageBox.warning(self, "错误", "请至少选择一种文件格式进行扫描！")
            return

        # 获取过滤天数
        keep_days = self.spin_filter_days.value() if hasattr(self, 'cb_filter_days') and self.cb_filter_days.isChecked() else 0

        # 显示进度条和取消按钮
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 忙碌状态
        self.btn_cancel_scan.setVisible(True)
        
        # 线程扫描
        self.btn_scan.setEnabled(False)
        self.btn_delete.setEnabled(False)
        
        self.scan_thread = QtCore.QThread(self)
        self.scan_worker = ScanWorker(valid_folders, formats_to_scan, keep_days)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_worker.progress_detail.connect(self._on_scan_progress)
        self.scan_worker.progress.connect(self._append_log_line)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(lambda: setattr(self, "scan_thread", None))
        self.scan_thread.start()
    
    def _on_scan_progress(self, current_dir: str, file_count: int, total_size: int) -> None:
        """更新扫描进度"""
        size_mb = total_size / (1024 * 1024)
        # 简化显示路径
        if len(current_dir) > 50:
            current_dir = "..." + current_dir[-47:]
        self.progress_label.setText(f"扫描: {file_count} 文件 | {size_mb:.1f} MB | {current_dir}")
    
    def _delete_files(self) -> None:
        """删除选中的文件（异步线程，支持回收站）"""
        checked_files = self.file_table.get_checked_files()
        if not checked_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有选中任何文件！")
            return

        total_size = sum(f.size for f in checked_files)
        
        # 生成清理清单摘要
        summary = self._generate_delete_summary(checked_files)
        
        # 从下拉菜单获取删除模式
        use_trash = self.action_trash.isChecked()
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

        self.delete_thread = QtCore.QThread(self)
        self.delete_worker = DeleteWorker(checked_files, use_trash)
        self.delete_worker.moveToThread(self.delete_thread)
        self.delete_worker.progress_value.connect(self._on_delete_progress_value)
        self.delete_worker.progress.connect(self._append_log_line)
        self.delete_worker.finished.connect(self._on_delete_finished)
        self.delete_thread.started.connect(self.delete_worker.run)
        self.delete_worker.finished.connect(self.delete_thread.quit)
        self.delete_thread.finished.connect(self.delete_worker.deleteLater)
        self.delete_thread.finished.connect(lambda: setattr(self, "delete_thread", None))
        self.delete_thread.start()
    
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

        self.btn_scan.setEnabled(True)
        self.btn_delete.setEnabled(len(self.all_files) > 0)

    def _on_delete_progress_value(self, current: int, total: int) -> None:
        """删除进度更新"""
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"正在删除: {current} / {total}")

    def _on_delete_finished(self, deleted_count: int, deleted_size: int, failed_count: int) -> None:
        """删除完成回调"""
        # 隐藏进度条
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
            self._append_log_line(f"删除完成，失败 {failed_count} 个文件。建议重新扫描确认。")
        else:
            self._append_log_line("删除完成，未发现失败项。")
        
        # 依据文件实际是否存在来更新列表
        remaining_files = [f for f in self.all_files if os.path.exists(f.path)]
        
        self.all_files = remaining_files
        self.file_table.load_files(self.all_files)
        
        # 更新统计
        total_size = sum(f.size for f in self.all_files)
        size_mb_total = total_size / (1024 * 1024)
        size_gb_total = total_size / (1024 * 1024 * 1024)
        self.stats_label.setText(
            f"剩余 {len(self.all_files)} 文件 | {size_mb_total:.1f} MB ({size_gb_total:.2f} GB)"
        )
        self.progress_label.setText(f"删除完成，成功 {deleted_count} 个")

        self.btn_scan.setEnabled(True)
        self.btn_delete.setEnabled(len(self.all_files) > 0)

        QtWidgets.QMessageBox.information(
            self,
            "删除完成",
            result_text
        )
