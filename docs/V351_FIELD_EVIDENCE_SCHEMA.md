# v3.5.1 现场证据格式

现场工程师应将实际执行结果保存为 `field_evidence.json`，不得使用回环地址、模拟服务或开发机结果替代现场证据。候选包 SHA-256 必须由校验器根据本地文件重新计算。

从 `docs/field_evidence.template.json` 复制模板后填写；模板默认必定输出 `NO-GO`，只有真实附件、对账和双签全部齐全才可能通过。

## 必填结构

```json
{
  "schema_version": 1,
  "candidate": {"sha256": "候选包的64位SHA-256"},
  "environment": {
    "clean_windows": true,
    "python_or_ide_absent": true,
    "real_network": true,
    "smb": {"host": "真实主机", "share": "\\\\server\\share", "probe_passed": true, "evidence": "evidence/smb.json"},
    "ftp": {"host": "真实主机", "port": 21, "probe_passed": true, "evidence": "evidence/ftp.json"},
    "max_file": {"bytes": 123456, "sha256": "现场最大文件的64位SHA-256", "evidence": "evidence/max-file.json"}
  },
  "scenarios": {
    "clean_windows_start": {"passed": true, "evidence": "evidence/clean.json"},
    "smb_upload": {"passed": true, "evidence": "evidence/smb-upload.json"},
    "ftp_upload": {"passed": true, "evidence": "evidence/ftp-upload.json"},
    "dual_protocol_partial_failure": {"passed": true, "evidence": "evidence/partial.json"},
    "network_jitter_server_restart": {"passed": true, "evidence": "evidence/restart.json"},
    "same_path_generations": {"passed": true, "evidence": "evidence/generations.json"},
    "kill_and_machine_restart": {"passed": true, "evidence": "evidence/recovery.json"},
    "disk_pressure_and_trash_failure": {"passed": true, "evidence": "evidence/disk.json"}
  },
  "soak": {"passed": true, "duration_hours": 24, "evidence": "evidence/soak.json"},
  "reconciliation": {"discovered": 0, "submitted": 0, "pending": 0, "archived": 0, "failed": 0, "duplicate": 0, "unexplained": 0},
  "rollback": {"passed": true, "evidence": "evidence/rollback.json"},
  "signatures": {
    "developer": {"name": "实际签署人", "date": "YYYY-MM-DD", "decision": "GO"},
    "field": {"name": "实际签署人", "date": "YYYY-MM-DD", "decision": "GO"}
  },
  "conclusion": "GO"
}
```

## 机器校验

```powershell
python field_tools\validate_v351_field_acceptance.py `
  --evidence .\field_evidence.json `
  --candidate-zip .\release_candidate\ImageUploadTool_v3.5.1_no_database_release.zip `
  --output .\field_evidence_validation.json
```

返回 `NO-GO` 时不得放行。模板中的占位符只能替换为现场实际值；填写 `GO` 不会绕过缺失证据、账目差额或签署检查。

现场完成后，可使用归档工具的 `--field-evidence-dir` 将本 JSON 及其附件原样冻结到发布证据归档的 `evidence/field/` 下。证据目录必须是独立目录，不能是归档输出目录或其子目录。
