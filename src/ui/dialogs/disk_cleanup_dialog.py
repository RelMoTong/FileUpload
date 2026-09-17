# -*- coding: utf-8 -*-
"""磁盘清理对话框及其纯展示层辅助组件。
文件名：src/ui/dialogs/disk_cleanup_dialog.py
文件作用：Qt 界面层的“disk_cleanup_dialog”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。


本模块只负责收集用户输入、显示进度和展示扫描结果。真正的目录遍历与删除都由
控制器、服务层和后台 Worker 执行，因此这里不能直接调用 ``os.remove`` 或在 UI
线程中扫描网络盘。
"""

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
    """读取清理界面的本地化文本，并填入动态参数。"""
    return t(key, key).format(**kwargs)


class CleanupGateway(Protocol):
    @property
    def trash_available(self) -> bool:
        """协议占位：声明“trash_available”的最小调用约定，由实现方提供既有行为。"""
        ...
    @property
    def is_scanning(self) -> bool:
        """协议占位：声明“is_scanning”的最小调用约定，由实现方提供既有行为。"""
        ...
    @property
    def is_deleting(self) -> bool:
        """协议占位：声明“is_deleting”的最小调用约定，由实现方提供既有行为。"""
        ...
    def set_manual_listener(self, listener: Any) -> None:
        """协议占位：声明“set_manual_listener”的最小调用约定，由实现方提供既有行为。"""
        ...
    def validate_scan_request(self, request: CleanupScanRequest) -> Any:
        """协议占位：声明“validate_scan_request”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_scan(self, request: CleanupScanRequest) -> Any:
        """协议占位：声明“start_scan”的最小调用约定，由实现方提供既有行为。"""
        ...
    def cancel_scan(self) -> None:
        """协议占位：声明“cancel_scan”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_delete(self, request: CleanupDeleteRequest) -> Any:
        """协议占位：声明“start_delete”的最小调用约定，由实现方提供既有行为。"""
        ...
    def close_manual(self) -> None:
        """协议占位：声明“close_manual”的最小调用约定，由实现方提供既有行为。"""
        ...


class CleanupSettingsGateway(Protocol):
    @property
    def cleanup_role(self) -> str:
        """协议占位：声明“cleanup_role”的最小调用约定，由实现方提供既有行为。"""
        ...
    @property
    def cleanup_settings_error(self) -> str:
        """协议占位：声明“cleanup_settings_error”的最小调用约定，由实现方提供既有行为。"""
        ...
    def cleanup_settings_snapshot(self) -> Dict[str, Any]:
        """协议占位：声明“cleanup_settings_snapshot”的最小调用约定，由实现方提供既有行为。"""
        ...
    def save_auto_cleanup_settings(self, config: Dict[str, Any]) -> bool:
        """协议占位：声明“save_auto_cleanup_settings”的最小调用约定，由实现方提供既有行为。"""
        ...


def calculate_dialog_responsive_metrics(
    available_width: int, available_height: int
) -> Dict[str, int]:
    """按当前屏幕可用区域计算清理窗口的安全尺寸。

    用途：保证现场电脑分辨率较小时窗口仍能显示核心按钮和结果表格。
    输入：屏幕可用宽度、高度。
    输出：最小尺寸和初始尺寸组成的字典。
    关键步骤：先设置可用下限，再按屏幕比例计算，最后使用上下限夹住结果。
    风险点：不能简单写死窗口大小，否则低分辨率设备会把确认和关闭按钮挤出屏幕。
    """
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


def format_cleanup_size(size: int) -> str:
    """把任意精度字节数转换为面向界面的容量文本。

    用途：统一显示单个文件和扫描总量，并支持 100 TB 以上容量。
    输入：Python 整数类型的字节数。
    输出：带 B、KB、MB、GB 或 TB 单位的文本。
    关键步骤：仅在展示时逐级换算；原始整数始终保存在模型或扫描统计中。
    风险点：此函数不能参与业务计算，浮点换算只适合显示，不能作为删除或阈值判断依据。
    """
    size_float = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024.0:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024.0
    return f"{size_float:.1f} TB"


