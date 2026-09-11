# -*- coding: utf-8 -*-
"""FTP 服务器独立日志测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import FTPEvent
from src.repositories import FTPEventLogRepository


def test_ftp_server_event_writes_dedicated_log(tmp_path):
    event = {
        "timestamp": "2026-06-11T12:00:00",
        "event": "upload_ok",
        "client_ip": "127.0.0.1",
        "username": "upload_user",
        "path": str(tmp_path / "received.jpg"),
        "size": 123,
        "message": "文件上传成功",
    }

    repository = FTPEventLogRepository(tmp_path)
    repository.write(FTPEvent.from_mapping(event))

    log_files = list((tmp_path / "logs").glob("ftp_server_*.txt"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "time\tevent\tclient_ip\tusername\tpath\tsize\tresult\tmessage" in content
    assert "upload_ok\t127.0.0.1\tupload_user" in content
    assert "\t123\tok\t文件上传成功" in content
