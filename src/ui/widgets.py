# -*- coding: utf-8 -*-
"""
文件名：src/ui/widgets.py
文件作用：Qt 界面层的“widgets”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。

Reusable generic Qt widgets.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    QtEnum = Qt
else:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    QtEnum = Qt


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
        """作用：执行界面“__init__”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
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
            p: QtWidgets.QWidget = self.parent()  # type: ignore[assignment]
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
        """作用：执行界面“__init__”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
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
        """作用：执行界面“__init__”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        super().__init__(parent)
        self._enabled_button_style = "QToolButton { border: none; font-weight: 700; }"
        self._disabled_button_style = (
            "QToolButton { border: none; font-weight: 700; color: #9CA3AF; "
            "background: #F3F4F6; padding: 4px 6px; }"
        )
        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setStyleSheet(self._enabled_button_style)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        self._enabled_cursor = self.toggle_button.cursor()

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
        if checked and not self.isEnabled():
            self.toggle_button.blockSignals(True)
            self.toggle_button.setChecked(False)
            self.toggle_button.blockSignals(False)
            self.toggle_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
            self.content_area.setVisible(False)
            return
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
        if expanded and not self.isEnabled():
            expanded = False
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
        if enabled:
            self.toggle_button.setStyleSheet(self._enabled_button_style)
            self.toggle_button.setCursor(self._enabled_cursor)
        else:
            self.toggle_button.setStyleSheet(self._disabled_button_style)
            self.toggle_button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            self.toggle_button.setToolTip("当前不可配置")
        if not enabled:
            # 禁用时强制收起
            self.set_expanded(False)
        else:
            self.toggle_button.setToolTip("")

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