class CleanupFileListModel(QtCore.QAbstractTableModel):  # type: ignore[misc]
    """保存全部清理候选、仅为可见行提供数据的虚拟化列表模型。

    用途：替代 QTableWidget 为每个文件创建五个 Qt 项目的做法，支持大量文件增量显示。
    输入：扫描 Worker 分批提交的 CleanupFileItem 列表。
    输出：QTableView 所需的单元格文本、勾选状态和排序原始值。
    关键步骤：只保存 Python 数据对象；Qt 仅向当前滚动区域请求可见行数据。
    风险点：候选列表仍需保留到用户确认删除，不能在扫描未结束时丢弃身份字段。
    """

    check_state_changed = Signal()
    _HEADERS = ("", "文件名", "路径", "大小", "修改时间")

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        """界面辅助：完成“__init__”对应的既有局部显示或事件工作。"""
        super().__init__(parent)
        self.file_items: List[FileItem] = []

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        """作用：执行界面“rowCount”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        return 0 if parent.isValid() else len(self.file_items)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        """作用：执行界面“columnCount”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """作用：执行界面“headerData”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        if (
            orientation == Qt.Orientation.Horizontal
            and role == int(Qt.ItemDataRole.DisplayRole)
            and 0 <= section < len(self._HEADERS)
        ):
            return self._HEADERS[section]
        return None

    def data(  # noqa: N802
        self,
        index: QtCore.QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """按 Qt 请求的角色提供单元格数据，而不是提前创建所有可视控件。

        用途：支持数十万条扫描结果滚动显示。
        输入：代理模型给出的行列索引和所需数据角色。
        输出：显示文本、勾选状态或供排序使用的原始 ``CleanupFileItem``。
        关键步骤：先校验索引，再只读取该行的 Python 对象，按列/角色返回所需内容。
        风险点：不要在这里遍历全部候选或做耗时格式化，否则滚动表格会再次卡顿。
        """
        if not index.isValid() or not 0 <= index.row() < len(self.file_items):
            return None
        file_item = self.file_items[index.row()]
        column = index.column()
        if role == int(Qt.ItemDataRole.UserRole):
            return file_item
        if role == int(Qt.ItemDataRole.CheckStateRole) and column == 0:
            return Qt.CheckState.Checked if file_item.checked else Qt.CheckState.Unchecked
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if column == 1:
            return file_item.name
        if column == 2:
            return file_item.path
        if column == 3:
            return format_cleanup_size(file_item.size)
        if column == 4:
            return datetime.fromtimestamp(file_item.mtime).strftime("%Y-%m-%d %H:%M:%S")
        return ""

    def flags(self, index: QtCore.QModelIndex) -> Qt.ItemFlag:
        """作用：执行界面“flags”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(  # noqa: N802
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = int(Qt.ItemDataRole.EditRole),
    ) -> bool:
        """处理第一列复选框的状态变更，并通知依赖勾选状态的界面。

        用途：把用户勾选结果写回候选对象，供“仅看已选”和删除请求读取。
        输入：Qt 编辑索引、目标状态和数据角色。
        输出：本次请求被处理时返回 ``True``，非复选框编辑返回 ``False``。
        关键步骤：限定首列和 CheckStateRole、更新对象、发出最小范围 dataChanged 信号。
        风险点：不能只更新视图文本而不更新对象，否则删除时会得到过期的勾选结果。
        """
        if (
            not index.isValid()
            or index.column() != 0
            or role != int(Qt.ItemDataRole.CheckStateRole)
        ):
            return False
        file_item = self.file_items[index.row()]
        # ``.value`` 是枚举的整数值；它与 Qt 发来的状态值比较，既保持原有行为，
        # 也避免静态检查把 PySide6 枚举对象误判为不能传给 int。
        checked = int(value) == Qt.CheckState.Checked.value
        if file_item.checked == checked:
            return True
        file_item.checked = checked
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.CheckStateRole)])
        self.check_state_changed.emit()
        return True

    def replace_files(self, file_items: List[FileItem]) -> None:
        """一次替换列表引用，用于新扫描或删除完成后重置数据源。

        这样做会触发 Qt 的完整模型重置，仅适用于“开始新扫描”或“删除完成”这类
        数据集整体变化的时刻；扫描进行中必须改用 ``append_files``。
        """
        self.beginResetModel()
        self.file_items = file_items
        self.endResetModel()

    def append_files(self, file_items: List[FileItem]) -> None:
        """把扫描到的小批次插入模型，使界面立即显示而不重建已有可见行。

        ``beginInsertRows/endInsertRows`` 告知 Qt 只更新新增行；这比反复重置整个
        表格更适合海量扫描结果，也让取消按钮继续获得主线程事件循环的处理机会。
        """
        if not file_items:
            return
        first_row = len(self.file_items)
        self.beginInsertRows(QtCore.QModelIndex(), first_row, first_row + len(file_items) - 1)
        self.file_items.extend(file_items)
        self.endInsertRows()

    def set_all_checked(self, checked: bool) -> None:
        """批量更新勾选状态，只通知一次视图和删除按钮。

        虽然此处必须遍历全部候选以修改业务状态，但只发出一个连续范围的 Qt 信号，
        避免为每个文件创建一次 UI 事件。
        """
        if not self.file_items:
            return
        changed = any(file_item.checked != checked for file_item in self.file_items)
        if not changed:
            return
        for file_item in self.file_items:
            file_item.checked = checked
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self.file_items) - 1, 0)
        self.dataChanged.emit(top_left, bottom_right, [int(Qt.ItemDataRole.CheckStateRole)])
        self.check_state_changed.emit()


