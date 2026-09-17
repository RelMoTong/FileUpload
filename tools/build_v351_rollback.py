"""Package the last reproducible pre-v3.5.1 release as an isolated rollback ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLLBACK_VERSION = "3.4.2"
ROLLBACK_COMMIT = "1322bf3"
ROLLBACK_NAME = f"ImageUploadTool_v{ROLLBACK_VERSION}_rollback"
RUNTIME_PATHS = {"config.json", "startup-error.log"}
RUNTIME_PREFIXES = ("logs/", "data/", "resume_data/")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_target(target: Path, release_root: Path) -> None:
    resolved = target.resolve(strict=False)
    root = release_root.resolve()
    allowed_names = {ROLLBACK_NAME, f"{ROLLBACK_NAME}.zip", f"{ROLLBACK_NAME}.zip.sha256"}
    if resolved.parent != root or resolved.name not in allowed_names:
        raise ValueError(f"拒绝操作非预期回退目标: {resolved}")


def entries(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "release_manifest.json"
    ]


def write_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())


def verify(candidate: Path, zip_path: Path, sidecar: Path) -> dict[str, Any]:
    manifest_path = candidate / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = entries(candidate)
    if manifest.get("files") != actual or manifest.get("file_count_excluding_manifest") != len(actual):
        raise RuntimeError("回退目录与清单不一致")
    file_names = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file()
    }
    runtime = sorted(
        name
        for name in file_names
        if name in RUNTIME_PATHS or name.startswith(RUNTIME_PREFIXES)
    )
    if runtime:
        raise RuntimeError(f"回退包混入运行产物: {'; '.join(runtime)}")
    with zipfile.ZipFile(zip_path, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("回退 ZIP CRC 校验失败")
        zipped = {info.filename for info in archive.infolist() if not info.is_dir()}
        if zipped != file_names:
            raise RuntimeError("回退 ZIP 与候选目录成员不一致")
    zip_hash = sha256_file(zip_path)
    if sidecar.read_text(encoding="ascii").strip().split()[0].upper() != zip_hash:
        raise RuntimeError("回退 ZIP 侧车哈希不一致")
    executable = candidate / f"ImageUploadTool_v{ROLLBACK_VERSION}.exe"
    return {
        "verified": True,
        "rollback_dir": str(candidate),
        "zip": str(zip_path),
        "zip_sha256": zip_hash,
        "exe_sha256": sha256_file(executable),
        "file_count_excluding_manifest": len(actual),
        "source_commit": ROLLBACK_COMMIT,
        "database_free": False,
        "runtime_matches": runtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    release_root = PROJECT_ROOT / "release_candidate"
    candidate = release_root / ROLLBACK_NAME
    zip_path = release_root / f"{ROLLBACK_NAME}.zip"
    sidecar = release_root / f"{ROLLBACK_NAME}.zip.sha256"
    if args.verify_only:
        print(json.dumps(verify(candidate, zip_path, sidecar), ensure_ascii=False, indent=2))
        return 0
    dist = args.dist_dir.resolve(strict=True)
    executable = dist / f"ImageUploadTool_v{ROLLBACK_VERSION}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"回退 EXE 不存在: {executable}")
    release_root.mkdir(parents=True, exist_ok=True)
    for target in (candidate, zip_path, sidecar):
        validate_target(target, release_root)
        if target.exists() and not args.force:
            raise FileExistsError(f"目标已存在，确认后使用 --force: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    shutil.copytree(dist, candidate)
    shutil.copy2(PROJECT_ROOT / "docs" / "V351_ROLLBACK_README.md", candidate / "ROLLBACK_README.md")
    shutil.copy2(
        PROJECT_ROOT / "docs" / "V342_ROLLBACK_PACKAGING_PATCH.diff",
        candidate / "V342_ROLLBACK_PACKAGING_PATCH.diff",
    )
    files = entries(candidate)
    manifest = {
        "schema_version": 1,
        "product": "图片异步上传工具",
        "version": ROLLBACK_VERSION,
        "release_name": ROLLBACK_NAME,
        "source_commit": ROLLBACK_COMMIT,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_free": False,
        "intended_use": "emergency rollback only",
        "manifest_self_excluded": True,
        "executable": executable.name,
        "executable_sha256": sha256_file(candidate / executable.name),
        "file_count_excluding_manifest": len(files),
        "files": files,
    }
    (candidate / "release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_zip(candidate, zip_path)
    sidecar.write_text(f"{sha256_file(zip_path)}  {zip_path.name}\n", encoding="ascii")
    print(json.dumps(verify(candidate, zip_path, sidecar), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
