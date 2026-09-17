"""Release-candidate and field-evidence tool regression tests."""

from __future__ import annotations

import json
from pathlib import Path

from tools import build_v351_release_candidate as builder
from tools import build_v351_rollback as rollback_builder
from tools.validate_v351_field_acceptance import EvidenceValidator, SCENARIOS, sha256_file


def _write_evidence_file(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def _valid_field_evidence(root: Path, candidate_zip: Path) -> dict[str, object]:
    evidence_paths = [
        "evidence/smb.json",
        "evidence/ftp.json",
        "evidence/max-file.json",
        "evidence/soak.json",
        "evidence/rollback.json",
    ]
    scenario_paths = {
        scenario: f"evidence/{scenario}.json" for scenario in SCENARIOS
    }
    for relative in (*evidence_paths, *scenario_paths.values()):
        _write_evidence_file(root, relative)
    return {
        "schema_version": 1,
        "candidate": {"sha256": sha256_file(candidate_zip)},
        "environment": {
            "clean_windows": True,
            "python_or_ide_absent": True,
            "real_network": True,
            "smb": {
                "host": "fileserver.factory.local",
                "share": r"\\fileserver.factory.local\images",
                "probe_passed": True,
                "evidence": "evidence/smb.json",
            },
            "ftp": {
                "host": "10.20.30.40",
                "port": 21,
                "probe_passed": True,
                "evidence": "evidence/ftp.json",
            },
            "max_file": {
                "bytes": 3,
                "sha256": "A" * 64,
                "evidence": "evidence/max-file.json",
            },
        },
        "scenarios": {
            scenario: {"passed": True, "evidence": relative}
            for scenario, relative in scenario_paths.items()
        },
        "soak": {
            "passed": True,
            "duration_hours": 24,
            "evidence": "evidence/soak.json",
        },
        "reconciliation": {
            "discovered": 1,
            "submitted": 1,
            "pending": 0,
            "archived": 1,
            "failed": 0,
            "duplicate": 0,
            "unexplained": 0,
        },
        "rollback": {"passed": True, "evidence": "evidence/rollback.json"},
        "signatures": {
            "developer": {"name": "张三", "date": "2026-09-14", "decision": "GO"},
            "field": {"name": "李四", "date": "2026-09-14", "decision": "GO"},
        },
        "conclusion": "GO",
    }


def test_field_validator_accepts_complete_real_evidence(tmp_path: Path) -> None:
    candidate_zip = tmp_path / "candidate.zip"
    candidate_zip.write_bytes(b"candidate")
    evidence_path = tmp_path / "field_evidence.json"
    payload = _valid_field_evidence(tmp_path, candidate_zip)
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = EvidenceValidator(evidence_path, candidate_zip).validate(payload)

    assert report["decision"] == "GO"
    assert report["errors"] == []
    assert all(report["checks"].values())


def test_field_validator_rejects_placeholder_loopback_and_missing_evidence(
    tmp_path: Path,
) -> None:
    candidate_zip = tmp_path / "candidate.zip"
    candidate_zip.write_bytes(b"candidate")
    evidence_path = tmp_path / "field_evidence.json"
    payload = _valid_field_evidence(tmp_path, candidate_zip)
    environment = payload["environment"]
    assert isinstance(environment, dict)
    smb = environment["smb"]
    assert isinstance(smb, dict)
    smb["host"] = "127.0.0.1"
    smb["evidence"] = "evidence/missing.json"
    signatures = payload["signatures"]
    assert isinstance(signatures, dict)
    developer = signatures["developer"]
    assert isinstance(developer, dict)
    developer["name"] = "待填写"
    payload["conclusion"] = "NO-GO"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = EvidenceValidator(evidence_path, candidate_zip).validate(payload)

    assert report["decision"] == "NO-GO"
    assert not report["checks"]["SMB:real_host"]
    assert not report["checks"]["evidence:SMB 探测"]
    assert not report["checks"]["signature:developer:name"]
    assert not report["checks"]["conclusion"]


def test_release_verifier_detects_directory_and_zip_tampering(
    tmp_path: Path, monkeypatch
) -> None:
    candidate_dir = tmp_path / builder.RELEASE_NAME
    candidate_dir.mkdir()
    executable = candidate_dir / f"{builder.APP_NAME}.exe"
    executable.write_bytes(b"fake-exe")
    (candidate_dir / "README.md").write_text("release\n", encoding="utf-8")
    monkeypatch.setattr(builder, "scan_pyinstaller_archive", lambda _path: None)
    builder.write_manifest(candidate_dir, executable)
    zip_path = tmp_path / f"{builder.RELEASE_NAME}.zip"
    sidecar = tmp_path / f"{builder.RELEASE_NAME}.zip.sha256"
    builder.write_zip(candidate_dir, zip_path)
    sidecar.write_text(f"{builder.sha256_file(zip_path)}  {zip_path.name}\n", encoding="ascii")

    report = builder.verify_release(candidate_dir, zip_path, sidecar)

    assert report["verified"] is True
    assert report["database_matches"] == []

    (candidate_dir / "README.md").write_text("tampered\n", encoding="utf-8")
    try:
        builder.verify_release(candidate_dir, zip_path, sidecar)
    except RuntimeError as exc:
        assert "发布清单不一致" in str(exc)
    else:
        raise AssertionError("tampered candidate was accepted")


def test_rollback_verifier_checks_manifest_zip_sidecar_and_runtime_cleanliness(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / rollback_builder.ROLLBACK_NAME
    candidate.mkdir()
    executable = candidate / f"ImageUploadTool_v{rollback_builder.ROLLBACK_VERSION}.exe"
    executable.write_bytes(b"rollback-exe")
    (candidate / "ROLLBACK_README.md").write_text("rollback\n", encoding="utf-8")
    files = rollback_builder.entries(candidate)
    manifest = {
        "file_count_excluding_manifest": len(files),
        "files": files,
    }
    (candidate / "release_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    zip_path = tmp_path / f"{rollback_builder.ROLLBACK_NAME}.zip"
    sidecar = tmp_path / f"{rollback_builder.ROLLBACK_NAME}.zip.sha256"
    rollback_builder.write_zip(candidate, zip_path)
    sidecar.write_text(
        f"{rollback_builder.sha256_file(zip_path)}  {zip_path.name}\n",
        encoding="ascii",
    )

    assert rollback_builder.verify(candidate, zip_path, sidecar)["verified"] is True

    (candidate / "config.json").write_text("{}", encoding="utf-8")
    files = rollback_builder.entries(candidate)
    manifest["file_count_excluding_manifest"] = len(files)
    manifest["files"] = files
    (candidate / "release_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    rollback_builder.write_zip(candidate, zip_path)
    sidecar.write_text(
        f"{rollback_builder.sha256_file(zip_path)}  {zip_path.name}\n",
        encoding="ascii",
    )
    try:
        rollback_builder.verify(candidate, zip_path, sidecar)
    except RuntimeError as exc:
        assert "运行产物" in str(exc)
    else:
        raise AssertionError("rollback runtime contamination was accepted")
