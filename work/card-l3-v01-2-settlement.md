**实施卡 L3 / V-01-2 真实结算管道（总工派工，2026-09-12）。**

## 基线

- 精确基线：`70b5b47bcff0618e0db1258a438b0875440d4ac5`
- 第一父 = 基线，隔离分支开工
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（**绝对路径**；worktree 内无 `.venv310`）

## 总工已定的契约决策（不再征询）

**分红/送转按「结果字段」实现，不新增 `OutcomeStatus` 枚举值。**

理由：`return_labels.py` 现有 `OutcomeStatus` 为 `PENDING_DUE / SUSPENSION / DATA_MISSING / PROVIDER_FAILURE / UNSUPPORTED_PRICE_BASIS / UNEXECUTABLE_ENTRY / EVALUATED_OK` 七值；另有 `ReturnType` 枚举 `price_return / total_return`，且 `HorizonReturnResult` 已定义 `cash_dividend_total` / `split_ratio_total` 字段（当前空置）。**加/不加分红是「算哪种收益」的问题，不是「这笔标签是否可用」的问题**——因此落在 `ReturnType` 与结果字段上，语义分层正确。**禁止新增 `CASH_DIVIDEND` / `SPLIT` 之类的状态值。**

## 现状（DAV-823 审计 + 总工复核，勿重复调查）

| 事实 | 证据 |
|---|---|
| V-01-1 纯契约已交付 | `return_labels.py` 自述「Pure functions and data structures only: zero network, zero side effects, zero DB access」「Does not fetch prices, does not calculate returns, does not evaluate suspensions or limit moves」 |
| 核心计算刻意空缺 | `resolve_horizon_return_label` 未实现 |
| 旧路径仍在用 | `api/services/backtest_service.py::_get_price_after` 仍用 `iloc[hold_days - 1]` 行数切片，**与真实 T+N 交易日历在节假日/停牌/缺行时存在漂移** |
| 分红/送转未连通 | `cash_dividend_total` / `split_ratio_total` 空置 |
| 停牌未对接 | `SUSPENSION` 已定义但未接行情源停牌标识或零成交判定 |
| 可执行入场未实现 | `UNEXECUTABLE_ENTRY` 仅在离线脚本 `v03_return_measure.py` 中作为孤立规则存在 |

## 唯一关注点

实现真实结算管道：把 `resolve_horizon_return_label` 从契约变为可执行，并把 `backtest_service` 从行数切片切到交易日历语义。**不重写 `calibration_service` 的隔离逻辑（V-02 已完成，保持不动）。**

## 允许改（白名单，严格）

- `tradingagents/dataflows/return_labels.py`（补 `resolve_horizon_return_label` 实现）
- `tradingagents/dataflows/trade_calendar.py`（仅在复用需要时最小增补）
- `api/services/backtest_service.py`（替换 `_get_price_after` 的行数切片逻辑）
- `api/services/calibration_service.py`（**仅在因价格语义变更而必须同步时**，且不得改动 V-02 已交付的隔离/资格/小样本闸门语义）
- 新增 `tests/test_horizon_return_settlement.py`；扩展 `tests/test_horizon_return_labels.py`

## 禁止改

claim 系与 `research_manager.py`（L1/DAV-828 领域）；`game_theory_tools.py` / `agent_states.py` / `graph/`（L2 领域）；`prompts/`；DB schema；`credit_weighting_enabled`；`DEFAULT_HOLD_DAYS = 5` 与 `shadow_credit` 的 T+5 语义（**V-02 不变性保护，必须保持**）；生产库数据。

## red_team_scenarios

| # | 场景 | 预期 |
|---|---|---|
| RT-1 | T+1 停牌 / 一字涨停无法买入 | `unexecutable_entry`；**不得假装以开盘价成交** |
| RT-2 | 持有期内停牌（T+4 有价、T+5 无价、T+6 有价） | `suspension`；**不得仅凭 T+5 单点缺失判停牌**（须双侧证据） |
| RT-3 | 目标日恰逢节假日 / 周末 / 缺行 | 按交易日历滚动到候选日；**与旧 `iloc[hold_days-1]` 的差异必须有负例证明** |
| RT-4 | 价格缺失 / 供应商返回结构异常 | `data_missing` / `provider_failure`；**不得当 0 收益** |
| RT-5 | 分红/送转 | 走 `ReturnType=total_return` + `cash_dividend_total` / `split_ratio_total` 字段；**不新增状态值**；缺分红数据不得静默按 0 计 |
| RT-6 | 旧报告（无 profile / legacy） | 缺省仍走 `DEFAULT_HOLD_DAYS = 5`；**不得把旧 T+5 结果当成 T+10/T+40** |
| RT-7 | 新 profile（显式 short T+10 / medium T+40） | 正确解析；未成熟样本标 `pending_due`，**禁止缩短 40 日窗口** |
| RT-8 | 跨档 / 跨配置样本混池 | 按 profile 隔离，不得混算 |
| RT-FULL | 候选完整 SHA 真全量 `pytest -q -p no:randomly` | 对照基线失败集**逐项比对**，有新增失败即不得 PASS；**必须含 `test_backtest_calibration_isolation.py` 与 `test_horizon_return_labels.py` 的不变性回归** |

## 验收钉子

1. **先交付经审定的标签契约文本**（T 定义 / cutoff 资格 / 主评价日 / 研究基准 / 可执行入场五者的区别），再做实现——v1.1 §8 V-01 明确要求「必须先产出经审定的标签契约，再实施评价」
2. 上表 8 条场景 + RT-FULL 逐条实跑并贴实际输出（命令 / 解释器 / SHA）
3. 旧 T+5 不变性：`DEFAULT_HOLD_DAYS=5` 与 `shadow_credit` T+5 语义有回归保护且实测不变
4. `git diff --check` 洁净；改动严格限于白名单

## 交付

完整 40 位 SHA、第一父、`git diff --name-status`、`git diff --check`、8 条 RT + RT-FULL 实测输出、契约文本、工作树状态。`git push` 后 `git ls-remote origin <分支>` 回读并贴输出。状态置 `in_review`，**由非实现者独立复审**。

## 硬约束

不 FF、不部署、不重启服务、不写生产库、不开加权、不执行真实采集。仅本卡白名单文件可改。
