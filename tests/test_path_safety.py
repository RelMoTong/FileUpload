"""P0-01 regression tests for local upload path separation."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import UploadTaskRequest
from src.services.path_safety import find_local_path_conflicts, normalize_local_path
from src.services.upload_service import UploadService


def _request(root: Path, **overrides: object) -> UploadTaskRequest:
    source = root / "source"
    target = root / "target"
    backup = root / "backup"
    for path in (source, target, backup):
        path.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {
        "source": str(source),
        "target": str(target),
        "backup": str(backup),
        "interval": 30,
        "mode": "periodic",
        "disk_threshold_percent": 10,
        "retry_count": 3,
        "filters": (".jpg",),
        "app_dir": root,
        "enable_backup": True,
        "upload_protocol": "smb",
    }
    values.update(overrides)
    return UploadTaskRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("source_rel", "target_rel", "backup_rel", "expected_labels"),
    [
        ("source", "source/target", "backup", ("源文件夹", "目标文件夹")),
        ("source/child", "source", "backup", ("源文件夹", "目标文件夹")),
        ("source", "target", "source/backup", ("源文件夹", "备份文件夹")),
        ("source", "target/child", "target", ("目标文件夹", "备份文件夹")),
    ],
)
def test_service_rejects_equal_or_nested_local_paths(
    tmp_path: Path,
    source_rel: str,
    target_rel: str,
    backup_rel: str,
    expected_labels: tuple[str, str],
) -> None:
    paths = [tmp_path / value for value in (source_rel, target_rel, backup_rel)]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)

    result = UploadService.validate_request(
        _request(
            tmp_path,
            source=str(paths[0]),
            target=str(paths[1]),
            backup=str(paths[2]),
        )
    )

    assert not result.is_valid
    message = "\n".join(result.errors)
    assert expected_labels[0] in message
    assert expected_labels[1] in message
    assert "嵌套" in message or "相同" in message


def test_normalizer_collapses_case_and_trailing_separators() -> None:
    assert normalize_local_path(r"C:\Camera\Images\\") == normalize_local_path(
        r"c:/camera/images"
    )


def test_unc_paths_are_compared_without_touching_the_network() -> None:
    conflicts = find_local_path_conflicts(
        (
            ("源文件夹", r"\\Server\Share\Images"),
            ("目标文件夹", r"\\server\share\images\uploaded"),
        )
    )

    assert len(conflicts) == 1
    assert conflicts[0].relation == "ancestor"


def test_symlink_alias_is_treated_as_the_same_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    try:
        os.symlink(source, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    conflicts = find_local_path_conflicts(
        (("源文件夹", str(source)), ("目标文件夹", str(alias)))
    )

    assert len(conflicts) == 1
    assert conflicts[0].relation == "same"


def test_ftp_remote_path_is_not_validated_as_a_local_target(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        target="/upload",
        backup="",
        enable_backup=False,
        upload_protocol="ftp_client",
        ftp_client_config={"remote_path": "/upload"},
    )

    assert UploadService.validate_request(request).is_valid


def test_start_rejects_conflict_before_worker_is_created(tmp_path: Path) -> None:
    created: list[object] = []

    def worker_factory(*args: object, **kwargs: object) -> object:
        created.append((args, kwargs))
        raise AssertionError("worker must not be created")

    source = tmp_path / "source"
    nested_target = source / "target"
    nested_target.mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    service = UploadService(worker_factory=worker_factory)

    result = service.start(
        _request(
            tmp_path,
            source=str(source),
            target=str(nested_target),
            backup=str(backup),
        ),
        lambda *_args: None,
    )

    assert not result.success
    assert not created
