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

## 前向代理不得绕过护栏（DAV-1818）

护栏放行 loopback（`127.0.0.1` 等）以便本地服务（sentinel、FastAPI TestClient、
AF_UNIX、用例自建的本地 TCP 服务）正常工作。但**前向代理是一个出网通道，不是本地服务**：
一旦设置了 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（大小写任一形式），`requests`/urllib3
会把目的地改写成代理端点；若代理监听在 loopback（Clash/mihomo 的常见形态，如 `:7897`），
旧护栏会把它当成「本地」而放行 —— 请求实际出网、拒绝逻辑根本不触发，调用方于是把代理
自己的响应当成结果（实测表现为 `'403' == 'network_denied'` 或 `'network_error'`）。

现行为：**目的地与环境变量中配置的代理端点一致时一律拒绝**，即便它在 loopback 上。
判定实时读取环境（不缓存），因此用例中途 `monkeypatch.setenv`/`delenv` 代理变量都会即时生效；
发往其它 loopback 端口的合法本地流量不受影响。代理命中时错误信息会追加
`(forward proxy configured in env)` 以便归因。

回归用例：`tests/test_tushare_gateway_denial_termination.py::TestForwardProxyCannotBypassGuardrail`
（含 `HTTPS_PROXY` 下设代理时类别仍为 `network_denied`）。

> 注：选用「让护栏拒绝发往代理的连接」而非「会话开始清除代理变量」，理由是后者无法满足
> 「在设有 `HTTPS_PROXY` 的环境下拒绝类别仍为 `network_denied`」这一验收 —— 用例自身会在
> 会话启动之后设置代理，清除只作用于进程启动时刻。

新增真实外网用例时必须打 `@pytest.mark.network`（marker 已在 `pyproject.toml` 注册）；纯 mock 用例不得打标，否则会被错误地踢出标准回归。
