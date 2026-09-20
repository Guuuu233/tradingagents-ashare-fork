**实施卡 L2 / 博弈论生产接线（总工派工，2026-09-12）。**

## 基线

- 精确基线：`70b5b47bcff0618e0db1258a438b0875440d4ac5`
- 第一父 = 基线，隔离分支开工
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（**绝对路径**；worktree 内无 `.venv310`）

## 现状（总工已核，勿重复调查）

| 事实 | 证据 |
|---|---|
| 工具模块存在 | `tradingagents/agents/utils/game_theory_tools.py` |
| 仅被工具聚合层 import，**无生产节点调用** | `git grep -n "game_theory_tools" -- tradingagents/agents/ api/` → 仅 `agent_utils.py:27` |
| **未接入 graph** | `git grep -n "game_theory" -- tradingagents/graph/ tradingagents/agents/utils/agent_states.py` → **零命中** |
| DB 列已存在（无需迁移） | `api/database.py:138 / :471 / :511` 有 `game_theory_report` |
| API 有读取点但恒为 None | `api/main.py:2118` `final_state.get("game_theory_report")` |
| 填充率硬编码 0 | `tradingagents/eval/v03_return_measure.py:131` `"game_theory_report_fill_rate": 0.0` |

**结论：工具、DB 列、API 读取点齐备，缺的是「生产者」——即 graph 中没有节点计算并写入该字段。**

## 唯一关注点

在 graph 中新增一个博弈论节点（或在既有节点内以最小侵入方式产出），使 `game_theory_report` / `game_theory_signals` 在真实运行中被写入 state 并落库。**不重构 graph 拓扑，不改计权，不新增平行决策路径。**

## 允许改（白名单，严格）

- `tradingagents/agents/utils/game_theory_tools.py`（仅当需要修正接口以适配节点调用）
- `tradingagents/agents/utils/agent_states.py`（补 state 字段）
- `tradingagents/graph/` 下**新增**节点文件 + `trading_graph.py` 接线 + `conditional_logic.py`（仅在需要路由时）
- `tradingagents/prompts/zh.py` / `en.py`（**仅新增博弈论所需的最小 prompt 小节**，不得改动既有分析师/辩论 prompt）
- 新增 `tests/test_game_theory_integration.py`

## 禁止改

`api/main.py`（读取点已存在，无需改）；`api/database.py`（列已存在）；claim 系（`claim_cluster.py` / `debate_utils.py` / `evidence_relations.py`）与 `research_manager.py`（**L1/DAV-828 正在改，严禁交叉**）；`return_labels.py` / `backtest_service.py` / `calibration_service.py`（L3 领域）；`credit_weighting_enabled`；辩论轮次 3/1；生产库数据。

## red_team_scenarios

| # | 场景 | 预期 |
|---|---|---|
| RT-1 | 正常输入（完整对手方/策略输入可用） | 节点产出 `game_theory_report`，落库后回读非空且内容一致 |
| RT-2 | 输入缺失（无对手方数据 / 数据源失败） | **显式标注该项不可用**，不得返回空字符串、不得填默认值、不得编造指标（AGENTS.md §3.4 / §3.5） |
| RT-3 | 节点执行失败 / 超时 | 不得中断整条分析流程（AGENTS.md §4），且必须留可查痕迹；该报告字段为缺失而非空串 |
| RT-4 | 图路由可达性 | 新节点确实在 graph 路由上被执行（有 trace / 日志证据），**不是定义了但没人调用** |
| RT-5 | 持久化回读 | `ReportDB.game_theory_report` 与 `game_theory_signals` 落库后经 `report_service.get_report` 回读一致 |
| RT-6 | 来源可追溯 | 报告内每个信号可追到具体输入与确定性计算；**不得由 LLM 自由生成数值** |
| RT-7 | 单双周期（short/medium）各跑一次 | 不得因档位不同导致字段串写或漏写 |
| RT-FULL | 候选完整 SHA 真全量 `pytest -q -p no:randomly` | 对照基线失败集**逐项比对**，有新增失败即不得 PASS |

## 验收钉子

1. 上表 7 条场景 + RT-FULL 逐条实跑并贴实际输出（命令 / 解释器 / SHA）
2. `game_theory_report_fill_rate` 由硬编码 `0.0` 变为**实测非零**（须给出测量方式，不得改常量充数）
3. `final_state.get("game_theory_report")` 在真实运行中非 None
4. **无伪造指标**：缺数据必须显式标缺失
5. `git diff --check` 洁净；改动严格限于白名单

## 交付

完整 40 位 SHA、第一父、`git diff --name-status`、`git diff --check`、7 条 RT + RT-FULL 实测输出、真实运行报告 id 与字段回读、工作树状态。`git push` 后 `git ls-remote origin <分支>` 回读并贴输出。状态置 `in_review`，**由非实现者独立复审**。

## 硬约束

不 FF、不部署、不重启服务、不写生产库、不开加权、不执行真实采集。仅本卡白名单文件可改。
