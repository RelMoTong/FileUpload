"""
文件名：src/services/runtime_service.py
文件作用：业务服务层的“runtime_service”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。

Operating-system and filesystem policies used by the application runtime.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Optional, Protocol, Tuple

from src.models.runtime import RuntimeCommandResult, RuntimeInitializationResult


class RuntimeLogWriter(Protocol):
    last_error: str

    def initialize(self) -> bool:
        """协议占位：声明“initialize”的最小调用约定，由实现方提供既有行为。"""
        ...
    def append(self, line: str) -> bool:
        """协议占位：声明“append”的最小调用约定，由实现方提供既有行为。"""
        ...


class StartupRepository(Protocol):
    last_error: str

    def read(self) -> str:
        """协议占位：声明“read”的最小调用约定，由实现方提供既有行为。"""
        ...
    def write(self, command: str) -> None:
        """协议占位：声明“write”的最小调用约定，由实现方提供既有行为。"""
        ...
    def delete(self) -> None:
        """协议占位：声明“delete”的最小调用约定，由实现方提供既有行为。"""
        ...


def parse_app_version(value: str) -> Optional[Tuple[int, ...]]:
    """作用：执行“parse_app_version”的既有业务服务职责。

    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
    """
    match = re.search(
        r"(?:^|[_-])v?(\d+(?:\.\d+){1,3})(?:\D|$)",
        value or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def extract_startup_target(command: str) -> str:
    """作用：执行“extract_startup_target”的既有业务服务职责。

    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
    """
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else ""
    match = re.match(r"(.+?\.(?:exe|com|bat|cmd|pyw?|py))(?=\s|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.split(maxsplit=1)[0]


def extract_startup_script(command: str) -> str:
    """作用：执行“extract_startup_script”的既有业务服务职责。

    参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
    返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
    执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
    风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
    """
    text = (command or "").strip()
    target = extract_startup_target(text)
    if not target:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        remainder = text[end + 1 :].strip() if end > 1 else ""
    else:
        remainder = text[len(target) :].strip()
    if not remainder:
        return ""
    if remainder.startswith('"'):
        end = remainder.find('"', 1)
        return remainder[1:end] if end > 1 else ""
    return remainder.split(maxsplit=1)[0]


class RuntimeService:
    def __init__(
        self,
        app_dir: Path,
        log_writer: RuntimeLogWriter,
        startup_repository: StartupRepository,
        *,
        executable: str,
        main_script: Path,
        frozen: bool,
        app_version: str,
        file_exists: Callable[[str], bool] = os.path.isfile,
    ) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self.app_dir = Path(app_dir)
        self._log_writer = log_writer
        self._startup_repository = startup_repository
        self._executable = os.path.abspath(executable)
        self._main_script = Path(main_script)
        self._frozen = frozen
        self._app_version = app_version
        self._file_exists = file_exists

    def initialize(self) -> RuntimeInitializationResult:
        """作用：执行“initialize”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        success = self._log_writer.initialize()
        return RuntimeInitializationResult(
            self.app_dir,
            success,
            "" if success else self._log_writer.last_error,
        )

    def append_log(self, line: str) -> bool:
        """作用：执行“append_log”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return self._log_writer.append(line)

    @staticmethod
    def is_network_path(path: str) -> bool:
        """作用：执行“is_network_path”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        if not path:
            return False
        if path.startswith("\\\\"):
            return True
        drive, _ = os.path.splitdrive(path)
        if not drive or os.name != "nt":
            return False
        try:
            root = drive + "\\"
            get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
            get_drive_type.argtypes = [ctypes.c_wchar_p]
            get_drive_type.restype = ctypes.c_uint
            return get_drive_type(root) == 4
        except Exception:
            return False

    def disk_free_percent(self, path: str, network_available: bool = True) -> float:
        """作用：执行“disk_free_percent”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        if not path or (self.is_network_path(path) and not network_available):
            return -1.0
        if self.is_network_path(path):
            try:
                env = os.environ.copy()
                env["IMAGE_UPLOAD_RUNTIME_DISK_PATH"] = path
                command = (
                    "$ErrorActionPreference='Stop'; "
                    "$p=$env:IMAGE_UPLOAD_RUNTIME_DISK_PATH; "
                    "$item=Get-Item -LiteralPath $p; $root=$item.PSDrive.Root; "
                    "$drive=[System.IO.DriveInfo]::new($root); "
                    "Write-Output ($drive.TotalSize.ToString()+'|'+"
                    "$drive.AvailableFreeSpace.ToString())"
                )
                create_flag = (
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                )
                completed = subprocess.run(
                    [
                        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                        "-Command", command,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                    creationflags=create_flag,
                    env=env,
                )
                if completed.returncode != 0:
                    return -1.0
                text = completed.stdout.decode("utf-8-sig", errors="replace").strip()
                total_text, free_text = text.split("|", 1)
                total, free = int(total_text), int(free_text)
                return (free / total) * 100 if total > 0 else 0.0
            except Exception:
                return -1.0
        if not os.path.exists(path):
            return -1.0
        try:
            usage = shutil.disk_usage(path)
            return (usage.free / usage.total) * 100 if usage.total > 0 else 0.0
        except Exception:
            return -1.0

    @staticmethod
    def quote_startup_arg(value: str) -> str:
        """作用：执行“quote_startup_arg”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        return '"' + value.replace('"', '\\"') + '"'

    def current_startup_command(self) -> str:
        """作用：执行“current_startup_command”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        executable = self.quote_startup_arg(self._executable)
        if self._frozen:
            return executable
        return f"{executable} {self.quote_startup_arg(str(self._main_script))}"

    def startup_target_exists(self, command: str) -> bool:
        """作用：执行“startup_target_exists”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        target = extract_startup_target(command)
        if not target or not self._file_exists(target):
            return False
        if Path(target).name.lower() in {
            "python", "python.exe", "pythonw", "pythonw.exe", "py", "py.exe"
        }:
            script = extract_startup_script(command)
            return bool(script and self._file_exists(script))
        return True

    def reconcile_startup(
        self, auto_enabled: bool, explicit: bool = False
    ) -> RuntimeCommandResult:
        """作用：执行“reconcile_startup”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        messages: list[str] = []
        try:
            existing = self._startup_repository.read()
            current = self.current_startup_command()
            current_target = extract_startup_target(current)
            if not self.startup_target_exists(current):
                raise RuntimeError(f"当前启动程序不存在: {current_target}")
            if not existing:
                if explicit or auto_enabled:
                    self._write_verified_startup(current)
                    messages.append(f"✓ 已写入开机自启动: {current}")
                    return RuntimeCommandResult(True, True, tuple(messages))
                return RuntimeCommandResult(True, False)

            existing_target = extract_startup_target(existing)
            existing_valid = self.startup_target_exists(existing)
            current_version = parse_app_version(self._app_version)
            existing_version = parse_app_version(existing_target)
            should_update = not existing_valid
            update_reason = "旧启动路径已失效"
            if existing_valid and current_version and existing_version:
                if current_version > existing_version:
                    should_update, update_reason = True, "检测到更高版本"
                elif current_version < existing_version:
                    messages.append(f"ℹ️ 已保留更高版本开机自启动: {existing_target}")
                    return RuntimeCommandResult(True, True, tuple(messages))
                elif existing != current and os.path.normcase(existing_target) == os.path.normcase(current_target):
                    should_update, update_reason = True, "规范化启动命令引号"
                elif explicit and os.path.normcase(existing_target) != os.path.normcase(current_target):
                    should_update, update_reason = True, "用户重新启用同版本启动项"
            elif existing_valid and existing_version is None:
                messages.append(f"⚠️ 启动项版本无法识别，保留现有路径: {existing_target}")
                return RuntimeCommandResult(True, True, tuple(messages))

            if should_update:
                self._write_verified_startup(current)
                messages.append(f"✓ {update_reason}，开机自启动已更新为: {current}")
            return RuntimeCommandResult(True, True, tuple(messages))
        except Exception as exc:
            return RuntimeCommandResult(False, False, tuple(messages), str(exc))

    def disable_startup(self) -> RuntimeCommandResult:
        """作用：执行“disable_startup”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        try:
            self._startup_repository.delete()
            if self._startup_repository.read():
                raise RuntimeError("注册表启动项删除校验失败")
            return RuntimeCommandResult(True, False, ("✓ 已从开机自启动移除",))
        except Exception as exc:
            return RuntimeCommandResult(False, True, error=str(exc))

    def _write_verified_startup(self, command: str) -> None:
        """内部辅助：完成“_write_verified_startup”对应的既有局部工作。"""
        self._startup_repository.write(command)
        written = self._startup_repository.read()
        if written != command or not self.startup_target_exists(written):
            raise RuntimeError("注册表启动命令校验失败")
