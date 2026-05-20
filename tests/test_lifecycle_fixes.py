# -*- coding: utf-8 -*-
"""回归测试：停止、网络检测和 FTP 连接生命周期。"""

import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.protocols import ftp as ftp_module
from src.protocols.ftp import FTPClientUploader, FTPServerManager
from src.workers.upload_worker import UploadWorker
from src.ui import main_window as main_window_module
from src.ui.main_window import MainWindow
from src.core.permissions import PermissionManager
from src.core.i18n import LANG_EN_US, LANG_ZH_CN, get_language, set_language


def make_worker(tmp_path: Path) -> UploadWorker:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    for path in (source, target, backup):
        path.mkdir()
    return UploadWorker(
        source=str(source),
        target=str(target),
        backup=str(backup),
        interval=1,
        mode="once",
        disk_threshold_percent=10,
        retry_count=1,
        filters=[".jpg"],
        app_dir=tmp_path,
    )


def test_path_exists_timeout_returns_default(monkeypatch):
    from src.workers import upload_worker

    def fake_run(*args, **kwargs):
        raise upload_worker.subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

    monkeypatch.setattr(upload_worker.os, "name", "nt")
    monkeypatch.setattr(upload_worker.subprocess, "run", fake_run)

    start = time.monotonic()
    result = UploadWorker._path_exists_with_timeout(r"\\server\share", 0.1, default=False)

    assert result is False
    assert time.monotonic() - start < 0.5


def test_unc_ping_success_still_validates_share_path(monkeypatch, tmp_path):
    from src.workers import upload_worker

    worker = make_worker(tmp_path)
    checked = {}

    class Completed:
        returncode = 0

    def fake_run(*args, **kwargs):
        return Completed()

    def fake_path_exists(path, seconds, default=False):
        checked["path"] = path
        checked["seconds"] = seconds
        return False

    monkeypatch.setattr(upload_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(UploadWorker, "_path_exists_with_timeout", staticmethod(fake_path_exists))

    assert worker._safe_net_check(r"\\server\missing_share", timeout=0.1, default=False) is False
    assert checked["path"] == r"\\server\missing_share"


def test_upload_worker_rejects_nested_source_target_paths(tmp_path):
    source = tmp_path / "source"
    target = source / "target"
    backup = tmp_path / "backup"
    for path in (source, target, backup):
        path.mkdir(parents=True)

    worker = UploadWorker(
        source=str(source),
        target=str(target),
        backup=str(backup),
        interval=1,
        mode="once",
        disk_threshold_percent=10,
        retry_count=1,
        filters=[".jpg"],
        app_dir=tmp_path,
    )

    assert worker._validate_paths() is False


def test_main_window_path_overlap_helper_detects_parent_child(tmp_path):
    source = tmp_path / "source"
    target = source / "target"
    target.mkdir(parents=True)

    assert MainWindow._path_contains_or_equals(str(source), str(target)) is True


def test_dangerous_cleanup_path_rejects_drive_root(tmp_path):
    root = Path(tmp_path.anchor)

    assert "根目录" in MainWindow._dangerous_cleanup_path_reason(str(root))


def test_stop_wait_is_bounded_when_executor_task_is_stuck(tmp_path):
    worker = make_worker(tmp_path)
    blocker = threading.Event()
    worker._executor.submit(blocker.wait, 5)

    start = time.monotonic()
    worker.stop(wait=True, timeout=0.1)

    assert time.monotonic() - start < 0.5


def test_disconnect_during_connect_prevents_late_connected_state(monkeypatch):
    connect_entered = threading.Event()
    finish_connect = threading.Event()
    instances = []

    class FakeFTP:
        def __init__(self):
            self.closed = False
            instances.append(self)

        def connect(self, *args, **kwargs):
            connect_entered.set()
            assert finish_connect.wait(1)

        def login(self, *args, **kwargs):
            pass

        def set_pasv(self, value):
            pass

        def close(self):
            self.closed = True

        def quit(self):
            self.closed = True

    monkeypatch.setattr(ftp_module, "FTP", FakeFTP)

    client = FTPClientUploader({"host": "example.invalid", "retry_count": 1})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", client.connect()))
    thread.start()
    assert connect_entered.wait(1)

    client.disconnect()
    finish_connect.set()
    thread.join(1)

    assert result["value"] is False
    assert client.connected is False
    assert client.ftp is None
    assert instances and instances[0].closed is True


def test_concurrent_connect_is_rejected_while_first_connects(monkeypatch):
    connect_entered = threading.Event()
    finish_connect = threading.Event()
    instances = []

    class FakeFTP:
        def __init__(self):
            self.closed = False
            instances.append(self)

        def connect(self, *args, **kwargs):
            connect_entered.set()
            assert finish_connect.wait(1)

        def login(self, *args, **kwargs):
            pass

        def set_pasv(self, value):
            pass

        def close(self):
            self.closed = True

        def quit(self):
            self.closed = True

    monkeypatch.setattr(ftp_module, "FTP", FakeFTP)

    client = FTPClientUploader({"host": "example.invalid", "retry_count": 1})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("first", client.connect()))
    thread.start()
    assert connect_entered.wait(1)

    assert client.connect() is False
    finish_connect.set()
    thread.join(1)

    assert result["first"] is True
    assert client.connected is True
    assert len(instances) == 1
    client.disconnect()


