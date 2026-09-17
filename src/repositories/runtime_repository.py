"""
文件名：src/repositories/runtime_repository.py
文件作用：运行期持久化边界模块“runtime_repository”。
主要功能：在既有分层内处理网络、文件、状态记录或后台任务。
模块关系：由服务或组合根注入调用；保留现有 JSON、网络和线程边界。
阅读重点：关注资源生命周期、失败路径、状态记录和路径/网络安全条件。

Persistence adapters for runtime logs and Windows startup registration.
"""

from __future__ import annotations

import datetime
from pathlib import Path
import threading
import winreg


STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ImageUploader"


class DailyLogRepository:
    RETENTION_DAYS = 30

    def __init__(self, app_dir: Path) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._app_dir = Path(app_dir)
        self._lock = threading.Lock()
        self.last_error = ""

    def initialize(self) -> bool:
        """作用：执行“initialize”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
        """作用：执行“append”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
        """内部辅助：完成“_path_for_today”对应的既有局部工作。"""
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
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self.last_error = ""

    def read(self) -> str:
        """作用：执行“read”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
        """作用：执行“write”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
        """作用：执行“delete”的既有业务或基础设施职责。

        参数：沿用当前函数签名及已有路径、端口、重试、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件或异常语义。
        执行流程：按现有代码顺序完成校验、处理、状态记录与结果交付。
        风险或注意事项：本说明不改变文件、网络、JSON 持久化、线程或公开接口约定。
        """
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
