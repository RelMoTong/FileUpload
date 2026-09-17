"""
文件名：src/services/path_probe_service.py
文件作用：业务服务层的“path_probe_service”模块。
主要功能：封装既有业务规则、后台任务生命周期与底层协作者调用。
模块关系：由控制器或组合根使用，可调用 Repository、Worker 和 Protocol；不直接操作 View。
阅读重点：关注输入校验、状态转换、线程/定时器收尾、文件与网络失败路径。

Bounded, cancellable filesystem probes for the UI layer.
"""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable
from src.models.path_probe import PathProbe, PathProbeResult


class PathProbeService:
    """Probe local and remote directories without blocking the Qt event loop.

    Remote checks run in a short-lived PowerShell process so a hung UNC redirector
    can be timed out.  A generation token prevents obsolete completions from
    being applied by the caller.
    """

    def __init__(self, max_workers: int = 2, timeout: float = 2.0) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="PathProbe"
        )
        self._timeout = max(0.5, timeout)
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False

    def probe(
        self,
        probes: Iterable[PathProbe],
        callback: Callable[[PathProbeResult], None],
        *,
        timeout: float | None = None,
    ) -> int:
        """Schedule a probe and call *callback* from a worker thread."""
        with self._lock:
            if self._closed:
                raise RuntimeError("PathProbeService has been shut down")
            self._generation += 1
            generation = self._generation
        items = tuple(probes)
        future = self._executor.submit(
            self._probe_all, generation, items, timeout or self._timeout
        )
        future.add_done_callback(lambda completed: self._deliver(completed, callback))
        return generation

    def cancel(self) -> int:
        """Invalidate outstanding callbacks and return the new generation."""
        with self._lock:
            self._generation += 1
            return self._generation

    def shutdown(self) -> None:
        """作用：执行“shutdown”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _deliver(
        self,
        future: Future[PathProbeResult],
        callback: Callable[[PathProbeResult], None],
    ) -> None:
        """内部辅助：完成“_deliver”对应的既有局部工作。"""
        try:
            result = future.result()
        except Exception as exc:
            result = PathProbeResult(0, (f"路径探测异常: {type(exc).__name__}: {exc}",))
        with self._lock:
            current = self._generation
        if result.generation != current:
            result = PathProbeResult(result.generation, cancelled=True)
        try:
            callback(result)
        except Exception:
            pass

    def _probe_all(
        self, generation: int, probes: tuple[PathProbe, ...], timeout: float
    ) -> PathProbeResult:
        """内部辅助：完成“_probe_all”对应的既有局部工作。"""
        errors: list[str] = []
        timed_out = False
        for probe in probes:
            with self._lock:
                if self._closed or generation != self._generation:
                    return PathProbeResult(generation, cancelled=True)
            ok, expired = self._probe_one(probe, timeout)
            timed_out = timed_out or expired
            if not ok:
                suffix = "探测超时或不可达" if expired else "不存在、不是目录或无访问权限"
                errors.append(f"{probe.label}{suffix}: {probe.path}")
        return PathProbeResult(generation, tuple(errors), timed_out=timed_out)

    @staticmethod
    def _is_remote_path(path: str) -> bool:
        """内部辅助：完成“_is_remote_path”对应的既有局部工作。"""
        normalized = path.replace("/", "\\")
        return normalized.startswith("\\\\")

    def _probe_one(self, probe: PathProbe, timeout: float) -> tuple[bool, bool]:
        """作用：执行“_probe_one”的既有业务服务职责。

        参数：沿用当前函数签名及已有路径、单位、超时、状态和类型约定。
        返回结果：沿用当前实现的返回值、事件、Future 或异常语义。
        执行流程：按现有代码顺序完成校验、任务调度、状态更新与结果交付。
        风险或注意事项：本说明不改变业务规则、线程模型、持久化格式或公开接口。
        """
        path = probe.path.strip()
        if not path:
            return False, False
        if not self._is_remote_path(path):
            candidate = Path(path)
            if not candidate.is_dir():
                if not probe.require_write or candidate.exists():
                    return False, False
                candidate = candidate.parent
                if not candidate.is_dir():
                    return False, False
            if probe.require_write:
                try:
                    probe_file = candidate / ".image_upload_probe"
                    with probe_file.open("xb"):
                        pass
                    probe_file.unlink()
                except OSError:
                    return False, False
            return True, False

        env = os.environ.copy()
        env["IMAGE_UPLOAD_PROBE_PATH"] = path
        write_check = ""
        if probe.require_write:
            write_check = (
                "; $f=Join-Path $p ('.image_upload_probe_' + [guid]::NewGuid().ToString('N')); "
                "try { [IO.File]::WriteAllBytes($f,[byte[]]@()); Remove-Item -LiteralPath $f -Force -ErrorAction Stop } catch { exit 1 }"
            )
        command = (
            "$p=$env:IMAGE_UPLOAD_PROBE_PATH; "
            "if (-not (Test-Path -LiteralPath $p -PathType Container)) { exit 1 }"
            + write_check
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=env,
            )
            return completed.returncode == 0, False
        except subprocess.TimeoutExpired:
            return False, True
        except OSError:
            return False, False
