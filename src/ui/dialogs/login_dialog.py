"""
文件名：src/ui/dialogs/login_dialog.py
文件作用：Qt 界面层的“login_dialog”模块。
主要功能：按既有 Gateway 协议收集输入、展示状态并转发用户事件。
模块关系：由 src.ui.main_window 或对话框组合；不直接依赖控制器、服务或持久化实现。
阅读重点：先读 Gateway 协议、事件转发与 render_* 方法；样式和布局按区域阅读。

Authentication input dialog.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

from src.core.i18n import t
from src.models import UserRole


class LoginDialog(QtWidgets.QDialog):
    """Collect credentials and emit a semantic login request."""

    login_requested = QtCore.Signal(object, str)
    validation_failed = QtCore.Signal(str)

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
        self.setWindowTitle("🔐 权限登录")
        self.setModal(True)
        self.resize(dialog_size or QtCore.QSize(400, 200))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(scale_px(15, 8))

        role_layout = QtWidgets.QHBoxLayout()
        role_label = QtWidgets.QLabel(t("login_role_label"))
        role_label.setMinimumWidth(scale_px(80, 64))
        self.role_combo = QtWidgets.QComboBox()
        self.role_combo.addItem(t("role_user_option"), UserRole.USER)
        self.role_combo.addItem(t("role_admin_option"), UserRole.ADMIN)
        role_layout.addWidget(role_label)
        role_layout.addWidget(self.role_combo)
        layout.addLayout(role_layout)

        password_layout = QtWidgets.QHBoxLayout()
        password_label = QtWidgets.QLabel(t("password_label"))
        password_label.setMinimumWidth(scale_px(80, 64))
        self.password_input = QtWidgets.QLineEdit()
        self.password_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(t("enter_password"))
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_input)
        layout.addLayout(password_layout)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        cancel_button = QtWidgets.QPushButton(t("cancel"))
        cancel_button.setProperty("class", "Secondary")
        cancel_button.clicked.connect(self.reject)
        self.login_button = QtWidgets.QPushButton(t("login"))
        self.login_button.setProperty("class", "Primary")
        self.login_button.setDefault(True)
        self.login_button.clicked.connect(self._submit)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(self.login_button)
        layout.addLayout(button_layout)

    def _submit(self) -> None:
        """界面辅助：完成“_submit”对应的既有局部显示或事件工作。"""
        password = self.password_input.text().strip()
        if not password:
            self.validation_failed.emit(t("please_enter_password"))
            return
        raw_role = self.role_combo.currentData()
        try:
            role = raw_role if isinstance(raw_role, UserRole) else UserRole(raw_role)
        except (TypeError, ValueError):
            role = UserRole.USER
        self.login_requested.emit(role, password)

    def render_authentication_failed(self) -> None:
        """作用：执行界面“render_authentication_failed”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        self.password_input.selectAll()
        self.password_input.setFocus()

    def render_authenticated(self) -> None:
        """作用：执行界面“render_authenticated”的既有输入、展示或事件转发职责。

        参数：沿用当前 Qt 信号、控件值、类型和状态约定。
        返回结果：沿用当前实现的返回值、界面更新或事件语义。
        执行流程：按现有代码顺序读取控件、调用 Gateway 或刷新界面。
        风险或注意事项：本说明不改变 Qt 线程边界、信号、布局、样式或公开接口。
        """
        self.accept()
