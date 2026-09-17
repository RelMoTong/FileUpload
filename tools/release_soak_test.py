"""Monitor a packaged release during a configurable Windows burn-in run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any, Sequence

import psutil


def _median_edge(values: Sequence[int | float], *, tail: bool) -> float:
    if not values:
        return 0.0
    width = max(1, len(values) // 5)
    edge = values[-width:] if tail else values[:width]
    return float(statistics.median(edge))


def _directory_bytes(path: Path) -> int:
    total = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    return total


def run_soak(executable: Path, duration_seconds: int, sample_interval: float) -> dict[str, Any]:
    executable = executable.resolve(strict=True)
    app_dir = executable.parent
    environment = os.environ.copy()
    system_root = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment.update(
        {
            "IMAGE_UPLOAD_SMOKE_TEST": "1",
            "IMAGE_UPLOAD_SMOKE_DURATION_MS": str(duration_seconds * 1000),
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONHOME": "",
            "PYTHONPATH": "",
            "PATH": os.pathsep.join((str(system_root / "System32"), str(system_root))),
        }
    )
    started_at = time.time()
    child = subprocess.Popen(
        [str(executable)],
        cwd=app_dir,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    process = psutil.Process(child.pid)
    process.cpu_percent(interval=None)
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + duration_seconds + 30
    timed_out = False
    try:
        while time.monotonic() < deadline and child.poll() is None:
            try:
                memory = process.memory_info()
                samples.append(
                    {
                        "elapsed_seconds": round(time.time() - started_at, 3),
                        "rss_bytes": int(memory.rss),
                        "cpu_percent": float(process.cpu_percent(interval=None)),
                        "threads": int(process.num_threads()),
                        "handles": int(process.num_handles()),
                        "log_bytes": _directory_bytes(app_dir / "logs"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            time.sleep(sample_interval)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            timed_out = True

    rss = [int(sample["rss_bytes"]) for sample in samples]
    threads = [int(sample["threads"]) for sample in samples]
    handles = [int(sample["handles"]) for sample in samples]
    logs = [int(sample["log_bytes"]) for sample in samples]
    rss_growth = _median_edge(rss, tail=True) - _median_edge(rss, tail=False)
    thread_growth = _median_edge(threads, tail=True) - _median_edge(threads, tail=False)
    handle_growth = _median_edge(handles, tail=True) - _median_edge(handles, tail=False)
    residual: list[int] = []
    for candidate in psutil.process_iter(("pid", "exe")):
        try:
            if candidate.info["exe"] and Path(candidate.info["exe"]).resolve() == executable:
                residual.append(int(candidate.info["pid"]))
        except (OSError, psutil.Error):
            continue
    expected_samples = max(2, int(duration_seconds / sample_interval) - 2)
    checks = {
        "completed_without_timeout": not timed_out,
        "exit_code_zero": child.returncode == 0,
        "sample_count_sufficient": len(samples) >= expected_samples,
        "rss_growth_under_25_mib": rss_growth <= 25 * 1024 * 1024,
        "thread_growth_under_5": thread_growth <= 5,
        "handle_growth_under_20": handle_growth <= 20,
        "no_residual_process": not residual,
    }
    return {
        "executable": str(executable),
        "started_at_epoch": started_at,
        "ended_at_epoch": time.time(),
        "requested_duration_seconds": duration_seconds,
        "sample_interval_seconds": sample_interval,
        "sample_count": len(samples),
        "exit_code": child.returncode,
        "timed_out": timed_out,
        "rss_growth_bytes": rss_growth,
        "rss_peak_bytes": max(rss, default=0),
        "thread_growth": thread_growth,
        "thread_peak": max(threads, default=0),
        "handle_growth": handle_growth,
        "handle_peak": max(handles, default=0),
        "cpu_peak_percent": max((float(sample["cpu_percent"]) for sample in samples), default=0.0),
        "log_growth_bytes": logs[-1] - logs[0] if len(logs) >= 2 else 0,
        "residual_pids": residual,
        "checks": checks,
        "passed": all(checks.values()),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 10 <= args.duration_seconds <= 72 * 60 * 60:
        parser.error("duration must be between 10 seconds and 72 hours")
    if not 0.5 <= args.sample_interval <= 60:
        parser.error("sample interval must be between 0.5 and 60 seconds")
    report = run_soak(args.exe, args.duration_seconds, args.sample_interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
