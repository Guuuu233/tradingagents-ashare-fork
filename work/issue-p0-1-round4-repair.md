# P0-1 第四轮返修：Risk revise 回路 + 提交门槛

## 目标

堵住第三轮独立复审的 High：Risk Judge `verdict=revise` 把 `VALID/BULL/BUY` 改写成 `ABSTAIN/WAIT` 后，Trader 因非可执行短路，图又绕回 Trader；同时补齐 P0-1 提交门槛里已红的校准生产测试。

这不是新功能，不是 P0-2，不是社交。

## 基线

- Repository: `/Users/davidliu/Documents/TradingAgents-AShare`
- Trunk（勿快进）: `codex/dav-4-p2a-trunk` @ `de88de4eb33b7595d6fcb9a4c4e84d0a69267db5`
- 施工基线分支: `wip/p0-1-run-integrity`
- Base SHA: `a09ca2ce571a31f28944828d02061e69aeb75fd5`
- 从该 SHA 开隔离分支，例如 `agent/<you>/p0-1-r4-repair`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 没有名为 `target` 的 remote

## 权威

- DECISIONS.md D-009
- `work/2026-08-27-audit-decision-semantics-plan.md` §6 P0-1 / 风险方向解耦
- Cursor × Multica 章程 §4.1、§9 B3、§16

## 已复现的 High（必须先写红测）

当前 `a09ca2ce` 上：

1. `status_from_risk_verdict(upstream=VALID/BULL/BUY, risk_verdict="revise")` → `ABSTAIN/N/A/WAIT/ELEVATED`
2. `is_non_executable_status` 对该结果为 True
3. Trader 不调用 LLM
4. Risk 短路 `**risk_feedback_state` 保留 `revision_required=True`，不增加 `retry_count`
5. `should_revise_after_risk_judge` 仍返回 `"Trader"`
6. `setup.py` 中 `Trader → Aggressive Analyst` 无条件，风控三辩会反复跑

隔离探针已在宿主复现：`next_route Trader`，`trader_llm_calls 0`。

## 行为契约

### A. `status_from_risk_verdict`

| 输入 | 必须输出 |
|---|---|
| upstream 已是 INVALID/ABSTAIN/NO_TRADE/WAIT | 保持非可执行；`risk_status=BLOCKED`；不得改成 BUY |
| `revise` 且重试未耗尽 | **保持** upstream 的 `analysis_status`（VALID 保持 VALID）、**保持 direction**、保持原 `trade_action` 作为待改写提案；`risk_status=ELEVATED`；`is_non_executable_status` 必须 False，Trader 必须能跑 LLM |
| `reject` / `blocked` | **保持原 direction**；`trade_action=NO_TRADE`；`risk_status=BLOCKED`；不得改成 BEAR；不得把 VALID/BULL 塌成 `ABSTAIN/N/A` |
| `pass` / `approve` | 保持 VALID 上游方向与动作；`risk_status=OK` |
| 改写次数耗尽 | `trade_action=NO_TRADE`；`risk_status=BLOCKED`；不得沿用旧 BUY；`revision_required=False`；下一跳 END |

章程表：`BULL + 风控 BLOCKED → 保留 BULL，动作 NO_TRADE`。

### B. 回路防御（必须同时做）

1. Risk 非可执行短路必须显式 `revision_required=False`
2. `should_revise_after_risk_judge`：若当前 canonical 已非可执行，必须返回 `END`，即使 feedback 仍写着 revise
3. Research Manager 已输出 ABSTAIN/INVALID/NO_TRADE 时，不得再进入 Aggressive/Conservative/Neutral 风控辩论 LLM。7/7 已在 Integrity Gate END；consistency/fund-flow ABSTAIN 目前仍会进风控三辩，必须停掉

### C. `resolve_soft`

缺 horizon `decision_status` 且报告并非失败时，禁止默认 `VALID/NEUTRAL/HOLD`。改为 ABSTAIN/NO_TRADE 或明确 unknown，不得重新坍缩成合格中性。

### D. 校准生产路径

`tests/test_calibration_service.py::test_buckets_and_rise_rate_are_computed_correctly` 在 `a09ca2ce` 上失败：`assert 0 == 3`。原因：种子没有 `analysis_status=VALID`，被 `_query_reports` 的 SQL 排除。这是预期产品行为，但测试未锁住新契约。

必须：

1. 种子 VALID+BUY/SELL/HOLD+probability 的样本仍能入选（原 count=3 用例转绿）
2. 另加：`analysis_status=NULL` 且有 probability 的 legacy 行被排除
3. 响应里能看到排除计数（至少 NULL/INVALID/ABSTAIN/NO_TRADE/WAIT 之一，或合计 `excluded_*`）。不要 `except Exception: pass` 吞掉 VALID 过滤失败

## 允许修改

- `tradingagents/agents/utils/decision_status.py`
- `tradingagents/agents/managers/risk_manager.py`
- `tradingagents/agents/trader/trader.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/graph/setup.py`
- `api/services/calibration_service.py`
- `tests/test_decision_status.py`
- `tests/test_research_manager_run_integrity.py`
- `tests/test_p0_1_review_fixes.py`
- `tests/test_calibration_service.py`

若 graph 短路需要 manager 边，可改 `tradingagents/graph/setup.py` 现有 conditional edges，禁止新并行路径。

## 禁止修改

- `AGENTS.md`
- `frontend/src/services/api.ts`
- `work/h1b_gates_report.json`
- 用户模型 / 3/1 轮次 / `credit_weighting_enabled` / 密钥 / 生产数据库
- 社交 `social/`、DAV-460 文件
- P0-2 EvidenceRecord / PIT
- `codex/dav-4-p2a-trunk` 快进
- `git add -A` / reset / clean

## 测试（TDD）

先加会在 `a09ca2ce` 上失败的测试，再改产品代码。禁止只改测试让现状变绿。

宿主命令：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_decision_status.py \
  tests/test_research_manager_run_integrity.py \
  tests/test_p0_1_review_fixes.py \
  tests/test_run_integrity.py \
  tests/test_report_schema_migration.py \
  tests/test_calibration_service.py \
  -q --tb=short
```

必须断言：

1. `revise` 后 Trader `astream` 调用数 ≥ 1
2. `revise` 后 `should_revise` 返回 Trader；Risk 短路后若已非可执行则 END
3. 连续两次 `should_revise` 不得在非可执行状态下无限返回 Trader
4. `reject` 保持 BULL，动作为 NO_TRADE，不是 ABSTAIN/N/A
5. 校准：VALID 入选；NULL+probability 排除
6. 既有 7/7 INVALID Gate→END、consistency→Trader astream=0 仍绿

前端若未改：不必重跑 build。若改了前端，必须 `cd frontend && npm run build`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push 到 origin
- `git diff --stat` 相对 `a09ca2ce`
- `git diff --check`
- 精确测试命令、passed/failed
- 改了哪些文件、没改哪些
- 不要写「彻底修复」；不要宣称可合入主干
- 完工后只 @项目调度助手 一次，并写明 SHA

## 协作

- 同一代码树只你一个 writer
- 不要审自己
- 不要开始 P0-2
- DAV-460 社交并行，不要碰那些文件
