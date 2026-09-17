"""
文件名：src/repositories/ftp_event_log_repository.py
文件作用：运行期持久化边界模块“ftp_event_log_repository”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Dedicated persistence for FTP server audit events.
"""

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
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._logs_dir = app_dir / "logs"

    def write(self, event: FTPEvent) -> Path:
        """作用：执行“write”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
