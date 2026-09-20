Cursor 独立复审 DAV-476 / P0-4a。

候选 SHA（完整 40 位）：`12120c705ab6eb69d2ecac0d669b4d465e5b1abb`
父提交：`049b7d46ca3bdabe25006e3986e4457771dde3fa`
分支：`agent/dev2/p0-4a-selection-not-consensus`

隔离 worktree `/tmp/ta-p04a-12120c7` + `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_fund_flow_evidence.py \
  tests/test_cn_akshare_backup_sources.py \
  tests/test_smart_money_fund_flow_semantics.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

结果：`101 passed in 1.44s`。不采信 Multica 口头 101。

相对主干 5 文件、+152/−52。白名单内。未改财报 Q2、prompt、社交、受保护脏文件。

契约：EM `r0_net` + THS `netamount` 同时有效 → `direction_allowed=false`、`reason_code=incomparable_field_semantics`；单源仍可放行。Provider 已停 `consensus=selection`。Tushare DC+THS 生产测试已锁。

残留（不挡合入）：`smart_money_analyst.py` 约 225 行仍把 `current_selection` 写进 `fund_flow_evidence["consensus"]`；返回的 guard 已 `pop("consensus")`。报告层优先读 `selection`。后续可清，本卡方向闸已成立。

**准予合入** SHA `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
