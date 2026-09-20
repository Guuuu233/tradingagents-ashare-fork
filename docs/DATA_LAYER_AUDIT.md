# TradingAgents-AShare 数据层探查与审计报告 (DATA_LAYER_AUDIT.md)

---

## 1. 目录结构与数据模块 (Directory Structure & Data Modules)

```
tradingagents/
├── dataflows/                          # 数据流核心模块
│   ├── interface.py                    # 数据获取统一路由入口 (Vendor Router)
│   ├── config.py                       # 数据源配置解析
│   ├── trade_calendar.py               # 交易日历工具
│   ├── stockstats_utils.py             # 技术指标计算库 (Stockstats 封装)
│   └── providers/                      # 适配器框架目录
│       ├── base.py                     # BaseProvider & DataResult 抽象基类
│       ├── registry.py                 # Provider 注册中心
│       ├── cn_akshare_provider.py      # AKShare 数据源适配器 (A股主力)
│       ├── cn_baostock_provider.py     # BaoStock 数据源适配器 (行情备选)
│       ├── cn_investoday_provider.py   # Investoday 实时行情与新闻适配器
│       ├── yfinance_provider.py        # YFinance 全球行情适配器
│       └── alpha_vantage_provider.py   # AlphaVantage 国际行情/指标适配器
└── agents/utils/                       # 工具封装与绑定层
    ├── core_stock_tools.py             # K线行情工具封装
    ├── technical_indicators_tools.py   # 技术指标工具封装
    ├── fundamental_data_tools.py       # 财务基本面工具封装
    └── news_data_tools.py              # 新闻与舆情工具封装
```

---

## 2. 数据源适配层 (Adapter & Routing Layer)

- **路由入口**: `tradingagents/dataflows/interface.py`
  - `route_to_vendor(method, *args, **kwargs)`：支持 Vendor 降级链（`configured -> fallback_chain`）。
- **统一抽象层**: `tradingagents/dataflows/providers/base.py`
  - `BaseProvider` 定义抽象基类与通用缓存逻辑。
  - `DataResult` 结构封装 `ok`, `data`, `as_of`, `source`, `error`, `stale`。
- **注册中心**: `tradingagents/dataflows/providers/registry.py`
  - `ProviderRegistry` 管理多 Vendor 实例。

---

## 3. 工具注册层 (Tool Registration Layer)

- 工具采用 LangChain `@tool` 装饰器定义在 `tradingagents/agents/utils/*.py` 文件中。
- `TOOLS_CATEGORIES`（位于 `tradingagents/dataflows/interface.py:7`）按类别划分工具：
  - `core_stock_apis`: `get_stock_data`
  - `technical_indicators`: `get_indicators`
  - `fundamental_data`: `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement`
  - `news_data`: `get_news`, `get_global_news`, `get_insider_transactions`
  - `cn_market_data`: `get_board_fund_flow`, `get_individual_fund_flow`, `get_lhb_detail`, `get_zt_pool`, `get_hot_stocks_xq`

---

## 4. Agent 与工具的绑定关系 (Agent-to-Tool Bindings)

位于 `tradingagents/graph/data_collector.py` 与 6 大分析师节点：

| 分析师角色 | 绑定的数据工具方法 |
|---|---|
| **基本面分析师 (fundamentals)** | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` |
| **技术与市场分析师 (market)** | `get_stock_data`, `get_indicators` |
| **新闻事件分析师 (news)** | `get_news`, `get_global_news` |
| **情绪与舆情分析师 (social)** | `get_hot_stocks_xq`, `get_news` |
| **宏观经济分析师 (macro)** | `get_global_news` |
| **主力资金分析师 (smart_money)** | `get_board_fund_flow`, `get_individual_fund_flow`, `get_lhb_detail`, `get_zt_pool` |

---

## 5. 配置层 (Configuration Layer)

- 配置文件：`tradingagents/default_config.py` 与 `.env`
- 配置读取器：`tradingagents/dataflows/config.py`
- 控制项：`data_vendors`（设置分类默认 Provider），`tool_vendors`（设置特定工具的 Provider 映射）。

---

## 6. 缓存机制 (Caching Mechanism)

- **缓存协议**: `BaseProvider` 支持 TTL 文件/内存缓存。
- **并发与限流**: `cn_akshare_provider.py` 内内置 `AKShareSemaphore` 信号量限制（默认 5 并发），防止频繁请求触发 AKShare / 新浪封禁。

---

## 7. `trade_date` 历史截断审查 (Trade Date Truncation Audit)

1. **行情数据 (`get_stock_data`)**: 通过 `_slice_hist_df(df, start_date, end_date)` 在 `cn_akshare_provider.py` 中实现了按 `end_date` 严格截断。
2. **高管增减持 (`get_insider_transactions`)**: 通过 `curr_date` 过去 180 天时间窗口实现了日期截断。
3. **新闻数据 (`get_news`)**: 通过 `start_date` / `end_date` 过滤新闻发布时间。
4. **财务报表数据**: Sina 财报接口按报告期组织，需在框架升级中引入基于**公告日（Disclosure Date）**的二重截断过滤。

---

## 8. 当前接入数据源与字段覆盖 (Data Sources & Coverage)

- **AKShare (`cn_akshare`)**: A股日线/周线/月线 K线、三大财务报表、资金流向（行业/个股）、龙虎榜明细、涨停池、雪球热股榜、高管增减持。
- **BaoStock (`cn_baostock`)**: 行情与复权数据备选。
- **Investoday (`cn_investoday`)**: 实时行情与快讯数据。
- **YFinance / AlphaVantage**: 全球市场与美股/港股行情备选。

---

## 9. 依赖依赖检查 (Dependencies Check)

在 `pyproject.toml` 中已有依赖：
- `akshare >= 1.16.80`
- `baostock >= 0.9.1`
- `yfinance >= 0.2.63`
- `stockstats >= 0.6.5`
- `pandas >= 2.3.0`
- `requests >= 2.32.4`
