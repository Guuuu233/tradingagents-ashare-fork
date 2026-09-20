# D-03-1 / C-09-3：资金规模归一纯计算（不接线 collector）

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `30e17a46688b3c8f392ad265d96e858ebfa52886`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 在 `fund_flow_evidence.py` 增加**纯函数**规模归一：具名比率「净额/流通市值」「净额/成交额」。记录分母来源与单位。零/负分母拒算。跨交易日或跨证券拒算。**本卡不改 collector、不新增 fetcher、不打网。**  
**禁止：** 改 `role_bindings`/`providers` 表；frontend；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`；开启加权。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

D-02 价格链已合入。C-09-2 已有 `_fetch_tushare_daily_basic`。本卡只做计算。collector 接线是 **D-03-2**，禁止本卡顺手接上。

官方 `daily_basic`（https://tushare.pro/document/2?doc_id=32）：`circ_mv`/`total_mv` 单位是**万元**。同页**未列出** `amount` 字段；仓库 C-09-2 虽要求 `amount`，**不得默认它是万元或千元**。调用方必须显式传入每个量的 `unit`。单位缺失：成交额占比拒算，流通市值占比仍可算（若 `circ_mv` 带来源与单位）。资金净额沿用现有 evidence 层已换算口径（常见：上游万元 → 证据亿元）；**禁止**把多源一致度当影响大小，禁止跨股强度排名。

## 允许改

- `tradingagents/dataflows/fund_flow_evidence.py`
- 新建 `tests/test_fund_flow_scale_metrics.py`

不要改：`data_collector.py`、`cn_akshare_provider.py`、`smart_money_analyst.py`、frontend。回归 `tests/test_tushare_daily_basic.py` 必须仍绿（本卡不应改它除非发现纯导入断裂，先停下来问）。

## 契约

1. 输入至少：`ts_code`、`trade_date`、净额+单位、分母（`circ_mv` 和/或成交额）+单位。同日同证券才可算。
2. 先换到同一货币单位再相除。错单位拒绝或只允许**显式**换算表（万元/亿元/元）。禁止 float 默认填 0。
3. 分母 0 或负：该比率拒算并记录缺口；可保留绝对净额。缺市值时允许只出成交额占比，二者不得当同一量纲互相替代。
4. 输出具名字段（例如 `net_to_circ_mv`、`net_to_amount`）+ `denominator_source` + `unit` + 缺口列表。不要写「主力强度排名」。
5. 测试：同单位可复算；错单位拒绝；跨日/跨证券拒绝；零分母拒算；缺 `amount` 单位时成交额占比缺失但市值占比仍在。全部用 fixture，禁止真打网。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_fund_flow_scale_metrics.py tests/test_tushare_daily_basic.py tests/test_h1b_gates.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
