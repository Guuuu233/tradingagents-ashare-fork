# D-02-5 / C-04-3：collector 接入分红旁证（空表≠无分红；不落地 PIT）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `d4103af21c6ba19483716a74594b90af361f90a2`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 在 `DataCollector`/`_fetch_all` 接入已合入的 `CnAkshareProvider._fetch_tushare_dividend(..., as_of=trade_date)`，把**公司行动旁证**写成紧凑文本（或结构化小对象）放进采集结果。空表必须显式上报「空表，不得据此判断无分红」。**禁止**用分红去改日线收盘价、禁止宣称 `pit_raw`/`pit_adjusted` 已落地、禁止改回测缺省。  
**禁止：** 改 `role_bindings`/`providers` 表；改 frontend；C-09-3；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

D-01 已有读取契约；D-02-4 已合入回测同口径取价。本卡只接 collector 旁证。不要改 `_fetch_tushare_dividend` 本体（除非测试发现签名对不上，先停下来问）。

## 允许改

- `tradingagents/graph/data_collector.py`
- 新建 `tests/test_price_basis_dividend_collateral.py`

不要改：`cn_akshare_provider.py`、`backtest_service.py`、`report_service.py`、`core_stock_tools.py`。不要从 `tradingagents/` 导入 `api.services`。

## 契约

1. 经 `_registry.get("cn_akshare")` 调用已有 `_fetch_tushare_dividend`。`as_of` 必须是本次 `trade_date`（只向前，不填「今天」）。无 provider → 显式失败字符串，不得假装无分红。
2. 成功：按列名整理（`ann_date`/`ex_date`/`cash_div`/`stk_div` 等已有字段），**压缩文本**，禁止把原始 list[dict]/JSON 整表塞进 prompt 池。
3. `no_rows` / 空表：显式「空表，不得据此判断无分红」，不得写成「该公司不分红」。
4. `token_missing` / `transport` / `permission_denied` / `date_exceeds_as_of` / `missing_field`：沿用 D-01 分类，写成 `【数据获取失败】分红旁证 — 原因：…该项不可用`。
5. **不得**用旁证改 `stock_data` 价格或 `price_basis`。vendor_qfq 与 raw 两条采集路径都要挂旁证键（例如 `dividend_evidence`），但日线通道行为与 D-02-3 一致。
6. 测试 mock provider，禁止真打网。覆盖：正常压缩、空表、token_missing、无 provider、断言收盘价序列未被分红改写。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_dividend_collateral.py tests/test_tushare_dividend_contract.py tests/test_price_basis_collector_raw.py tests/test_h1b_gates.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
