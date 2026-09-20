# P0-3a：财务 period_kind 分类（不含单季派生）

## 目标

D-009 / 审计稿 §P0-3 的**第一刀**：在原路径给财务报告期打上不可含糊的 `period_kind`，并写进注入 LLM 的 cutoff header，堵住把 `20240630` / `2026H1` **累计损益**叫成 **Q2 单季**。

**本卡不做** H1−Q1 单季派生。那是 P0-3b。不要在本 commit 里写减法、scope 比对或 `single_quarter_derived`。

不是资金 `period_kind`（`historical_daily` / `five_day_cumulative` 等），不是 EvidenceRecord 全量迁移，不是社交，不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `6ea84afa21a3ef9bcbc9f82055dccaa3b8273aa5`
- 从该 SHA **新建**隔离分支，例如 `agent/dev2/p0-3a-period-kind`
- **不要**在 `agent/dev2/p0-2b-unverified-as-of` 或宿主 `agent/senior-dev-2/p0-1-r5-graph-tests` 上继续堆
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`

宿主 checkout 仍停在落后 SHA，且有脏文件 `AGENTS.md` / `frontend/src/services/api.ts` / `work/h1b_gates_report.json`。**不要动它们。**

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-3
- 事故：把 H1 累计营收误作 Q2 单季
- 已有 `format_report_period_label`：`0630` → `YYYYH1`（**保留**，不要改成 Q2）
- 已有有效公告日逻辑：`resolve_effective_announce_date` / `filter_financial_df_by_effective_announce` / `LATE_FILING_GRACE_DAYS` **不得改行为**

## 已复现（主干 `6ea84af`）

1. `financial_announce.py` 没有 `classify_financial_period_kind`（或等价原路径函数）。
2. `financial_cutoff_header` 只写「截至 2026Q1 / 2025H1」和生效公告日，**不写 period_kind**，也不写「不是 Q2 单季」。
3. `cn_akshare_provider._financial_report_sina` 四次调用 `financial_cutoff_header(...)`（Sina 空表、Sina 成功、backup 空表、backup 成功）均未传 statement kind。
4. `tests/test_financial_period_kind.py` **不存在**。

仓库里其它 `period_kind`（资金窗口、全球指数 snapshot）是**另一套语义**。禁止复用那些字符串，禁止改 `fund_flow_evidence.py`。

## 行为契约

在 `tradingagents/dataflows/financial_announce.py` **原路径**增加分类函数（名称建议 `classify_financial_period_kind`；禁止 `_v2` / `_new`）。输入：`report_period` + `statement_kind`。`statement_kind` 只允许：`income` | `cashflow` | `balance`。

返回 frozen dataclass 或等价不可变结构，字段至少：

- `reported_period_label`：继续用现有 `format_report_period_label`（`0331→Q1`，`0630→H1`，`0930→Q3`，`1231→A`）
- `period_kind`：见下表
- `derivation_formula`：本卡恒为 `None` / `"not_derived"`（字符串二选一，测试锁死一种）
- 非法 period 或非法 statement_kind → `period_kind="unknown"`，不得猜、不得填今天

| statement_kind | 报告期 MMDD | period_kind |
|---|---|---|
| income / cashflow | 0331 | `first_quarter` |
| income / cashflow | 0630 | `half_year_cumulative` |
| income / cashflow | 0930 | `nine_month_cumulative` |
| income / cashflow | 1231 | `annual_cumulative` |
| balance | 0331/0630/0930/1231 | `period_end_stock` |
| 其它 | 其它 | `unknown` |

**硬钉子：** `("20240630", "income")` 和 `("20240630", "cashflow")` 必须是 `half_year_cumulative`，`reported_period_label` 必须是 `2024H1`，**禁止**出现 `Q2` 作为 period label。资产负债表同日必须是 `period_end_stock`，**禁止**套累计或单季流量语义。

### Header

给 `financial_cutoff_header` 增加可选关键字 `statement_kind: Optional[str] = None`。

- `statement_kind is None`：保持现有文案，**现有** `test_financial_cutoff_header_*` 必须仍绿。
- 传入 `income` / `cashflow` / `balance` 且 `latest` 非空：在原有「截至 {label} / 生效公告日」行上**追加**机器可读片段，必须同时含：
  - `reported_period_label=...`
  - `period_kind=...`
  - `derivation_formula=not_derived`（或你们锁死的 None 文本，如 `derivation_formula=N/A`）
  - 中文口径一句：利润表/现金流 `0630` **是 1–6 月累计，禁止当作 Q2 单季**；资产负债表 **是期末点值**。

不要改 `dropped_yoy_refresh` 免责声明逻辑。

### Provider 接线（生产路径，必须锁）

`cn_akshare_provider.py` 里 `_financial_report_sina` 已有 `kind_map`（资产负债表→balance，利润表→income，现金流量表→cashflow）。**四处** `financial_cutoff_header(...)` 都必须传入对应 `statement_kind`（空表失败路径也要传，避免只测成功路径）。

财务摘要 `filter_abstract_period_columns` 那一处 header **本卡不要改**（摘要列混指标，留给后续卡）。

只改 header 调用与必要 import。禁止顺手改 `_shrink_table`、公告日截断、backup 选源、资金流。

## 允许修改

- `tradingagents/dataflows/financial_announce.py`
- `tradingagents/dataflows/providers/cn_akshare_provider.py`（仅 `_financial_report_sina` 的 header 调用点 + 必要 import）
- `tests/test_financial_period_kind.py`（新建；审计稿点名此文件。本卡只覆盖分类 + header，**不要**写 H1−Q1 派生绿测）
- `tests/test_financial_announce_cutoff.py`（只加生产路径钉子；不要改既有 cutoff 期望）

## 禁止修改

- `resolve_effective_announce_date` / `build_effective_announce_map` / `filter_financial_df_by_effective_announce` / `LATE_FILING_GRACE_DAYS` 的判定结果
- 资金 `period_kind`、`fund_flow_evidence.py`、`data_collector.py`、`evidence_verifier.py`
- 社交 `tradingagents/dataflows/social/`、DAV-460
- P0-3b 派生、P0-4、P0-5、前端、schema
- `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 主干快进、部署
- 新增无人调用的 `EvidenceRecord` 模块

