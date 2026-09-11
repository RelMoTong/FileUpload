"""SQLite cleanup index persistence, recovery, and lifecycle tests."""

from __future__ import annotations

from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import sys
import threading
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.controllers import CleanupController
from src.models import AutoCleanupRequest, AutoCleanupResult, CleanupIndexResult
from src.repositories import CleanupIndexRepository
from src.services import CleanupService
from src.services.cleanup_service import CLEANUP_INDEX_WRITE_BATCH


DiskUsage = namedtuple("DiskUsage", "total used free")


class MemoryAudit:
    last_error = ""

    def __init__(self) -> None:
        self.records = []

    def write(self, event, run_id, **fields):
        self.records.append({"event": event, "run_id": run_id, **fields})
        return True


def request_for(root: Path, formats=(".jpg",)) -> AutoCleanupRequest:
    return AutoCleanupRequest(
        enabled=True,
        folders=(str(root),),
        trigger_percent=90,
        target_percent=85,
        formats=formats,
        use_trash=False,
        trigger_source="test",
    )


def test_baseline_marker_is_zero_bytes_and_concurrent_upserts_are_unique(
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文监控目录"
    app_dir = tmp_path / "app"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"image")
    repository = CleanupIndexRepository(app_dir)
    service = CleanupService(MemoryAudit(), index_repository=repository)
    request = request_for(root)

    result = service.build_cleanup_index(request, threading.Event(), lambda _message: None)

    fingerprint = service.cleanup_scope_fingerprint(request)
    assert result.success
    assert repository.marker_path.is_file()
    assert repository.marker_path.stat().st_size == 0
    assert repository.count(fingerprint) == 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: service.index_generated_file(
                    str(image), request, "upload"
                ),
                range(32),
            )
        )

    assert all(results)
    assert repository.count(fingerprint) == 1
    assert repository.oldest(fingerprint, 1)[0].source == "upload"


def test_baseline_database_writes_stay_bounded_for_large_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for index in range(CLEANUP_INDEX_WRITE_BATCH * 2 + 17):
        (root / f"{index:04d}.jpg").write_bytes(b"")
    repository = CleanupIndexRepository(tmp_path / "app")
    service = CleanupService(MemoryAudit(), index_repository=repository)
    request = request_for(root)
    original_upsert = repository.upsert_many
    batch_sizes = []

    def track_batch(records, fingerprint):
        batch_sizes.append(len(records))
        return original_upsert(records, fingerprint)

    with mock.patch.object(repository, "upsert_many", side_effect=track_batch):
        result = service.build_cleanup_index(
            request, threading.Event(), lambda _message: None
        )

    assert result.success
    assert batch_sizes == [CLEANUP_INDEX_WRITE_BATCH, CLEANUP_INDEX_WRITE_BATCH, 17]
    assert max(batch_sizes) <= CLEANUP_INDEX_WRITE_BATCH


def test_incremental_index_ignores_outside_and_format_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    repository = CleanupIndexRepository(tmp_path / "app")
    service = CleanupService(MemoryAudit(), index_repository=repository)
    request = request_for(root)
    assert service.build_cleanup_index(
        request, threading.Event(), lambda _message: None
    ).success

    (root / "ignored.png").write_bytes(b"png")
    (outside / "ignored.jpg").write_bytes(b"jpg")
    assert service.index_generated_file(str(root / "ignored.png"), request)
    assert service.index_generated_file(str(outside / "ignored.jpg"), request)

    fingerprint = service.cleanup_scope_fingerprint(request)
    assert repository.count(fingerprint) == 0


def test_cancelled_baseline_never_creates_ready_marker(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "image.jpg").write_bytes(b"x")
    repository = CleanupIndexRepository(tmp_path / "app")
    service = CleanupService(MemoryAudit(), index_repository=repository)
    cancel = threading.Event()
    cancel.set()

    result = service.build_cleanup_index(request_for(root), cancel, lambda _message: None)

    assert result.status == "已取消"
    assert not repository.marker_path.exists()


def test_scope_change_revokes_old_marker_and_rebuilds(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "image.jpg").write_bytes(b"jpg")
    repository = CleanupIndexRepository(tmp_path / "app")
    service = CleanupService(MemoryAudit(), index_repository=repository)
    jpg_request = request_for(root, (".jpg",))
    png_request = request_for(root, (".png",))
    assert service.build_cleanup_index(
        jpg_request, threading.Event(), lambda _message: None
    ).success

    assert not service.is_index_ready(png_request)
    assert not repository.marker_path.exists()
    rebuilt = service.build_cleanup_index(
        png_request, threading.Event(), lambda _message: None
    )

    assert rebuilt.success
    assert repository.count(service.cleanup_scope_fingerprint(png_request)) == 0
    assert repository.marker_path.stat().st_size == 0


