# TradingAgents-AShare 数据源与适配器指南 (PROVIDERS.md)

本文档说明 `TradingAgents-AShare` 接入的数据源、数据字段、时间截断规则与异常容错说明。

---

## 1. 数据源概览

系统主路径使用免费开源的 **AKShare** 接口作为数据支撑，无积分与付费限制；同时具备 BaoStock、Investoday、YFinance、AlphaVantage 等降级备选链。

| 数据分类 (Category) | 数据项 | 接口方法 | 主选数据源 | 关键字段 | 更新频率 |
|---|---|---|---|---|---|
| `core_stock_apis` | K线行情 | `get_stock_data` | `akshare.stock_zh_a_hist` | 日期/开/高/低/收/成交量/成交额/换手率 | 日频/盘后 |
| `technical_indicators` | 技术指标 | `get_indicators` | `stockstats` (本地向量计算) | SMA50/SMA200/EMA10/RSI/MACD/Boll/ATR/VWMA | 日频 |
| `fundamental_data` | 财务三大报表 | `get_balance_sheet`, `get_cashflow`, `get_income_statement` | `akshare` / Sina 财报 | 资产负债表、现金流量表、利润表 | 季报/半年报/年报 |
| `news_data` | 剔除未披露新闻 | `get_news`, `get_global_news` | `akshare` / Investoday | 新闻标题、发布时间、摘要 | 实时/分钟级 |
| `cn_market_data` | 筹码与资金 | `get_shareholder_count`, `get_margin_trading`, `get_northbound_flow`, `get_lhb_detail`, `get_zt_pool` | `akshare` | 股东户数、融资融券余额、北向持股占比、龙虎榜席位 | 每日/季度 |
| `institutional_risk` | 制度性风险 | `get_restricted_release`, `get_share_pledge`, `get_earnings_forecast` | `akshare` | 解禁日期与比例、大股东质押率、业绩预警 | 每日/动态 |

---

## 2. 数据容错与显式表达 (Data Result Pattern)

所有数据接口在获取失败或为空时，**绝不返回空字符串或掩码**，统一通过 `DataResult.to_prompt()` 输出显式的处理说明，避免 LLM 基于记忆幻觉编造数据：

- **获取失败**: 格式如 `【数据获取失败】解禁风险 — 原因：接口超时 (来源: akshare.stock_restricted_release_detail_em)。该项分析不可用，请在报告中标注"解禁风险未排查"，不要基于记忆推测。`
- **排查结果正常/空记录**: 格式如 `【数据排查结果】解禁风险 — 暂无相关事件/无触发记录。该项排查结果：已排查，无异常或未触发风险记录。`

---

## 3. 历史日期截断 (Trade Date Truncation)

为确保历史分析时不引入未来信息：
1. **行情数据**: 按 `end_date` (即 `trade_date`) 严格截断 DataFrame 行。
2. **财务报表与公告**: 按公告日 `announcement_date <= trade_date` 过滤，避免未来报表超前泄露。
3. **新闻与高管变动**: 按 `发布时间 <= trade_date` 过滤。

---

## 4. 依赖项与配置

- 依赖库：`akshare >= 1.16.80`, `baostock >= 0.9.1`, `pandas >= 2.3.0`
- 配置文件：`.env` 及 `tradingagents/default_config.py`