def test_close_event_stops_ftp_manager_on_real_exit():
    window = MainWindow.__new__(MainWindow)
    stopped = {"value": False}
    accepted = {"value": False}

    class DummyManager:
        def stop_all(self):
            stopped["value"] = True

    class DummyExecutor:
        def shutdown(self, wait=False):
            pass

    class DummyEvent:
        def accept(self):
            accepted["value"] = True

        def ignore(self):
            raise AssertionError("real exit should not hide to tray")

    window.minimize_to_tray = False
    window.tray_icon = None
    window.show_notifications = False
    window.worker = None
    window.ftp_manager = DummyManager()
    window._ftp_server_standalone = True
    window._append_log = lambda *args, **kwargs: None
    window._update_protocol_status = lambda *args, **kwargs: None
    window._log_executor = DummyExecutor()
    window._disk_executor = DummyExecutor()
    window._cleanup_executor = DummyExecutor()

    MainWindow.closeEvent(window, DummyEvent())

    assert accepted["value"] is True
    assert stopped["value"] is True
    assert window.ftp_manager is None
    assert window._ftp_server_standalone is False


def test_close_event_keeps_ftp_manager_when_hiding_to_tray():
    window = MainWindow.__new__(MainWindow)
    stopped = {"value": False}
    ignored = {"value": False}

    class DummyManager:
        def stop_all(self):
            stopped["value"] = True

    class DummyTray:
        def isVisible(self):
            return True

    class DummyEvent:
        def accept(self):
            raise AssertionError("tray close should not accept real exit")

        def ignore(self):
            ignored["value"] = True

    window.minimize_to_tray = True
    window.tray_icon = DummyTray()
    window.show_notifications = False
    window.ftp_manager = DummyManager()
    window.hide = lambda *args, **kwargs: None

    MainWindow.closeEvent(window, DummyEvent())

    assert ignored["value"] is True
    assert stopped["value"] is False
    assert window.ftp_manager is not None


def test_auto_start_upload_allows_empty_backup_when_backup_disabled():
    window = MainWindow.__new__(MainWindow)
    logs = []
    started = {"value": False}

    class DummyEdit:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    window.auto_run_on_startup = True
    window.enable_backup = False
    window.src_edit = DummyEdit("D:/Image")
    window.tgt_edit = DummyEdit("Z:/vision/PB-018")
    window.bak_edit = DummyEdit("")
    window._append_log = logs.append
    window._on_start = lambda: started.__setitem__("value", True)

    MainWindow._auto_start_upload(window)

    assert started["value"] is True
    assert "⚠ 自动运行失败：文件夹路径未设置" not in logs


def test_auto_start_upload_requires_backup_when_backup_enabled():
    window = MainWindow.__new__(MainWindow)
    logs = []
    started = {"value": False}

    class DummyEdit:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    window.auto_run_on_startup = True
    window.enable_backup = True
    window.src_edit = DummyEdit("D:/Image")
    window.tgt_edit = DummyEdit("Z:/vision/PB-018")
    window.bak_edit = DummyEdit("")
    window._append_log = logs.append
    window._on_start = lambda: started.__setitem__("value", True)

    MainWindow._auto_start_upload(window)

    assert started["value"] is False
    assert "⚠ 自动运行失败：文件夹路径未设置" in logs


def test_loaded_language_is_applied_and_refreshes_ui():
    window = MainWindow.__new__(MainWindow)
    calls = []
    window._sync_language_actions = lambda lang: calls.append(("sync", lang))
    window._refresh_ui_texts = lambda: calls.append(("refresh", None))

    try:
        set_language(LANG_ZH_CN)
        loaded = MainWindow._apply_loaded_language(window, {"language": LANG_EN_US})

        assert loaded == LANG_EN_US
        assert get_language() == LANG_EN_US
        assert calls == [("sync", LANG_EN_US), ("refresh", None)]
    finally:
        set_language(LANG_ZH_CN)


