# -*- coding: utf-8 -*-
"""FTP 功能基础测试。"""

from ftplib import FTP
from pathlib import Path
import shutil
import threading
import time

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer


@pytest.fixture
def ftp_server(tmp_path, free_tcp_port):
    share_dir = tmp_path / "ftp_share"
    share_dir.mkdir()
    (share_dir / "test.txt").write_text("这是一个测试文件", encoding="utf-8")

    authorizer = DummyAuthorizer()
    authorizer.add_user(
        username="test_user",
        password="test_pass",
        homedir=str(share_dir),
        perm="elradfmwMT",
    )

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.banner = "测试 FTP 服务器"
    server = FTPServer(("127.0.0.1", free_tcp_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)

    try:
        yield {
            "host": "127.0.0.1",
            "port": free_tcp_port,
            "share_dir": share_dir,
        }
    finally:
        server.close_all()
        thread.join(timeout=1)


def connect(server_info):
    ftp = FTP()
    ftp.connect(server_info["host"], server_info["port"], timeout=5)
    ftp.login("test_user", "test_pass")
    return ftp


def test_ftp_server_starts_and_lists_seed_file(ftp_server):
    ftp = connect(ftp_server)
    try:
        assert "test.txt" in ftp.nlst()
    finally:
        ftp.quit()


def test_ftp_client_upload_and_download(ftp_server, tmp_path):
    ftp = connect(ftp_server)
    upload_file = tmp_path / "test_upload.txt"
    download_file = tmp_path / "test_download.txt"
    upload_file.write_text("这是要上传的测试文件", encoding="utf-8")

    try:
        with open(upload_file, "rb") as f:
            ftp.storbinary(f"STOR {upload_file.name}", f)

        uploaded_path = ftp_server["share_dir"] / upload_file.name
        assert uploaded_path.exists()

        with open(download_file, "wb") as f:
            ftp.retrbinary(f"RETR {upload_file.name}", f.write)
        assert download_file.read_text(encoding="utf-8") == "这是要上传的测试文件"
    finally:
        ftp.quit()


def test_ftp_folder_upload_preserves_structure(ftp_server, tmp_path):
    source = tmp_path / "source_folder"
    (source / "subdir1").mkdir(parents=True)
    (source / "subdir2").mkdir()
    (source / "file1.txt").write_text("文件1", encoding="utf-8")
    (source / "subdir1" / "file2.txt").write_text("文件2", encoding="utf-8")
    (source / "subdir2" / "file3.txt").write_text("文件3", encoding="utf-8")

    ftp = connect(ftp_server)
    try:
        def upload_folder(local_folder: Path, remote_base: str) -> None:
            try:
                ftp.mkd(remote_base)
            except Exception:
                pass

            for item in local_folder.iterdir():
                remote_path = f"{remote_base}/{item.name}"
                if item.is_file():
                    with open(item, "rb") as f:
                        ftp.storbinary(f"STOR {remote_path}", f)
                elif item.is_dir():
                    upload_folder(item, remote_path)

        upload_folder(source, "uploaded_folder")

        uploaded = ftp_server["share_dir"] / "uploaded_folder"
        assert (uploaded / "file1.txt").read_text(encoding="utf-8") == "文件1"
        assert (uploaded / "subdir1" / "file2.txt").read_text(encoding="utf-8") == "文件2"
        assert (uploaded / "subdir2" / "file3.txt").read_text(encoding="utf-8") == "文件3"
    finally:
        ftp.quit()


def cleanup(server, test_dir):
    """兼容旧脚本入口的清理函数。"""
    if server:
        server.close_all()
    if test_dir and Path(test_dir).exists():
        shutil.rmtree(test_dir)


def main():
    raise SystemExit("请使用 pytest 运行 tests/test_ftp_basic.py")


if __name__ == "__main__":
    main()
