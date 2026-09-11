# -*- coding: utf-8 -*-
"""Regression tests for low-resolution responsive UI sizing."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtCore, QtWidgets  # type: ignore[import-untyped]

from src.controllers import AuthController, CleanupController, FTPController, LifecycleController, RuntimeController, UploadController
from src.models import AuthModel, UploadRuntimeState
from src.models import ApplicationSettings, RuntimeCommandResult, RuntimeInitializationResult
from src.services import AuthService, CleanupService, FTPService, UploadService
from src.ui.main_window import MainWindow
from src.ui.dialogs import DiskCleanupDialog, calculate_dialog_responsive_metrics


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


class ResponsiveLayoutTests(unittest.TestCase):
    def test_main_window_metrics_for_1024x768(self) -> None:
        metrics = MainWindow.calculate_responsive_metrics(1024, 768)

        self.assertTrue(metrics["compact"])
        self.assertLessEqual(metrics["min_width"], 900)
        self.assertLessEqual(metrics["initial_width"], 1024)
        self.assertLessEqual(metrics["initial_height"], 768)
        self.assertEqual(metrics["status_columns"], 2)
        self.assertEqual(metrics["content_min_width"], 0)

    def test_main_window_metrics_keep_full_layout_on_large_screen(self) -> None:
        metrics = MainWindow.calculate_responsive_metrics(1920, 1080)

        self.assertFalse(metrics["compact"])
        self.assertEqual(metrics["min_width"], 1200)
        self.assertEqual(metrics["status_columns"], 4)
        self.assertEqual(metrics["initial_width"], 1350)

    def test_disk_cleanup_metrics_for_1024x768(self) -> None:
        metrics = calculate_dialog_responsive_metrics(1024, 768)

        self.assertLessEqual(metrics["min_width"], 960)
        self.assertLessEqual(metrics["initial_width"], 963)
        self.assertLessEqual(metrics["initial_height"], 700)

    def test_main_window_smoke_uses_no_forced_horizontal_width(self) -> None:
        get_qt_app()
        window = create_main_window()
        try:
            self.assertEqual(window.central_content.minimumWidth(), 0)
            self.assertEqual(
                window.main_scroll_area.horizontalScrollBarPolicy(),
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            window.resize(1024, 728)
            window.show()
            QtWidgets.QApplication.processEvents()
            self.assertLessEqual(
                window.central_content.minimumSizeHint().width(),
                window.main_scroll_area.viewport().width(),
            )
            self.assertLessEqual(window.minimumWidth(), window.window_initial_width)
            self.assertIn(window.status_grid_columns, {2, 4})
        finally:
            window.minimize_to_tray = False
            window.close()

    def test_disk_cleanup_dialog_smoke_clamps_to_available_screen(self) -> None:
        app = get_qt_app()
        dialog = DiskCleanupDialog(
            cleanup_controller=CleanupController(CleanupService())
        )
        try:
            screen = app.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.assertLessEqual(dialog.minimumWidth(), max(available.width(), 800))
                self.assertLessEqual(dialog.minimumHeight(), max(available.height(), 600))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
