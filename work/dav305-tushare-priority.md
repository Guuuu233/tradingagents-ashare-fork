# DAV-305 P1：产业链/外盘优先走已付费 Tushare

**基线父提交：`49a40035248b18cff330a89f310868aabda87ee0`（DAV-302 fail-closed 交付，父为 `ab3cda62`）。在该分支上续作，不要从落后的 `ab3cda62` 另开导致丢 fail-closed。不合主干。禁止改 `.env` / 打印 token。**

## 用户纠正（必须遵守）

Tushare **是已付费数据源**，功能和数据较全。**有条件时优先 Tushare**。不得再把碳酸锂等标成「待接入/无付费源」。

## 已坐实（Hermes 2026-08-22 脱敏探针，token 在宿主 `.env`，走 `TUSHARE_API_URL`）

| 接口 | 结果 |
|---|---|
| trade_cal | code=0 |
| index_global SPX/IXIC/DJI/HSI/N225/KS11/GDAXI/FTSE | 有 20260820/21 真值 |
| fut_daily `LC.GFE` 碳酸锂主力 | 20260821 close=158680 |
| fut_daily `SI.GFE` 工业硅 / `PS.GFE` 多晶硅 / `CU.SHF` 沪铜 | 均有 15 行 |
| moneyflow_dc / moneyflow_ths | 京东方 20260821 各 1 行（资金流已接） |
| us_daily NVDA/TSM | **403 无权限** → 国际股价仍可 yfinance，失败标【数据缺失】 |
| SOX/NDX | 本账户 index_global 无这两码 |

产业链图谱 **162 指标：pending_api 68 / yfinance 54 / manual 38 / akshare 2 / tushare 0**。  
`IndustryLinkageProvider._fetch_indicator` 对 pending_api **直接返回空值、不调任何 API**。

现有 `_tushare_post` 已读 `TUSHARE_API_URL`（资金流）；产业链 provider **完全没接**。

## 范围（最小可验，禁止一次改 162 条）

1. **复用** `CnAkshareProvider._tushare_post` / URL+token 解析，或抽一小段共享客户端到现有文件（禁止新建平行 SDK）。Token 只读 env，禁止写进代码/评论。
2. `_fetch_indicator` 增加 `source=="tushare"` 分支：按 `symbol` 调对应 api（先支持 `fut_daily` 与 `index_global`），`trade_date<=as_of`，失败写【数据缺失】+ 原因分类（token/403/空行），**不得**改回 pending_api 静默。
3. 图谱**只改已探针成功的指标**（至少）：
   - 碳酸锂价格 / 电池级碳酸锂价格 → `fut_daily` `LC.GFE`
   - 多晶硅致密料价格（若该 name 存在）→ `PS.GFE`
   - 消费电子/有色里 LME铜价保持 akshare，可**额外**用 `CU.SHF` 作 tushare 备源（不要删 LME）
4. **本卡不要改** `get_global_indices` / `cn_akshare_provider.py`（DAV-301 资深开发1 正在改同一文件）。美股指数等 301 交付后再开补丁把 `index_global` 插到回退链最前。
5. 不接 us_daily（403）。manual 的 SIA 销售额等无接口的保持 manual+【数据缺失】。

## 白名单

- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tradingagents/dataflows/industry_linkage.py`（仅上述指标的 source/symbol/status）
- `tests/test_industry_linkage_provider.py`
- `tests/test_industry_linkage.py`（pending_api 碳酸锂断言改为 tushare mock）

## 验收

- 单测 mock：token 缺失 → 分类失败；LC.GFE 有 close → current_value 非空且 source=tushare；as_of 截断。
- 定向：`pytest tests/test_industry_linkage_provider.py tests/test_industry_linkage.py -q`
- 宿主探针（评论只写 api/code/n/as_of/source，**禁止贴 token 与完整行情**）：`fut_daily LC.GFE`、`index_global SPX`。
- compileall + git diff --check。推独立分支精确 SHA。不得 @项目调度助手。
