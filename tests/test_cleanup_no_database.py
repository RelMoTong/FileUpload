"""Fail-closed cleanup coverage for the v3.5.1 no-database release."""

from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path
from unittest import mock

from src.core.safe_deletion import SafeDeletionPolicy, SafeDeletionRequest
from src.models import (
    AutoCleanupRequest,
    CleanupCandidate,
    CleanupDeleteRequest,
    CleanupFileItem,
    CleanupScanRequest,
)
from src.services.cleanup_service import (
    CleanupService,
    _DeleteWorker,
    _ScanWorker,
    iter_cleanup_candidates,
    oldest_cleanup_candidates,
)


class AuditRecorder:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.last_error = "" if accepted else "injected audit failure"
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def write(self, event: str, run_id: str, **fields: object) -> bool:
        self.records.append((event, run_id, fields))
        return self.accepted


class NeverCancelled:
    def is_set(self) -> bool:
        return False


def _item(path: Path) -> CleanupFileItem:
    stat_result = path.stat()
    return CleanupFileItem(
        str(path),
        stat_result.st_size,
        stat_result.st_mtime,
        mtime_ns=stat_result.st_mtime_ns,
        file_id=CleanupService.file_identity(stat_result),
    )


def _run_delete(worker: _DeleteWorker) -> tuple[dict[str, object], list[str]]:
    results: list[dict[str, object]] = []
    messages: list[str] = []
    worker.finished.connect(results.append)
    worker.worker_event.connect(
        lambda kind, payload: messages.append(str(payload.get("message", "")))
        if kind == "log" and isinstance(payload, dict)
        else None
    )
    worker.run()
    assert len(results) == 1
    return results[0], messages


def test_manual_scan_progress_preserves_totals_above_one_hundred_tb() -> None:
    """Scan progress must not cross a 32-bit Qt integer boundary."""
    one_hundred_tb = 100 * 1024**4
    candidate = CleanupCandidate(
        path="Z:/image/large.raw",
        root_path="Z:/image",
        size=one_hundred_tb + 123,
        mtime=1.0,
        mtime_ns=1_000_000_000,
        file_id="1:1",
    )
    worker = _ScanWorker(CleanupScanRequest(("Z:/image",), (".raw",)))
    events: list[tuple[str, dict[str, object]]] = []
    worker.worker_event.connect(
        lambda kind, payload: events.append((kind, dict(payload)))
    )

    with mock.patch(
        "src.services.cleanup_service.iter_cleanup_candidates",
        return_value=iter((candidate,)),
    ):
        worker.run()

    progress = next(payload for kind, payload in events if kind == "scan_progress")
    assert progress["total_size_bytes"] == one_hundred_tb + 123
    assert progress["total_size_bytes"] > 2**31 - 1


def test_manual_scan_cancellation_stops_before_collecting_more_candidates() -> None:
    candidate = CleanupCandidate(
        path="Z:/image/first.raw",
        root_path="Z:/image",
        size=1,
        mtime=1.0,
        mtime_ns=1_000_000_000,
        file_id="1:1",
    )
    worker = _ScanWorker(CleanupScanRequest(("Z:/image",), (".raw",)))
    completed: list[dict[str, object]] = []
    discovered: list[CleanupFileItem] = []

    def cancel_after_first_progress(kind: str, _payload: object) -> None:
        if kind == "scan_progress":
            worker.cancel()

    worker.worker_event.connect(cancel_after_first_progress)
    worker.worker_event.connect(
        lambda kind, payload: discovered.extend(payload["files"])
        if kind == "scan_items" and isinstance(payload, dict)
        else None
    )
    worker.finished.connect(completed.append)
    with mock.patch(
        "src.services.cleanup_service.iter_cleanup_candidates",
        return_value=iter((candidate, candidate, candidate)),
    ):
        worker.run()

    assert len(completed) == 1
    assert completed[0]["cancelled"] is True
    assert completed[0]["file_count"] == 1
    assert len(discovered) == 1


