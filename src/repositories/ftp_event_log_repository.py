"""Dedicated persistence for FTP server audit events."""

from __future__ import annotations

from pathlib import Path

from src.models import FTPEvent


class FTPEventLogRepository:
    HEADER = "time\tevent\tclient_ip\tusername\tpath\tsize\tresult\tmessage\n"
    SUCCESS_EVENTS = {
        "server_started",
        "server_stopped",
        "connect",
        "login_ok",
        "disconnect",
        "upload_ok",
    }

    def __init__(self, app_dir: Path):
        self._logs_dir = app_dir / "logs"

    def write(self, event: FTPEvent) -> Path:
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        day = event.timestamp[:10]
        log_path = self._logs_dir / f"ftp_server_{day}.txt"
        if not log_path.exists():
            log_path.write_text(self.HEADER, encoding="utf-8")

        result = "ok" if event.event in self.SUCCESS_EVENTS else "fail"
        message = event.message.replace("\t", " ").replace("\n", " ")
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"{event.timestamp}\t{event.event}\t{event.client_ip}\t"
                f"{event.username}\t{event.path}\t{event.size}\t{result}\t{message}\n"
            )
        return log_path

