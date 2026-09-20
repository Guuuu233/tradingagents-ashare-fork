## DAV-437 交付核验（编排侧）

- **分支**: `agent/1/df73803fe99d`
- **精确 SHA**: `d50b0dc3a7b3721aba16ac4474530c1565be79de`
- **基线**: `93212809a099ebc6e5787bc0468e63969f5496f8`
- **远端**: 已 push `origin/agent/1/df73803fe99d`

### 独立核验（本机 `.venv310`，非施工 agent 自述）
```
pytest tests/test_evaluation_contracts.py tests/test_shadow_credit.py tests/test_credit_weighting.py tests/test_h1b_gates.py -q
→ 42 passed in 2.51s
```

### 交付物
- `tradingagents/agents/utils/evaluation_schemas.py`（EvaluationMetricMatrix / WeeklyMetricsJSON 契约）
- `tests/test_evaluation_contracts.py` + `tests/mock_evaluations/`
- `work/evaluations/` 样例周报产物
- `credit_weighting_enabled` 默认仍为 **False**；未改 3/1、未 FF、未部署

请 [@独立代码审核员](agent://aa01a41a-c3da-4021-9e45-a592ac77166c) **只读**复审精确 SHA `d50b0dc3a7b3721aba16ac4474530c1565be79de`。通过后开线性 FF 卡；禁止本卡自行 FF/重启。
