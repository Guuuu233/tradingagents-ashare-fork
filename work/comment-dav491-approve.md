Cursor 独立复审 DAV-491 / P1-4。

候选 SHA（完整 40 位）：`ca4747c8653afe1de83f410b666537e511a26b0f`
父提交：`6a799d460318acd9865584e80d9ad07b8e71df25`（线性，无 merge）
分支：`agent/dev2/p1-4-provider-red-lights`

隔离 worktree `/tmp/ta-p14-ca4747c` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_as_of.py \
  tests/test_financial_announce_cutoff.py::test_provider_historical_refuses_ths_fallback \
  tests/test_financial_announce_cutoff.py::test_fund_flow_requires_curr_date_and_oor_message \
  -q --tb=short
```

结果：**21 passed, 3 deselected in 0.38s**（0 failed）。不采信口头；本次为 Cursor 独立复跑。

相对主干 3 文件、+719/−3（fixture + `test_financial_as_of.py` + `pyproject.toml` network mark / 默认 `not network`）。未改产品 provider 实现、社交、脏文件。

契约核对：
- 三股票八接口默认走离线 fixture；`as_of` 非空且 ≤ curr_date
- 默认收集 deselected 网络 smoke；`-m network` 仍可收集到 3 条
- THS fallback 拒绝 + 资金流 OOR 既有钉仍绿；本卡追加 sina 失败拒绝测

残留（不挡合入）：未改 `cn_akshare_provider` 实网 SOCKS 根因（本卡以离线回归隔离为主，符合 brief）；全局 `addopts=-m 'not network'` 会影响全仓默认收集（仅一处 network mark，可接受）。

**准予合入** SHA `ca4747c8653afe1de83f410b666537e511a26b0f`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。** 不宣称案例已修。P1（至 P1-4）合入后可视为决策语义 P1 收口。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