def test_protocol_combo_texts_match_three_protocol_modes():
    window = MainWindow.__new__(MainWindow)

    class DummyCombo:
        def __init__(self):
            self.items = ["", "", ""]

        def count(self):
            return len(self.items)

        def setItemText(self, index, text):
            self.items[index] = text

    try:
        set_language(LANG_ZH_CN)
        window.combo_protocol = DummyCombo()

        MainWindow._refresh_protocol_combo_texts(window)

        assert window.combo_protocol.items == [
            "SMB (网络共享)",
            "FTP 客户端模式",
            "SMB + FTP客户端 (双写)",
        ]
        assert "FTP 服务器模式" not in window.combo_protocol.items
    finally:
        set_language(LANG_ZH_CN)


def test_protocol_config_uses_current_protocol_as_source_of_truth():
    protocol, enable_ftp, migrated = MainWindow._resolve_protocol_from_config({
        "upload_protocol": "smb",
        "current_protocol": "both",
        "enable_ftp_server": True,
    })

    assert protocol == "both"
    assert enable_ftp is True
    assert migrated is False


def test_legacy_ftp_server_protocol_is_migrated_to_smb_with_server_enabled():
    protocol, enable_ftp, migrated = MainWindow._resolve_protocol_from_config({
        "upload_protocol": "ftp_server",
        "current_protocol": "ftp_server",
        "enable_ftp_server": False,
    })

    assert protocol == "smb"
    assert enable_ftp is True
    assert migrated is True


def test_save_config_persists_current_language(tmp_path):
    window = MainWindow.__new__(MainWindow)
    captured = {}

    class DummyText:
        def __init__(self, value=""):
            self.value = value

        def text(self):
            return self.value

    class DummyBool:
        def __init__(self, value=False):
            self.value = value

        def isChecked(self):
            return self.value

    class DummyValue:
        def __init__(self, value):
            self._value = value

        def value(self):
            return self._value

    class DummyCombo:
        def __init__(self, value):
            self._value = value

        def currentText(self):
            return self._value

    window.last_config_save_error = ""
    window.current_role = "admin"
    window.current_protocol = "smb"
    window.enable_ftp_server = False
    window.enable_auto_delete = False
    window.auto_delete_folder = ""
    window.auto_delete_folders = []
    window.auto_delete_threshold = 80
    window.auto_delete_target_percent = 40
    window.auto_delete_keep_days = 10
    window.auto_delete_check_interval = 300
    window.auto_delete_formats = []
    window.auto_delete_use_trash = True
    window.app_dir = tmp_path
    window.saved_config = {}
    window.src_edit = DummyText("D:/Image")
    window.tgt_edit = DummyText("Z:/vision/PB-018")
    window.bak_edit = DummyText("")
    window.cb_enable_backup = DummyBool(False)
    window.spin_interval = DummyValue(30)
    window.spin_disk = DummyValue(10)
    window.spin_retry = DummyValue(3)
    window.spin_disk_check = DummyValue(5)
    window.cb_ext = {
        ".jpg": DummyBool(True),
        ".png": DummyBool(True),
        ".bmp": DummyBool(True),
        ".gif": DummyBool(True),
        ".raw": DummyBool(True),
    }
    window.cb_auto_start_windows = DummyBool(False)
    window.cb_auto_run_on_startup = DummyBool(False)
    window.cb_auto_ftp_on_startup = DummyBool(False)
    window.cb_show_notifications = DummyBool(True)
    window.cb_limit_rate = DummyBool(False)
    window.spin_max_rate = DummyValue(10.0)
    window.cb_dedup_enable = DummyBool(False)
    window.combo_hash = DummyCombo("MD5")
    window.combo_strategy = DummyCombo("询问")
    window.spin_network_check = DummyValue(10)
    window.cb_network_auto_pause = DummyBool(True)
    window.cb_network_auto_resume = DummyBool(True)
    window.ftp_server_host = DummyText("0.0.0.0")
    window.ftp_server_port = DummyValue(2121)
    window.ftp_server_user = DummyText("upload_user")
    window.ftp_server_pass = DummyText("")
    window.ftp_server_share = DummyText("")
    window.cb_server_passive = DummyBool(True)
    window.ftp_server_passive_start = DummyValue(60000)
    window.ftp_server_passive_end = DummyValue(65535)
    window.cb_server_tls = DummyBool(False)
    window.ftp_server_max_conn = DummyValue(256)
    window.ftp_server_max_conn_per_ip = DummyValue(5)
    window.ftp_client_host = DummyText("")
    window.ftp_client_port = DummyValue(21)
    window.ftp_client_user = DummyText("")
    window.ftp_client_pass = DummyText("")
    window.ftp_client_remote = DummyText("/upload")
    window.ftp_client_timeout = DummyValue(30)
    window.ftp_client_retry = DummyValue(3)
    window.cb_client_passive = DummyBool(True)
    window.cb_client_tls = DummyBool(False)
    window._append_log = lambda *args, **kwargs: None
    window._toast = lambda *args, **kwargs: None
    window._validate_paths = lambda: (True, [])
    window._encrypt_ftp_password = lambda password, label: (password, "")
    window._update_auto_cleanup_schedule = lambda: None
    window._write_config_payload = lambda cfg: captured.setdefault("cfg", cfg) or True

    try:
        set_language(LANG_EN_US)

        assert MainWindow._save_config(window) is True
        assert captured["cfg"]["language"] == LANG_EN_US
    finally:
        set_language(LANG_ZH_CN)