def test_manual_scan_streams_candidates_before_emitting_final_summary() -> None:
    """手动扫描不能把完整结果积压到结束时才交给 GUI。"""
    candidates = tuple(
        CleanupCandidate(
            path=f"Z:/image/{index:04d}.raw",
            root_path="Z:/image",
            size=index + 1,
            mtime=1.0,
            mtime_ns=1_000_000_000 + index,
            file_id=f"1:{index}",
        )
        for index in range(300)
    )
    worker = _ScanWorker(CleanupScanRequest(("Z:/image",), (".raw",)))
    events: list[tuple[str, dict[str, object]]] = []
    completed: list[dict[str, object]] = []
    worker.worker_event.connect(
        lambda kind, payload: events.append((kind, dict(payload)))
    )
    worker.finished.connect(completed.append)

    with mock.patch(
        "src.services.cleanup_service.iter_cleanup_candidates",
        return_value=iter(candidates),
    ), mock.patch("src.services.cleanup_service.time.monotonic", return_value=0.0):
        worker.run()

    batches = [payload["files"] for kind, payload in events if kind == "scan_items"]
    assert batches
    assert sum(len(batch) for batch in batches) == len(candidates)
    assert all(len(batch) <= 128 for batch in batches)
    assert completed == [
        {
            "file_count": len(candidates),
            "total_size_bytes": sum(candidate.size for candidate in candidates),
            "cancelled": False,
        }
    ]


def test_manual_scan_coalesces_progress_events_for_large_file_counts() -> None:
    candidate = CleanupCandidate(
        path="Z:/image/frame.raw",
        root_path="Z:/image",
        size=1,
        mtime=1.0,
        mtime_ns=1_000_000_000,
        file_id="1:1",
    )
    worker = _ScanWorker(CleanupScanRequest(("Z:/image",), (".raw",)))
    progress_events: list[dict[str, object]] = []
    worker.worker_event.connect(
        lambda kind, payload: progress_events.append(dict(payload))
        if kind == "scan_progress"
        else None
    )

    with mock.patch(
        "src.services.cleanup_service.iter_cleanup_candidates",
        return_value=iter((candidate,) * 10_000),
    ), mock.patch("src.services.cleanup_service.time.monotonic", return_value=0.0):
        worker.run()

    assert len(progress_events) == 1
    assert progress_events[0]["file_count"] == 1


