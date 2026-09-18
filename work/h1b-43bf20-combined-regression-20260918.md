# H1b 修复候选组合回归证据

## 候选与主线

- 当前远端主线：`061f007ebf58692024c78772cb21e1429695a569`
- 候选：`43bf20b825f4a6336308069d65c80a3b2ddc04fa`
- 候选直接父：`061f007ebf58692024c78772cb21e1429695a569`
- 候选改动：`tradingagents/agents/managers/research_manager.py`、`tests/test_expectation_revision_contract.py`
- 组合回归在独立 worktree `/private/tmp/ta-combined-h1b-full-43bf20` 临时应用候选完成；未合入、未部署、未写生产库。

## 定向证据

- H1b 合约 + 4 个 research_manager 定向测试：`112 passed`
- `compileall`：通过
- `git diff --check`：通过
- 当前主线+候选组合的真实输入形态复现：字符串/None `excluded_evidence` 保留，Mapping 按 `claim_id` 去重，二次调用幂等；通过。

## 聚合全量回归

命令（独立临时 SQLite，不写生产库）：

```text
env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/h1b-full-43bf20.db \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
  -m pytest tests -q -p no:randomly \
  --deselect tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields
```

结果：

- `4974 passed`
- `1 skipped`
- `6 deselected`
- `183 warnings`
- `3 subtests passed`
- 用时 `322.61s`
- `0 failed`

## 生产状态

- 8000 仍运行 `6ee148699339efefc2f7f7548eb286be485524e1`
- 生产库仍为 `794 completed / 622 failed`
- H1b 加权 flag 未改，仍保持关闭
- 候选尚未合入主线，尚未部署；下一闸门是用户逐项放行合入与部署