class CleanupFileFilterProxyModel(QtCore.QSortFilterProxyModel):  # type: ignore[misc]
    """把搜索和快捷筛选放在代理模型中，避免逐行隐藏大量 Qt 控件。

    用途：在不复制候选列表、不创建单元格控件的前提下筛选当前显示内容。
    输入：源模型和搜索/勾选/大小/时间四类筛选条件。
    输出：QTableView 看到的只是符合条件的代理行，源模型仍保留所有候选。
    关键步骤：每次条件改变后让 Qt 重新计算行可见性；排序时比较原始文件字段。
    风险点：删除必须从源模型中的已勾选文件获取，不能只读取当前筛选后的可见行。
    """

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        """界面辅助：完成“__init__”对应的既有局部显示或事件工作。"""
        super().__init__(parent)
        self._search_text = ""
        self._show_checked_only = False
        self._show_large_only = False
        self._show_recent_only = False
        self._size_threshold = 10 * 1024 * 1024
        self._recent_cutoff = 0.0

    def set_filters(
        self,
        search_text: str,
        show_checked_only: bool,
        show_large_only: bool,
        show_recent_only: bool,
        recent_cutoff: float,
    ) -> None:
        """更新全部筛选条件，并由 Qt 重新计算当前可见的实际行。"""
        self._search_text = search_text.strip().casefold()
        self._show_checked_only = show_checked_only
        self._show_large_only = show_large_only
        self._show_recent_only = show_recent_only
        self._recent_cutoff = recent_cutoff
        self.refresh_filters()

    def refresh_filters(self) -> None:
        """通知 Qt 仅重新计算行过滤，避免调用已废弃的全量失效接口。"""
        self.beginFilterChange()
        self.endFilterChange(QtCore.QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(  # noqa: N802
        self, source_row: int, source_parent: QtCore.QModelIndex
    ) -> bool:
        """判断源模型的一行是否满足全部当前筛选条件。"""
        source_model = self.sourceModel()
        if not isinstance(source_model, CleanupFileListModel):
            return True
        if not 0 <= source_row < len(source_model.file_items):
            return False
        file_item = source_model.file_items[source_row]
        if self._search_text and (
            self._search_text not in file_item.name.casefold()
            and self._search_text not in file_item.path.casefold()
        ):
            return False
        if self._show_checked_only and not file_item.checked:
            return False
        if self._show_large_only and file_item.size < self._size_threshold:
            return False
        return not self._show_recent_only or file_item.mtime >= self._recent_cutoff

    def lessThan(  # noqa: N802
        self, left: QtCore.QModelIndex, right: QtCore.QModelIndex
    ) -> bool:
        """按原始大小、时间和文本排序，避免按“1.0 GB”之类展示文本排序。"""
        left_item = left.data(int(Qt.ItemDataRole.UserRole))
        right_item = right.data(int(Qt.ItemDataRole.UserRole))
        if not isinstance(left_item, CleanupFileItem) or not isinstance(right_item, CleanupFileItem):
            return super().lessThan(left, right)
        if left.column() == 3:
            return left_item.size < right_item.size
        if left.column() == 4:
            return left_item.mtime < right_item.mtime
        if left.column() == 1:
            return left_item.name.casefold() < right_item.name.casefold()
        if left.column() == 2:
            return left_item.path.casefold() < right_item.path.casefold()
        return left.row() < right.row()


class FileListTable(QtWidgets.QTableView):  # type: ignore[misc]
    """采用模型/视图虚拟化的清理结果表格，不使用固定分页上限。

    用途：显示全部已发现文件，但只由 Qt 为当前视口绘制必要的行。
    输入：扫描 Worker 持续发来的小批 ``CleanupFileItem``。
    输出：筛选、排序、勾选和右键菜单操作所需的表格视图。
    关键步骤：源模型保存数据、代理模型筛选排序、视图按需请求可见单元格。
    风险点：不能退回 QTableWidget 的“每个文件五个对象”模式，海量文件会耗尽 UI 内存。
    """

    check_state_changed = Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        """创建源模型、代理模型和视图，并连接勾选状态变化。"""
        super().__init__(parent)
        self._source_model = CleanupFileListModel(self)
        self._proxy_model = CleanupFileFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._source_model)
        self.setModel(self._proxy_model)
        self._setup_table()
        self._setup_context_menu()
        self._source_model.check_state_changed.connect(self._on_check_state_changed)

    @property
    def file_items(self) -> List[FileItem]:
        """返回模型持有的候选列表；不会额外复制大规模扫描结果。"""
        return self._source_model.file_items

    def _setup_table(self) -> None:
        """设置列宽和选择方式；排序仅在用户点击表头时执行。"""
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._sort_by_clicked_column)
        self.setColumnWidth(0, 40)
        self.setColumnWidth(1, 200)
        self.setColumnWidth(3, 100)
        self.setColumnWidth(4, 150)

    def _setup_context_menu(self) -> None:
        """设置与当前实际可见行对应的右键菜单。"""
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _on_check_state_changed(self) -> None:
        """勾选状态会影响“仅看已选”筛选，因此同时刷新代理和删除按钮。"""
        self._proxy_model.refresh_filters()
        self.check_state_changed.emit()

    def _sort_by_clicked_column(self, column: int) -> None:
        """按用户选择的列排序；扫描期间不自动排序，避免大列表反复重排。"""
        header = self.horizontalHeader()
        order = (
            Qt.SortOrder.DescendingOrder
            if header.sortIndicatorSection() == column
            and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        header.setSortIndicator(column, order)
        self._proxy_model.sort(column, order)
        self._proxy_model.setDynamicSortFilter(True)

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        """从代理索引读取源文件对象，再显示文件操作菜单。"""
        index = self.indexAt(pos)
        file_item = index.data(int(Qt.ItemDataRole.UserRole)) if index.isValid() else None
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
        """调用当前操作系统的文件管理器定位用户选择的文件。"""
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["explorer", "/select,", file_path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            elif platform.system() == "Darwin":
                subprocess.run(
                    ["open", "-R", file_path],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            else:
                subprocess.run(
                    ["xdg-open", os.path.dirname(file_path)],
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "错误", f"无法打开文件夹：{exc}")

    def load_files(self, file_items: List[FileItem]) -> None:
        """替换当前结果列表；Qt 只按滚动区域请求实际需要显示的行。

        在替换前暂停动态排序，避免模型重置过程中对每个新索引重新比较；用户点击
        表头后仍可按需要排序。
        """
        self._proxy_model.setDynamicSortFilter(False)
        self._source_model.replace_files(file_items)

    def clear_files(self) -> None:
        """清空当前扫描结果，并返回由表格持有的新空列表。"""
        self.load_files([])

    def append_files(self, file_items: List[FileItem]) -> None:
        """追加刚扫描到的一小批结果，不重建已有可见行。"""
        self._source_model.append_files(file_items)

    def set_filters(
        self,
        search_text: str,
        show_checked_only: bool,
        show_large_only: bool,
        show_recent_only: bool,
        recent_cutoff: float,
    ) -> None:
        """把搜索和快捷条件交给代理模型，避免遍历并隐藏表格控件。"""
        self._proxy_model.set_filters(
            search_text,
            show_checked_only,
            show_large_only,
            show_recent_only,
            recent_cutoff,
        )

    def get_checked_files(self) -> List[FileItem]:
        """返回全部已勾选候选，不受当前搜索或快捷筛选影响。"""
        return [file_item for file_item in self.file_items if file_item.checked]

    def select_all(self) -> None:
        """勾选全部扫描结果。"""
        self._source_model.set_all_checked(True)

    def select_none(self) -> None:
        """取消勾选全部扫描结果。"""
        self._source_model.set_all_checked(False)

class DiskCleanupDialog(QtWidgets.QDialog):  # type: ignore[misc]
    """按目录与扩展名执行手动清理，并提供自动清理配置入口。

    用途：给管理员提供扫描、筛选、二次确认删除和自动清理配置界面。
    输入：清理控制器、设置网关以及可选父窗口。
    输出：通过控制器启动异步任务，并把异步事件呈现为进度、日志和结果。
    关键步骤：收集设置、先校验再启动、接收增量事件、删除前二次确认、关闭时非阻塞取消。
    风险点：此窗口只展示和发命令；网络扫描和删除必须留在后台线程，永久删除必须二次授权。

    参数：
        parent：仅用于 Qt 窗口所有权。

    说明：``type: ignore[misc]`` 是 Qt 动态导入导致的 Pylance 误报抑制标记。
    """

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        cleanup_controller: Optional[CleanupGateway] = None,
        settings_gateway: Optional[CleanupSettingsGateway] = None,
    ):
        """创建窗口并绑定控制器事件监听器。

        用途：初始化界面状态、读取当前设置快照，并确保所有手动事件回到本窗口。
        输入：可选父窗口、必填清理控制器、可选设置网关。
        输出：已构建但未启动扫描的清理对话框。
        关键步骤：先读取快照，再登记监听器，最后构建控件和权限状态。
        风险点：控制器不能为空；如果未登记监听器，后台扫描完成后界面不会恢复按钮状态。
        """
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
        self._scan_file_count = 0
        self._scan_total_size_bytes = 0
        self._scan_cancel_requested = False
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
        """安全读取设置快照；设置网关异常时返回空字典，让窗口仍可打开。"""
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
        """根据主窗口角色和运行状态更新对话框操作权限。

        用途：统一处理管理员、普通用户和未登录状态，避免只禁用部分危险按钮。
        输入：当前设置网关暴露的角色，以及已创建的控件。
        输出：可操作控件按权限启用/禁用，删除按钮还会检查是否确实勾选了文件。
        关键步骤：枚举业务控件、更新目录行、更新阈值控件、更新删除模式菜单。
        风险点：权限控制不能只依赖 UI 禁用；后续控制器/服务层仍会执行业务校验。
        """
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
        """复选框状态变化时刷新删除按钮。"""
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
        """构建左右分栏的主界面，并按屏幕大小设置安全初始尺寸。

        用途：将扫描设置与结果表格同时展示，减少现场人员在多个窗口之间切换。
        输入：当前显示器可用区域；无显示器对象时使用保守默认尺寸。
        输出：包含设置区、结果区和底部操作区的完整窗口布局。
        关键步骤：应用样式、计算尺寸、创建左右 Splitter、设置伸缩比例、放入底部按钮。
        风险点：不能在此处启动扫描；构造期间启动后台任务会导致控件尚未创建就收到事件。
        """
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
        """界面辅助：完成“_append_log_line”对应的既有局部显示或事件工作。"""
        if hasattr(self, 'log_view'):
            self.log_view.appendPlainText(text.rstrip())

    def _clear_log(self) -> None:
        """界面辅助：完成“_clear_log”对应的既有局部显示或事件工作。"""
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
        """将快捷筛选状态交给虚拟化代理模型重新计算可见行。"""
        self._filter_files()

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
        """处理标签页切换：首次打开时才构建高级设置页，减少初始创建成本。"""
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
        """更新删除模式菜单与显示标签，但不在这里执行删除。

        用途：让用户明确选择“移入回收站”或“永久删除”。
        输入：是否使用回收站。
        输出：两个菜单项、显式永久删除标记和提示标签保持一致。
        关键步骤：始终互斥设置两个选项，再记录永久模式是否由用户主动选择。
        风险点：回收站不可用时不能自动改成永久删除，必须由用户显式选择并二次确认。
        """
        self.action_trash.setChecked(use_trash)
        self.action_permanent.setChecked(not use_trash)
        self._permanent_mode_explicit = not use_trash
        self.delete_mode_label.setText("(回收站)" if use_trash else "(永久)")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """关闭对话框时只请求取消，不在 GUI 线程等待网络盘扫描线程退出。

        用途：避免 SMB 或网络盘 I/O 卡住时，关闭清理窗口连带冻结整个应用。
        输入：Qt 发送的关闭事件。
        输出：删除任务运行中拒绝关闭；其他情况立即关闭窗口并解除事件监听。
        关键步骤：删除中先阻止关闭；扫描中发出协作取消；随后委托控制器解绑监听。
        风险点：系统级 I/O 无法强制中断，后台线程会在当前 I/O 返回后自行收尾。
        """
        if self.cleanup_controller.is_deleting:
            QtWidgets.QMessageBox.warning(
                self, "正在删除", "正在删除文件，请等待完成后再关闭。"
            )
            event.ignore()
            return
        if self.cleanup_controller.is_scanning:
            self.cleanup_controller.cancel_scan()
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
        """打开自动清理配置窗口，并复用当前窗口的配置控件。

        用途：把可能不常用的自动策略配置从手动扫描主界面中分离出来。
        输入：无；从当前内存设置和目录控件读取默认值。
        输出：用户保存时由设置网关写入配置，取消时不修改任何配置。
        关键步骤：先校验管理员权限、创建模态窗口、连接保存/取消、关闭后刷新摘要。
        风险点：这里不能直接写 ``config.json``，所有持久化必须经过设置网关以统一校验和报错。
        """
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
            """界面辅助：完成“_on_save_clicked”对应的既有局部显示或事件工作。"""
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
        """通过显式设置网关校验并保存自动清理配置。

        用途：把 UI 控件值转换为最小配置字典，并请求主流程持久化。
        输入：当前目录、阈值、格式和启用状态控件的值。
        输出：保存成功返回 ``True``；任何校验/写入失败返回 ``False`` 并提示用户。
        关键步骤：权限校验、收集目录、验证阈值、首次启用二次确认、调用网关、读回快照。
        风险点：手动清理扫描配置不写入 ``config.json``；这里只保存自动清理策略，且固定回收站模式。
        """
        if self.settings_gateway is None:
            return False
        if not self._ensure_cleanup_permission("保存自动清理配置"):
            return False

        try:
            # 自动清理必须保留隐藏的已保存目录，避免用户尚未展开高级区域时意外丢失配置。
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
                # 只有从“未启用”切换到“启用”时才弹出二次确认，
                # 这样既强调风险，也不会让正常的后续参数调整反复打断操作。
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

            # 将逗号分隔的扩展名输入转换为配置列表；具体格式规范化由服务层统一处理。
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

            # 通过网关持久化，不直接触碰 config.json，保证主窗口的配置与运行时状态同步。
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
        """根据搜索框和快捷条件更新代理模型，不逐行操作大量视图控件。

        用途：在海量扫描结果中快速缩小显示范围。
        输入：搜索框和四个快捷筛选控件的当前状态。
        输出：代理模型重新决定哪些行可见；源候选列表不会丢失。
        关键步骤：计算“最近七天”的临界时间，再一次性提交全部筛选条件。
        风险点：不能调用逐行 ``setRowHidden``，那会为大列表制造大量同步 UI 操作。
        """
        search_text = self.search_edit.text()
        import time
        cutoff_time_7days = time.time() - (7 * 24 * 3600)
        self.file_table.set_filters(
            search_text,
            self.chip_show_checked.isChecked(),
            self.chip_show_large.isChecked(),
            self.chip_show_recent.isChecked(),
            cutoff_time_7days,
        )

    def _cancel_scan(self) -> None:
        """请求后台扫描在下一个可取消点停止，界面保持可操作。

        用途：允许用户中止大目录或网络盘扫描，而不冻结窗口。
        输入：无；当前 Worker 由控制器持有。
        输出：取消标记被设置，取消按钮隐藏，状态文本说明仍在等待系统 I/O 返回。
        关键步骤：只发出协作取消请求，不等待线程，再更新界面提示。
        风险点：``stat/scandir`` 可能被 SMB 阻塞，Python 无法强制打断，不能在这里调用 ``wait``。
        """
        self.cleanup_controller.cancel_scan()
        self._scan_cancel_requested = True
        self.btn_cancel_scan.setVisible(False)
        self.progress_label.setText("正在请求取消扫描，请稍候...")
        self._append_log_line("已请求取消扫描；网络盘当前 I/O 返回后将结束。")

    def _scan_files(self) -> None:
        """收集扫描输入并委托控制器启动后台文件系统任务。

        用途：将目录、扩展名和保留天数组装为请求，同时把耗时扫描留在 Worker 中。
        输入：目录行、格式复选框、自定义扩展名和可选保留天数。
        输出：扫描成功启动后显示不确定进度；校验失败时显示原因且不创建 Worker。
        关键步骤：权限检查、清空旧结果、收集/校验目录与格式、构建请求、交给控制器启动。
        风险点：不能在 UI 线程使用 ``os.scandir``；新扫描必须先清空旧候选，防止删除到上次结果。
        """
        if not self._ensure_cleanup_permission("扫描文件"):
            return

        self._update_summary()
        self._clear_log()
        self._append_log_line("准备扫描...")
        # 先让源模型整体重置，再把 all_files 指向新列表；后续 Worker 结果会增量追加到它。
        self.file_table.clear_files()
        self.all_files = self.file_table.file_items
        self._scan_file_count = 0
        self._scan_total_size_bytes = 0
        self._scan_cancel_requested = False
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

        # 基础复选框和自定义文本框共同构成格式过滤；服务层最终还会进行格式规范化。
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
        # 在启动线程前先做同步轻量校验，避免 Worker 运行后才发现目录不存在或无权限。
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

        # 目录总数未知，使用不确定进度条；实际文件数和容量由异步进度事件更新。
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
        """按事件类型把 Worker/服务层消息分发给对应的纯 UI 更新方法。

        用途：将跨线程桥接后的统一字典事件还原为界面动作。
        输入：至少带有 ``type`` 字段的事件字典。
        输出：日志、扫描进度、增量文件、完成状态或删除进度被更新到相应控件。
        关键步骤：每种事件只调用一个专门方法；新旧扫描汇总字段同时兼容。
        风险点：这里绝不能重新扫描、排序全部文件或执行删除，否则队列事件会阻塞主线程。
        """
        event_type = event.get("type", "")
        if event_type == "log":
            self._append_log_line(str(event.get("message", "")))
        elif event_type == "scan_progress":
            self._on_scan_progress(
                str(event.get("current_dir", "")),
                int(event.get("file_count", 0)),
                # 新 Worker 使用明确的字节字段；保留旧字段兼容分阶段升级。
                int(event.get("total_size_bytes", event.get("total_size", 0))),
            )
        elif event_type == "scan_items":
            self._on_scan_items(list(event.get("files", ())))
        elif event_type == "scan_finished":
            self._on_scan_finished(
                list(event.get("files", ())),
                int(event.get("file_count", len(self.all_files))),
                int(event.get("total_size_bytes", self._scan_total_size_bytes)),
                bool(event.get("cancelled", self._scan_cancel_requested)),
            )
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
        """更新不受 32 位限制的扫描计数、容量和当前目录提示。

        总量使用 ``max`` 是为了容忍队列中较早的进度事件晚到；统计值不会因为事件乱序而倒退。
        """
        self._scan_file_count = max(self._scan_file_count, file_count)
        self._scan_total_size_bytes = max(self._scan_total_size_bytes, total_size)
        # 简化显示路径
        if len(current_dir) > 50:
            current_dir = "..." + current_dir[-47:]
        self.progress_label.setText(
            f"扫描: {file_count} 文件 | {format_cleanup_size(total_size)} | {current_dir}"
        )
        self.stats_label.setText(
            f"扫描中：已发现 {len(self.all_files)} 文件 | "
            f"{format_cleanup_size(self._scan_total_size_bytes)}"
        )

    def _on_scan_items(self, files: List[FileItem]) -> None:
        """把刚发现的小批次直接追加到虚拟化表格，无需等待全量扫描完成。

        用途：让用户及时看到结果，同时避免一次性创建大量 Qt 表格项。
        输入：后台 Worker 已完成身份采样的一批文件。
        输出：表格和内部候选列表同步追加这些文件。
        关键步骤：模型插入行后复用其列表引用，再更新当前统计文本。
        风险点：不能在此处排序全部候选，否则大量文件会重新阻塞 GUI 线程。
        """
        if not files:
            return
        self.file_table.append_files(files)
        self.all_files = self.file_table.file_items
        self.stats_label.setText(
            f"扫描中：已发现 {len(self.all_files)} 文件 | "
            f"{format_cleanup_size(self._scan_total_size_bytes)}"
        )

    def _delete_files(self) -> None:
        """经摘要与二次确认后，提交已勾选文件的异步删除请求。

        用途：将用户当前选择转交给后台删除 Worker，并在启动前充分提示不可恢复风险。
        输入：源模型中已勾选的候选、当前删除模式和已扫描的允许根目录。
        输出：删除任务启动后显示进度；用户取消确认或校验失败时不改变任何文件。
        关键步骤：权限校验、读取全部已选项、展示摘要、永久删除口令确认、构建安全请求、启动 Worker。
        风险点：不能只删除筛选后可见行；永久删除必须由用户主动选模式并输入 DELETE。
        """
        if not self._ensure_cleanup_permission("删除文件"):
            return

        checked_files = self.file_table.get_checked_files()
        if not checked_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有选中任何文件！")
            return

        # 文件大小是 Python 整数相加，不使用 Qt 控件数值，避免超大容量发生溢出。
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

        # “菜单选中永久删除”与“输入 DELETE”是两次独立确认，避免误点造成不可恢复删除。
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

        # allowed_roots 来自本轮已校验扫描目录；Worker/策略会再次验证路径不越界。
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
        """生成删除前摘要，只展示最大的五个文件以便用户快速复核。

        用途：在确认框中提供具体文件示例，而不是只有抽象数量。
        输入：全部已勾选候选。
        输出：包含最大五项和剩余数量的多行文本。
        关键步骤：按大小降序排序、截取前五项、追加其余数量说明。
        风险点：摘要用于人工复核，不是删除依据；真正删除仍以完整 ``files`` 列表为准。
        """
        sorted_files = sorted(files, key=lambda x: x.size, reverse=True)
        top_files = sorted_files[:5]

        summary_lines = ["Top 5 最大文件:"]
        for i, file in enumerate(top_files, 1):
            size_mb = file.size / (1024 * 1024)
            summary_lines.append(f"  {i}. {file.name} ({size_mb:.2f} MB)")

        if len(files) > 5:
            summary_lines.append(f"  ... 及其他 {len(files) - 5} 个文件")

        return "\n".join(summary_lines)

    def _on_scan_finished(
        self,
        legacy_files: List[FileItem],
        file_count: int,
        total_size: int,
        cancelled: bool,
    ) -> None:
        """完成增量扫描并恢复操作按钮，不再执行全量排序或全量渲染。

        用途：收尾新流式协议，也兼容旧 Worker 一次性返回的文件列表。
        输入：可选旧列表、Worker 汇总计数、总字节数及取消状态。
        输出：界面显示最终统计，并允许用户开始下一次扫描或删除。
        关键步骤：旧协议才替换表格列表；新协议复用已增量插入的模型列表。
        风险点：取消结果同样可供用户核对，但不能把“取消”误报为完整扫描完成。
        """
        if legacy_files:
            self.file_table.load_files(legacy_files)
        self.all_files = self.file_table.file_items
        self._scan_file_count = max(file_count, len(self.all_files))
        self._scan_total_size_bytes = max(total_size, self._scan_total_size_bytes)

        # 隐藏进度条和取消按钮，并恢复下一步操作。
        self.progress_bar.setVisible(False)
        self.btn_cancel_scan.setVisible(False)
        self.stats_label.setText(
            f"共 {len(self.all_files)} 文件 | "
            f"{format_cleanup_size(self._scan_total_size_bytes)}"
        )
        if cancelled:
            self.progress_label.setText(
                f"扫描已取消，已发现 {len(self.all_files)} 个文件"
            )
            self._append_log_line(
                f"扫描已取消，保留已发现的 {len(self.all_files)} 个文件供核对。"
            )
        else:
            self.progress_label.setText(f"扫描完成，找到 {len(self.all_files)} 个文件")
            self._append_log_line(f"扫描完成，找到 {len(self.all_files)} 个文件。")

        can_manage = self._can_manage_cleanup()
        self.btn_scan.setEnabled(can_manage)
        self.btn_delete.setEnabled(can_manage and bool(self.file_table.get_checked_files()))

    def _on_delete_progress_value(self, current: int, total: int) -> None:
        """仅更新删除进度控件；文件系统操作已经在后台 Worker 完成。"""
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"正在删除: {current} / {total}")

    def _on_delete_finished(
        self,
        deleted_count: int,
        deleted_size: int,
        failed_count: int,
        remaining_files: List[FileItem],
    ) -> None:
        """展示服务层返回的删除汇总，并用剩余候选重置表格。

        用途：让用户知道成功、失败和仍可继续处理的文件，而不是假设全部删除成功。
        输入：删除数量、删除字节数、失败数量和仍存在的候选列表。
        输出：进度隐藏、结果提示、表格/统计/按钮恢复为新的稳定状态。
        关键步骤：先生成结果文本，再替换源模型，最后基于剩余文件重新计算界面统计。
        风险点：失败文件必须保留在表格；不能仅按“请求数量”从界面删除，避免掩盖失败或身份变更。
        """
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

        self.file_table.load_files(remaining_files)
        self.all_files = self.file_table.file_items
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
