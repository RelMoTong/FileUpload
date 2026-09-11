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


def test_quick_stop_persists_archive_and_restart_avoids_duplicate_upload(
    tmp_path: Path,
) -> None:
    first = _worker(tmp_path)
    source = Path(first.source) / "camera-01.jpg"
    destination = Path(first.backup) / source.name
    source.write_bytes(b"image-data")
    first._running = True

    assert first._queue_archive(str(source), str(destination))
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

    assert worker._queue_archive(str(source), str(destination))
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


def test_pending_archive_repository_replace_failure_preserves_old_journal(
    tmp_path: Path,
) -> None:
    repository = PendingArchiveRepository(tmp_path)
    old_source = str(tmp_path / "old.jpg")
    assert repository.add(old_source, str(tmp_path / "backup" / "old.jpg"), "move")
    old_payload = repository.path.read_bytes()

    with mock.patch(
        "src.repositories.pending_archive_repository.os.replace",
        side_effect=OSError("disk full"),
    ):
        assert not repository.add(
            str(tmp_path / "new.jpg"),
            str(tmp_path / "backup" / "new.jpg"),
            "move",
        )

    assert repository.path.read_bytes() == old_payload
    assert [record["source"] for record in repository.load()] == [old_source]


def test_corrupt_pending_archive_journal_is_not_silently_overwritten(
    tmp_path: Path,
) -> None:
    repository = PendingArchiveRepository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text("{broken", encoding="utf-8")

    assert repository.load() == ()
    assert not repository.add("source.jpg", "backup.jpg", "move")
    assert repository.path.read_text(encoding="utf-8") == "{broken"
    assert repository.last_error.startswith("JSONDecodeError:")


def test_backup_disabled_persists_delete_action_until_source_is_deleted(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path, enable_backup=False)
    source = Path(worker.source) / "camera-03.jpg"
    source.write_bytes(b"image-data")

    assert worker._queue_archive(str(source), "")
    item = worker.archive_queue.get_nowait()
    assert item["action"] == "delete"
    worker._process_archive_item(item)

    assert not source.exists()
    assert worker._archive_repository.load() == ()
