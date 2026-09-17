"""
文件名：src/repositories/cleanup_audit_repository.py
文件作用：运行期持久化边界模块“cleanup_audit_repository”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Sequential JSON-lines audit persistence for automatic cleanup.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import threading
from typing import Any


class CleanupAuditRepository:
    def __init__(self, app_dir: Path) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._logs_dir = app_dir / "logs"
        self._lock = threading.Lock()
        self.last_error = ""

    def write(self, event: str, run_id: str, **fields: Any) -> bool:
        """作用：执行“write”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
