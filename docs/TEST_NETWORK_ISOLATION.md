# 测试网络隔离（DAV-1768）

测试套件默认零真实外网：`tests/conftest.py` 的双层离线护栏（socket-patch 层 + PEP 578 审计钩子层 + curl_cffi 守卫）会拦截一切非本地外连，`pyproject.toml` 的 `addopts = "-m 'not network'"` 让标准回归自动跳过 `network` 标记用例。两条固定口径如下。

## 标准回归（默认，离线）

```bash
pytest -q -p no:randomly
# addopts 已自动 -m 'not network'，无需额外参数。
# DAV-979 主干既有死锁用例需按门禁口径追加：
#   --deselect tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields
```

## 联网回归（单独跑 network 桶）

```bash
pytest -m network --allow-network
# --allow-network 是解封离线护栏的唯一开关（tests/conftest.py::pytest_configure）。
# 裸 pytest -m network（不带 --allow-network）会被护栏拒绝，marked 用例直接抛
# OfflineTestGuardrailError —— 这是设计行为（fail-closed），不是 bug。
```

## 双门控用例

既需要 `network` 标记、又需要显式环境变量才会真正执行的用例（如
`tests/test_provider_date_guards.py::test_two_historical_collects_differ_in_date_upper_bound`，
真实 `_fetch_all` e2e）：

```bash
RUN_LIVE_DATA_TESTS=1 pytest -m network --allow-network
```

两个门控缺一不可：缺 `RUN_LIVE_DATA_TESTS=1` → 用例自行 skip；缺 `--allow-network` → 护栏拦截。

## 现有 network 标记用例（截至 DAV-1768，共 6 例）

- `tests/test_financial_as_of.py::test_network_smoke_three_tickers_eight_interfaces_as_of`（3 参数）
- `tests/test_offline_network_guardrail.py::TestGuardrailControlsAndMarkerExemption::test_network_marker_exempts_test_from_guardrail`（护栏自检）
- `tests/test_provider_date_guards.py::test_two_historical_collects_differ_in_date_upper_bound`（双门控）
- `tests/test_v03_return_measure.py::test_p0_real_provider_verifiable_metadata`

新增真实外网用例时必须打 `@pytest.mark.network`（marker 已在 `pyproject.toml` 注册）；纯 mock 用例不得打标，否则会被错误地踢出标准回归。
