## 准予合入（不准予部署）

**候选 SHA：** `b9de29e2605d29a2809ad306f9c7900fc46a87eb`  
**分支：** `origin/agent/1/66356630f34c`  
**第一父：** `b9e7238376e2ba74f1e3510f90f3b5a27dd8c08c`（当前主干 tip）  
**DAV-647：** ✅通过

**白名单：** `tradingagents/dataflows/news_event_evidence.py`、`tests/test_news_event_collateral_attach.py`（+1517 / −34）

**Cursor 隔离：** `/tmp/ta-iso-b9de29e` detached `b9de29e`  
`env -u PYTHONPATH .venv310/bin/python -m pytest -q --tb=short`  
`tests/test_news_event_collateral_attach.py`  
`tests/test_news_event_coverage.py`  
`tests/test_tushare_forecast_collateral.py`  
`tests/test_tushare_repurchase_collateral.py`  
`tests/test_tushare_disclosure_date_collateral.py`  
→ **91 passed** in 0.55s

可线性 FF 到 `codex/dav-4-p2a-trunk`。禁止 merge 改写、禁止部署、禁止接线 `data_collector`。
