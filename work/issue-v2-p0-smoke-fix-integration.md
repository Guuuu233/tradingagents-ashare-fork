## 固定输入

- 目标基线：`target/codex/dav-4-p2a-trunk@23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- typed gap 候选：`83856c129f3fab96285549ae2485f1e52bed7621`（DAV-359，DAV-361 条件 PASS）
- probability note 候选：`25f043ea101372cde9a9ccaa98e81287134781db`（等待 DAV-362 PASS；未 PASS 不得开工）
- DAV-357 `7040e4d...` 和 DAV-356 所有旧分支全部废弃，禁止使用。

## 唯一 writer 与步骤

从精确基线 fresh checkout，严格串行：
1. cherry-pick `83856c1`；
2. cherry-pick `25f043e`；
3. 仅修改 `tests/test_dav37_stage16_regressions.py:237-241`，把三条 `provider call failed` 期望改为 `数据源调用失败`。除此之外禁止改该文件。

## 允许的总文件范围

- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector.py`
- `api/services/report_service.py`
- `tests/test_verdict_extraction.py`
- `tests/test_dav37_stage16_regressions.py`（仅三条展示预期）

禁止修改配置、DB schema、API main、providers、debate、golden fixture、用户模型/providers/role bindings/API Key、主干和服务。

## 验证

宿主 `.venv310`，全部用 `env -u PYTHONPATH`：

1. `git diff --check 23e09e5..HEAD`
2. `python -m compileall -q api tradingagents tests`
3. 定向：
   - `tests/test_data_collector.py`
   - `tests/test_dav37_stage16_regressions.py`
   - `tests/test_verdict_extraction.py`
   - `tests/test_confidence_extraction.py`
   - `tests/test_report_recovery.py`
   - 原 Phase 0 106矩阵全部测试文件
4. replay verifier。
5. 干净 checkout 全量 `pytest tests/`。不得用系统 Python或新建 venv。

## 交付

- 新远端组合 branch/SHA；
- parent chain；
- changed files；
- 定向/全量/replay/diff-check/compileall结果；
- 明确未合入/未重启/未上线。

禁止直接推 trunk，禁止重启。不要 mention 项目调度助手。