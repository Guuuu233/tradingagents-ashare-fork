# Cursor 独立验收（DAV-469 / P0-2b 链）

独立核验远端 `origin/agent/dev2/p0-2b-unverified-as-of` @ `6ea84afa21a3ef9bcbc9f82055dccaa3b8273aa5`（祖先 `c10fed8` ← `80c099f` ← 主干 `bb39ad4`）。

- 文件范围相对主干：`data_collector.py`、`evidence_verifier.py`、`test_data_collector.py`、`test_ohlcv_fail_closed_verdict.py`、`test_evidence_verifier_fairness.py`、golden 两例期望
- A1 保留：`available_unverified_as_of` + `provenance_status=unverified`，不是【数据获取失败】
- 报告路径 High 已堵：unverified fundamentals + `fundamentals_report` 含同一数值 → `unsupported`，不是 `verified`
- 3.4 `is_forward_scenario` 已删除；`净利润预期为 10 元` / `目标净利润 10 元` 在 verified 源上为 `contradicted`
- 隔离 worktree + `.venv310`：相关套件 **116 passed, 3 deselected**

**准予合入** SHA `6ea84afa21a3ef9bcbc9f82055dccaa3b8273aa5` 线性 FF 到 `codex/dav-4-p2a-trunk`。

不准予部署。不要开部署卡，不要重启生产。不要 FF `80c099f` 或 `c10fed8`。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
