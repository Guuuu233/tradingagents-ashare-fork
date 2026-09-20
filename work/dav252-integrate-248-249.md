# DAV-252 统一集成 DAV-248 + DAV-249 到独立组合树

## 精确输入

- 主干基线：`target/codex/dav-4-p2a-trunk@c956e43db0fea1fea88b85103d10218734b2b1c8`
- 候选A（DAV-249，终审 DAV-251 PASS）：`target/agent/worker1/01a02179@51092c93c6f5c97bdacbef568de6a39e1d2087b7`
  - 文件：`api/main.py`、`tests/test_job_lifecycle.py`
  - 父提交：`c956e43`
- 候选B（DAV-248，终审 DAV-250 PASS）：`target/fix/dav-248-fundflow-model-validation@51a314e891fc795844711713d9bf2b14a2aae785`
  - 文件：`tradingagents/dataflows/fund_flow_evidence.py`、`tests/test_fund_flow_evidence.py`、`tests/test_smart_money_fund_flow_semantics.py`
  - 父提交：`c956e43`
- 两提交文件不重叠，均可直接叠在 `c956e43` 上。

## 唯一 owner 与隔离

1. 必须 `multica repo checkout` 后进入返回 workdir，或新建独立 worktree。禁止改宿主主干。
2. 从 `c956e43` 建远端集成分支：`integration/dav248-dav249`。
3. 固定顺序：先合入 `51092c9`（astream 吞异常），再合入 `51a314e`（资金流误阻断）。可用 cherry-pick，禁止强推主干。
4. 冲突则停止并报告，不得手工改业务逻辑绕过。

## 组合树验证（宿主 `.venv310`，Python 3.10）

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest \
  tests/test_fund_flow_evidence.py \
  tests/test_smart_money_fund_flow_semantics.py \
  tests/test_job_lifecycle.py \
  tests/test_debate_state_persistence.py \
  tests/test_report_service_fund_flow.py -q
TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q
python -m compileall -q api tradingagents tests
git diff --check
```

分文件硬超时，禁止前台无限收集。

## 静态验收

- 组合 diff 只能包含上述 5 个文件；不得夹带 `.env`、providers、用户配置、Prompt。
- 父链必须是 `c956e43 → <249内容> → <248内容>`。
- 不部署、不合主干、不跑真实 LLM/3/3。

## 交付

推送 `target/integration/dav248-dav249` 精确 SHA、父链、changed files、RED/GREEN/全量结果。明确尚未进入主干、未部署。
