"""Regression and scale tests for incremental duplicate detection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.repositories import DedupIndexRepository
from src.workers.upload_worker import UploadWorker


def _worker(tmp_path: Path) -> UploadWorker:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    source.mkdir(exist_ok=True)
    target.mkdir(exist_ok=True)
    backup.mkdir(exist_ok=True)
    worker = UploadWorker(
        str(source), str(target), str(backup), 30, "periodic", 10, 3,
        [".jpg"], tmp_path, enable_deduplication=True,
    )
    worker._running = True
    return worker


def test_duplicate_lookup_hashes_only_same_size_candidates(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    target = Path(worker.target)
    for index in range(2000):
        (target / f"other-{index}.bin").write_bytes(b"x" * (100 + index))
    duplicate = target / "same-content.bin"
    duplicate.write_bytes(b"camera-image-data")
    digest = hashlib.md5(b"camera-image-data").hexdigest()
    original = worker._calculate_file_hash
    hashed: list[str] = []

    def tracked(path: str, buffer_size: int = 8192) -> str:
        hashed.append(path)
        return original(path, buffer_size)

    with mock.patch.object(worker, "_calculate_file_hash", side_effect=tracked):
        found = worker._find_duplicate_by_hash(
            digest, worker.target, len(b"camera-image-data")
        )

    assert os.path.normcase(found) == os.path.normcase(str(duplicate))
    assert [os.path.normcase(path) for path in hashed] == [
        os.path.normcase(str(duplicate))
    ]


def test_persistent_index_reuses_hash_after_worker_restart(tmp_path: Path) -> None:
    first = _worker(tmp_path)
    duplicate = Path(first.target) / "persisted.bin"
    duplicate.write_bytes(b"persistent-hash")
    digest = hashlib.md5(b"persistent-hash").hexdigest()
    assert os.path.normcase(first._find_duplicate_by_hash(
        digest, first.target, duplicate.stat().st_size
    )) == os.path.normcase(str(duplicate))

    restarted = _worker(tmp_path)
    with mock.patch.object(
        restarted, "_calculate_file_hash", side_effect=AssertionError("rehash")
    ):
        found = restarted._find_duplicate_by_hash(
            digest, restarted.target, duplicate.stat().st_size
        )

    assert os.path.normcase(found) == os.path.normcase(str(duplicate))


def test_changed_candidate_invalidates_cached_hash(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    candidate = Path(worker.target) / "changed.bin"
    candidate.write_bytes(b"first-content")
    old_digest = hashlib.md5(b"first-content").hexdigest()
    assert os.path.normcase(worker._find_duplicate_by_hash(
        old_digest, worker.target, candidate.stat().st_size
    )) == os.path.normcase(str(candidate))

    old_mtime = candidate.stat().st_mtime_ns
    candidate.write_bytes(b"other-content")
    os.utime(candidate, ns=(old_mtime + 10_000_000, old_mtime + 10_000_000))

    assert worker._find_duplicate_by_hash(
        old_digest, worker.target, candidate.stat().st_size
    ) == ""


def test_hundred_thousand_target_entries_are_scanned_once_and_size_filtered(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    target = Path(worker.target)
    total = 100_000
    matching_path = str(target / f"synthetic-{total - 1}.bin")
    digest = "expected-digest"
    scans = 0
    hashed: list[str] = []

    def paths(_target_dir: str):
        nonlocal scans
        scans += 1
        for index in range(total):
            yield str(target / f"synthetic-{index}.bin")

    def stat(path: str):
        index = int(Path(path).stem.split("-")[-1])
        size = 424_242 if index == total - 1 else index + 1
        return SimpleNamespace(st_size=size, st_mtime_ns=index + 1000)

    def calculate(path: str, _buffer_size: int = 8192) -> str:
        hashed.append(path)
        return digest

    with mock.patch.object(worker, "_iter_target_files", side_effect=paths), \
         mock.patch.object(worker, "_stat_dedup_file", side_effect=stat), \
         mock.patch.object(worker, "_calculate_file_hash", side_effect=calculate):
        first = worker._find_duplicate_by_hash(digest, worker.target, 424_242)
        second = worker._find_duplicate_by_hash(digest, worker.target, 424_242)

    assert os.path.normcase(first) == os.path.normcase(matching_path)
    assert os.path.normcase(second) == os.path.normcase(matching_path)
    assert scans == 1
    assert [os.path.normcase(path) for path in hashed] == [
        os.path.normcase(matching_path)
    ]


def test_finished_rescan_removes_deleted_paths(tmp_path: Path) -> None:
    repository = DedupIndexRepository(tmp_path)
    target = str(tmp_path / "target")
    path = str(tmp_path / "target" / "old.bin")
    first_generation = repository.begin_scan()
    assert repository.upsert_metadata_batch(
        target, "md5", first_generation, [(path, 3, 1)]
    )
    assert repository.finish_scan(target, "md5", first_generation)
    assert repository.candidates(target, "md5", 3)

    second_generation = repository.begin_scan()
    assert repository.finish_scan(target, "md5", second_generation)

    assert repository.candidates(target, "md5", 3) == ()


def test_each_repository_operation_releases_sqlite_file_handle(tmp_path: Path) -> None:
    repository = DedupIndexRepository(tmp_path)
    target = str(tmp_path / "target")
    candidate = str(tmp_path / "target" / "candidate.bin")
    generation = repository.begin_scan()

    assert repository.upsert_metadata_batch(
        target, "md5", generation, [(candidate, 3, 1)]
    )
    assert repository.finish_scan(target, "md5", generation)
    assert repository.candidates(target, "md5", 3)

    moved_database = repository.path.with_suffix(".moved")
    os.replace(repository.path, moved_database)
    os.replace(moved_database, repository.path)

    assert repository.remove(target, "md5", candidate)
