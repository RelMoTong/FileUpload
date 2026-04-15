# -*- coding: utf-8 -*-
"""
DiskCleanupDialog 行为测试
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.ui.widgets import DiskCleanupDialog


def get_qt_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class MockMainWindow(QtWidgets.QWidget):  # type: ignore[misc]
    def __init__(
        self,
        backup_path: str = "C:/Backup",
        target_path: str = "C:/Target",
        monitor_path: str = "C:/Monitor",
        auto_delete_folders=None,
        save_result: bool = True,
        current_role: str = "admin",
        is_running: bool = False,
    ) -> None:
        super().__init__()
        self.bak_edit = QtWidgets.QLineEdit()
        self.bak_edit.setText(backup_path)

        self.tgt_edit = QtWidgets.QLineEdit()
        self.tgt_edit.setText(target_path)

        self.auto_delete_folder = monitor_path
        self.auto_delete_folders = list(auto_delete_folders or [])
        self.enable_auto_delete = False
        self.auto_delete_threshold = 80
        self.auto_delete_target_percent = 40
        self.auto_delete_keep_days = 10
        self.auto_delete_check_interval = 300
        self.last_config_save_error = ""
        self.save_result = save_result
        self.save_calls = 0
        self.current_role = current_role
        self.is_running = is_running

    def _save_config(self) -> bool:
        self.save_calls += 1
        if not self.save_result and not self.last_config_save_error:
            self.last_config_save_error = "模拟保存失败"
        return self.save_result

    def _save_auto_cleanup_config(self, cleanup_config: dict) -> bool:
        self.save_calls += 1
        if not self.save_result:
            if not self.last_config_save_error:
                self.last_config_save_error = "模拟保存失败"
            return False
        self.enable_auto_delete = cleanup_config["enable_auto_delete"]
        self.auto_delete_folders = list(cleanup_config["auto_delete_folders"])
        self.auto_delete_folder = self.auto_delete_folders[0] if self.auto_delete_folders else ""
        self.auto_delete_threshold = cleanup_config["auto_delete_threshold"]
        self.auto_delete_target_percent = cleanup_config["auto_delete_target_percent"]
        self.auto_delete_check_interval = cleanup_config["auto_delete_check_interval"]
        return True


class TestDiskCleanupDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_qt_app()

    def _open_advanced_tab(self, dialog: DiskCleanupDialog) -> None:
        dialog._on_tab_changed(1)

    def _create_auto_cleanup_controls(self, dialog: DiskCleanupDialog) -> None:
        dialog._test_auto_group = dialog._create_auto_cleanup_group()

    def test_prefilled_paths_are_enabled_for_editing(self) -> None:
        parent = MockMainWindow()
        dialog = DiskCleanupDialog(parent)

        self.assertTrue(dialog.cb_backup.isChecked())
        self.assertTrue(dialog.cb_target.isChecked())
        self.assertTrue(dialog.cb_monitor.isChecked())
        self.assertTrue(dialog.edit_backup.isEnabled())
        self.assertTrue(dialog.edit_target.isEnabled())
        self.assertTrue(dialog.edit_monitor.isEnabled())

        dialog.close()
        parent.close()

    def test_saved_auto_cleanup_paths_restore_checkbox_state(self) -> None:
        parent = MockMainWindow(
            auto_delete_folders=["C:/Backup", "C:/Target"],
            monitor_path="C:/Monitor",
        )
        dialog = DiskCleanupDialog(parent)

        self.assertTrue(dialog.cb_backup.isChecked())
        self.assertTrue(dialog.cb_target.isChecked())
        self.assertFalse(dialog.cb_monitor.isChecked())

        dialog.close()
        parent.close()

    def test_sync_does_not_trigger_implicit_save(self) -> None:
        parent = MockMainWindow()
        dialog = DiskCleanupDialog(parent)
        self._open_advanced_tab(dialog)

        dialog.edit_target.setText("D:/ManualTarget")
        dialog._sync_auto_cleanup_folders()

        self.assertEqual(parent.save_calls, 0)
        self.assertIn("D:/ManualTarget", dialog.auto_path_label.text())

        dialog.close()
        parent.close()

    def test_save_auto_config_reports_parent_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = MockMainWindow(
                backup_path=temp_dir,
                auto_delete_folders=[temp_dir],
                save_result=False,
            )
            parent.enable_auto_delete = True
            dialog = DiskCleanupDialog(parent)
            self._create_auto_cleanup_controls(dialog)

            with mock.patch.object(QtWidgets.QMessageBox, "information") as info_mock, \
                 mock.patch.object(QtWidgets.QMessageBox, "warning") as warn_mock:
                result = dialog._save_auto_config()

            self.assertFalse(result)
            self.assertEqual(parent.save_calls, 1)
            self.assertTrue(parent.enable_auto_delete)
            self.assertEqual(parent.auto_delete_folders, [temp_dir])
            info_mock.assert_not_called()
            warn_mock.assert_called()

            dialog.close()
            parent.close()

    def test_save_auto_config_updates_parent_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = MockMainWindow(
                backup_path=temp_dir,
                target_path="",
                monitor_path="",
            )
            dialog = DiskCleanupDialog(parent)
            self._create_auto_cleanup_controls(dialog)
            dialog.cb_backup.setChecked(True)
            dialog.cb_target.setChecked(False)
            dialog.cb_monitor.setChecked(False)
            dialog.cb_custom.setChecked(False)
            dialog.cb_enable_auto.setChecked(True)

            with mock.patch.object(QtWidgets.QMessageBox, "information") as info_mock, \
                 mock.patch.object(QtWidgets.QMessageBox, "warning") as warn_mock:
                result = dialog._save_auto_config()

            self.assertTrue(result)
            self.assertEqual(parent.save_calls, 1)
            self.assertTrue(parent.enable_auto_delete)
            self.assertEqual(parent.auto_delete_folders, [temp_dir])
            self.assertEqual(parent.auto_delete_folder, temp_dir)
            info_mock.assert_called_once()
            warn_mock.assert_not_called()

            dialog.close()
            parent.close()

    def test_guest_cannot_operate_disk_cleanup(self) -> None:
        parent = MockMainWindow(current_role="guest")
        dialog = DiskCleanupDialog(parent)
        self._open_advanced_tab(dialog)
        self._create_auto_cleanup_controls(dialog)

        self.assertFalse(dialog.btn_scan.isEnabled())
        self.assertFalse(dialog.edit_backup.isEnabled())
        self.assertFalse(dialog.btn_auto_config.isEnabled())

        with mock.patch.object(QtWidgets.QMessageBox, "warning") as warn_mock:
            result = dialog._save_auto_config()

        self.assertFalse(result)
        self.assertEqual(parent.save_calls, 0)
        warn_mock.assert_called()

        dialog.close()
        parent.close()

    def test_hidden_auto_cleanup_folders_are_preserved_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2, \
             tempfile.TemporaryDirectory() as d3, \
             tempfile.TemporaryDirectory() as d4, \
             tempfile.TemporaryDirectory() as d5:
            parent = MockMainWindow(
                backup_path=d1,
                target_path=d2,
                monitor_path=d3,
                auto_delete_folders=[d1, d2, d3, d4, d5],
            )
            dialog = DiskCleanupDialog(parent)
            self._create_auto_cleanup_controls(dialog)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.cb_target.setChecked(True)
            dialog.cb_monitor.setChecked(True)
            dialog.cb_custom.setChecked(True)

            with mock.patch.object(QtWidgets.QMessageBox, "information"), \
                 mock.patch.object(QtWidgets.QMessageBox, "warning") as warn_mock:
                result = dialog._save_auto_config()

            self.assertTrue(result)
            self.assertEqual(parent.save_calls, 1)
            self.assertEqual(parent.auto_delete_folders, [d1, d2, d3, d4, d5])
            warn_mock.assert_not_called()

            dialog.close()
            parent.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
