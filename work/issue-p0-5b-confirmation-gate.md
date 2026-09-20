# P0-5b：confirmation_state 硬闸 — 未确认核心分歧不得开仓（WAIT / NO_TRADE）

## 目标

D-009 / 审计稿 §P0-5 的**第二刀**（confirmation / recency）：核心 claim 未独立确认时，不得把 `winner=tie` 或「看似完整」的 manager 路径直接变成可执行买入。必须写出：

- `confirmation_state=UNRESOLVED`（或 PARTIAL，见下）
- `trade_action=WAIT`
- 执行层不得次日自动新建仓（Trader / Risk 读到 WAIT/NO_TRADE）

本卡**不做** prompt 去人格化（P0-5a 已在主干 `5e04125`）、**不做** capitulation 特征（P1-2）、不是社交、不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `5e04125c8668adc99abe791a7686299f648de223`
- **新建**隔离分支，例如 `agent/dev2/p0-5b-confirmation-gate`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §5 confirmation/recency、§4.1 歌尔、§P0-5 末句
- 验收钉子：核心分歧未确认时 `WAIT/NO_TRADE`，不能次日自动开仓

## 已复现（主干 `5e04125`）

1. `status_from_manager_verdict`（`decision_status.py` 约 297–302）在成功 manager 路径**无条件**写 `confirmation_state=CONFIRM_CONFIRMED`，即使辩论仍有 `unresolved_claim_ids`。
2. `research_manager.py` 终端只调用 `status_from_manager_verdict(manager_verdict, …)`，**不**把未决 claim / 核验结果喂进 confirmation。
3. `tests/test_decision_status.py` 几乎不覆盖「未确认 → WAIT」。
4. P0-1 已有 ABSTAIN/NO_TRADE 闸（资金流、一致性硬闸）；本卡补的是**确认态**，不是再发明一套 ABSTAIN。

## 行为契约

改 **原路径**（禁止 `_v2`）。建议在 `decision_status.py` 增加确定性函数（例如 `status_from_manager_verdict_with_confirmation(...)` 或扩展现有 `status_from_manager_verdict` 的关键字参数），由 `research_manager` 接线。

### 输入（确定性，不靠 LLM 自觉）

至少使用：

- `investment_debate_state["unresolved_claim_ids"]` / `focus_claim_ids`
- `claims_verification` / `claim_evidence_summary`（已有 verified / contradicted / unsupported）
- 可选：`manager_verdict["winner"]`、`dispute_map`

### 规则（锁死）

| 条件 | confirmation_state | trade_action | 备注 |
|---|---|---|---|
| 存在未决 **焦点/核心** claim，且缺少独立 verified 证据，或仍有 fatal/contradicted 未裁决 | `UNRESOLVED` | `WAIT` | direction 可保留 N/A 或沿用 manager 方向展示，但**不得** BUY/SELL |
| 部分核心 claim 已 verified、其余未决 | `PARTIAL` | `WAIT` | 不得 BUY |
| 核心 claim 均已独立 verified、无致命冲突 | `CONFIRMED` | 沿用现有 `map_verdict_trade_action` | 保持 P0-1 VALID 路径 |
| 上游已是 INVALID/ABSTAIN/资金闸 | 不降级为「假 CONFIRMED」 | 保持既有 NO_TRADE/ABSTAIN | 先闸优先 |

「核心 claim」定义（本卡最小可测）：`focus_claim_ids` 非空则用 focus；否则用 `unresolved_claim_ids`；若两者皆空且 verification 无 contradicted/fatal，可 CONFIRMED。

**禁止**：把 `winner=tie` 直接映射为 Neutral+HOLD 合格成交样本；tie + 未确认 → WAIT。

Trader / 下游：若 `trade_action=WAIT` 或 `confirmation_state=UNRESOLVED`，不得产出次日开仓执行计划（断言 investment_plan / trader 路径含 WAIT/观望/NO_TRADE，且不得出现明确买入开仓指令）。能复用既有 ABSTAIN 短路则优先接线，不要平行再写一套 trader。

### Prompt

可在 research_manager / trader 加**一句**硬约束：「confirmation_state≠CONFIRMED 时不得 BUY/SELL」。不要大改五步裁决与 DAV-336。

## 不要改

- P0-5a 去人格化文案（除非测试强制）
- 辩论 3/1、`credit_weighting_enabled`
- 社交 DAV-460、财务、资金流 selection、cluster 计票算法
- 受保护脏文件、DB schema

## 允许修改

- `tradingagents/agents/utils/decision_status.py`
- `tradingagents/agents/managers/research_manager.py`（接线 confirmation 输入）
- 必要时 `tradingagents/agents/trader/` 或等价执行短路（只读 WAIT）
- `tradingagents/prompts/zh.py` / `en.py`（仅 confirmation 一句）
- `tests/test_decision_status.py`
- `tests/test_research_manager_run_integrity.py` 或新建 `tests/test_confirmation_gate.py`

## 测试（TDD）

先在 **`5e04125` 上写会失败的测试**，再改产品代码。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_decision_status.py \
  tests/test_confirmation_gate.py \
  tests/test_research_manager_run_integrity.py \
  tests/test_prompt_depersonification.py \
  -q --tb=short
```

必须覆盖：

1. **未决焦点 claim + 无 verified** → `confirmation_state=UNRESOLVED`，`trade_action=WAIT`（不得 BUY）。
2. **部分 verified** → `PARTIAL` + `WAIT`。
3. **全部核心 verified、无致命冲突** → 可 `CONFIRMED`，trade_action 可沿用原映射。
4. **资金闸 / consistency 硬闸** 仍优先于 confirmation（ABSTAIN/NO_TRADE 不被本卡改坏）。
5. **歌尔钉子（最小）**：模拟「核心分歧未确认」state → manager 终端 status 为 WAIT，投资计划不得写成次日开仓。
6. P0-5a 去人格化测仍绿。

不要引入 `pytest-asyncio`；异步测用 `asyncio.run`。不要 `assert result is not None`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `5e04125c8668adc99abe791a7686299f648de223`
- 精确 pytest 数字
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor「准予合入」+完整 SHA
- **不准予部署**；不要 @项目调度助手催工
