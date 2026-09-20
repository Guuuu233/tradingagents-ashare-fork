# P1：产业链 AkShare/yfinance 补 actual_as_of，失败状态 fail-closed

**基线：`2ee1fb8fdc1e4dc791b416fa6963f9a70055e703`。独立分支，不合主干。只允许一个 writer。**

## 真实报告证据

- 宁德时代 `cb95eee4`：Tushare LC.GFE 已完整 `requested_as_of=actual_as_of=2026-08-21`。
- 京东方 `c626ace1`：LME铜价 14181.5、苹果 309.35、三星 281500 均有数值，但 AkShare/yfinance 结果没有 `actual_as_of`，导致整体 provenance 只能 `actual_as_of=None + gap`。
- provider 失败路径从 `config.model_dump()` 继承 `status=active`，只置空 current_value，可能生成“无值但 active”的矛盾结构。

## 契约

1. `_fetch_lme_copper` AkShare 成功路径穿透 `_calculate_series_metrics.actual_as_of`，输出：`requested_as_of/actual_as_of/retrieved_at/transport_provider=akshare/api_name=futures_foreign_hist`。
2. `_fetch_yfinance_indicator` 成功路径同样输出日期与 provenance：`transport_provider=yfinance/api_name=history`。
3. 所有失败/空数据/无有效序列/异常路径必须显式：`status=unavailable`、`current_value=None`、`actual_as_of=None`、`category` 类型化；不得保留 `active`。
4. 成功路径必须 `actual_as_of <= requested_as_of`；违反时 fail-closed。
5. pending_api/manual 原有状态保持，不把它们伪装成 unavailable/active。
6. 不改图谱、不改 `.env`、用户配置、Tushare客户端、collector、主干。

## 白名单

- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tests/test_industry_linkage_provider.py`

## 验收

- AkShare/yfinance 成功真实日期穿透测试。
- yfinance empty/exception、AkShare+Tushare双失败均断言 `status=unavailable`。
- 周末请求不允许返回未来实际日期。
- `.venv310/bin/python -m pytest tests/test_industry_linkage_provider.py tests/test_industry_linkage.py -q`
- compileall、diff-check；推远端精确 SHA。
- 测试必须隔离宿主 TUSHARE_TOKEN，不能让真实付费回退破坏 mock 契约。

禁止 @项目调度助手。
