# v3.5.1 无数据库版现场验收与放行记录

本记录是 P4-03 与 P4-04 的现场证据表。只允许填写实际执行结果；本机回环测试、开发机截图或推断不能替代任何现场项。

## 输入与冻结物

- 候选包：`release_candidate/ImageUploadTool_v3.5.1_no_database_release.zip`
- 候选包 SHA-256：`307267F76807F502F2DAB56E985A060CACB1B2A47CED93199F9858FB4E14459B`
- 候选 EXE SHA-256：`2DDB5A5179DB766BB653EAB1A3931B34233A8BD9CF6DE77358E9874469BC84C9`
- 回退包：`release_candidate/ImageUploadTool_v3.5.0_rollback.zip`
- 回退包 SHA-256：`A9642202E0DF23CBCCEF4401D6D5DBD279D50921C19156346D4219E659ACAFD0`
- 回退 EXE SHA-256：`4304CB993A57CE4CEB7214EC7E103E11D67615D57CAF8A427785622311DA3DA1`
- 发布证据归档：`release_candidate/v3.5.1_no_database_release_bundle_canonical_v24.zip`
- 发布证据归档 SHA-256：见归档旁的 `release_candidate/v3.5.1_no_database_release_bundle_canonical_v24.zip.sha256` 侧车文件（归档内容不自引用自身哈希）。
- 候选清单：候选目录内 `release_manifest.json`
- 配置模板：候选目录内 `config.template.json`
- 验证方式：按本表现场逐项执行，并由发布负责人复核证据归档
- 当前结论：`NO-GO`，直到本表全部通过并完成签署。

现场工程师必须先核对 ZIP 的 SHA-256 与发布清单。解压时不得覆盖旧版本目录、现场配置或源文件；新目录首次运行前不得包含 `config.json`、`logs/`、`data/`、`resume_data/`。

## P4-03 现场矩阵

| 项目 | 实际命令/步骤 | 通过条件 | 结果/证据 |
| --- | --- | --- | --- |
| 干净 Windows 首次启动 | 在无 Python/IDE 的机器解压，双击 EXE | 启动、登录、配置、停止、重启均成功；无 `startup-error.log` | 待填写 |
| SMB 权限与上传 | 配置真实源、SMB 目标、独立备份；上传最大现场文件 | 目标完整、源仅在归档成功后移动；目录不嵌套 | 待填写 |
| FTP/FTPS 上传 | 用真实服务器、账户和证书上传最大现场文件 | 临时对象不暴露为最终文件，远端校验和最终 rename 均成功 | 待填写 |
| 双协议部分失败 | 使 SMB 或 FTP 单侧失败后恢复 | 不误报成功；源保留并可重试；已成功通道不重复提交 | 待填写 |
| 网络抖动与服务端重启 | 传输中断开/恢复网络并重启服务器 | 自动暂停和明确恢复；无重复归档或源丢失 | 待填写 |
| 同名冲突与覆盖 | 同一相对路径生成不同代际文件 | 以精确文件身份处理；新代际不被旧任务删除 | 待填写 |
| 强杀与机器重启 | 传输/归档中结束进程并重启机器 | 状态记录恢复；源、目标、待重试、归档账目守恒 | 待填写 |
| 磁盘压力与回收站失败 | 制造低空间、磁盘 API 失败和回收站失败 | 上传暂停；自动清理失败关闭；源文件保留 | 待填写 |
| 24–72 小时长稳 | 使用最终包、真实网络运行以下命令 | 资源无持续增长，日志/状态受控，队列可解释 | 待填写 |

```powershell
python tools\release_soak_test.py `
  --exe "C:\Release\ImageUploadTool_v3.5.1\ImageUploadTool_v3.5.1.exe" `
  --duration-seconds 86400 `
  --sample-interval 30 `
  --output "C:\Release\v351-soak-24h.json"
```

