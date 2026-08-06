# -*- coding: utf-8 -*-
"""Regression tests for the More menu permission state."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.ui.main_window import MainWindow


def get_qt_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class MoreMenuPermissionTests(unittest.TestCase):
    def test_guest_only_login_action_is_enabled(self) -> None:
        get_qt_app()
        window = MainWindow()
        try:
            window.current_role = "guest"
            window.is_running = False
            window._update_ui_permissions()

            states = {name: action.isEnabled() for name, action in window.menu_items.items()}

            self.assertFalse(states["clear_logs"])
            self.assertFalse(states["disk_cleanup"])
            self.assertTrue(states["login"])
            self.assertFalse(states["change_password"])
            self.assertFalse(states["logout"])
            self.assertFalse(states["lang_menu"])
            self.assertTrue(window.btn_more.isEnabled())
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_only_admin_can_use_disk_cleanup_menu(self) -> None:
        get_qt_app()
        window = MainWindow()
        try:
            for role, is_running, expected in (
                ("guest", False, False),
                ("user", False, False),
                ("user", True, False),
                ("admin", False, True),
                ("admin", True, True),
            ):
                with self.subTest(role=role, is_running=is_running):
                    window.current_role = role
                    window.is_running = is_running
                    window._update_ui_permissions()

                    self.assertEqual(window.menu_items["disk_cleanup"].isEnabled(), expected)
                    self.assertEqual(window._can_manage_disk_cleanup(), expected)
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_only_admin_can_start_independent_ftp_server_in_smb_mode(self) -> None:
        get_qt_app()
        window = MainWindow()
        try:
            window.current_protocol = "smb"
            window.enable_ftp_server = True
            window.is_running = False

            window.current_role = "user"
            window._update_ui_permissions()
            self.assertFalse(window.btn_toggle_ftp_server.isEnabled())

            window.current_role = "admin"
            window._update_ui_permissions()
            self.assertTrue(window.cb_enable_ftp_server.isEnabled())
            self.assertTrue(window.btn_toggle_ftp_server.isEnabled())
            self.assertTrue(window.ftp_server_collapsible.isEnabled())
            self.assertFalse(window.ftp_client_collapsible.isEnabled())
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_ftp_server_only_configuration_does_not_require_upload_paths(self) -> None:
        get_qt_app()
        window = MainWindow()
        try:
            window.current_protocol = "smb"
            window.enable_ftp_server = True
            window.src_edit.setText("")
            window.tgt_edit.setText("")

            self.assertTrue(window._is_server_only_configuration())
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_menu_disabled_items_have_disabled_style(self) -> None:
        get_qt_app()
        window = MainWindow()
        try:
            style_sheet = window.styleSheet()

            self.assertIn("QMenu::item:disabled", style_sheet)
            self.assertIn("QMenu::item:selected:disabled", style_sheet)
        finally:
            window.minimize_to_tray = False
            window.close()


if __name__ == "__main__":
    unittest.main()
