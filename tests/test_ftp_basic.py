# -*- coding: utf-8 -*-
"""FTP 基础回归测试。"""

import datetime
import ipaddress
import ssl
import time
import sys
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.protocols.ftp import FTPServerManager


def _write_self_signed_cert(folder: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file = folder / "server-cert.pem"
    key_file = folder / "server-key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


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


def test_ftps_server_completes_control_and_data_tls_handshake(
    tmp_path: Path, free_tcp_port: int,
) -> None:
    share_dir = tmp_path / "ftps_share"
    share_dir.mkdir()
    cert_file, key_file = _write_self_signed_cert(tmp_path)
    server = FTPServerManager(
        {
            "host": "127.0.0.1",
            "port": free_tcp_port,
            "username": "test_user",
            "password": "test_pass",
            "shared_folder": str(share_dir),
            "enable_tls": True,
            "cert_file": str(cert_file),
            "key_file": str(key_file),
        }
    )
    assert server.start()
    time.sleep(0.3)

    client = FTP_TLS(context=ssl._create_unverified_context())
    try:
        client.connect("127.0.0.1", free_tcp_port, timeout=10)
        client.login("test_user", "test_pass")
        client.prot_p()
        payload = tmp_path / "secure-upload.bin"
        payload.write_bytes(b"encrypted-transfer")
        with payload.open("rb") as stream:
            client.storbinary(f"STOR {payload.name}", stream)
        client.quit()
    finally:
        client.close()
        server.stop()

    assert (share_dir / "secure-upload.bin").read_bytes() == b"encrypted-transfer"
