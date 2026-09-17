# -*- coding: utf-8 -*-
"""Independent UI component tests for phase 7 view extraction."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets

from src.controllers import CleanupController
from src.models import CleanupFileItem, UserRole
from src.services import CleanupService
from src.ui.dialogs import ChangePasswordDialog, DiskCleanupDialog, LoginDialog
from src.ui.dialogs.disk_cleanup_dialog import FileListTable
from src.ui.main_window import MainWindow
from src.ui.panels import (
    UploadFoldersPanel,
    UploadLogPanel,
    UploadSettingsPanel,
    UploadStatusPanel,
)
from src.core.i18n import t


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_login_dialog_emits_semantic_credentials() -> None:
    _app()
    dialog = LoginDialog()
    emitted: list[tuple[UserRole, str]] = []
    dialog.login_requested.connect(lambda role, password: emitted.append((role, password)))
    dialog.role_combo.setCurrentIndex(1)
    dialog.password_input.setText("secret")

    dialog.login_button.click()

    assert emitted == [(UserRole.ADMIN, "secret")]
    dialog.close()


def test_login_dialog_rejects_blank_password_before_emitting() -> None:
    _app()
    dialog = LoginDialog()
    requests: list[object] = []
    failures: list[str] = []
    dialog.login_requested.connect(requests.append)
    dialog.validation_failed.connect(failures.append)

    dialog.login_button.click()

    assert not requests
    assert failures
    dialog.close()


def test_change_password_dialog_emits_all_form_values() -> None:
    _app()
    dialog = ChangePasswordDialog()
    emitted: list[tuple[UserRole, str, str, str]] = []
    dialog.change_requested.connect(
        lambda role, old, new, confirm: emitted.append((role, old, new, confirm))
    )
    dialog.target_combo.setCurrentIndex(1)
    dialog.old_input.setText("old")
    dialog.new_input.setText("new")
    dialog.confirm_input.setText("new")

    dialog.confirm_button.click()

    assert emitted == [(UserRole.ADMIN, "old", "new", "new")]
    dialog.close()


def test_upload_panels_can_be_built_as_independent_widgets() -> None:
    _app()

    class PanelHost(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.ui_scale = 1.0
            self.responsive_metrics = {"status_columns": 2}
            self.status_grid_columns = 2

        def _scale_px(self, value, minimum=1, maximum=None):
            result = max(value, minimum)
            return min(result, maximum) if maximum is not None else result

        def _font_pt(self, value, minimum=8): return max(value, minimum)

        def _card(self, title, title_key=""):
            card = QtWidgets.QFrame()
            layout = QtWidgets.QVBoxLayout(card)
            label = QtWidgets.QLabel(title)
            layout.addWidget(label)
            return card, layout, label

        def _hline(self): return QtWidgets.QFrame()
        def _set_checkbox_mark(self, *args): return None
        def __getattr__(self, name):
            if name.startswith("_"):
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    host = PanelHost()
    panels = [
        UploadFoldersPanel(host),
        UploadSettingsPanel(host),
        UploadStatusPanel(host),
        UploadLogPanel(host),
    ]
    try:
        assert all(isinstance(panel, QtWidgets.QWidget) for panel in panels)
        assert all(panel.content is not None for panel in panels)
        assert host.log.document().maximumBlockCount() == 5000
        assert not host.cb_dedup_enable.isChecked()
        assert not host.cb_dedup_enable.isEnabled()
        assert "无数据库版智能去重暂不可启用" in host.cb_dedup_enable.toolTip()
    finally:
        for panel in panels:
            panel.close()
        host.close()


def test_network_status_renderer_restores_unknown_visual_state() -> None:
    class Chip:
        def __init__(self) -> None:
            self.value = "旧状态"
            self.style = "old"

        def setValue(self, value: str) -> None:
            self.value = value

        def setStyleSheet(self, style: str) -> None:
            self.style = style

    chip = Chip()
    host = type("Host", (), {"lbl_network": chip})()

    MainWindow.render_network_status(host, "unknown")

    assert chip.value == t("network_unknown")
    assert "#ECEFF1" in chip.style
    assert "#546E7A" in chip.style


def test_cleanup_folder_row_visual_state_is_consistent_from_initial_render() -> None:
    app = _app()

    class CleanupHost(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.current_role = "admin"
            self.bak_edit = QtWidgets.QLineEdit(r"C:\Backup")
            self.tgt_edit = QtWidgets.QLineEdit(r"C:\Target")
            self.auto_delete_folders = [r"C:\Monitor"]
            self.auto_delete_folder = r"C:\Monitor"
            self.enable_auto_delete = False

        @property
        def cleanup_role(self) -> str:
            return self.current_role

        @property
        def cleanup_settings_error(self) -> str:
            return ""

        def cleanup_settings_snapshot(self):
            return {
                "backup_path": self.bak_edit.text(),
                "target_path": self.tgt_edit.text(),
                "auto_delete_folders": list(self.auto_delete_folders),
                "auto_delete_folder": self.auto_delete_folder,
                "enable_auto_delete": self.enable_auto_delete,
            }

        def save_auto_cleanup_settings(self, config):
            self.enable_auto_delete = bool(config["enable_auto_delete"])
            self.auto_delete_folders = list(config["auto_delete_folders"])
            return True

    host = CleanupHost()
    controller = CleanupController(CleanupService())
    dialog = DiskCleanupDialog(host, controller, settings_gateway=host)
    try:
        dialog._on_tab_changed(1)
        dialog.show()
        app.processEvents()

        assert dialog.btn_auto_config.isEnabled()
        assert "打开自动清理配置窗口" in dialog.btn_auto_config.toolTip()
        assert "未启用" in dialog.auto_status_label.text()
        assert "不可启用" not in dialog.auto_status_label.text()

        assert not dialog.cb_backup.isChecked()
        assert dialog.cb_backup.property("folderActive") is False
        assert dialog.edit_backup.property("folderActive") is False
        assert not dialog.edit_backup.isEnabled()

        dialog.cb_backup.click()
        app.processEvents()
        assert dialog.cb_backup.isChecked()
        assert dialog.cb_backup.property("folderActive") is True
        assert dialog.edit_backup.property("folderActive") is True
        assert dialog.edit_backup.isEnabled()

        dialog.cb_backup.click()
        app.processEvents()
        assert not dialog.cb_backup.isChecked()
        assert dialog.cb_backup.property("folderActive") is False
        assert dialog.edit_backup.property("folderActive") is False
        assert not dialog.edit_backup.isEnabled()
    finally:
        dialog.close()
        controller.shutdown()
        host.close()


def test_enabling_auto_cleanup_requires_explicit_no_retention_confirmation() -> None:
    app = _app()

    class CleanupHost(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.saved: list[dict] = []
            self.settings = {
                "enable_auto_delete": False,
                "auto_delete_folders": [],
                "auto_delete_folder": "",
            }

        @property
        def cleanup_role(self) -> str:
            return "admin"

        @property
        def cleanup_settings_error(self) -> str:
            return ""

        def cleanup_settings_snapshot(self):
            return dict(self.settings)

        def save_auto_cleanup_settings(self, config):
            self.saved.append(dict(config))
            self.settings.update(config)
            return True

    host = CleanupHost()
    controller = CleanupController(CleanupService())
    dialog = DiskCleanupDialog(host, controller, settings_gateway=host)
    auto_group = dialog._create_auto_cleanup_group()
    auto_group.setParent(dialog)
    dialog.cb_enable_auto.setChecked(True)
    try:
        with mock.patch.object(
            dialog,
            "_collect_selected_folders",
            return_value=[r"E:\Camera"],
        ), mock.patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.No,
        ) as question:
            assert not dialog._save_auto_config()
        assert not host.saved
        prompt = question.call_args.args[2]
        assert "不受“保留天数”限制" in prompt

        with mock.patch.object(
            dialog,
            "_collect_selected_folders",
            return_value=[r"E:\Camera"],
        ), mock.patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Yes,
        ), mock.patch.object(QtWidgets.QMessageBox, "information"):
            assert dialog._save_auto_config()
        assert host.saved[-1]["enable_auto_delete"] is True
        app.processEvents()
    finally:
        dialog.close()
        controller.shutdown()
        host.close()


def test_cleanup_file_table_virtualizes_one_hundred_thousand_incremental_rows() -> None:
    _app()
    table = FileListTable()
    files = [
        CleanupFileItem(
            path=fr"E:\images\{index:06d}.jpg",
            size=index,
            mtime=float(index),
        )
        for index in range(100_000)
    ]
    try:
        table.load_files([])
        table.append_files(files[:500])
        table.append_files(files[500:])
        _app().processEvents()

        assert isinstance(table, QtWidgets.QTableView)
        assert table.model().rowCount() == 100_000
        assert table.file_items[1000].name == "001000.jpg"
        assert not table.findChildren(QtWidgets.QTableWidget)

        table.select_none()
        assert not table.get_checked_files()
        table.select_all()
        assert len(table.get_checked_files()) == 100_000
    finally:
        table.close()
