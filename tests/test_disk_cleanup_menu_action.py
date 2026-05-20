# -*- coding: utf-8 -*-
"""Disk cleanup menu action permission-state tests."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.core.i18n import LANG_EN_US, LANG_ZH_CN, set_language
from src.ui.main_window import MainWindow


def get_qt_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class TestDiskCleanupMenuAction(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_qt_app()

    def setUp(self) -> None:
        set_language(LANG_ZH_CN)
        self.menu = QtWidgets.QMenu()
        self.action = self.menu.addAction("💿 磁盘清理")
        self.window = MainWindow.__new__(MainWindow)
        self.window.menu_items = {"disk_cleanup": self.action}
        self.window.is_running = False

    def tearDown(self) -> None:
        self.menu.close()
        set_language(LANG_ZH_CN)

    def _sync(self, role: str, is_running: bool = False) -> None:
        self.window.current_role = role
        self.window.is_running = is_running
        MainWindow._sync_disk_cleanup_menu_action(self.window)

    def test_guest_disk_cleanup_action_is_disabled_without_colored_icon(self) -> None:
        self._sync("guest")

        self.assertFalse(self.action.isEnabled())
        self.assertEqual(self.action.text(), "磁盘清理")
        self.assertNotIn("💿", self.action.text())
        self.assertEqual(self.action.toolTip(), "请先登录后再使用磁盘清理功能")
        self.assertEqual(self.action.statusTip(), "请先登录后再使用磁盘清理功能")

    def test_user_disk_cleanup_action_is_disabled_without_colored_icon(self) -> None:
        self._sync("user")

        self.assertFalse(self.action.isEnabled())
        self.assertEqual(self.action.text(), "磁盘清理")
        self.assertNotIn("💿", self.action.text())
        self.assertEqual(self.action.toolTip(), "普通用户无权使用磁盘清理功能，请切换管理员")
        self.assertEqual(self.action.statusTip(), "普通用户无权使用磁盘清理功能，请切换管理员")

    def test_admin_disk_cleanup_action_is_enabled_with_icon(self) -> None:
        self._sync("admin")

        self.assertTrue(self.action.isEnabled())
        self.assertEqual(self.action.text(), "💿 磁盘清理")

    def test_running_upload_keeps_disk_cleanup_action_enabled(self) -> None:
        self._sync("admin", is_running=True)

        self.assertTrue(self.action.isEnabled())
        self.assertEqual(self.action.text(), "💿 磁盘清理")
        self.assertNotEqual(self.action.toolTip(), "上传运行中，不能执行磁盘清理")
        self.assertEqual(self.action.statusTip(), "")

    def test_guest_disk_cleanup_action_stays_plain_after_language_switch(self) -> None:
        self._sync("guest")

        set_language(LANG_EN_US)
        self._sync("guest")

        self.assertFalse(self.action.isEnabled())
        self.assertEqual(self.action.text(), "Disk Cleanup")
        self.assertNotIn("💿", self.action.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
