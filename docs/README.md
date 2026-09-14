# 文档索引

## 当前文档

- [MVC 架构说明](MVC_ARCHITECTURE.md)：最终目录、职责边界、事件流、组合根及启动/退出顺序。
- [v3.5.1 无数据库基线](V351_NO_DATABASE_BASELINE.md)：当前稳定性改造的基线与限制。
- [v3.5.1 运行手册](V351_OPERATIONS_MANUAL.md)：现场运行、回退和放行要求。
- [v3.5.1 现场验收表](V351_FIELD_ACCEPTANCE.md)：现场验证结果记录模板。
- [v3.5.1 现场证据字段说明](V351_FIELD_EVIDENCE_SCHEMA.md)：结构化验收证据格式。
- [v3.5.1 发布验证记录](V351_RELEASE_VERIFICATION.md)：发布前验证结论和检查项。
- [版本变更](CHANGELOG.md)：版本更新记录。

## 运行

```powershell
pip install -r requirements.txt
python -m src.main
```

应用启动必须经过 `src/main.py`。新增业务能力时，应先阅读 MVC 架构说明，并在独立开发环境运行完整测试。