def test_corrupt_database_is_backed_up_and_marker_is_revoked(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    repository = CleanupIndexRepository(app_dir)
    repository.database_path.parent.mkdir(parents=True)
    repository.database_path.write_bytes(b"not a sqlite database")
    repository.marker_path.write_bytes(b"")
    fingerprint = repository.scope_fingerprint((str(tmp_path / "root"),), (".jpg",))

    assert not repository.is_ready(fingerprint)
    assert not repository.marker_path.exists()
    assert list(repository.database_path.parent.glob("cleanup_index.sqlite3.corrupt-*"))
    assert repository.ensure_schema()


def test_replaced_file_is_updated_and_skipped_for_current_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    image = root / "image.jpg"
    image.write_bytes(b"old")
    repository = CleanupIndexRepository(tmp_path / "app")
    audit = MemoryAudit()
    service = CleanupService(audit, index_repository=repository)
    request = request_for(root)
    assert service.build_cleanup_index(
        request, threading.Event(), lambda _message: None
    ).success
    fingerprint = service.cleanup_scope_fingerprint(request)
    indexed_record = repository.oldest(fingerprint, 1)[0]
    image.write_bytes(b"replacement-content")

    with mock.patch("shutil.disk_usage", return_value=DiskUsage(1000, 900, 100)), mock.patch(
        "src.services.cleanup_service.os.remove"
    ) as remove:
        result = service.run_auto_cleanup(
            request, threading.Event(), lambda _message: None
        )

    remove.assert_not_called()
    assert result.status == "索引已刷新"
    refreshed = repository.oldest(fingerprint, 1)[0]
    assert refreshed.size_bytes == len(b"replacement-content")
    assert refreshed.modified_at_ns != indexed_record.modified_at_ns
    assert refreshed.source == "revalidate"
    assert any(record["event"] == "DELETE_SKIP_CHANGED" for record in audit.records)


def test_schema_v1_is_invalidated_and_rebuilt_without_old_records(tmp_path: Path) -> None:
    repository = CleanupIndexRepository(tmp_path / "app")
    repository.database_path.parent.mkdir(parents=True)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "CREATE TABLE cleanup_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO cleanup_index_meta(key, value) VALUES(?, ?)",
            (("schema_version", "1"), ("scope_fingerprint", "old"), ("baseline_complete", "1")),
        )
        connection.execute(
            """
            CREATE TABLE cleanup_files (
                normalized_path TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                created_at REAL NOT NULL,
                size_bytes INTEGER NOT NULL,
                root_path TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO cleanup_files VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("old", "old", "old.jpg", 1.0, 1, "root", "scan", 1.0),
        )
    repository.marker_path.write_bytes(b"")

    assert not repository.is_ready("old")
    assert not repository.marker_path.exists()
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cleanup_files)")
        }
        assert {"modified_at_ns", "file_id"}.issubset(columns)
        assert connection.execute("SELECT COUNT(*) FROM cleanup_files").fetchone()[0] == 0


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        function(*args, **kwargs)
        return object()

    def shutdown(self, **_kwargs):
        return None


class ExhaustedService:
    trash_available = True
    is_scanning = False
    is_deleting = False
    has_running_workers = False
    index_last_error = ""

    def __init__(self) -> None:
        self.ready = True
        self.cleanup_calls = 0
        self.index_calls = 0

    def should_trigger(self, _request):
        return True, ""

    def is_index_ready(self, _request):
        return self.ready

    def cleanup_scope_fingerprint(self, _request):
        return "scope"

    def run_auto_cleanup(self, _request, _cancel, _log, _delete_mode_provider=None):
        self.cleanup_calls += 1
        return AutoCleanupResult("索引已耗尽")

    def build_cleanup_index(self, _request, _cancel, _log):
        self.index_calls += 1
        return CleanupIndexResult("建立完成", scope_fingerprint="scope")

    def record_blocked(self, *_args):
        return None


def test_empty_index_rebuilds_only_once_before_stopping(tmp_path: Path) -> None:
    service = ExhaustedService()
    controller = CleanupController(
        service, executor=ImmediateExecutor(), index_executor=ImmediateExecutor()
    )

    assert controller.maybe_trigger_auto_cleanup(request_for(tmp_path), "disk low")
    assert service.index_calls == 1
    assert service.cleanup_calls == 2


def test_controller_publishes_saved_delete_mode_to_running_cleanup(
    tmp_path: Path,
) -> None:
    class LiveModeService(ExhaustedService):
        def __init__(self) -> None:
            super().__init__()
            self.observed_modes = []
            self.switch_mode = lambda: None

        def run_auto_cleanup(
            self, _request, _cancel, _log, delete_mode_provider=None
        ):
            assert delete_mode_provider is not None
            self.observed_modes.append(delete_mode_provider())
            self.switch_mode()
            self.observed_modes.append(delete_mode_provider())
            return AutoCleanupResult("达到目标")

    service = LiveModeService()
    controller = CleanupController(
        service, executor=ImmediateExecutor(), index_executor=ImmediateExecutor()
    )
    trash_request = replace(request_for(tmp_path), use_trash=True)
    permanent_request = replace(trash_request, use_trash=False)
    controller.configure_index(trash_request)
    service.switch_mode = lambda: controller.configure_index(permanent_request)

    assert controller.submit_auto_cleanup(trash_request)
    assert service.observed_modes == [True, False]
