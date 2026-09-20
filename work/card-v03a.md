**实施卡 V-03a（收益对照测量引擎，只读）。** 依据 [冻结表](work/v03-freeze-sheet-20260909.md)（全部冻结决策在此，逐条遵守）。前置 blocker **DAV-800 已合入 trunk `5a00c753`**（symbol canonical 模块可用）。第一父 = 当前 trunk。

## 定位（David 2026-09-10 定：路 1）

系统尚未建完（舆情等未接，见冻结表"系统完整度"节）。本引擎是**进度基线/测量工具**，**不是"AI 能否盈利"的定性判断**。每份结果必须盖章标注当时系统完整度，谁都不得把半成品数字当定论。

## 唯一关注点

对已冻结协议做**只读**收益对照测量：加载历史报告 → 规范化 → 按协议算入场/成本/收益 → 按 OOS 三段切分 → 产出收益指标 + 相对沪深300 超额 + coverage/可评估性指标，全部盖章。

## 必须遵守的冻结口径（摘自冻结表，逐条实现）

- **入场**：T+1 Open；T+1 停牌/涨跌停封死/不可交易 → 标 `untradable`，**禁止假装 Open 成交**。
- **成本**：佣金(≤3‰,账户假设) + 过户费 0.01‰双 + 印花税 0.5‰卖单边；**佣金含交易规费，禁止再加经手费/证管费**。
- **滑点**：固定单边 5bps。
- **基准**：沪深300,算超额收益。
- **OOS 三段**：`DEV≤2025-12-31` / `HISTORICAL_OOS=2026-01-01~2026-09-08` / `FORWARD_OOS≥2026-09-09`；冻结后不得据 OOS 结果调参再报同一 OOS。
- **股票池**：用 DAV-800 `symbol_canonical` 模块**读取时规范化**（不改库）；沪深A普通股(主板+创业板+科创板),排除 ST/*ST、北交所、上市<60交易日、untradable。
- **蓝思 typed-gap / 一般缺失**：`typed-missing`——`return=NULL`、`included_in_return_metrics=false`、`included_in_coverage_metrics=true`,记 `missing_reason`；**禁止 carry-forward、禁止静默 drop**。
- **基线元数据**：每份结果盖章 model(`gemini-3.8-flash-high`)、prompt hash(`5489166b` + 代码 prompt @SHA)、code SHA、运行服务 SHA。
- **系统完整度盖章**：记录当时各输入填充/接入状态（game_theory 0%、舆情真实源未接、~30%缺项等）。

## 允许改

- 新增 `tradingagents/eval/v03_return_measure.py`（或相近路径）
- 新增 `tests/test_v03_return_measure.py`
- 只读脚本 + 产出报告到 `work/`（在**副本库**验证，不写生产库）

不要改：DAV-800 symbol 模块、V-02 评价/校准、门槛逻辑。

## red_team_scenarios（D-012 §5b，须第二双眼睛复核覆盖面）

| # | 场景 | 预期 |
|---|---|---|
| RT-1 | 样本 T+1 停牌/涨跌停封死 | 标 `untradable`,不以 Open 成交,不计入收益 |
| RT-2 | typed-missing（蓝思6月缺口类） | return=NULL,进 coverage 不进 return 指标,记原因,不 drop 不 carry-forward |
| RT-3 | collision 股票(000001/000001.SZ) | 经 canonical 合并为一只,收益不劈成两份 |
| RT-4 | 股票池过滤 | ST/*ST、北交所(8xx)、上市<60d 正确排除并计数 |
| RT-5 | 成本口径 | =佣金+过户费+印花税(卖),**不含**经手费/证管费(防重复计) |
| RT-6 | OOS 边界 | DEV/HISTORICAL/FORWARD 三段正确切分,无跨段泄漏 |
| RT-7 | 入场价 | T+1 Open,非当日 Close,无前视 |
| RT-8 | 超额收益 | 相对沪深300 计算正确 |
| RT-9 | 元数据盖章 | 每结果含 model/prompt hash/code SHA + 系统完整度 |
| RT-10 | coverage vs return 分离 | typed-missing 进 coverage、不污染 return 指标;分母不缩水 |
| RT-FULL | 候选 SHA 真全量 `pytest -q -p no:randomly` | 对照 trunk 基线,零新增失败 |

## 硬约束

- **只读生产库**;测量/报告只在 `sqlite3 .backup()` 副本上跑,路径写进交付;**禁止写生产库**。
- 结果必须显式标"半成品基线,非定性判断"。
- 交付前 `git push` + `git ls-remote origin <分支>` 回读贴输出。
- 禁止 FF、部署、重启、写生产库、开加权、接任何新数据源（本卡只测量,不施工）。

## 交付

完整 40 位 SHA、第一父、diff --stat、diff --check、10 个 RT + RT-FULL 实测输出、副本库路径、样例结果(含元数据盖章与系统完整度标注)、工作树状态。状态置 in_review。