def test_control_states_limit_config_editing_to_admin():
    window = MainWindow.__new__(MainWindow)
    window._append_log = lambda *args, **kwargs: None

    guest = MainWindow._compute_control_states(window, "guest", False, True)
    user = MainWindow._compute_control_states(window, "user", False, True)
    admin = MainWindow._compute_control_states(window, "admin", False, True)
    admin_running = MainWindow._compute_control_states(window, "admin", True, True)

    assert guest["btn_start"] is False
    assert guest["btn_save"] is False
    assert user["btn_start"] is True
    assert user["btn_save"] is False
    assert user["btn_choose_src"] is False
    assert user["upload_settings"] is False
    assert admin["btn_save"] is True
    assert admin["btn_choose_src"] is True
    assert admin_running["btn_save"] is False
    assert admin_running["btn_start"] is False


def test_permission_manager_save_config_is_admin_only():
    manager = PermissionManager()

    assert manager.has_permission("save_config") is False

    assert manager.login(PermissionManager.ROLE_USER, PermissionManager.DEFAULT_USER_PASSWORD)[0] is True
    assert manager.has_permission("start") is True
    assert manager.has_permission("save_config") is False

    assert manager.login(PermissionManager.ROLE_ADMIN, PermissionManager.DEFAULT_ADMIN_PASSWORD)[0] is True
    assert manager.has_permission("save_config") is True


def test_save_config_rejects_non_admin_roles():
    window = MainWindow.__new__(MainWindow)
    logs = []
    toasts = []
    window._append_log = logs.append
    window._toast = lambda *args: toasts.append(args)

    for role in ("guest", "user"):
        window.current_role = role
        window.last_config_save_error = ""
        assert MainWindow._save_config(window) is False
        assert window.last_config_save_error == "仅管理员可以保存配置"

    assert any("无权保存配置" in message for message in logs)


def test_write_config_payload_uses_config_manager_save(monkeypatch, tmp_path):
    window = MainWindow.__new__(MainWindow)
    window.app_dir = tmp_path
    calls = []

    class FakeConfigManager:
        def __init__(self, path):
            self.path = path

        def save(self, cfg):
            calls.append((self.path, cfg))
            return True

    monkeypatch.setattr(main_window_module, "ConfigManager", FakeConfigManager)

    payload = {"source_folder": "D:/Image"}
    assert MainWindow._write_config_payload(window, payload) is True
    assert calls == [(tmp_path / "config.json", payload)]
    assert window.last_config_save_error == ""


def test_write_config_payload_reports_config_manager_failure(monkeypatch, tmp_path):
    window = MainWindow.__new__(MainWindow)
    window.app_dir = tmp_path

    class FakeConfigManager:
        def __init__(self, path):
            pass

        def save(self, cfg):
            return False

    monkeypatch.setattr(main_window_module, "ConfigManager", FakeConfigManager)

    assert MainWindow._write_config_payload(window, {"source_folder": "D:/Image"}) is False
    assert window.last_config_save_error == "配置保存失败"


def test_ftp_server_stop_closes_listener_and_waits_for_thread():
    server = FTPServerManager.__new__(FTPServerManager)
    server.is_running = True
    server.server_thread = None
    server._stop_event = threading.Event()

    calls = []

    class FakeServer:
        def close_all(self):
            calls.append("close_all")

        def close(self):
            calls.append("close")

    server.server = FakeServer()

    assert FTPServerManager.stop(server) is True
    assert calls == ["close_all", "close"]
    assert server.server is None
    assert server.is_running is False