## 阅读纪律（AGENTS.md）

动手前：

1. **完整读** `financial_announce.py`，grep 其全部调用点。
2. **完整读** `_financial_report_sina`（约 1195–1370 行）和 `get_income_statement` / `get_balance_sheet` / `get_cashflow`；grep 该文件全部 `financial_cutoff_header(`。
3. 改原路径。禁止平行函数把旧 header 留在旁边。

## 测试（TDD）

先在 **`6ea84af` 上写会失败的测试**，再改产品代码。不要先改实现再补测试。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_period_kind.py \
  tests/test_financial_announce_cutoff.py \
  -q --tb=short
```

必须断言（红测转绿）：

1. `classify_financial_period_kind("20240630", "income")` → `period_kind=="half_year_cumulative"`，label `2024H1`，formula 为约定的未派生值。同日 cashflow 相同；同日 balance → `period_end_stock`。
2. `0331/income` → `first_quarter`；`0930/income` → `nine_month_cumulative`；`1231/income` → `annual_cumulative`。**没有任何** `0630` 分类结果含 `Q2`。
3. `financial_cutoff_header(latest, curr, statement_kind="income")` 对 latest=`20240630` 含 `period_kind=half_year_cumulative` 和「不是 Q2」口径；`statement_kind="balance"` 含 `period_end_stock`。
4. **生产路径**：复用 `tests/test_financial_announce_cutoff.py` 的 `_FinFixtureProvider` + `_three_tables()`。`get_income_statement("600519", curr_date="2024-08-20")` 的返回文本必须含 `period_kind=half_year_cumulative`，且不得把该期标成 `2024Q2`。`get_balance_sheet` 同日必须含 `period_kind=period_end_stock`。只测 `financial_cutoff_header` 单元而不测 provider，视为未锁生产路径（P0-2b 已因此否决过一次）。
5. 既有 cutoff / YoY disclaimer / truncation 测试仍绿。

不要写只 `assert result is not None` 的测试。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `6ea84afa21a3ef9bcbc9f82055dccaa3b8273aa5`
- 精确 pytest 输出（passed/failed 数字，不要只报「全绿」）
- 不要写「彻底修复」；不要自行合主干
- 完工后只在本卡评论写 SHA。合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」（D-010）。**不准予部署。**
- 不要 @项目调度助手催工。