def test_trash_failure_is_fail_closed_and_preserves_file(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    policy = SafeDeletionPolicy(
        is_trash_available=lambda: True,
        move_to_trash=lambda _path: (_ for _ in ()).throw(OSError("trash offline")),
    )

    result = policy.delete(
        SafeDeletionRequest(
            path=str(source),
            allowed_roots=(str(tmp_path),),
            identity_verifier=lambda: (True, ""),
            mode="trash",
        )
    )

    assert not result.success
    assert result.status == "delete_failed"
    assert source.read_bytes() == b"keep"


def test_automatic_permanent_delete_is_always_rejected(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    removed: list[str] = []
    policy = SafeDeletionPolicy(remove_file=removed.append)

    result = policy.delete(
        SafeDeletionRequest(
            path=str(source),
            allowed_roots=(str(tmp_path),),
            identity_verifier=lambda: (True, ""),
            mode="permanent",
            permanent_authorized=True,
            automatic=True,
        )
    )

    assert result.status == "automatic_permanent_forbidden"
    assert not removed
    assert source.exists()


def test_manual_permanent_delete_requires_authorization_and_audit(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    service = CleanupService(audit_writer=AuditRecorder())
    request = CleanupDeleteRequest(
        (_item(source),),
        use_trash=False,
        allowed_roots=(str(tmp_path),),
    )

    result = service.start_delete(request, lambda *_args: None)

    assert not result.success
    assert "二次授权" in result.message
    assert source.exists()


def test_manual_permanent_delete_stops_when_intent_audit_fails(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    audit = AuditRecorder(accepted=False)
    worker = _DeleteWorker(
        CleanupDeleteRequest(
            (_item(source),),
            use_trash=False,
            permanent_authorized=True,
            allowed_roots=(str(tmp_path),),
        ),
        audit,
    )

    result, messages = _run_delete(worker)

    assert result["deleted_count"] == 0
    assert result["failed_count"] == 1
    assert source.exists()
    assert audit.records[0][0] == "MANUAL_PERMANENT_DELETE_INTENT"
    assert any("审计日志写入失败" in message for message in messages)


def test_manual_permanent_delete_records_intent_and_result(tmp_path: Path) -> None:
    source = tmp_path / "remove.jpg"
    source.write_bytes(b"remove")
    audit = AuditRecorder()
    worker = _DeleteWorker(
        CleanupDeleteRequest(
            (_item(source),),
            use_trash=False,
            permanent_authorized=True,
            allowed_roots=(str(tmp_path),),
        ),
        audit,
    )

    result, _messages = _run_delete(worker)

    assert result["deleted_count"] == 1
    assert not source.exists()
    assert [record[0] for record in audit.records] == [
        "MANUAL_PERMANENT_DELETE_INTENT",
        "MANUAL_PERMANENT_DELETE_OK",
    ]


def test_manual_delete_skips_a_file_replaced_after_scan(tmp_path: Path) -> None:
    source = tmp_path / "changed.jpg"
    source.write_bytes(b"old")
    scanned = _item(source)
    source.write_bytes(b"replacement-content")
    worker = _DeleteWorker(
        CleanupDeleteRequest(
            (scanned,),
            use_trash=True,
            allowed_roots=(str(tmp_path),),
        )
    )

    result, messages = _run_delete(worker)

    assert result["skipped_changed_count"] == 1
    assert source.read_bytes() == b"replacement-content"
    assert any("扫描后发生变化" in message for message in messages)


def test_manual_and_auto_scans_share_candidate_identity(tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    ignored = tmp_path / "ignored.txt"
    first.write_bytes(b"1")
    second.write_bytes(b"22")
    ignored.write_bytes(b"ignored")
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))

    streamed = tuple(iter_cleanup_candidates((str(tmp_path),), (".jpg",)))
    bounded, scanned_count = oldest_cleanup_candidates(
        (str(tmp_path),), (".jpg",), limit=100
    )

    assert scanned_count == 2
    assert [(item.path, item.size, item.mtime_ns, item.file_id) for item in streamed] == [
        (item.path, item.size, item.mtime_ns, item.file_id) for item in bounded
    ]


def test_oldest_candidate_selection_is_bounded_and_deterministic(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index in range(7):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(str(index).encode("ascii"))
        timestamp = (index + 1) * 1_000_000_000
        os.utime(path, ns=(timestamp, timestamp))
        paths.append(path)

    candidates, scanned_count = oldest_cleanup_candidates(
        (str(tmp_path),), (".jpg",), limit=3
    )

    assert scanned_count == 7
    assert [Path(item.path).name for item in candidates] == ["0.jpg", "1.jpg", "2.jpg"]


def test_one_hundred_thousand_candidate_scan_retains_only_bounded_batch() -> None:
    def generated_candidates(*_args, **_kwargs):
        for index in range(100_000):
            yield CleanupCandidate(
                path=f"C:/images/{index:06d}.jpg",
                root_path="C:/images",
                size=index + 1,
                mtime=float(index),
                mtime_ns=index + 1,
                file_id=f"1:{index + 1}",
            )

    with mock.patch(
        "src.services.cleanup_service.iter_cleanup_candidates",
        side_effect=generated_candidates,
    ):
        candidates, scanned_count = oldest_cleanup_candidates(
            ("C:/images",), (".jpg",), limit=100
        )

    assert scanned_count == 100_000
    assert len(candidates) == 100
    assert candidates[0].path.endswith("000000.jpg")
    assert candidates[-1].path.endswith("000099.jpg")


def test_auto_cleanup_stops_before_scan_when_disk_api_fails(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    audit = AuditRecorder()
    service = CleanupService(audit_writer=audit)
    request = AutoCleanupRequest(True, (str(tmp_path),), 80, 70, (".jpg",), True)

    with mock.patch("src.services.cleanup_service.trash_supported", return_value=True), mock.patch(
        "src.services.cleanup_service.shutil.disk_usage", side_effect=OSError("disk offline")
    ):
        result = service.run_auto_cleanup(request, NeverCancelled(), lambda _message: None)

    assert result.status == "任务异常"
    assert "disk offline" in result.error
    assert source.exists()
    assert [record[0] for record in audit.records] == ["START", "END"]


def test_auto_cleanup_uses_safe_policy_and_reaches_target(tmp_path: Path) -> None:
    source = tmp_path / "old.jpg"
    source.write_bytes(b"old")
    audit = AuditRecorder()
    service = CleanupService(audit_writer=audit)
    usage = namedtuple("usage", "total used free")
    disk_samples = [usage(100, 90, 10), usage(100, 70, 30)]
    request = AutoCleanupRequest(True, (str(tmp_path),), 80, 70, (".jpg",), True)

    def move_to_trash(path: str) -> None:
        Path(path).unlink()

    with mock.patch("src.services.cleanup_service.trash_supported", return_value=True), mock.patch(
        "src.services.cleanup_service.send_to_trash", side_effect=move_to_trash
    ), mock.patch(
        "src.services.cleanup_service.shutil.disk_usage", side_effect=disk_samples
    ):
        result = service.run_auto_cleanup(request, NeverCancelled(), lambda _message: None)

    assert result.status == "达到目标"
    assert result.deleted_count == 1
    assert not source.exists()
    assert [record[0] for record in audit.records] == ["START", "DELETE_OK", "END"]


def test_auto_cleanup_preserves_changed_candidate(tmp_path: Path) -> None:
    source = tmp_path / "changed.jpg"
    source.write_bytes(b"old")
    scanned_stat = source.stat()
    candidate = CleanupCandidate(
        str(source),
        str(tmp_path),
        scanned_stat.st_size,
        scanned_stat.st_mtime,
        scanned_stat.st_mtime_ns,
        CleanupService.file_identity(scanned_stat),
        CleanupService.file_created_at(scanned_stat),
    )
    source.write_bytes(b"replacement-content")
    usage = namedtuple("usage", "total used free")
    audit = AuditRecorder()
    service = CleanupService(audit_writer=audit)
    request = AutoCleanupRequest(True, (str(tmp_path),), 80, 70, (".jpg",), True)

    with mock.patch("src.services.cleanup_service.trash_supported", return_value=True), mock.patch(
        "src.services.cleanup_service.shutil.disk_usage", return_value=usage(100, 90, 10)
    ), mock.patch(
        "src.services.cleanup_service.oldest_cleanup_candidates",
        return_value=((candidate,), 1),
    ):
        result = service.run_auto_cleanup(request, NeverCancelled(), lambda _message: None)

    assert result.status == "候选已刷新"
    assert result.skipped_changed_count == 1
    assert source.read_bytes() == b"replacement-content"


def test_cleanup_run_does_not_create_database_files(tmp_path: Path) -> None:
    source = tmp_path / "keep.jpg"
    source.write_bytes(b"keep")
    tuple(iter_cleanup_candidates((str(tmp_path),), (".jpg",)))

    forbidden = {".db", ".sqlite", ".sqlite3"}
    assert not [path for path in tmp_path.rglob("*") if path.suffix.lower() in forbidden]
