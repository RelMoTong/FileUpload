# v3.5.1 无数据库版发布验证报告

## 冻结输入

- Python：3.13.5
- 依赖：`requirements.lock.txt`
- 构建目录：`dist/ImageUploadTool_v3.5.1`
- 正式包：`release_candidate/ImageUploadTool_v3.5.1_no_database_release.zip`
- 回退包：`release_candidate/ImageUploadTool_v3.5.0_rollback.zip`

## 自动化证据

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 全量 pytest | `220 passed, 5 subtests passed` | 2026-09-13，命令：`python -m pytest -q tests -p no:cacheprovider --basetemp .pytest_tmp_field_gate_v19b_full` |
| Pyright | `0 errors, 0 warnings, 0 informations` | `.p4_02_clean_venv`，含发布与归档工具 |
| 正式包资源 smoke | 通过，exit 0；13 samples；RSS 增长约 5.4 MiB；线程增长 0；句柄增长 -1；无残留进程 | `.local_acceptance_20260912155636173/release_soak_10s.json` |
| P4-03 本机补充矩阵 | `44 passed, 1 skipped`；路径、FTP 提交、文件代际、原子状态、安全清理、无数据库专项 | `.local_acceptance_20260912155636173/p4_03_local_matrix/`（JUnit XML 与 stdout） |
| 现场前置检查 | Windows LanmanServer/LanmanWorkstation 为 Running；因未提供真实 SMB/FTP 主机，网络探针跳过；现场门禁未通过 | `.local_acceptance_20260912155636173/field_preflight_20260913_rerun.json` |
| 现场证据机器门禁 | 当前预检因缺少现场矩阵、真实端点、长稳、对账、附件和签署而输出 `NO-GO`；校验器拒绝放行 | `.local_acceptance_20260912155636173/field_evidence_validation_no_go_v3.json` |
| 现场门禁规则 | 真实端点、最大现场文件、长稳、对账、回退和签署证据均为放行前必填项；缺失即 `NO-GO` | `V351_FIELD_ACCEPTANCE.md`、现场证据归档 |
| 现场附件检查 | 场景、端点、最大文件、长稳和回退证据须与验收表逐项对应，并由发布负责人复核 | 现场验收记录 |
| 发布归档检查 | 发布负责人复核 ZIP 哈希、清单文件数、禁用文件和核心文档一致性 | 发布归档清单 |
| 正式包清单 | 237 files；数据库扫描通过；首次运行纯净通过 | `release_candidate/ImageUploadTool_v3.5.1_no_database_release/release_manifest.json` |
| 回退包清单 | 232 files；数据库扫描通过；首次运行纯净通过 | `release_candidate/ImageUploadTool_v3.5.0_rollback/release_manifest.json` |
| 源码编译检查 | 通过 | `python -m compileall -q src` |

## 校验值

- 正式包 ZIP：`307267F76807F502F2DAB56E985A060CACB1B2A47CED93199F9858FB4E14459B`
- 正式 EXE：`2DDB5A5179DB766BB653EAB1A3931B34233A8BD9CF6DE77358E9874469BC84C9`
- 回退包 ZIP：`A9642202E0DF23CBCCEF4401D6D5DBD279D50921C19156346D4219E659ACAFD0`
- 回退 EXE：`4304CB993A57CE4CEB7214EC7E103E11D67615D57CAF8A427785622311DA3DA1`

## 范围与限制

上述结果证明本机代码、构建物和交付物满足自动化门禁，但不证明现场门禁。真实 SMB/FTP、最大文件、断网恢复、服务端重启、强杀/断电、磁盘压力、24--72 小时长稳、逐文件对账、真实回退演练和负责人签署必须在现场完成。完成前发布结论保持 `NO-GO`。
