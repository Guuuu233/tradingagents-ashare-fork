# DAV-297 P1：全球外盘指数纳入分析（采集恢复 + 强制注入 + 落库）

**基线父提交：`346af8049ddd2a0d8c9c69f3147732de0537ec0a`。独立分支，不合主干。禁止改 `.env` / providers / role_bindings / 持久轮次。**

## 已坐实的缺口（Hermes 2026-08-22 宿主实测）

1. 采集链已有 `get_global_indices`（DataCollector + 宏观/基本面分析师），**不是“没做”**，是**生产路径全失败**：
   - 默认 vendor：`macro_market_data = cn_akshare,yfinance`
   - `cn_akshare.index_global_hist_em`：东财 hist `RemoteDisconnected`（9/9 指数全挂）
   - `yfinance.Ticker(^GSPC 等).history`：`YFRateLimitError Too Many Requests`
   - 失败后返回 `【数据获取失败】全球核心指数`；京东方报告 `6e8b20a6` 宏观正文 **0 次**出现标普/纳指/恒生/日经/DAX（LLM 静默省略失败块）
2. 同一时刻 **实时源可用**（禁止打印完整 payload，仅记结构）：
   - 新浪 `hq.sinajs.cn/list=int_dji,int_nasdaq,int_sp500,int_hangseng,int_nikkei`：HTTP 文本行情，含名称/点位/涨跌额/涨跌幅
   - 东财 `push2.eastmoney.com/api/qt/ulist.np/get` secids=`100.NDX,100.SPX,100.DJIA,100.N225,100.GDAXI,100.KS11,100.FTSE`：JSON `rc=0`，含 f2 最新价、f3 涨跌幅、f14 名称；韩国 KOSPI 在此源可用
   - 东财 hist kline（`push2his`）对本机仍 RemoteDisconnected，**不可作为主路径**
3. 覆盖缺口：现有 map 无 **KOSPI**；恒生科技仅 akshare 路径有、yfinance 路径无。
4. 落库缺口：`market_data_context` 只存 daily/realtime/fund_flow/provenance/ledger，**不持久化** `global_indices`/`cn_indices`/`major_assets` 原文，前端/审计看不到外盘表。

国内指数 `get_cn_indices` 与大类资产 `get_major_assets` **当前可用**（2026-08-21 上证 3905.20、美债 4.740%、LME 铜 14181.50），不要改坏。

## 目标

分析师报告必须基于**真实外盘点位/涨跌**做跨市场传导（美股→港股/中概→A股风险偏好；日韩欧作为亚太/欧洲风险温度计），失败则显式【数据缺失】，禁止静默省略。

## 指数清单（最小集，不得阉割）

| 市场 | 指数 | 必须 |
|---|---|---|
| 美国 | 标普500、纳斯达克综合（或纳指100，须标注口径）、道琼斯 | 是 |
| 中国香港 | 恒生指数、恒生科技 | 是（恒生科技允许缺失标注） |
| 日本 | 日经225 | 是 |
| 韩国 | KOSPI | 是（新增） |
| 欧洲 | 德国DAX、英国富时100；法国CAC40 尽量 | DAX+富时必须，CAC 允许缺失标注 |

## 数据契约

1. **优先级（新算法组）**：东财 ulist 实时 → 新浪 hq 实时 → 现有 `index_global_hist_em` → yfinance 历史。任一路成功即可；失败继续下一路。新浪/东财实时属 **session snapshot**，必须带 `period_kind=session_snapshot`、`retrieved_at`、`actual_as_of`（源日期，不得写成未来日）。
2. **防前视**：`actual_as_of <= trade_date`。若实时快照日期 > 分析日，丢弃该源，改走历史或【数据缺失】。
3. **历史日期**：优先 hist 接口；两条 hist 都失败时，不得用“今天”快照回填历史报告。
4. **部分成功**：至少 4/8 核心指数有真值则 `status=partial` 并列出缺失项；0 条才 `failed`。禁止“一条失败就整包失败”。
5. **字段**：名称、代码、最新点位、1d/5d/20d 涨跌幅（实时源若无 5d/20d 标【数据缺失】，保留 1d）、来源、as_of。
6. **单位/口径**：点位原值；涨跌幅百分数。纳指须在 markdown 标明“纳斯达克综合”或“纳斯达克100”，禁止混称。

## 代码改动（白名单）

- `tradingagents/dataflows/providers/cn_akshare_provider.py`：`get_global_indices` 增加东财 ulist + 新浪 hq 回退；补 KOSPI；单指数失败不拖垮整包。
- `tradingagents/dataflows/providers/yfinance_provider.py`：补 `^KS11`、`^HSTECH`（或等价代码）；捕获 `YFRateLimitError` 记 gap 不抛死。
- `tradingagents/dataflows/macro_market_utils.py`：markdown 增加韩/欧观察句；部分缺失行写【数据缺失】。
- `tradingagents/graph/data_collector.py`：`market_data_context` 增加 `global_indices`/`cn_indices`/`major_assets` 摘要（原文或截断+hash），provenance 必须有这三项。
- `tradingagents/agents/analysts/macro_analyst.py` 与 `zh.py` 宏观 prompt：强制引用外盘指数表；若失败块存在必须原样写出【数据缺失】，禁止改写为“外围平稳”。
- `tests/test_macro_market_dataflows.py` + 新增 `tests/test_global_indices_fallback.py`：
  - mock 东财 hist 失败 + ulist 成功 → 部分/全部指数有值
  - mock 全部失败 → 显式失败文案且不含臆造点位
  - as_of > trade_date 的快照被拒绝
  - KOSPI 出现在成功或缺失清单中
  - 回归：`get_cn_indices`/`get_major_assets` 既有用例仍绿

## 禁止

禁止改辩论轮次、分析师拓扑、`.env`、providers 表、role_bindings；禁止购买付费源；禁止把实时快照标成收盘序列；禁止 `_v2`。

## 验收

1. 宿主 `.venv310`：`pytest tests/test_macro_market_dataflows.py tests/test_macro_market_utils.py tests/test_global_indices_fallback.py -q` 全绿。
2. 定向：`route_to_vendor('get_global_indices', '2026-08-21')` 在本机应返回至少标普/纳指/恒生/日经之一的真值（若外部又挂，测试用 mock 锁行为；issue 评论附脱敏探针：成功指数名+as_of+source，禁止贴原始行情串）。
3. `compileall` + `git diff --check`。
4. 推送独立分支精确 SHA。不得合主干、不得 @项目调度助手。
