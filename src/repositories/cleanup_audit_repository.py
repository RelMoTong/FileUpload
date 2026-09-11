"""Sequential JSON-lines audit persistence for automatic cleanup."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import threading
from typing import Any


class CleanupAuditRepository:
    def __init__(self, app_dir: Path) -> None:
        self._logs_dir = app_dir / "logs"
        self._lock = threading.Lock()
        self.last_error = ""

    def write(self, event: str, run_id: str, **fields: Any) -> bool:
        record = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "event": event,
            **fields,
        }
        try:
            with self._lock:
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                path = self._logs_dir / f"cleanup_{today}.log"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

