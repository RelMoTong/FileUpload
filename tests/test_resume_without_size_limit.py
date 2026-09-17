from pathlib import Path
import os
import sys
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ConfigManager
from src.core.resume_manager import ResumeManager, ResumableFileUploader
from src.models import ApplicationSettings
from src.workers.upload_worker import UploadWorker


def test_resume_min_size_is_removed_from_configuration() -> None:
    defaults = ConfigManager.get_default_config()
    assert "resume_min_size_mb" not in defaults

    legacy_config = dict(defaults, resume_min_size_mb=10, future_option="kept")
    restored = ApplicationSettings.from_config(legacy_config).to_config()

    assert "resume_min_size_mb" not in restored
    assert restored["future_option"] == "kept"
    assert not hasattr(ApplicationSettings().upload, "resume_min_size_mb")


@pytest.mark.parametrize("content", [b"", b"small file"])
def test_resumable_uploader_accepts_every_file_size(
    tmp_path: Path, content: bytes
) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(content)

    manager = ResumeManager(tmp_path)
    uploader = ResumableFileUploader(manager, buffer_size=2)

    success, error = uploader.upload_with_resume(str(source), str(target))

    assert success, error
    assert target.read_bytes() == content
    assert not list((tmp_path / "resume_data").glob("*.resume"))


def test_completed_upload_atomically_replaces_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"new-content")
    target.write_bytes(b"old-content")
    manager = ResumeManager(tmp_path)
    uploader = ResumableFileUploader(manager, buffer_size=3)
    original_replace = os.replace

    with mock.patch(
        "src.core.resume_manager.os.replace", wraps=original_replace
    ) as replace:
        success, error = uploader.upload_with_resume(str(source), str(target))

    assert success, error
    replace.assert_called_once_with(str(tmp_path / ".target.bin.part"), str(target))
    assert target.read_bytes() == b"new-content"
    assert not (tmp_path / ".target.bin.part").exists()
    assert not list((tmp_path / "resume_data").glob("*.resume"))


def test_replace_failure_preserves_old_target_temp_and_resume_record(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"new-content")
    target.write_bytes(b"old-content")
    manager = ResumeManager(tmp_path)
    uploader = ResumableFileUploader(manager, buffer_size=3)

    with mock.patch(
        "src.core.resume_manager.os.replace",
        side_effect=PermissionError("replace denied"),
    ):
        success, error = uploader.upload_with_resume(str(source), str(target))

    assert not success
    assert "replace denied" in error
    assert target.read_bytes() == b"old-content"
    assert (tmp_path / ".target.bin.part").read_bytes() == b"new-content"
    assert len(list((tmp_path / "resume_data").glob("*.resume"))) == 1


def test_length_mismatch_never_replaces_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"abc")
    target.write_bytes(b"old")
    manager = ResumeManager(tmp_path)
    record = manager.create_resume_record(str(source), str(target))
    Path(record["temp_file"]).write_bytes(b"abcd")
    uploader = ResumableFileUploader(manager, buffer_size=2)

    success, error = uploader.upload_with_resume(str(source), str(target))

    assert not success
    assert "临时文件长度不完整" in error
    assert target.read_bytes() == b"old"
    assert Path(record["temp_file"]).read_bytes() == b"abcd"
    assert len(list((tmp_path / "resume_data").glob("*.resume"))) == 1


def test_interrupted_upload_can_resume_and_commit_once(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"abcdef")
    manager = ResumeManager(tmp_path)
    holder: dict[str, ResumableFileUploader] = {}

    def stop_after_first_chunk(uploaded: int, _total: int, _name: str) -> None:
        if uploaded >= 2:
            holder["uploader"].stop()

    first = ResumableFileUploader(
        manager, buffer_size=2, progress_callback=stop_after_first_chunk
    )
    holder["uploader"] = first
    first_success, first_error = first.upload_with_resume(str(source), str(target))

    assert not first_success
    assert "中断" in first_error
    assert not target.exists()
    assert (tmp_path / ".target.bin.part").read_bytes() == b"ab"

    second = ResumableFileUploader(manager, buffer_size=2)
    second_success, second_error = second.upload_with_resume(str(source), str(target))

    assert second_success, second_error
    assert target.read_bytes() == b"abcdef"
    assert not (tmp_path / ".target.bin.part").exists()
    assert not list((tmp_path / "resume_data").glob("*.resume"))


def test_worker_routes_small_files_through_resumable_upload() -> None:
    worker = SimpleNamespace(
        _upload_with_resume=Mock(return_value=True),
        _log_event=Mock(),
    )

    assert UploadWorker._upload_via_smb(worker, "small.bin", "target.bin")
    worker._upload_with_resume.assert_called_once_with("small.bin", "target.bin")
    worker._log_event.assert_not_called()


def test_worker_does_not_repeat_success_cleanup() -> None:
    manager = Mock()
    manager.get_resume_info.return_value = None
    uploader = Mock()
    uploader.upload_with_resume.return_value = (True, "")
    worker = SimpleNamespace(
        resume_manager=manager,
        file_progress=Mock(),
        log=Mock(),
        limit_upload_rate=False,
        max_upload_rate_bytes=0,
        resumable_uploader=None,
    )

    with mock.patch(
        "src.workers.upload_worker.ResumableFileUploader", return_value=uploader
    ):
        assert UploadWorker._upload_with_resume(worker, "source.bin", "target.bin")

    manager.complete_upload.assert_not_called()
