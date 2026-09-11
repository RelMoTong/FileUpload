"""P0-02 FTP transfer-result and disconnect-race regressions."""

from __future__ import annotations

from ftplib import error_perm
from pathlib import Path
import sys
import threading
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.protocols.ftp import FTPClientUploader, FTPServerManager
from src.models import FTPOperationResult
from src.workers.upload_worker import UploadWorker


class ConfirmingFTP:
    def __init__(self, remote_size: int | None = None) -> None:
        self.remote_size = remote_size
        self.command_count = 0

    def storbinary(self, _command, stream, callback=None):
        self.command_count += 1
        payload = stream.read()
        if callback:
            callback(payload)
        if self.remote_size is None:
            self.remote_size = len(payload)
        return "226 Transfer complete"

    def size(self, _remote_path):
        return self.remote_size

    def close(self):
        return None


def _connected_client(ftp: object | None) -> FTPClientUploader:
    client = FTPClientUploader({"remote_path": "/", "retry_count": 1})
    client.ftp = ftp  # type: ignore[assignment]
    client.connected = True
    return client


def test_missing_socket_cannot_report_success(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")
    client = _connected_client(None)

    result = client.upload_file_result(source, "/image.bin")

    assert not result.success
    assert result.status["code"] == "connection_missing"
    assert result.status["command_executed"] is False


def test_connection_cleared_after_stor_is_still_failure(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")
    ftp = ConfirmingFTP()
    client = _connected_client(ftp)
    original = ftp.storbinary

    def clear_connection(*args, **kwargs):
        response = original(*args, **kwargs)
        client.ftp = None
        client.connected = False
        return response

    ftp.storbinary = clear_connection  # type: ignore[method-assign]

    result = client.upload_file_result(source, "/image.bin")

    assert not result.success
    assert result.status["code"] == "connection_lost"
    assert result.status["command_executed"] is True


def test_remote_size_mismatch_is_failure(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")
    client = _connected_client(ConfirmingFTP(remote_size=2))

    result = client.upload_file_result(source, "/image.bin")

    assert not result.success
    assert result.status["code"] == "remote_size_mismatch"
    assert result.status["local_size"] == 7
    assert result.status["remote_size"] == 2


def test_server_rejection_is_structured_failure(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")

    class RejectingFTP(ConfirmingFTP):
        def storbinary(self, *_args, **_kwargs):
            self.command_count += 1
            raise error_perm("550 write denied")

    client = _connected_client(RejectingFTP())

    result = client.upload_file_result(source, "/image.bin")

    assert not result.success
    assert result.status["code"] == "permission_denied"
    assert result.status["command_executed"] is True


def test_non_success_stor_response_is_structured_failure(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")

    class UnconfirmedFTP(ConfirmingFTP):
        def storbinary(self, command, stream, callback=None):
            super().storbinary(command, stream, callback)
            return "150 Transfer in progress"

    result = _connected_client(UnconfirmedFTP()).upload_file_result(source, "/image.bin")

    assert not result.success
    assert result.status["code"] == "response_unconfirmed"
    assert result.status["command_executed"] is True


def test_ftp_failure_never_queues_archive_or_deletes_source(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    source_root.mkdir()
    target_root.mkdir()
    backup_root.mkdir()
    source = source_root / "camera.jpg"
    source.write_bytes(b"image-data")

    worker = UploadWorker(
        str(source_root), str(target_root), str(backup_root), 30, "periodic", 10,
        3, [".jpg"], tmp_path, upload_protocol="ftp_client",
        ftp_client_config={"remote_path": "/upload"},
    )

    class RejectingUploader:
        def upload_file_result(self, *_args, **_kwargs):
            return FTPOperationResult(
                False,
                "FTP connection lost",
                errors=("FTP connection lost",),
                status={"code": "connection_lost"},
            )

    worker.ftp_client = RejectingUploader()
    worker._running = True
    worker.retry_queue[str(source)] = {"count": 1, "next": 0.0}

    worker._process_retry_queue()

    assert source.exists()
    assert worker.archive_queue.empty()
    assert str(source) in worker.retry_queue


def test_confirmed_stor_and_matching_remote_size_succeeds(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")
    ftp = ConfirmingFTP()
    client = _connected_client(ftp)

    result = client.upload_file_result(source, "/image.bin")

    assert result.success
    assert result.status["code"] == "confirmed"
    assert result.status["local_size"] == result.status["remote_size"] == 7
    assert ftp.command_count == 1


def test_disconnect_during_upload_never_reports_success(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")

    for _ in range(25):
        entered = threading.Event()
        closed = threading.Event()

        class BlockingFTP(ConfirmingFTP):
            def storbinary(self, *_args, **_kwargs):
                self.command_count += 1
                entered.set()
                closed.wait(1)
                raise OSError("socket closed")

            def close(self):
                closed.set()

        client = _connected_client(BlockingFTP())
        results = []
        thread = threading.Thread(
            target=lambda: results.append(
                client.upload_file_result(source, "/image.bin")
            )
        )
        thread.start()
        assert entered.wait(1)
        client.disconnect()
        thread.join(1)

        assert not thread.is_alive()
        assert len(results) == 1
        assert not results[0].success
        assert results[0].status["code"] == "transfer_error"


def test_disconnect_during_remote_size_confirmation_is_not_success(tmp_path: Path) -> None:
    source = tmp_path / "image.bin"
    source.write_bytes(b"payload")
    entered_size_check = threading.Event()
    closed = threading.Event()

    class SizeCheckingFTP(ConfirmingFTP):
        def size(self, _remote_path):
            entered_size_check.set()
            closed.wait(1)
            return self.remote_size

        def close(self):
            closed.set()

    client = _connected_client(SizeCheckingFTP())
    results = []
    thread = threading.Thread(
        target=lambda: results.append(client.upload_file_result(source, "/image.bin"))
    )
    thread.start()
    assert entered_size_check.wait(1)
    client.disconnect()
    thread.join(1)

    assert not thread.is_alive()
    assert len(results) == 1
    assert not results[0].success
    assert results[0].status["code"] == "connection_lost"


def test_real_server_upload_requires_stor_and_remote_size_confirmation(
    tmp_path: Path,
    free_tcp_port: int,
) -> None:
    share = tmp_path / "share"
    share.mkdir()
    server = FTPServerManager(
        {
            "host": "127.0.0.1",
            "port": free_tcp_port,
            "username": "upload_user",
            "password": "upload_pass",
            "shared_folder": str(share),
        }
    )
    source = tmp_path / "confirmed.bin"
    source.write_bytes(b"confirmed-payload")
    client = FTPClientUploader(
        {
            "host": "127.0.0.1",
            "port": free_tcp_port,
            "username": "upload_user",
            "password": "upload_pass",
            "remote_path": "/",
            "retry_count": 1,
        }
    )
    assert server.start()
    time.sleep(0.2)
    try:
        assert client.connect()
        result = client.upload_file_result(source, "/confirmed.bin")
    finally:
        client.disconnect()
        server.stop()

    assert result.success
    assert result.status["response"].startswith("2")
    assert result.status["local_size"] == result.status["remote_size"]
    assert (share / "confirmed.bin").read_bytes() == b"confirmed-payload"
