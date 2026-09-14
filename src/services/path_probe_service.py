"""Bounded, cancellable filesystem probes for the UI layer."""

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
        normalized = path.replace("/", "\\")
        return normalized.startswith("\\\\")

    def _probe_one(self, probe: PathProbe, timeout: float) -> tuple[bool, bool]:
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
