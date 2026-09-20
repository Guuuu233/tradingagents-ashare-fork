# 独立审核（只读）：H-01 期限唯一解析

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（合入用 SHA）

- **审核/合入 tip：** `872d94e9477809a236b09139afe145b3343e3856`
- **分支：** `origin/agent/1/b564e6857273`
- **第一父 / 基线 tip：** `c83881809da88686c30f097b1c3872187a5733ca`（`git rev-parse HEAD^` 已核为直接第一父）
- **关联开发卡：** [DAV-669](mention://issue/01a0727e-f62a-7135-be14-2557b29b3a6b)
- **Commit：** `feat(graph): 期限唯一解析：显式优先，未提供即 short，禁止 query 扩档 (H-01, DAV-669)`

## 白名单（`git diff --stat c838818..872d94e`）

```
 api/main.py                            | 110 +++++----
 tests/test_horizon_profile_contract.py | 416 +++++++++++++++++++++++++++++++++
 tests/test_report_dual_horizon.py      |   9 +-
 tradingagents/graph/horizon_profile.py | 189 +++++++++++++++
```

允许范围内。未改 `intent_parser.py` 可接受（LLM horizons 仍须在 `api/main.py` chat 路径被当作非 explicit）。超出即打回。禁止 frontend、collector、分析师、校准/回测、H1b、收益标签。

## 复核要点

1. `_normalize_analysis_horizons` 不得与新解析并存第二套规则；全部旧调用点（含 chat 约 4828/4936、`_run_job_inner` 约 2861/2885）已切到唯一函数，query 不得扩档。
2. 显式优先；未提供 → short/default；chat/LLM 的 horizons 不是 explicit。
3. `tests/test_report_dual_horizon.py` 旧锁测已反转。隔离复跑：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_profile_contract.py tests/test_report_dual_horizon.py tests/test_dual_horizon_bugs.py tests/test_intent_parser.py -q
```

在**本候选 SHA 的干净 checkout**上跑，不要用脏宿主。若有失败，对照同一命令在 `c838818` 上的结果，区分基线既有失败与本卡回归。
4. 未改 `DEFAULT_HOLD_DAYS`、未写 `evaluation_eligible`、未部署。

detached checkout 远端 tip，书面 ✅ / ⚠️ / ❌，含路径行号。勿 FF。
