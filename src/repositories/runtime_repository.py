"""Persistence adapters for runtime logs and Windows startup registration."""

from __future__ import annotations

import datetime
from pathlib import Path
import threading
from typing import Optional
import winreg


STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ImageUploader"


class DailyLogRepository:
    RETENTION_DAYS = 30

    def __init__(self, app_dir: Path) -> None:
        self._app_dir = Path(app_dir)
        self._lock = threading.Lock()
        self.last_error = ""

    def initialize(self) -> bool:
        try:
            with self._lock:
                path = self._path_for_today()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._prune_old_logs(path.parent)
                if not path.exists():
                    today = datetime.datetime.now().strftime("%Y-%m-%d")
                    path.write_text(
                        f"{'=' * 60}\n"
                        "  图片异步上传工具 - 运行日志\n"
                        f"  日期: {today}\n"
                        f"{'=' * 60}\n\n",
                        encoding="utf-8",
                    )
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def append(self, line: str) -> bool:
        try:
            with self._lock:
                path = self._path_for_today()
                path.parent.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(f"[{timestamp}] {line}\n")
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def _path_for_today(self) -> Path:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        return self._app_dir / "logs" / f"upload_{today}.txt"

    def _prune_old_logs(self, log_dir: Path) -> None:
        """Daily rotation with a fixed 30-day retention window."""
        cutoff = (
            datetime.datetime.now() - datetime.timedelta(days=self.RETENTION_DAYS)
        ).timestamp()
        for path in log_dir.glob("upload_*.txt"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                # Retention failure must not prevent today's log from opening.
                continue


class WindowsStartupRepository:
    def __init__(self) -> None:
        self.last_error = ""

    def read(self) -> str:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REGISTRY_PATH,
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
                self.last_error = ""
                return value if isinstance(value, str) else ""
        except FileNotFoundError:
            self.last_error = ""
            return ""
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def write(self, command: str) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REGISTRY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def delete(self) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REGISTRY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            raise
