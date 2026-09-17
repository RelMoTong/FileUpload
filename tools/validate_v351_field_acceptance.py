"""Validate v3.5.1 field evidence and emit an explicit GO/NO-GO report."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from datetime import date
from pathlib import Path
from typing import Any


SCENARIOS = (
    "clean_windows_start",
    "smb_upload",
    "ftp_upload",
    "dual_protocol_partial_failure",
    "network_jitter_server_restart",
    "same_path_generations",
    "kill_and_machine_restart",
    "disk_pressure_and_trash_failure",
)
PLACEHOLDER_WORDS = ("待填写", "实际签署人", "真实主机", "占位", "example")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


class EvidenceValidator:
    def __init__(self, evidence_path: Path, candidate_zip: Path) -> None:
        self.evidence_path = evidence_path.resolve(strict=True)
        self.evidence_root = self.evidence_path.parent
        self.candidate_zip = candidate_zip.resolve(strict=True)
        self.errors: list[str] = []
        self.checks: dict[str, bool] = {}

    def check(self, name: str, condition: bool, error: str) -> bool:
        passed = bool(condition)
        self.checks[name] = passed
        if not passed:
            self.errors.append(error)
        return passed

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def clean_text(value: Any) -> str:
        return str(value or "").strip()

    def evidence_file(self, value: Any, label: str) -> bool:
        raw = self.clean_text(value)
        if not raw:
            return self.check(f"evidence:{label}", False, f"{label} 缺少证据路径")
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            return self.check(
                f"evidence:{label}", False, f"{label} 证据必须是证据目录内的相对路径"
            )
        resolved = (self.evidence_root / relative).resolve(strict=False)
        try:
            inside = resolved.is_relative_to(self.evidence_root)
        except AttributeError:
            inside = self.evidence_root == resolved or self.evidence_root in resolved.parents
        return self.check(
            f"evidence:{label}", inside and resolved.is_file(), f"{label} 证据文件不存在: {raw}"
        )

    def validate_endpoint(self, endpoint: dict[str, Any], label: str) -> None:
        host = self.clean_text(endpoint.get("host"))
        valid_host = bool(host) and not any(word in host.lower() for word in PLACEHOLDER_WORDS)
        if valid_host:
            try:
                address = ipaddress.ip_address(host.strip("[]"))
                valid_host = not (
                    address.is_loopback
                    or address.is_unspecified
                    or address.is_link_local
                    or address.is_multicast
                )
            except ValueError:
                valid_host = host.lower() != "localhost" and ".example" not in host.lower()
        self.check(f"{label}:real_host", valid_host, f"{label} 必须使用真实非回环主机")
        self.check(
            f"{label}:probe",
            endpoint.get("probe_passed") is True,
            f"{label} 真实端点探测未通过",
        )
        self.evidence_file(endpoint.get("evidence"), f"{label} 探测")

    def validate(self, payload: Any) -> dict[str, Any]:
        root = self.mapping(payload)
        self.check("schema_version", root.get("schema_version") == 1, "schema_version 必须为 1")

        candidate = self.mapping(root.get("candidate"))
        declared_hash = self.clean_text(candidate.get("sha256")).upper()
        actual_hash = sha256_file(self.candidate_zip)
        self.check(
            "candidate_sha256",
            len(declared_hash) == 64 and declared_hash == actual_hash,
            "候选包 SHA-256 与现场重新计算结果不一致",
        )

        environment = self.mapping(root.get("environment"))
        for key, label in (
            ("clean_windows", "干净 Windows"),
            ("python_or_ide_absent", "无 Python/IDE"),
            ("real_network", "真实网络"),
        ):
            self.check(f"environment:{key}", environment.get(key) is True, f"{label} 条件未确认")
        smb = self.mapping(environment.get("smb"))
        ftp = self.mapping(environment.get("ftp"))
        self.validate_endpoint(smb, "SMB")
        self.validate_endpoint(ftp, "FTP")
        share = self.clean_text(smb.get("share"))
        self.check("SMB:share", share.startswith("\\\\") and len(share.split("\\")) >= 4, "SMB share 必须是完整 UNC 路径")
        port = ftp.get("port")
        self.check("FTP:port", isinstance(port, int) and 1 <= port <= 65535, "FTP port 无效")

        max_file = self.mapping(environment.get("max_file"))
        max_hash = self.clean_text(max_file.get("sha256"))
        max_bytes = max_file.get("bytes")
        self.check("max_file:size", isinstance(max_bytes, int) and max_bytes > 0, "最大现场文件大小无效")
        self.check("max_file:sha256", len(max_hash) == 64 and all(char in "0123456789abcdefABCDEF" for char in max_hash), "最大现场文件 SHA-256 无效")
        self.evidence_file(max_file.get("evidence"), "最大现场文件")

        scenarios = self.mapping(root.get("scenarios"))
        for scenario in SCENARIOS:
            result = self.mapping(scenarios.get(scenario))
            self.check(f"scenario:{scenario}", result.get("passed") is True, f"现场场景未通过: {scenario}")
            self.evidence_file(result.get("evidence"), f"现场场景 {scenario}")

        soak = self.mapping(root.get("soak"))
        duration = soak.get("duration_hours")
        self.check("soak:passed", soak.get("passed") is True, "长稳测试未通过")
        self.check("soak:duration", isinstance(duration, (int, float)) and duration >= 24, "长稳测试必须至少 24 小时")
        self.evidence_file(soak.get("evidence"), "长稳测试")

        reconciliation = self.mapping(root.get("reconciliation"))
        counts: dict[str, int] = {}
        for key in ("discovered", "submitted", "pending", "archived", "failed", "duplicate", "unexplained"):
            value = reconciliation.get(key)
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            self.check(f"reconciliation:{key}", valid, f"对账字段 {key} 必须是非负整数")
            counts[key] = value if isinstance(value, int) and not isinstance(value, bool) else -1
        conserved = counts["discovered"] == counts["pending"] + counts["archived"] + counts["failed"]
        self.check("reconciliation:conserved", conserved, "发现数不等于待重试数 + 归档数 + 失败数")
        self.check("reconciliation:no_duplicate", counts["duplicate"] == 0, "存在重复上传")
        self.check("reconciliation:no_unexplained", counts["unexplained"] == 0, "存在未解释差额")
        self.check("reconciliation:submitted", 0 <= counts["submitted"] <= counts["discovered"], "成功提交数超出发现数")

        rollback = self.mapping(root.get("rollback"))
        self.check("rollback:passed", rollback.get("passed") is True, "真实回退演练未通过")
        self.evidence_file(rollback.get("evidence"), "回退演练")

        signatures = self.mapping(root.get("signatures"))
        for role in ("developer", "field"):
            signature = self.mapping(signatures.get(role))
            name = self.clean_text(signature.get("name"))
            valid_name = bool(name) and not any(word in name.lower() for word in PLACEHOLDER_WORDS)
            self.check(f"signature:{role}:name", valid_name, f"{role} 签署人未填写")
            try:
                date.fromisoformat(self.clean_text(signature.get("date")))
                valid_date = True
            except ValueError:
                valid_date = False
            self.check(f"signature:{role}:date", valid_date, f"{role} 签署日期无效")
            self.check(f"signature:{role}:decision", signature.get("decision") == "GO", f"{role} 未签署 GO")
        self.check("conclusion", root.get("conclusion") == "GO", "现场结论不是 GO")

        decision = "GO" if not self.errors else "NO-GO"
        return {
            "schema_version": 1,
            "decision": decision,
            "candidate_zip": str(self.candidate_zip),
            "candidate_sha256": actual_hash,
            "evidence": str(self.evidence_path),
            "checks": self.checks,
            "errors": self.errors,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--candidate-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
        report = EvidenceValidator(args.evidence, args.candidate_zip).validate(payload)
    except Exception as exc:
        report = {
            "schema_version": 1,
            "decision": "NO-GO",
            "checks": {},
            "errors": [f"无法校验证据: {type(exc).__name__}: {exc}"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
