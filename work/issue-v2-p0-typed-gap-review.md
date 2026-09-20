## 固定审核对象

- 基线：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 分支：`agent/agent/5e932309d59c`
- SHA：`83856c129f3fab96285549ae2485f1e52bed7621`
- 只读审核，禁止改代码/测试/配置/DB/服务。

## 审核要求

1. 远端核验 SHA 与基线 ancestry，changed files 必须恰好：
   - `tradingagents/graph/data_collector.py`
   - `tests/test_data_collector.py`
2. 核验严格 TDD证据：宿主 Python3.10 RED→GREEN；生产代码仅把 failed 固定显示从 `provider call failed` 改为 `数据源调用失败`。
3. 复跑宿主 `.venv310`：新测试与完整 `tests/test_data_collector.py`。
4. 明确复现旧测试 `tests/test_dav37_stage16_regressions.py::test_merge_data_gaps_consumes_collector_ledger_for_new_markers` 是否因硬编码旧英文而失败。
5. 审核结论分两层：
   - 候选实现本身 PASS/BLOCK；
   - 能否单独合入主干。若唯一阻断是旧展示断言，应给出“候选实现 PASS，但必须由 integration owner 在组合提交中仅更新该测试三条预期”的精确结论。
6. 检查 diff-check/compileall，不得扩写需求。

交付文件:行号、测试结果、PASS/BLOCK、0 code changes、未合入/未重启/未上线。不要 mention 项目调度助手。