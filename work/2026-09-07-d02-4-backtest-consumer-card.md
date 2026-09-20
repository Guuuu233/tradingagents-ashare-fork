# D-02-4 / C-04-3：回测收益消费者按分析口径取价（缺省仍 vendor_qfq）

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `166b705b31a60e994fe6330555b6dd05563db4c7`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 回测进出场价格必须与该条分析声明的 `price_basis` **同一口径**。缺省仍 `vendor_qfq`（现有 `route_to_vendor("get_stock_data")`）。仅当分析显式 `price_basis="raw"` 时，进出场价走 `cn_akshare.get_stock_data(..., price_basis="raw")`。不接 dividend、不实现 PIT 引擎、不改 collector/provider 缺省、不改分析师。  
**禁止：** 改 `role_bindings`/`providers` 表；改 frontend；C-09-3；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

D-02-3 已在主干：collector 显式 raw。本卡只修收益消费者。当前 `_get_price_after` / `_get_price_on` **无条件** `route_to_vendor`，会把声明 raw 的分析用 qfq 算出收益。

`backtest_service.PRICE_BASIS_UNSPECIFIED == "unspecified"` 与 `report_service.PRICE_BASIS_UNSPECIFIED == "price_basis.unspecified"` 不要在本卡「统一」字符串。本卡只处理短标签 `vendor_qfq` / `raw`。`pit_raw` / `pit_adjusted` / 未知：进出场价必须失败闭合（记录不可用、不得 silently 用 qfq 还声称该口径）。

## 允许改

- `api/services/backtest_service.py`
- 扩展 `tests/test_backtest_calibration_isolation.py` **或** 新建 `tests/test_price_basis_backtest_consumer.py`

不要改：`cn_akshare_provider.py`、`data_collector.py`、`report_service.py`（报告 cohort 缺省仍 `price_basis.unspecified`）、`core_stock_tools.py`、calibration 缺省。不要接 `_fetch_tushare_dividend`。不要从 `tradingagents/` 导入 `api.services`。

## 契约

1. **缺省 / `vendor_qfq`：** `_get_price_after` / `_get_price_on` 仍走现有 vendor `get_stock_data`，不得把结果标成 raw。
2. **显式 raw：** 必须调用 `cn_akshare.get_stock_data(..., price_basis="raw")`（经 registry 或等价直调，禁止其它未改签名的 vendor `TypeError`）。raw 失败 → 该样本进出场价不可用（沿用现有 None/skip 语义），**禁止**回退 qfq 还声称 raw。
3. `_run_single_analysis` 缺省仍 `vendor_qfq`；已有 `test_single_analysis_preserves_explicit_price_basis` 不得破坏。
4. 禁止缩短 `hold_days`（D-009）。测试 mock，禁止真打网。
5. 形状：缺省路径不调用 raw；raw 路径调用带 `price_basis="raw"`；raw 失败不回退；显式 raw 的 return/exit 不得来自 vendor qfq。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_backtest_consumer.py tests/test_backtest_calibration_isolation.py tests/test_price_basis_pipeline.py tests/test_h1b_gates.py
```

若未新建 consumer 文件，把该文件从命令里去掉，但仍须覆盖上述契约。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
