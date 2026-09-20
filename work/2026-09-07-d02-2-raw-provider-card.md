# D-02-2 / C-04-3：provider 可选 raw 日线通道（缺省仍 vendor_qfq）

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `fbdc598ef527ea01fba7f6072f600bd557762715`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 在 `CnAkshareProvider` 上把已合入的 `_fetch_tushare_raw_daily` 接到 **显式** `price_basis=raw` 的行情读取；**缺省路径必须仍走现有 qfq 链**（eastmoney/sina/tencent `adjust="qfq"`），并继续标成 `vendor_qfq`。不接 collector、不接回测缺省、不接 dividend 旁证、不实现 PIT 引擎。  
**禁止：** 改 `role_bindings`/`providers` 表；改分析师 / frontend / `data_collector.py` / `backtest_service.py` 缺省；C-09-3；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`（测试 mock HTTP）。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **D-02 第二刀**（provider 接线）。D-02-1 只冻结标签；D-01 只冻结读取契约。下一刀才是 collector。本卡不得把全站行情切成 raw。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（`get_stock_data` / `_fetch_hist_df` 原路径增加可选 `price_basis`；按列名；失败显式上报）
- 新建 `tests/test_price_basis_raw_provider.py`
- 如需同步标签常量，可 import `api.services.price_basis_labels`（或 provider 内只用短标签字符串 `raw` / `vendor_qfq`，禁止把 qfq DataFrame 标 `raw`）

不要改：`data_collector.py`、`backtest_service.py`、`report_service.py`、`calibration_service.py`、`_fetch_tushare_dividend` 业务接线。

## 契约

1. **缺省**：`get_stock_data(symbol, start, end)` 行为与合入前一致（qfq 链）。返回文本或元数据必须能表明口径是 `vendor_qfq`，不得写成 `raw`。
2. **显式 raw**：`price_basis="raw"`（或同名 kw-only 参数）才走 `_fetch_tushare_raw_daily`。无 token / 结构异常 / `date_exceeds_as_of` / `no_rows` / `missing` 必须沿用 D-01 失败类型，禁止空串冒充成功，禁止回退到 qfq 还声称 raw。
3. **禁止**：`price_basis="pit_raw"` / `"pit_adjusted"` 本卡实现为可用通道；未知标签失败闭合（可复用 `UnknownPriceBasisError`），不得默默 qfq。
4. 测试 mock HTTP，禁止真打网关。形状：缺省 qfq 仍走 ak 链（mock ak，不断言 tushare daily）；raw 走 tushare daily mock；raw 失败不得落到 qfq。
5. 本卡**不得**改回测 `_run_single_analysis` 缺省，不得宣称 PIT 已落地。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_raw_provider.py tests/test_tushare_raw_daily_contract.py tests/test_price_basis_pipeline.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
