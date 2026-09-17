"""Focused tests for the no-database stability primitives."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src.core.atomic_json_store import AtomicJsonStore
from src.core.file_identity import FileIdentity
from src.core.file_task_registry import FileTaskRegistry, FileTaskState
from src.core.pause_state import PauseState
from src.models.path_probe import PathProbe
from src.services.path_probe_service import PathProbeService


def test_file_task_registry_allows_only_one_concurrent_claim_per_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "camera.jpg"
    source.write_bytes(b"generation")
    identity = FileIdentity.capture(source)

    for _iteration in range(100):
        registry = FileTaskRegistry()
        barrier = threading.Barrier(8)

        def claim() -> bool:
            barrier.wait()
            return registry.claim_for_upload(identity)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _index: claim(), range(8)))
        assert results.count(True) == 1
        assert registry.get(identity).state is FileTaskState.UPLOADING  # type: ignore[union-attr]


def test_retry_wait_cannot_be_reclaimed_by_scanner_or_before_due_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "camera.jpg"
    source.write_bytes(b"generation")
    identity = FileIdentity.capture(source)
    registry = FileTaskRegistry()
    task = registry.schedule_retry(
        identity,
        retry_count=2,
        next_retry_at=50.0,
        protocol_results={"smb": True, "ftp": False},
        reason="ftp_failed",
    )

    assert registry.should_skip_scan(identity)
    assert not registry.claim_for_upload(identity)
    assert not registry.claim_due_retry(identity, now=49.9)
    assert registry.claim_due_retry(identity, now=50.0)
    assert task.protocol_results == {"smb": True, "ftp": False}
    assert task.last_reason == "retry_claimed"
    assert task.updated_at > 0


def test_same_path_new_generation_has_independent_task_key(tmp_path: Path) -> None:
    source = tmp_path / "camera.jpg"
    source.write_bytes(b"one")
    first = FileIdentity.capture(source)
    source.write_bytes(b"two-new")
    second = FileIdentity.capture(source)
    registry = FileTaskRegistry()

    assert registry.claim_for_upload(first)
    assert registry.claim_for_upload(second)
    assert len(registry.for_path(str(source))) == 2


def test_pause_state_does_not_clear_manual_pause_when_network_recovers() -> None:
    state = PauseState()
    state.set("manual", True)
    state.set("network", True)

    state.set("network", False)

    assert state.is_paused
    assert state.reasons == frozenset({"manual"})
    state.set("manual", False)
    assert not state.is_paused


def test_atomic_json_store_recovers_old_complete_record_after_replace_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    store = AtomicJsonStore(path)
    assert store.write({"items": [{"id": 1}]})
    original = path.read_bytes()
    store._replace_func = mock.Mock(side_effect=OSError("power loss"))

    assert not store.write({"items": [{"id": 2}]})

    assert path.read_bytes() == original
    assert store.read() == {"items": [{"id": 1}]}
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_atomic_json_store_recovers_backup_and_quarantines_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    store = AtomicJsonStore(path)
    assert store.write({"items": [{"id": 1}]})
    assert store.write({"items": [{"id": 2}]})
    path.write_text("{broken", encoding="utf-8")

    recovered = store.read()

    assert recovered == {"items": [{"id": 1}]}
    assert "recovered_from_backup" in store.last_error
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_atomic_json_store_rejects_bad_checksum_and_record_overflow(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "checksum_sha256": "0" * 64, "payload": {"x": 1}}
        ),
        encoding="utf-8",
    )
    store = AtomicJsonStore(path, max_records=1)

    assert store.read(default={"safe": True}) == {"safe": True}
    assert "checksum" in store.last_error
    assert not store.write({"items": [{"id": 1}, {"id": 2}]})
    assert "record limit" in store.last_error


def test_path_probe_generation_cancels_stale_completion() -> None:
    service = PathProbeService(max_workers=1, timeout=0.5)
    entered = threading.Event()
    release = threading.Event()
    delivered = threading.Event()
    results = []

    def deliver(result) -> None:
        results.append(result)
        delivered.set()

    def slow_probe(_probe: PathProbe, _timeout: float) -> tuple[bool, bool]:
        entered.set()
        release.wait(1)
        return True, False

    with mock.patch.object(service, "_probe_one", side_effect=slow_probe):
        generation = service.probe(
            (PathProbe("source", r"\\server\share"),),
            deliver,
        )
        assert entered.wait(1)
        assert service.cancel() > generation
        release.set()
        assert delivered.wait(1)
    service.shutdown()

    assert results[0].generation == generation
    assert results[0].cancelled


def test_remote_path_probe_reports_subprocess_timeout() -> None:
    service = PathProbeService(timeout=0.5)
    with mock.patch(
        "src.services.path_probe_service.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired("powershell", 0.5),
    ):
        ok, timed_out = service._probe_one(
            PathProbe("source", r"\\server\offline"), 0.5
        )
    service.shutdown()

    assert not ok
    assert timed_out