该脚本只测进程资源和启动探针。运行期间必须同时按上表人工执行真实 SMB/FTP 场景，并把应用日志、状态 JSON 和逐文件对账结果作为附件。

### 逐文件对账

填写：发现数 `____`，成功提交数 `____`，待重试数 `____`，归档数 `____`，失败数 `____`，重复上传数 `____`，未解释差额 `____`。

只有“未解释差额 = 0、重复上传数 = 0、错误成功数 = 0、错移/误删数 = 0”时本节可通过。

### 机器门禁校验

将实际现场结果按校验器 schema 写入 `field_evidence.json`，然后执行：

```powershell
python tools\validate_v351_field_acceptance.py `
  --evidence .\field_evidence.json `
  --candidate-zip .\release_candidate\ImageUploadTool_v3.5.1_no_database_release.zip `
  --output .\field_evidence_validation.json
```

校验器要求 8 个现场故障/上传场景全部通过、真实非回环 SMB/FTP 端点且探测成功、最大现场文件哈希证据、至少 24 小时长稳、账目守恒、真实回退演练及开发/现场负责人 `GO` 签署；所有引用的现场附件还必须真实存在于证据文件同目录及其子目录内，任何缺项均输出 `NO-GO`。

现场证据完成后，用归档工具参数 `--field-evidence-dir <现场证据目录>` 将 `field_evidence.json` 及附件冻结到归档；该目录不得位于归档输出目录内。

## P4-04 发布、回退与签署

### 回退演练

1. 停止 v3.5.1，保留 `logs/`、`data/`、`resume_data/` 与 `config.json` 的时间戳副本，不删除任何源目录文件。
2. 将此前已验证版本解压到独立目录；禁止覆盖 v3.5.1 安装目录。
3. 仅在管理员确认配置字段兼容后，复制配置副本；先以停止状态启动并检查路径，再允许上传。
4. 上传一个非生产测试文件，核对目标和备份；失败时停止并恢复原目录，不操作源文件。

回退演练结果：本机独立目录启动验证通过（5 秒运行、无 `startup-error.log`）；未执行真实现场配置迁移、测试上传和数据无损回退，故现场回退门禁仍为 `待填写`。证据路径：`.local_acceptance_20260912155636173/rollback/`。

本机发布包隔离启动证据：`.local_acceptance_20260912155636173/release_soak_10s.json`，10 秒探针通过（exit 0、13 samples、RSS 增长约 5.4 MiB、线程增长 0、句柄增长 -1、无残留进程）。该证据不替代 P4-03 的真实 SMB/FTP、断网/恢复、强杀/断电及 24–72 小时验收。回退包不支持 `IMAGE_UPLOAD_SMOKE_TEST` 探针环境变量，使用该脚本会超时，不能作为回退包长稳结论。

本机补充矩阵：`.local_acceptance_20260912155636173/p4_03_local_matrix/`，`44 passed, 1 skipped`，覆盖路径安全、FTP 提交、文件代际、原子状态、安全清理和无数据库专项；该结果仍不替代真实现场网络、断电和负责人签署。

现场前置检查：`.local_acceptance_20260912155636173/field_preflight_20260913_rerun.json`。本机 LanmanServer/LanmanWorkstation 均为 Running，但未提供真实 SMB/FTP 主机，网络探针按规则跳过，不能作为现场验收通过。

### 运行红线

出现以下任一情况立即停止运行，保留日志与状态文件，并维持 `NO-GO`：误报成功、重复上传、错移/误删、状态损坏、磁盘检查失败、连续三次不可恢复上传失败、未解释的任务对账差额。

### 最终签署

| 角色 | 姓名 | 日期 | 结论 |
| --- | --- | --- | --- |
| 开发负责人 | 待填写 | 待填写 | GO / NO-GO |
| 现场负责人 | 待填写 | 待填写 | GO / NO-GO |

签署前必须确认方案中 20 项任务全部已勾选、候选包哈希与清单匹配，并将本记录、资源报告、测试日志和回退证据放入同一发布归档。
