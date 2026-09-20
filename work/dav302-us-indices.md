# DAV-302 P1：美股三大指数 1 日真值（新浪 int_* + 时区 as_of）

**基线父提交：`ab3cda62bc676562c9421f809c0f1fa95d62f34e`。独立分支，不合主干。**

## 已坐实

京东方 `81f63cad` 宏观：「美股三大指数点位【数据缺失】」。  
`get_global_indices('2026-08-21')` 有恒生/日经/KOSPI/DAX/富时/CAC，**标普/纳指/道指为 0**。

根因：
1. `_fetch_global_indices_sina_hq` 使用 `gb_inx/gb_ixic/gb_dji`。Hermes 实测可用：`int_dji,int_nasdaq,int_sp500`（字段：名称,点位,涨跌额,涨跌幅）。
2. 东财 ulist 的 SPX/DJIA/NDX 若 `as_of` 被标成次日（美股收盘在北京时间次日），会被 `as_of > curr_date` 丢弃。应对：美股 as_of 用**美股交易日**（US/Eastern 收盘日），再与 `curr_date`（A 股分析日）比较——允许美股最近一个已收盘日 ≤ 分析日；禁止用分析日之后的美股 session。

## 契约

1. 优先新浪 `int_dji`/`int_nasdaq`/`int_sp500`；东财 ulist 的 SPX/DJIA 作第二源。纳指必须标明「纳斯达克综合」或「纳斯达克100」，禁止混称。
2. 至少标普+纳指+道指 **1d 点位+涨跌幅** 有真值；5d/20d 仍可【数据缺失】。
3. 港/日/韩/欧现网真值不得回退。
4. 部分成功：美股 3/3 或 2/3 即可 `partial/available`，禁止因美股失败把整包打成 failed。
5. 防前视：不得写入分析日之后的收盘。

## 白名单

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_global_indices_fallback.py`（补 sina int_* mock + as_of 时区）

## 验收

`pytest tests/test_global_indices_fallback.py tests/test_macro_market_utils.py -q`  
宿主定向（脱敏）：`route_to_vendor('get_global_indices','2026-08-21')` 评论只写成功指数名+as_of+source，禁止贴行情串。  
compileall + git diff --check。推独立分支精确 SHA。不得 @项目调度助手。
