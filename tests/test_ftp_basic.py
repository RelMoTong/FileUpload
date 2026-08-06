# -*- coding: utf-8 -*-
"""FTP 基础回归测试。"""

import time
import sys
from ftplib import FTP, error_perm
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.protocols.ftp import FTPServerManager


@pytest.fixture
def ftp_server(tmp_path, free_tcp_port):
    events = []
    share_dir = tmp_path / "ftp_share"
    share_dir.mkdir()
    (share_dir / "test.txt").write_text("这是一个测试文件", encoding="utf-8")

    server = FTPServerManager({
        "host": "127.0.0.1",
        "port": free_tcp_port,
        "username": "test_user",
        "password": "test_pass",
        "shared_folder": str(share_dir),
        "event_callback": events.append,
    })
    assert server.start()
    time.sleep(0.3)

    try:
        yield server, share_dir, events, free_tcp_port
    finally:
        server.stop()
        time.sleep(0.2)


def test_ftp_client_upload_download_and_events(ftp_server, tmp_path):
    _server, share_dir, events, port = ftp_server
    upload_file = tmp_path / "test_upload.txt"
    upload_file.write_text("这是要上传的测试文件", encoding="utf-8")
    download_file = tmp_path / "test_download.txt"

    ftp = FTP()
    ftp.connect("127.0.0.1", port, timeout=10)
    ftp.login("test_user", "test_pass")

    assert "test.txt" in ftp.nlst()
    with open(upload_file, "rb") as f:
        ftp.storbinary(f"STOR {upload_file.name}", f)
    with open(download_file, "wb") as f:
        ftp.retrbinary(f"RETR {upload_file.name}", f.write)
    ftp.quit()

    assert (share_dir / upload_file.name).exists()
    assert download_file.read_text(encoding="utf-8") == "这是要上传的测试文件"
    assert any(event["event"] == "login_ok" for event in events)
    assert any(
        event["event"] == "upload_ok" and event["path"].endswith(upload_file.name)
        for event in events
    )


def test_ftp_login_failure_event(ftp_server):
    _server, _share_dir, events, port = ftp_server
    ftp = FTP()
    ftp.connect("127.0.0.1", port, timeout=10)

    with pytest.raises(error_perm):
        ftp.login("test_user", "wrong_pass")
    ftp.close()

    assert any(
        event["event"] == "login_failed" and event["username"] == "test_user"
        for event in events
    )


def test_ftp_incomplete_upload_event(ftp_server):
    _server, _share_dir, events, port = ftp_server
    ftp = FTP()
    ftp.connect("127.0.0.1", port, timeout=10)
    ftp.login("test_user", "test_pass")

    data_sock = ftp.transfercmd("STOR incomplete.bin")
    data_sock.sendall(b"partial-data")
    ftp.abort()
    data_sock.close()
    ftp.close()
    time.sleep(0.5)

    assert any(
        event["event"] == "upload_incomplete" and event["path"].endswith("incomplete.bin")
        for event in events
    )
