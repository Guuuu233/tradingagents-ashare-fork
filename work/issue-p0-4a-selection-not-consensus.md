# P0-4a：资金流 selection ≠ consensus；不可比字段不得放行方向

## 目标

D-009 / 审计稿 §P0-4 的**第一刀**：`select_fund_flow_source()` 是**选择器**，不是同字段多源共识。禁止再把选择器结果写进 `metadata["consensus"]`。EM 主力净额 `r0_net` 与 THS 总净额 `netamount` **同时有效**时，资金方向 `direction_allowed=false`，只保留 side evidence / conflict，禁止挑一个有利口径当「共识」。

本卡**不做** claim `cluster_id` 去重计票（P0-4b）、**不做** VWMA/主力成本去人格化（P0-5）、不是社交、不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `049b7d46ca3bdabe25006e3986e4457771dde3fa`
- **新建**隔离分支，例如 `agent/dev2/p0-4a-selection-not-consensus`
- **不要**在 `agent/dev2/p0-3b-q2-derived` 或宿主 `agent/senior-dev-2/p0-1-r5-graph-tests` 上继续堆
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-4、§4.1 歌尔
- 事故：东方财富与同花顺口径不同却选择性只留有利口径；选择器被命名为 consensus

## 已复现（主干 `049b7d4`）

1. `select_fund_flow_source` 在 EM `r0_net` 与 THS `netamount` 都有效时仍 `direction_allowed is True`，并选出东财（`tests/test_fund_flow_evidence.py::test_conflicting_ths_side_value_never_overrides_eastmoney_priority`）。
2. `cn_akshare_provider.py` 多处 `metadata["consensus"] = selection`（约 2437、2574、2624、3594 行）。
3. Tushare DC+THS 生产路径：`consensus_audit.reason_code == incomparable_field_semantics`，但 `meta["consensus"]["direction_allowed"] is True`（`tests/test_cn_akshare_backup_sources.py` 约 198–202 行）。审计已经知道不可比，方向闸仍放行。
4. `smart_money_analyst.py` 把 `consensus: selection` 写进 guard。

研究总监在资金闸阻断时已经走 ABSTAIN/`DIRECTION_NA`（P0-1）。本卡不要把资金不可比改回 `direction=中性`。资金闸只禁用**资金方向证据**，不得把整场市场方向改成 bear/neutral。

## 行为契约

改 `select_fund_flow_source` **原路径**（禁止 `_v2`）。在已得到 `new_groups` / `valid_groups` 之后：

- 仅一个有效字段（例如只有 `r0_net`，或 EM 无效后只剩 THS `netamount`）：保持现有「按优先级选首个合格来源」，`direction_allowed=true`。
- **两个及以上不同 `field` 的新算法组同时有效**（典型：`r0_net` 与 `netamount` 同日同窗口）：
  - `direction_allowed=false`
  - `hard_guard.blocked=true`
  - 稳定 `reason_code`：`incomparable_field_semantics`（或你们锁死的等价码，测试锁死）
  - 两源都留在 `raw_values` / `alternative_sources`，禁止平均、禁止只留有利口径
  - 可以保留 `selected_source` 作展示优先级，但**不得**因此放行方向
- 新浪 legacy 仍只在全部新算法失败后作 fallback；本卡不放宽 legacy。

`build_consensus_evidence` 继续只做同字段审计。把它的结果命名为 `same_field_consensus_audit`。本卡可保留 `consensus_audit` 作为同一对象的别名，避免静默丢审计。

### 命名（生产 metadata / guard）

| 键 | 含义 |
|---|---|
| `selection` | `select_fund_flow_source` 的结果 |
| `same_field_consensus_audit` | `build_consensus_evidence` 或 `_tushare_incomparable_consensus` |
| `consensus` | **禁止**再赋值为 `selection` |

`consensus`：删掉，或改成指向审计对象（`direction_allowed=false` 的 conflict）。旧读者若读 `consensus.selected_source` 会断，测试改读 `selection`。不要留「consensus 其实是 selector」的兼容。

### Provider / analyst

`_attach_chain`、`_merge_side_evidence`、`_format_tushare_fund_flow`、THS 快照合并处：写 `selection`，停止 `consensus = selection`。`smart_money_analyst` 的 guard 同样：`selection` 是闸，不要把选择器塞进 `consensus`。

方向闸继续看 `selection.direction_allowed` 与 `hard_guard`。不可比时资金 guard `blocked=true`。

### 不要改

- 财务 `period_kind` / Q2 派生
- prompt 去人格化（P0-5）
- claim cluster 计票（P0-4b）
- 辩论 3/1、`credit_weighting_enabled`
- 社交 DAV-460
- 受保护脏文件

## 允许修改

- `tradingagents/dataflows/fund_flow_evidence.py`
- `tradingagents/dataflows/providers/cn_akshare_provider.py`（仅资金流 selection/consensus 接线；不要改财报路径）
- `tradingagents/agents/analysts/smart_money_analyst.py`（仅 consensus/selection 赋值）
- `tests/test_fund_flow_evidence.py`
- `tests/test_cn_akshare_backup_sources.py`
- `tests/test_smart_money_fund_flow_semantics.py`
- 若夹具键名必须跟着改：`tests/fund_flow_fixtures.py`

`tests/test_research_manager_run_integrity.py::test_fund_flow_block_is_abstain_not_neutral` **不要改坏**：资金阻断仍是 ABSTAIN，不是中性。

## 阅读纪律

1. **完整读** `fund_flow_evidence.py`，grep `select_fund_flow_source` / `metadata["consensus"]`。
2. 完整读 `_attach_chain`、`_tushare_consensus`、`_tushare_incomparable_consensus`。
3. 改原路径。

## 测试（TDD）

先在 **`049b7d4` 上写会失败的测试**，再改产品代码。现有 `test_conflicting_ths_side_value_never_overrides_eastmoney_priority` 的 `direction_allowed is True` **必须改掉**：那正是本卡要堵的洞，不是回归锁。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_fund_flow_evidence.py \
  tests/test_cn_akshare_backup_sources.py \
  tests/test_smart_money_fund_flow_semantics.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

必须覆盖：

1. **单源 EM `r0_net`**：仍可选中，`direction_allowed is True`。
2. **EM `r0_net` + THS `netamount` 同时有效**（含符号相反）：`direction_allowed is False`，`hard_guard.blocked is True`，reason 含不可比；两源都在 raw/alternative 里；不得只剩东财流入当方向。
3. **EM 无效、THS `netamount` 有效**：仍可选 THS，`direction_allowed is True`（只有一个有效字段）。
4. **生产路径 Tushare DC+THS**：`selection.direction_allowed is False`；`consensus` 不再是选择器（无 `selected_source` 当共识，或 consensus 等于审计且 `direction_allowed is False`）；`same_field_consensus_audit` 或 `consensus_audit` 仍为 `incomparable_field_semantics`。
5. **P0-1 回归**：资金闸阻断 → ABSTAIN / `DIRECTION_NA`，投资计划不得写成 Neutral/HOLD 观点。

不要 `assert result is not None`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `049b7d46ca3bdabe25006e3986e4457771dde3fa`
- 精确 pytest 数字
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」
- **不准予部署**
- 不要 @项目调度助手催工
