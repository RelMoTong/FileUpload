"""
文件名：src/ui/dialogs/change_password_dialog.py
文件作用：Qt 界面层的“change_password_dialog”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。

Password-change input dialog.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

from src.models import UserRole


class ChangePasswordDialog(QtWidgets.QDialog):
    """Collect password fields and emit a semantic change request."""

    change_requested = QtCore.Signal(object, str, str, str)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        scale_px: Callable[[int, int], int] = lambda value, minimum: max(value, minimum),
        dialog_size: Optional[QtCore.QSize] = None,
    ) -> None:
        """作用：执行界面“__init__”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        super().__init__(parent)
        self.setWindowTitle("🔑 修改密码")
        self.setModal(True)
        self.resize(dialog_size or QtCore.QSize(400, 300))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(scale_px(15, 8))

        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItem("👤 用户密码", UserRole.USER)
        self.target_combo.addItem("👑 管理员密码", UserRole.ADMIN)
        self._add_row(layout, "修改对象:", self.target_combo, scale_px)

        self.old_input = self._password_input("请输入原密码")
        self._add_row(layout, "原密码:", self.old_input, scale_px)
        self.new_input = self._password_input("请输入新密码")
        self._add_row(layout, "新密码:", self.new_input, scale_px)
        self.confirm_input = self._password_input("请再次输入新密码")
        self._add_row(layout, "确认密码:", self.confirm_input, scale_px)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        cancel_button = QtWidgets.QPushButton("取消")
        cancel_button.setProperty("class", "Secondary")
        cancel_button.clicked.connect(self.reject)
        self.confirm_button = QtWidgets.QPushButton("确认修改")
        self.confirm_button.setProperty("class", "Primary")
        self.confirm_button.clicked.connect(self._submit)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(self.confirm_button)
        layout.addLayout(button_layout)

    @staticmethod
    def _password_input(placeholder: str) -> QtWidgets.QLineEdit:
        """界面辅助：完成“_password_input”对应的既有局部显示或事件工作。"""
        line_edit = QtWidgets.QLineEdit()
        line_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        line_edit.setPlaceholderText(placeholder)
        return line_edit

    @staticmethod
    def _add_row(
        layout: QtWidgets.QVBoxLayout,
        label_text: str,
        widget: QtWidgets.QWidget,
        scale_px: Callable[[int, int], int],
    ) -> None:
        """界面辅助：完成“_add_row”对应的既有局部显示或事件工作。"""
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(scale_px(80, 64))
        row.addWidget(label)
        row.addWidget(widget)
        layout.addLayout(row)

    def _submit(self) -> None:
        """界面辅助：完成“_submit”对应的既有局部显示或事件工作。"""
        raw_role = self.target_combo.currentData()
        try:
            role = raw_role if isinstance(raw_role, UserRole) else UserRole(raw_role)
        except (TypeError, ValueError):
            role = UserRole.USER
        self.change_requested.emit(
            role,
            self.old_input.text().strip(),
            self.new_input.text().strip(),
            self.confirm_input.text().strip(),
        )

    def render_change_failed(self) -> None:
        """作用：执行界面“render_change_failed”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        self.old_input.selectAll()
        self.old_input.setFocus()

    def render_changed(self) -> None:
        """作用：执行界面“render_changed”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        self.accept()
