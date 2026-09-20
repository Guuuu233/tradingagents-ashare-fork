DAV-119 final compact handoff

Baseline: remote branch agent/2/dav119-review3 @ 103ead0c1d480b8372af91e2879e73c7f4f9f51a.

Scope: only fund_flow_evidence.py, cn_akshare_provider.py, smart_money_analyst.py. No tests/new files/config/user settings/provider credentials.

Correction to latest acceptance: Sina App has no verified public fund-flow endpoint; do not guess or reverse-engineer it. Treat App screenshot as manual calibration only. Automated new-algorithm consensus may use independently verifiable Eastmoney + THS only when same field/date/window/unit are comparable. Sina Web MoneyFlow remains legacy_web_algorithm. MCP is non-blocking typed gap.

Required: (1) preserve typed gap/manual observation when App cannot be reproduced; (2) strict raw-unit/date/window/period/duplicate validation; (3) EM invalid/empty result continues fallback; (4) buffer smart-money stream until guard; (5) downstream guard remains deterministic; (6) tests must use existing fixtures/call paths, not only helper consensus.

Run: env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_fund_flow_evidence.py tests/test_smart_money_fund_flow_semantics.py tests/test_cn_akshare_backup_sources.py tests/test_sina_historical_fund_flow.py tests/test_take_latest_ordering.py; env -u PYTHONPATH .venv310/bin/python -m compileall -q tradingagents/dataflows/fund_flow_evidence.py tradingagents/dataflows/providers/cn_akshare_provider.py tradingagents/agents/analysts/smart_money_analyst.py; git diff --check.

Deliver a new pushed branch/SHA, exact tests, and redacted evidence.
