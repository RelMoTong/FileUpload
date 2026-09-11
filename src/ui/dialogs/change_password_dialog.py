"""Password-change input dialog."""

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
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(scale_px(80, 64))
        row.addWidget(label)
        row.addWidget(widget)
        layout.addLayout(row)

    def _submit(self) -> None:
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
        self.old_input.selectAll()
        self.old_input.setFocus()

    def render_changed(self) -> None:
        self.accept()
