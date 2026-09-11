"""Pure unit tests for the FTP MVC boundary."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.controllers import FTPController
from src.models import FTPEvent
from src.protocols.ftp import FTPClientUploader
from src.services import FTPService


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, config: dict):
        self.config = config
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, config: dict):
        self.config = config
        self.disconnected = False
        self.instances.append(self)

    def test_connection(self) -> bool:
        return True

    def disconnect(self) -> None:
        self.disconnected = True


class FakeManager:
    def __init__(self):
        self.server = None
        self.config: dict = {}
        self.stop_all_called = False

    def start_server(self, config: dict) -> bool:
        self.config = config
        self.server = SimpleNamespace(is_running=True)
        return True

    def stop_server(self) -> bool:
        if self.server is not None:
            self.server.is_running = False
        return True

    def get_status(self) -> dict:
        if self.server is None:
            return {"server": None}
        return {
            "server": {
                "running": self.server.is_running,
                "address": "127.0.0.1:2121",
                "shared_folder": self.config.get("shared_folder", ""),
                "connections": 0,
            }
        }

    def stop_all(self) -> None:
        self.stop_all_called = True
        self.stop_server()


class EventWriter:
    def __init__(self):
        self.events: list[FTPEvent] = []

    def write(self, event: FTPEvent) -> None:
        self.events.append(event)


def _server_config(shared_folder: Path) -> dict:
    return {
        "host": "127.0.0.1",
        "port": 2121,
        "username": "upload_user",
        "password": "upload_pass",
        "shared_folder": str(shared_folder),
        "enable_passive": True,
        "passive_ports_start": 60000,
        "passive_ports_end": 60100,
    }


def _client_config() -> dict:
    return {
        "host": "ftp.example.com",
        "port": 21,
        "username": "upload_user",
        "password": "upload_pass",
        "remote_path": "/upload",
    }


def _service(managers: list[FakeManager] | None = None) -> FTPService:
    def manager_factory() -> FakeManager:
        manager = FakeManager()
        if managers is not None:
            managers.append(manager)
        return manager

    return FTPService(
        manager_factory=manager_factory,
        server_factory=FakeServer,
        client_factory=FakeClient,
    )


def test_validation_is_independent_from_main_window(tmp_path: Path) -> None:
    service = _service()

    assert service.validate_server(_server_config(tmp_path)).is_valid
    assert service.validate_client(_client_config()).is_valid
    invalid = service.validate_configuration(
        True,
        "ftp_client",
        {**_server_config(tmp_path), "shared_folder": str(tmp_path / "missing")},
        {**_client_config(), "remote_path": "relative"},
    )

    assert not invalid.is_valid
    assert any("共享目录不存在" in error for error in invalid.errors)
    assert any("应以 / 开头" in error for error in invalid.errors)


def test_ftps_requires_existing_certificate_and_private_key(tmp_path: Path) -> None:
    service = _service()
    config = {**_server_config(tmp_path), "enable_tls": True}

    missing = service.validate_server(config)

    assert not missing.is_valid
    assert "FTPS 证书文件为空" in missing.errors
    assert "FTPS 私钥文件为空" in missing.errors


def test_ftps_validates_cert_chain_and_passes_paths_to_protocol(tmp_path: Path) -> None:
    service = _service()
    cert_file = tmp_path / "server-cert.pem"
    key_file = tmp_path / "server-key.pem"
    cert_file.write_text("certificate", encoding="utf-8")
    key_file.write_text("private-key", encoding="utf-8")
    config = {
        **_server_config(tmp_path),
        "enable_tls": True,
        "cert_file": str(cert_file),
        "key_file": str(key_file),
    }

    with mock.patch(
        "src.services.ftp_service.TLS_FTPHandler", object()
    ), mock.patch("ssl.SSLContext.load_cert_chain") as load_chain:
        validation = service.validate_server(config)

    assert validation.is_valid
    load_chain.assert_called_once_with(
        certfile=str(cert_file), keyfile=str(key_file)
    )
    built = service.build_server_config(config)
    assert built["cert_file"] == str(cert_file)
    assert built["key_file"] == str(key_file)


def test_ftps_rejects_invalid_or_mismatched_cert_chain(tmp_path: Path) -> None:
    service = _service()
    cert_file = tmp_path / "server-cert.pem"
    key_file = tmp_path / "server-key.pem"
    cert_file.write_text("invalid", encoding="utf-8")
    key_file.write_text("invalid", encoding="utf-8")

    with mock.patch("src.services.ftp_service.TLS_FTPHandler", object()):
        validation = service.validate_server(
            {
                **_server_config(tmp_path),
                "enable_tls": True,
                "cert_file": str(cert_file),
                "key_file": str(key_file),
            }
        )

    assert not validation.is_valid
    assert any("无效或不匹配" in error for error in validation.errors)


def test_connection_tests_release_temporary_protocol_objects(tmp_path: Path) -> None:
    FakeServer.instances.clear()
    FakeClient.instances.clear()
    service = _service()

    assert service.test_server(_server_config(tmp_path)).success
    assert FakeServer.instances[-1].started
    assert FakeServer.instances[-1].stopped
    assert service.test_client(_client_config()).success
    assert FakeClient.instances[-1].disconnected


def test_controller_tracks_server_owner_and_delegates_lifecycle(tmp_path: Path) -> None:
    managers: list[FakeManager] = []
    controller = FTPController(_service(managers))

    started = controller.start_server(_server_config(tmp_path), source="upload")

    assert started.success
    assert controller.server_started_by_upload
    assert not controller.server_started_independently
    assert controller.is_server_running()
    assert controller.server_status()["address"] == "127.0.0.1:2121"

    stopped = controller.stop_upload_server()
    assert stopped.success
    assert not controller.is_server_running()
    assert not controller.server_started_by_upload


def test_controller_persists_and_forwards_formatted_server_events(tmp_path: Path) -> None:
    managers: list[FakeManager] = []
    writer = EventWriter()
    notifications: list[dict] = []
    controller = FTPController(_service(managers), writer)
    controller.set_event_listener(notifications.append)
    assert controller.start_server(_server_config(tmp_path), source="independent").success

    managers[0].config["event_callback"](
        {
            "timestamp": "2026-08-07T12:00:00",
            "event": "upload_ok",
            "client_ip": "127.0.0.1",
            "username": "upload_user",
            "path": str(tmp_path / "received.jpg"),
            "size": 123,
        }
    )

    assert writer.events[0].event == "upload_ok"
    assert notifications[0]["display_message"] == "✅ [FTP-SERVER] 上传成功: received.jpg (123 字节)"
    assert controller.server_started_independently


def test_controller_shutdown_stops_all_resources(tmp_path: Path) -> None:
    managers: list[FakeManager] = []
    controller = FTPController(_service(managers))
    assert controller.start_server(_server_config(tmp_path), source="independent").success

    controller.shutdown()

    assert managers[0].stop_all_called
    assert not controller.server_started_independently
    assert not controller.is_server_running()


def test_connect_final_failure_has_no_extra_retry_wait() -> None:
    failed_ftp = mock.Mock()
    failed_ftp.connect.side_effect = OSError("offline")
    client = FTPClientUploader({**_client_config(), "retry_count": 1})

    started = time.monotonic()
    with mock.patch("src.protocols.ftp.FTP", return_value=failed_ftp) as factory:
        assert not client.connect()
    elapsed = time.monotonic() - started

    assert factory.call_count == 1
    assert failed_ftp.connect.call_count == 1
    assert elapsed < 0.2


def test_connect_retry_wait_can_be_cancelled_immediately() -> None:
    attempts = threading.Event()
    failed_ftp = mock.Mock()
    failed_ftp.connect.side_effect = OSError("offline")
    client = FTPClientUploader({**_client_config(), "retry_count": 3})
    outcome: list[bool] = []

    with mock.patch("src.protocols.ftp.FTP", return_value=failed_ftp):
        thread = threading.Thread(
            target=lambda: outcome.append(
                client.connect(progress_callback=lambda *_: attempts.set())
            )
        )
        thread.start()
        assert attempts.wait(1)
        client.cancel()
        thread.join(1)

    assert not thread.is_alive()
    assert outcome == [False]
    assert failed_ftp.connect.call_count == 1


def test_disconnect_does_not_wait_for_connect_lock() -> None:
    entered = threading.Event()
    released = threading.Event()

    class BlockingFTP:
        def connect(self, **_kwargs):
            entered.set()
            released.wait(5)
            raise OSError("cancelled")

        def close(self):
            released.set()

    client = FTPClientUploader({**_client_config(), "retry_count": 1})
    outcome: list[bool] = []
    with mock.patch("src.protocols.ftp.FTP", return_value=BlockingFTP()):
        thread = threading.Thread(target=lambda: outcome.append(client.connect()))
        thread.start()
        assert entered.wait(1)
        started = time.monotonic()
        client.disconnect()
        elapsed = time.monotonic() - started
        thread.join(1)

    assert elapsed < 0.2
    assert not thread.is_alive()
    assert outcome == [False]


def test_async_client_test_emits_cancelled_event_and_shutdown_returns() -> None:
    entered = threading.Event()
    events: list[dict] = []

    class SlowClient:
        def __init__(self, _config):
            self.cancelled = threading.Event()

        def test_connection(self, cancel_event=None, progress_callback=None):
            if progress_callback:
                progress_callback(1, 3)
            entered.set()
            assert cancel_event is not None
            cancel_event.wait(5)
            return False

        def cancel(self):
            self.cancelled.set()

        def disconnect(self):
            return None

    service = FTPService(
        manager_factory=FakeManager,
        server_factory=FakeServer,
        client_factory=SlowClient,
    )
    controller = FTPController(service)
    started = time.monotonic()
    result = controller.start_client_test(_client_config(), events.append)

    assert result.success
    assert time.monotonic() - started < 0.2
    assert entered.wait(1)
    controller.cancel_client_test()
    deadline = time.monotonic() + 1
    while controller.client_test_running and time.monotonic() < deadline:
        time.sleep(0.01)
    shutdown_started = time.monotonic()
    controller.shutdown()

    assert time.monotonic() - shutdown_started < 0.2
    assert any(event["type"] == "progress" for event in events)
    assert any(event["type"] == "cancelled" for event in events)
