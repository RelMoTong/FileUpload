# -*- coding: utf-8 -*-
"""Regression tests for the More menu permission state."""

import os
import sys
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.controllers import AuthController, CleanupController, FTPController, LifecycleController, RuntimeController, UploadController
from src.models import AuthModel, UploadRuntimeState, UserRole
from src.models import ApplicationSettings, RuntimeCommandResult, RuntimeInitializationResult
from src.services import AuthService, CleanupService, FTPService, UploadService
from src.ui.main_window import MainWindow


def get_qt_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def create_main_window() -> MainWindow:
    class SettingsStub:
        last_error = ""
        config_exists = False
        def load_raw(self): return ApplicationSettings().to_config()
        def save_raw(self, config, preserve_users=True): return True
        def decode_ftp_password(self, section, default=""):
            return str(section.get("password", default) or "")
        def encode_ftp_password(self, password, label): return "", password

    class RuntimeStub:
        app_dir = Path.cwd()
        def initialize(self): return RuntimeInitializationResult(self.app_dir, True)
        def append_log(self, line): return True
        def disk_free_percent(self, path, network_available=True): return -1.0
        def reconcile_startup(self, auto_enabled, explicit=False):
            return RuntimeCommandResult(True, bool(auto_enabled or explicit))
        def disable_startup(self): return RuntimeCommandResult(True, False)

    auth = AuthController(AuthService(), model=AuthModel())
    ftp = FTPController(FTPService())
    upload = UploadController(UploadService(), UploadRuntimeState())
    cleanup = CleanupController(CleanupService())
    runtime = RuntimeController(RuntimeStub())
    lifecycle = LifecycleController(cleanup, upload, ftp, runtime)
    return MainWindow(
        settings_controller=SettingsStub(),
        auth_controller=auth,
        ftp_controller=ftp,
        upload_controller=upload,
        cleanup_controller=cleanup,
        runtime_controller=runtime,
        lifecycle_controller=lifecycle,
    )


class MoreMenuPermissionTests(unittest.TestCase):
    def test_default_password_login_opens_change_flow_and_locks_start(self) -> None:
        app = get_qt_app()
        window = create_main_window()

        class LoginDialogStub:
            def render_authentication_failed(self): raise AssertionError("login failed")
            def render_authenticated(self): self.accepted = True

        dialog = LoginDialogStub()
        try:
            with mock.patch.object(window, "_show_change_password") as show_change:
                window._on_login_requested(dialog, UserRole.ADMIN, "Tops123")
                app.processEvents()

            self.assertTrue(dialog.accepted)
            self.assertTrue(window.auth_controller.password_change_required)
            self.assertFalse(window.btn_start.isEnabled())
            self.assertTrue(window.menu_items["change_password"].isEnabled())
            show_change.assert_called_once()
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_ftps_certificate_controls_follow_tls_and_are_collected(self) -> None:
        get_qt_app()
        window = create_main_window()
        try:
            window.cb_server_tls.setEnabled(True)
            window.cb_server_tls.setChecked(False)
            window._update_ftp_tls_controls()
            self.assertFalse(window.ftp_server_cert.isEnabled())
            self.assertFalse(window.ftp_server_key.isEnabled())

            window.cb_server_tls.setChecked(True)
            window.ftp_server_cert.setText("D:/certs/server.pem")
            window.ftp_server_key.setText("D:/certs/server.key")
            config = window._collect_ftp_server_config()

            self.assertTrue(window.ftp_server_cert.isEnabled())
            self.assertTrue(window.ftp_server_key.isEnabled())
            self.assertEqual(config["cert_file"], "D:/certs/server.pem")
            self.assertEqual(config["key_file"], "D:/certs/server.key")
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_quit_action_emits_request_without_hiding_tray_or_quitting_directly(self) -> None:
        get_qt_app()
        window = create_main_window()
        emitted: list[bool] = []
        window.app_exit_requested.disconnect(window._handle_app_exit_requested)
        window.app_exit_requested.connect(lambda: emitted.append(True))
        try:
            with mock.patch.object(
                QtWidgets.QMessageBox,
                "question",
                return_value=QtWidgets.QMessageBox.StandardButton.Yes,
            ):
                window._quit_application()

            self.assertEqual(emitted, [True])
            self.assertTrue(window.tray_icon.isVisible())
            self.assertFalse(window._exit_pending)
        finally:
            window.app_exit_requested.disconnect()
            window.app_exit_requested.connect(window._handle_app_exit_requested)
            window.minimize_to_tray = False
            window.close()

    def test_exit_pending_disables_task_entry_points_and_can_be_aborted(self) -> None:
        get_qt_app()
        window = create_main_window()
        try:
            window.current_role = "admin"
            window._update_ui_permissions()
            window.set_all_tasks_pending_stop()

            self.assertTrue(window._exit_pending)
            self.assertFalse(window.btn_start.isEnabled())
            self.assertFalse(window.btn_pause.isEnabled())
            self.assertFalse(window.btn_stop.isEnabled())
            self.assertFalse(window.btn_toggle_ftp_server.isEnabled())
            self.assertFalse(window.menu_items["disk_cleanup"].isEnabled())

            window.abort_pending_exit(("worker timeout",))
            self.assertFalse(window._exit_pending)
            self.assertTrue(window.btn_start.isEnabled())
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_wakeup_always_shows_single_instance_tray_message(self) -> None:
        get_qt_app()
        window = create_main_window()

        class Socket:
            disconnected = False
            def waitForReadyRead(self, timeout): return True
            def readAll(self): return b"WAKEUP"
            def disconnectFromServer(self): self.disconnected = True

        class Server:
            def __init__(self, socket): self.socket = socket
            def nextPendingConnection(self): return self.socket
            def close(self): return None

        class Tray:
            def __init__(self): self.messages = []
            def isVisible(self): return True
            def showMessage(self, *args): self.messages.append(args)

        socket = Socket()
        tray = Tray()
        real_tray = window.tray_icon
        window.local_server = Server(socket)
        window.tray_icon = tray
        try:
            window._handle_wakeup_request()

            self.assertTrue(socket.disconnected)
            self.assertEqual(tray.messages[0][0:2], ("程序已在运行", "实例已激活"))
            self.assertEqual(tray.messages[0][3], 3000)
        finally:
            window.tray_icon = real_tray
            window.minimize_to_tray = False
            window.close()

    def test_guest_only_login_action_is_enabled(self) -> None:
        get_qt_app()
        window = create_main_window()
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
        window = create_main_window()
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
        window = create_main_window()
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
        window = create_main_window()
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
        window = create_main_window()
        try:
            style_sheet = window.styleSheet()

            self.assertIn("QMenu::item:disabled", style_sheet)
            self.assertIn("QMenu::item:selected:disabled", style_sheet)
        finally:
            window.minimize_to_tray = False
            window.close()


if __name__ == "__main__":
    unittest.main()
