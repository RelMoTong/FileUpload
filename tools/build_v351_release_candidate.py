"""Build a reproducible v3.5.1 no-database release candidate.

The PyInstaller build must already exist. This tool assembles a clean field
package, rejects database artifacts, writes a per-file SHA-256 manifest, and
creates a ZIP plus checksum sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import __version__
from src.config import ConfigManager


APP_NAME = f"ImageUploadTool_v{__version__}"
RELEASE_NAME = f"{APP_NAME}_no_database_release"
FORBIDDEN_PATH_PATTERN = re.compile(
    r"(^|[\\/._-])(sqlite3?|_sqlite3|sqlalchemy)([\\/._-]|$)", re.IGNORECASE
)
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
RELEASE_DOCUMENTS = (
    Path("README.md"),
    Path("requirements.lock.txt"),
    Path("docs/CHANGELOG.md"),
    Path("docs/V351_OPERATIONS_MANUAL.md"),
    Path("docs/V351_RELEASE_VERIFICATION.md"),
    Path("docs/V351_FIELD_ACCEPTANCE.md"),
    Path("docs/V351_FIELD_EVIDENCE_SCHEMA.md"),
    Path("docs/V351_MANUAL_TEST_CHECKLIST.md"),
    Path("docs/V351_ROLLBACK_README.md"),
    Path("docs/V342_ROLLBACK_PACKAGING_PATCH.diff"),
    Path("docs/field_evidence.template.json"),
    Path("v3.5.1_无数据库稳定性改造实施方案.html"),
)
RELEASE_FIELD_TOOLS = (
    Path("tools/release_soak_test.py"),
    Path("tools/validate_v351_field_acceptance.py"),
)
RUNTIME_ONLY_PATHS = {
    "config.json",
    "startup-error.log",
}
RUNTIME_ONLY_PREFIXES = ("logs/", "data/", "resume_data/")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_release_target(target: Path, release_root: Path) -> None:
    resolved_root = release_root.resolve()
    resolved_target = target.resolve(strict=False)
    if resolved_target.parent != resolved_root:
        raise ValueError(f"拒绝操作发布目录之外的路径: {resolved_target}")
    if resolved_target.name not in {RELEASE_NAME, f"{RELEASE_NAME}.zip"}:
        raise ValueError(f"拒绝操作非预期发布目标: {resolved_target}")


def remove_existing_target(target: Path, release_root: Path) -> None:
    validate_release_target(target, release_root)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def scan_for_database_paths(root: Path) -> list[str]:
    matches: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or FORBIDDEN_PATH_PATTERN.search(
            relative
        ):
            matches.append(relative)
    return sorted(matches)


def scan_pyinstaller_archive(executable: Path) -> None:
    sibling_viewer = Path(sys.executable).with_name("pyi-archive_viewer.exe")
    viewer = str(sibling_viewer) if sibling_viewer.is_file() else shutil.which("pyi-archive_viewer")
    if not viewer:
        raise RuntimeError("未找到 pyi-archive_viewer，无法验证内嵌模块")
    completed = subprocess.run(
        [viewer, "-r", "-b", str(executable)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"PyInstaller 内嵌模块扫描失败: {completed.stderr.strip()}"
        )
    matches = [
        line.strip()
        for line in completed.stdout.splitlines()
        if FORBIDDEN_PATH_PATTERN.search(line.strip())
    ]
    if matches:
        raise RuntimeError(f"发现数据库内嵌模块: {'; '.join(matches)}")


def copy_release_documents(candidate_dir: Path) -> None:
    for relative in RELEASE_DOCUMENTS:
        source = PROJECT_ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少发布文档: {relative}")
        destination = candidate_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for relative in RELEASE_FIELD_TOOLS:
        source = PROJECT_ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少现场验证工具: {relative}")
        destination = candidate_dir / "field_tools" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def write_config_template(candidate_dir: Path) -> None:
    config = ConfigManager.get_default_config()
    config["enable_deduplication"] = False
    config["enable_auto_delete"] = False
    config["users"] = {}
    (candidate_dir / "config.template.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def manifest_entries(candidate_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(candidate_dir.rglob("*")):
        if not path.is_file() or path.name == "release_manifest.json":
            continue
        entries.append(
            {
                "path": path.relative_to(candidate_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def write_manifest(candidate_dir: Path, executable: Path) -> Path:
    entries = manifest_entries(candidate_dir)
    payload = {
        "schema_version": 1,
        "product": "图片异步上传工具",
        "version": __version__,
        "release_name": RELEASE_NAME,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_free": True,
        "database_path_scan": "passed",
        "pyinstaller_archive_scan": "passed",
        "manifest_self_excluded": True,
        "executable": executable.relative_to(candidate_dir).as_posix(),
        "executable_sha256": sha256_file(executable),
        "file_count_excluding_manifest": len(entries),
        "files": entries,
    }
    manifest_path = candidate_dir / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def write_zip(candidate_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(candidate_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(candidate_dir).as_posix())


def _zip_member_hash(archive: zipfile.ZipFile, name: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "r") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest().upper()


def verify_release(candidate_dir: Path, zip_path: Path, sidecar_path: Path) -> dict[str, Any]:
    """Independently verify directory, manifest, ZIP, checksum, and DB gates."""
    if not candidate_dir.is_dir() or not zip_path.is_file() or not sidecar_path.is_file():
        raise FileNotFoundError("候选目录、ZIP 或 SHA-256 侧车文件缺失")
    manifest_path = candidate_dir / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != __version__ or manifest.get("database_free") is not True:
        raise RuntimeError("发布清单版本或无数据库标记无效")

    database_matches = scan_for_database_paths(candidate_dir)
    if database_matches:
        raise RuntimeError(f"候选目录发现数据库文件: {'; '.join(database_matches)}")
    executable_relative = str(manifest.get("executable", ""))
    executable = candidate_dir / Path(executable_relative)
    if not executable.is_file():
        raise RuntimeError("发布清单中的可执行文件不存在")
    scan_pyinstaller_archive(executable)

    actual_entries = {entry["path"]: entry for entry in manifest_entries(candidate_dir)}
    declared_entries = {
        str(entry.get("path", "")): entry
        for entry in manifest.get("files", [])
        if isinstance(entry, dict)
    }
    if actual_entries != declared_entries:
        missing = sorted(set(declared_entries) - set(actual_entries))
        extra = sorted(set(actual_entries) - set(declared_entries))
        changed = sorted(
            path
            for path in set(actual_entries) & set(declared_entries)
            if actual_entries[path] != declared_entries[path]
        )
        raise RuntimeError(
            f"候选目录与发布清单不一致: missing={missing}, extra={extra}, changed={changed}"
        )
    if manifest.get("file_count_excluding_manifest") != len(actual_entries):
        raise RuntimeError("发布清单文件计数不一致")
    if manifest.get("executable_sha256") != sha256_file(executable):
        raise RuntimeError("可执行文件 SHA-256 与发布清单不一致")

    directory_files = {
        path.relative_to(candidate_dir).as_posix()
        for path in candidate_dir.rglob("*")
        if path.is_file()
    }
    runtime_matches = sorted(
        name
        for name in directory_files
        if name in RUNTIME_ONLY_PATHS or name.startswith(RUNTIME_ONLY_PREFIXES)
    )
    if runtime_matches:
        raise RuntimeError(f"候选包混入首次运行产物: {'; '.join(runtime_matches)}")

    with zipfile.ZipFile(zip_path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"ZIP CRC 校验失败: {corrupt}")
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise RuntimeError("ZIP 存在重复成员")
        unsafe = [
            name
            for name in names
            if name.startswith(("/", "\\")) or ".." in Path(name).parts
        ]
        if unsafe:
            raise RuntimeError(f"ZIP 存在不安全路径: {'; '.join(unsafe)}")
        if set(names) != directory_files:
            raise RuntimeError("ZIP 成员与候选目录不一致")
        for name, entry in declared_entries.items():
            size, digest = _zip_member_hash(archive, name)
            if size != entry.get("size_bytes") or digest != entry.get("sha256"):
                raise RuntimeError(f"ZIP 成员与清单不一致: {name}")
        manifest_size, manifest_hash = _zip_member_hash(archive, "release_manifest.json")
        if manifest_size != manifest_path.stat().st_size or manifest_hash != sha256_file(manifest_path):
            raise RuntimeError("ZIP 中的发布清单与候选目录不一致")

    zip_hash = sha256_file(zip_path)
    sidecar_parts = sidecar_path.read_text(encoding="ascii").strip().split()
    if not sidecar_parts or sidecar_parts[0].upper() != zip_hash:
        raise RuntimeError("ZIP SHA-256 侧车文件不匹配")
    return {
        "verified": True,
        "candidate_dir": str(candidate_dir),
        "zip": str(zip_path),
        "zip_sha256": zip_hash,
        "exe_sha256": sha256_file(executable),
        "file_count_excluding_manifest": len(actual_entries),
        "database_matches": database_matches,
        "runtime_matches": runtime_matches,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only the exact v3.5.1 candidate directory and ZIP.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify the existing candidate directory, ZIP, manifest, and sidecar without modifying them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dist_dir = PROJECT_ROOT / "dist" / APP_NAME
    executable = dist_dir / f"{APP_NAME}.exe"
    release_root = PROJECT_ROOT / "release_candidate"
    candidate_dir = release_root / RELEASE_NAME
    zip_path = release_root / f"{RELEASE_NAME}.zip"
    sidecar_path = release_root / f"{RELEASE_NAME}.zip.sha256"

    if args.verify_only:
        print(
            json.dumps(
                verify_release(candidate_dir, zip_path, sidecar_path),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not executable.is_file():
        raise FileNotFoundError(f"缺少 PyInstaller 输出: {executable}")
    release_root.mkdir(parents=True, exist_ok=True)
    for target in (candidate_dir, zip_path):
        if target.exists() and not args.force:
            raise FileExistsError(f"发布目标已存在，确认后使用 --force: {target}")
        if target.exists():
            remove_existing_target(target, release_root)
    if sidecar_path.exists():
        if not args.force:
            raise FileExistsError(f"发布哈希已存在，确认后使用 --force: {sidecar_path}")
        sidecar_path.unlink()

    shutil.copytree(dist_dir, candidate_dir)
    copy_release_documents(candidate_dir)
    write_config_template(candidate_dir)

    database_matches = scan_for_database_paths(candidate_dir)
    if database_matches:
        raise RuntimeError(f"发现数据库文件: {'; '.join(database_matches)}")
    candidate_executable = candidate_dir / executable.name
    scan_pyinstaller_archive(candidate_executable)
    manifest_path = write_manifest(candidate_dir, candidate_executable)
    write_zip(candidate_dir, zip_path)
    zip_hash = sha256_file(zip_path)
    sidecar_path.write_text(
        f"{zip_hash}  {zip_path.name}\n",
        encoding="ascii",
    )
    summary = verify_release(candidate_dir, zip_path, sidecar_path)
    summary["manifest"] = str(manifest_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
