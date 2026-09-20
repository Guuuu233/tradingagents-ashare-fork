## 任务：修复资金流 `r0_net` 语义错配导致的系统性 ABSTAIN 误杀

**基线 tip（必钉）**：`e967bf16687969d5dbc4de64cd4f42493c642157`（`codex/dav-4-p2a-trunk` 当前 tip，已含 a290f18 部署与台账）
**分支**：从该 tip 新建 `codex/dav-fundflow-r0net-semantic`；禁止在脏 host 工作树施工，建议干净 worktree。

### 背景证据（已只读审计确认，先读这两份）
- `work/fund-flow-field-semantics-audit-20260919.md`（根因）
- `work/issue-fund-flow-r0net-semantic-mismatch-20260919.md`（修复卡）

生产报告 `c21456dd.result_data.market_data_context.fund_flow_evidence.records` 三条记录显示：
- 东财 `moneyflow_dc.net_amount` = 今日**主力**净流入额（≈超大单+大单）→ `r0_net = 0.40185`
- 同花顺 `moneyflow_ths.buy_lg_amount` = **大单**净额（缺超大单分量）→ `r0_net = 0.758931`
同名不同义被塞进 `r0_net` 做同字段共识 → `unexplained_dispersion` 30.76% > 20% → ABSTAIN。**非单位错误**（万元→亿元一致）、**非日期错配**（同 2026-09-18）。东财自带 `buy_lg_amount=0.874816` 与同花顺 `buy_lg_amount=0.758931` 仅差 ~14%（阈值内）——证明错在字段错配不在数据。

### 修复要求（不可妥协）
1. **字段分离**：`net_amount`(主力) 与 `buy_lg_amount`(大单) 分开保存，不得归一成同名 `r0_net` 互比。同花顺 `buy_lg_amount` 改独立字段（建议 `lg_net`），`r0_net` 仅承载真主力净额口径。
2. **同义才可比**：共识比较仅在同语义、同单位、同日期字段间（主力对主力、大单对大单）。
3. **不可比 fail-closed**：同语义字段无对端可比值时标记 `semantic_incomparable`，保持 `blocked`/`not_checked`，不得退化为跨口径比较或放行。
4. **不放宽阈值**：20% 离散阈值不变；不取平均；不合并不同口径。
5. **口径文档化**：`fund_flow_evidence.py` 字段语义表 + provider 注释写明东财 `net_amount`=主力、同花顺 `buy_lg_amount`=大单，防再混装。

### 前置必办（动手前）
取 Tushare `moneyflow_dc` / `moneyflow_ths` 官方字段口径文档，确认：东财主力是否严格=超大单+大单、东财 `buy_lg_amount` 能否直接对齐同花顺同字段、同花顺 `buy_lg_amount` 是否缺超大单。据此在 选项A(按 upstream_field 同义对齐)/B(同花顺大单改独立字段)/C(补超大单分量) 定方案，并在 issue 评论里写明所选方案与依据再施工。

### 允许改动
- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tradingagents/dataflows/fund_flow_evidence.py`
- `tradingagents/agents/analysts/smart_money_analyst.py`（如需配合字段改名）
- 相关 `tests/`（新增回归 + 更新被语义改掉的旧断言）

### 禁止
- 部署；改 `credit_weighting_enabled`（保持 false）；自行 merge/FF 主干
- 碰 `AGENTS.md`、`frontend/`、社交开关、H1b 门槛常量
- 放宽 `relative_dispersion_threshold`、取平均、跨口径合并
- 改生产库数据（只用 `.backup()` 副本与 fixture 验证）

### 回归测试（TDD 先红后绿，逐条实跑）
- [ ] 复现 `c21456dd`：东财 `net_amount=4018.5万`、同花顺 `buy_lg_amount=7589.31万` fixture，断言修复后不再 `unexplained_dispersion`，落入可比/不可比正确分支
- [ ] 真同字段冲突：两个同语义源差 >20%，断言仍 `unexplained_dispersion`+`blocked`（不误放行真冲突）
- [ ] 语义不可比：单源单口径可用时 `semantic_incomparable` + fail-closed
- [ ] 大单对大单：东财与同花顺 `buy_lg_amount` 离散 <20% 时可形成共识（若方案支持）

### 完成定义
- 环境：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20）
- TDD 红→绿记录 + 聚焦测试全绿 + `git diff --check` 干净 + compileall 通过
- 完整 40 字符 SHA 上报，分支推到远端
- 在本 issue 评论汇报并原样 mention [@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

**不要 merge/部署**。施工完成后由 David 指派的独立审核员复审，再决定合入。
