# D-01 / C-04-2：Tushare raw `daily` + `dividend` 读取契约

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `309bdbcb9a9655a9fc29828484a66cefd5a336d9`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只封装私有网关对 Tushare **`daily`（未复权）** 与 **`dividend`** 的读取契约与单元测试。不接入 collector / 回测 / 报告业务流。不实现 PIT 复权引擎。不接入 `adj_factor`。不接 C-09-3 / `daily_basic` 规模归一。  
**禁止：** 打印或日志写入 token；直连 `api.tushare.pro`（只走现有 `TUSHARE_API_URL` / `TUSHARE_BASE_URL` / `_tushare_post*`）；改 `role_bindings`/`providers` 表；改分析师 / research_manager / frontend；H-05；D-02 口径接线；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **D-01**（C-04-2）。H 链已合入。DAV-628 评估稿 `work/2026-09-05-c04-pit-raw-dividend-eval.md` 已冻结：现网行情是 `vendor_qfq`；`daily`/`dividend` 网关矩阵标可用但**未接入**。本卡只做读取契约。D-02 价格链路接线是下一包。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（复用现有 `_tushare_post` / `_tushare_post_once` / 失败分类；按列名取数；缺列进 `missing`）
- `tests/test_tushare_raw_daily_contract.py`（新建）
- `tests/test_tushare_dividend_contract.py`（新建）

不要改 `data_collector.py`、`backtest_service.py`、`report_service.py`。不要新增 `adj_factor` API。

## 契约

1. **`daily`**：请求未复权日线。WANTED 至少按列名取 `ts_code, trade_date, open, high, low, close, pre_close, vol, amount`（评估稿未给完整官方字段表时，以网关实际返回列名为准，缺列进 `missing`，禁止 `iloc`）。不得带复权参数、不得把 qfq 结果标成 raw。`trade_date > as_of` 必须在发网前拒绝（`date_exceeds_as_of`）。无 token 不发网（`token_missing`）。
2. **`dividend`**：字段至少覆盖评估稿 3.1：`ts_code, end_date, ann_date, div_proc, stk_div, stk_bo_rate, cash_div, cash_div_tax, record_date, ex_date, pay_date, imp_ann_date`。空表必须显式上报，**不得**解释为「无分红」。`ann_date`（及实施相关日期）相对 `as_of` 的前视行必须剔除并记缺口，不得把未来预案喂给历史 as_of。
3. 失败类型与现网资金流/`daily_basic` 一致：timeout / 认证 / 限流 / 返回结构异常 / no_rows / missing_field，显式字符串，禁止空 DataFrame/空串冒充成功。
4. 测试必须 mock HTTP，禁止真实打网关。三个形状：正常返回、接口失败、返回结构异常。另加：空 dividend 表；daily 缺列；as_of 越界。
5. 本卡**不得**写 `price_basis=raw` 进回测缺省，不得宣称 PIT 已落地。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_tushare_raw_daily_contract.py tests/test_tushare_dividend_contract.py tests/test_tushare_daily_basic.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
