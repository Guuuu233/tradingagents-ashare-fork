# DAV-866 独立复核记录（2026-09-13）

## 对象

- 候选提交：`020d3e3b18147f5ea20e90d3878b20952d20fd97`
- 直接父提交：`8c69eab186bde58e49cbc134fb4da5f015c77e92`
- 线上服务：`a227cdc3bb466edf2e910419cb6013cfc021d309`
- 变更白名单：
  - `tradingagents/eval/v03_return_measure.py`
  - `scripts/run_v03_return_measure.py`
  - `tests/test_v03_return_measure.py`

## 独立核验

1. 目标测试重跑：`1 failed, 42 passed`；唯一失败为既有 RT-S4 实时计数基线（当前全量 318、截点口径 317），与 DAV-865 父版本的已知基线一致。
2. 与父版本的 AST 函数对照：父版本原有函数全部保留且函数体未改；仅新增 DAV-865/DAV-866 专项测试函数。
3. 默认 provenance：
   - `EvaluationStamp().running_service_sha == offline_replay_gap`
   - `SnapshotManifest(...).running_service_sha == offline_replay_gap`
   - `V03ReturnMeasureEngine().measure_dataset([])` 的 stamp 与 manifest 均为 `offline_replay_gap`
   - 历史样本生成 SHA 仍保留为 `a6d4540feaa8043ff36b0607a31c1d2d5f004149`，未被冒充为当前运行服务。
4. 显式运行服务 SHA 与 healthz 探针路径保持可回读；离线/探针不可达时为显式 typed gap。
5. 生产库运行前后 SHA256 均为
   `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`；`quick_check` 与 `integrity_check` 均为 `ok`。

## 当前门禁

本记录不构成合入或部署授权。仍需 DAV-867 红队报告、同一完整 SHA 的“代码审核员”只读审查，以及按验收口径完成合入前回归证据。
