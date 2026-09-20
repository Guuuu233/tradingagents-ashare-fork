# Cursor 独立验收（DAV-465 / P0-2a）

独立核验远端 `origin/agent/dev2/p0-2a-cached-at` @ `bb39ad4bfab3e715fcefd610a4fee3172470ed26`（基线 `4fa76815d5aa7d1cfab9942c8f9a9606034c279d`）。

- 文件范围：仅 `tradingagents/graph/data_collector.py`、`tests/test_data_collector.py`
- `_extract_source_as_of` 已删除 `cached_at` 回退；解析失败打 warning，不再 `except Exception: pass`
- 宿主隔离 worktree + `.venv310`（proxies unset）：`test_data_collector.py` + `test_financial_as_of.py`（排除 live smoke）+ `test_industry_linkage_dataflows.py` → **62 passed**
- live smoke 3 failed 与主干 `4fa7681` 相同（SOCKS 依赖缺失），不记为本卡回归

**准予合入** SHA `bb39ad4bfab3e715fcefd610a4fee3172470ed26` 线性 FF 到 `codex/dav-4-p2a-trunk`。

不准予部署。不要开部署卡，不要重启生产。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
