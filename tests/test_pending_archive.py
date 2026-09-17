"""Regression tests for durable post-upload archive handling."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.repositories import PendingArchiveRepository
from src.core.file_identity import FileIdentity
from src.core.file_task_registry import FileTaskState
from src.workers.upload_worker import UploadWorker


def _worker(tmp_path: Path, *, enable_backup: bool = True) -> UploadWorker:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    source.mkdir(exist_ok=True)
    target.mkdir(exist_ok=True)
    backup.mkdir(exist_ok=True)
    return UploadWorker(
        str(source), str(target), str(backup), 30, "periodic", 10, 3,
        [".jpg"], tmp_path, enable_backup=enable_backup,
    )


def _queue_archive(worker: UploadWorker, source: Path, destination: Path) -> bool:
    return worker._queue_archive(
        str(source),
        str(destination),
        FileIdentity.capture(source),
    )


def test_quick_stop_persists_archive_and_restart_avoids_duplicate_upload(
    tmp_path: Path,
) -> None:
    first = _worker(tmp_path)
    source = Path(first.source) / "camera-01.jpg"
    destination = Path(first.backup) / source.name
    source.write_bytes(b"image-data")
    first._running = True

    assert _queue_archive(first, source, destination)
    first.stop(wait=False)
    assert source.exists()
    assert len(first._archive_repository.load()) == 1

    restarted = _worker(tmp_path)
    restarted._running = True
    restarted._restore_pending_archives()

    assert str(source) not in list(restarted._get_image_files())
    item = restarted.archive_queue.get_nowait()
    restarted._process_archive_item(item)

    assert destination.read_bytes() == b"image-data"
    assert not source.exists()
    assert restarted._archive_repository.load() == ()


def test_archive_failure_keeps_source_and_journal_for_next_start(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    source = Path(worker.source) / "camera-02.jpg"
    destination = Path(worker.backup) / source.name
    source.write_bytes(b"image-data")

    assert _queue_archive(worker, source, destination)
    item = worker.archive_queue.get_nowait()
    with mock.patch(
        "src.workers.upload_worker.shutil.move", side_effect=OSError("offline")
    ):
        try:
            worker._process_archive_item(item)
        except OSError:
            pass
        else:
            raise AssertionError("archive failure must propagate to the worker loop")

    assert source.exists()
    assert len(worker._archive_repository.load()) == 1


def _assert_stale_without_source_mutation(
    worker: UploadWorker,
    source: Path,
    destination: Path,
    reason: str,
) -> None:
    records = worker._archive_repository.load()
    assert source.exists()
    assert not destination.exists()
    assert len(records) == 1
    assert records[0]["state"] == "stale"
    assert records[0]["stale_reason"] == reason
    assert worker._archive_repository.normalize(str(source)) not in worker._pending_archive_sources


def test_archive_does_not_move_new_generation_at_same_path(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    source = Path(worker.source) / "camera-04.jpg"
    destination = Path(worker.backup) / source.name
    source.write_bytes(b"generation-one")
    assert _queue_archive(worker, source, destination)
    item = worker.archive_queue.get_nowait()

    source.write_bytes(b"generation-two")
    worker._process_archive_item(item)

    assert source.read_bytes() == b"generation-two"
    _assert_stale_without_source_mutation(
        worker, source, destination, "source_identity_changed"
    )


def test_archive_does_not_delete_recreated_source_file(tmp_path: Path) -> None:
    worker = _worker(tmp_path, enable_backup=False)
    source = Path(worker.source) / "camera-05.jpg"
    source.write_bytes(b"original")
    assert worker._queue_archive(str(source), "", FileIdentity.capture(source))
    item = worker.archive_queue.get_nowait()

    source.unlink()
    source.write_bytes(b"replacement")
    worker._process_archive_item(item)

    assert source.read_bytes() == b"replacement"
    _assert_stale_without_source_mutation(
        worker, source, Path(worker.backup) / source.name, "source_identity_changed"
    )


def test_archive_detects_same_size_timestamp_collision_by_digest(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    source = Path(worker.source) / "camera-06.jpg"
    destination = Path(worker.backup) / source.name
    source.write_bytes(b"before")
    identity = FileIdentity.capture(source)
    assert worker._queue_archive(str(source), str(destination), identity)
    item = worker.archive_queue.get_nowait()

    source.write_bytes(b"after!")
    os.utime(source, ns=(identity.mtime_ns, identity.mtime_ns))
    worker._process_archive_item(item)

    assert source.read_bytes() == b"after!"
    _assert_stale_without_source_mutation(
        worker, source, destination, "source_identity_changed"
    )


def test_legacy_archive_record_is_marked_stale_and_does_not_block_rescan(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    source = Path(worker.source) / "camera-07.jpg"
    destination = Path(worker.backup) / source.name
    source.write_bytes(b"new-generation")
    repository = worker._archive_repository
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(
        json.dumps(
            {
                repository.normalize(str(source)): {
                    "source": str(source),
                    "destination": str(destination),
                    "action": "move",
                }
            }
        ),
        encoding="utf-8",
    )

    worker._running = True
    worker._restore_pending_archives()

    records = repository.load()
    assert len(records) == 1
    assert records[0]["state"] == "stale"
    assert records[0]["stale_reason"].startswith("unsafe_record:")
    assert str(source) in list(worker._get_image_files())


def test_pending_archive_repository_replace_failure_preserves_old_journal(
    tmp_path: Path,
) -> None:
    repository = PendingArchiveRepository(tmp_path)
    old_source = str(tmp_path / "old.jpg")
    Path(old_source).write_bytes(b"old")
    assert repository.add(
        old_source,
        str(tmp_path / "backup" / "old.jpg"),
        "move",
        FileIdentity.capture(old_source),
    )
    old_payload = repository.path.read_bytes()

    repository._store._replace_func = mock.Mock(side_effect=OSError("disk full"))
    new_source = tmp_path / "new.jpg"
    new_source.write_bytes(b"new")
    assert not repository.add(
        str(new_source),
        str(tmp_path / "backup" / "new.jpg"),
        "move",
        FileIdentity.capture(new_source),
    )

    assert repository.path.read_bytes() == old_payload
    assert [record["source"] for record in repository.load()] == [old_source]


def test_corrupt_pending_archive_journal_is_not_silently_overwritten(
    tmp_path: Path,
) -> None:
    repository = PendingArchiveRepository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text("{broken", encoding="utf-8")
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")

    assert repository.load() == ()
    assert not repository.add(
        str(source),
        "backup.jpg",
        "move",
        FileIdentity.capture(source),
    )
    assert repository.path.read_text(encoding="utf-8") == "{broken"
    assert repository.last_error.startswith("JSONDecodeError:")


def test_backup_disabled_persists_trash_action_until_source_is_recycled(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path, enable_backup=False)
    source = Path(worker.source) / "camera-03.jpg"
    source.write_bytes(b"image-data")

    assert worker._queue_archive(str(source), "", FileIdentity.capture(source))
    item = worker.archive_queue.get_nowait()
    assert item["action"] == "trash"
    with mock.patch(
        "src.core.safe_deletion.send_to_trash",
        side_effect=lambda path: Path(path).unlink(),
    ):
        worker._process_archive_item(item)

    assert not source.exists()
    assert worker._archive_repository.load() == ()


def test_archive_journal_failure_blocks_rescan_and_recovers_in_session(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    source = Path(worker.source) / "camera-08.jpg"
    destination = Path(worker.backup) / source.name
    source.write_bytes(b"image-data")
    identity = FileIdentity.capture(source)
    real_add = worker._archive_repository.add
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            worker._archive_repository.last_error = "injected disk full"
            return False
        return real_add(*args, **kwargs)

    with mock.patch.object(worker._archive_repository, "add", side_effect=fail_once):
        assert not worker._queue_archive(
            str(source), str(destination), identity, {"smb": True}
        )
        task = worker._task_registry.get(identity)
        assert task is not None
        assert task.state is FileTaskState.ARCHIVE_PERSIST_FAILED
        assert source.exists()
        assert str(source) not in list(worker._get_image_files())

        retry = worker._archive_persist_retries[
            worker._archive_repository.normalize(str(source))
        ]
        retry["next_retry_at"] = 0.0
        worker._process_archive_persist_retries()

    task = worker._task_registry.get(identity)
    assert task is not None
    assert task.state is FileTaskState.ARCHIVE_PENDING
    assert worker._archive_persist_retries == {}
    assert worker.archive_queue.qsize() == 1
    assert source.exists()
