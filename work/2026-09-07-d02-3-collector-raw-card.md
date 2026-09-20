# D-02-3 / C-04-3：collector 显式 raw 行情接线（缺省仍 vendor 链）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `f9be5c6f7aa139c01a87d792d74d0c3470714d88`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 让 `data_collector` 在**显式** `price_basis=raw` 时调用已合入的 `CnAkshareProvider.get_stock_data(..., price_basis="raw")`；**缺省**仍走现有 `get_stock_data` vendor 链（qfq/`vendor_qfq`）。不接 dividend 旁证、不改回测缺省、不实现 PIT 引擎、不改分析师。  
**禁止：** 改 `role_bindings`/`providers` 表；改 frontend；C-09-3；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

D-02-2 已在主干：provider 可选 raw。本卡只接 collector。不要改 `cn_akshare_provider.py`（除非测试发现签名对不上，先停下来问）。

`get`/`collect` 缓存键目前是 ticker+date。若 raw 与 qfq 共用同一 cache key，会串口径。必须把 `price_basis` 纳入 cache key，或 raw 路径禁止写入与缺省混用的 cache。

## 允许改

- `tradingagents/graph/data_collector.py`
- 新建 `tests/test_price_basis_collector_raw.py`

不要改：`cn_akshare_provider.py`、`backtest_service.py`、`report_service.py`、`core_stock_tools.py`（缺省继续走现有 tool；raw 走 registry 里的 `cn_akshare` provider 直调，避免未改签名的其它 vendor `TypeError`）。不要接 `_fetch_tushare_dividend`。

## 契约

1. **缺省**：不传或 `vendor_qfq` → 现有 `get_stock_data(symbol, start, end)`，不得把结果标成 `raw`。
2. **显式 raw**：仅 `price_basis="raw"` 时直调 `cn_akshare.get_stock_data(..., price_basis="raw")`。无 cn_akshare / raw 失败必须显式失败字符串（沿用 D-01/D-02-2 类型），**禁止**回退 vendor qfq 还声称 raw。
3. `pit_raw` / `pit_adjusted` / 未知标签：失败闭合，不得当 raw 或 qfq。
4. 测试 mock provider，禁止真打网。形状：缺省不调用 raw；raw 调用带 `price_basis="raw"`；raw 失败不回退；cache 不串口径。
5. 不得改 `_run_single_analysis` 缺省，不得宣称 PIT 已落地。分析入口若尚未传 `price_basis`，缺省行为必须与合入前一致。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_collector_raw.py tests/test_price_basis_raw_provider.py tests/test_h1b_gates.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
