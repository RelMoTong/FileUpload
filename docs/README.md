# 文档索引

## 当前文档

- [MVC 架构说明](MVC_ARCHITECTURE.md)：最终目录、职责边界、事件流、组合根及启动/退出顺序。
- [MVC 最终验收报告](MVC_FINAL_AUDIT.md)：逐项验收证据、测试结果与已知限制。
- [MVC 重构阶段 0 报告](MVC_PHASE0_BASELINE.md)：重构前基线与人工回归清单。
- `MVC_PHASE1_REPORT.md` 至 `MVC_PHASE8_REPORT.md`：各阶段实施记录。
- [版本变更](CHANGELOG.md)：版本更新记录。

## 运行与测试

```powershell
pip install -r requirements.txt
python -m src.main
pytest -q -p no:cacheprovider
```

应用启动必须经过 `src/main.py`。新增业务能力时，应先阅读 MVC 架构说明，并同步更新架构边界测试。
