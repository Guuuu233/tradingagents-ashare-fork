# D-03-2 / C-09-3：collector 接入资金规模归一（不改分析师）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `98ba99f474723d0f98a323721918cc0fb5cb8d57`（或其线性后代）。脏宿主禁止当工作树。  
**一个关注点：** 在 `DataCollector`/`_fetch_all` 把已合入的 `calculate_fund_flow_scale_metrics` 接到 `fund_flow_context`（或并列键 `scale_metrics`）。分母来自已有 `_fetch_tushare_daily_basic`（registry `cn_akshare`，`as_of=trade_date`）。**不改** `smart_money_analyst.py`、不改纯计算函数语义、不新增 fetcher。  
**禁止：** frontend；H1b 补样本；FF/部署；push 主干；打印 token；真打 `api.tushare.pro`；跨股排名；开加权。本卡评论禁止 @独立代码审核员。

## 允许改

- `tradingagents/graph/data_collector.py`
- 新建 `tests/test_fund_flow_scale_collector.py`

不要改：`fund_flow_evidence.py`（除非签名对不上，先停下来问）、`cn_akshare_provider.py`、`smart_money_analyst.py`。

## 契约

1. 经 `_registry.get("cn_akshare")` 调 `_fetch_tushare_daily_basic(symbol, trade_date, as_of=trade_date)`。无 provider / token_missing / 失败：`scale_metrics` 显式缺口，不得填 0 比率。
2. `circ_mv` 官方单位**万元**（https://tushare.pro/document/2?doc_id=32）。`amount` **不得默认单位**；无显式 `amount_unit` 则只算 `net_to_circ_mv`（若 circ 可用），成交额占比记缺口。
3. 净额取现有个股资金流 evidence 的已选值+单位；净额缺失或 0 必须原样交给纯函数（0 是合法净额）。跨日/跨证券由纯函数拒算。
4. 输出挂到 `market_data_context["fund_flow_evidence"]` 旁，键名稳定（建议 `scale_metrics`）。禁止「强度排名」。
5. 测试 mock provider 与 `calculate_fund_flow_scale_metrics` 的真实调用（或真实纯函数+mock 数据）。覆盖：正常 circ 占比、amount 无单位时成交额占比缺失、daily_basic 失败、净额 0。禁止真打网。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_fund_flow_scale_collector.py tests/test_fund_flow_scale_metrics.py tests/test_tushare_daily_basic.py tests/test_h1b_gates.py
```

一个 commit，push 功能分支，评论 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
