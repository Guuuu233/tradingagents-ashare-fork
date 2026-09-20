# P0-3b：同口径 H1−Q1 派生 Q2 单季（不做 Q3/Q4）

## 目标

D-009 / 审计稿 §P0-3 的**第二刀**：只有利润表/现金流的 **H1 与同年 Q1 都已公开、字段同口径** 时，才派生 `single_quarter_derived = H1 − Q1`。无 Q1、口径不一致、只给同比百分比、或资产负债表，Q2 单季为 **N/A 并写明原因**，禁止 LLM 把 H1 累计当 Q2。

P0-3a 已合入主干 `ba47284e3284fbed70fc104901fbe354907c7bee`：H1 分类是 `half_year_cumulative`，`classify_financial_period_kind` / cutoff header 的 `derivation_formula=not_derived` **必须保持**。派生是**另附一块**，不要改 H1 自己的 period_kind。

本卡**只做 Q2 = H1−Q1**。不要做 前三季度−H1、年度−前三季度。不是资金 `period_kind`，不是社交，不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `ba47284e3284fbed70fc104901fbe354907c7bee`
- **新建**隔离分支，例如 `agent/dev2/p0-3b-q2-derived`
- **不要**在 `agent/dev2/p0-3a-period-kind` 或宿主 `agent/senior-dev-2/p0-1-r5-graph-tests` 上继续堆
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-3
- 事故：把 H1 累计营收误作 Q2 单季
- P0-3a 已禁止把 `0630` 标成 Q2；本卡补「能减才减，不能减就明示 N/A」

## 已复现（主干 `ba47284`）

1. `classify_financial_period_kind("20240630", "income").derivation_formula == "not_derived"`，没有 `single_quarter_derived`。
2. 无 `derive_q2_*`（或等价原路径函数）。
3. `_financial_report_sina` 成功路径是 `return f"{header}\n\n{table}"`，没有派生块。
4. `_three_tables()` 在 `curr_date="2024-08-20"` 下利润表 latest 是 `20240630`，**没有**同年 `20240331` 行 → 今天生产文本不会声明「Q2 单季不可用」。

## 行为契约

在 `tradingagents/dataflows/financial_announce.py` **原路径**增加派生函数（名称建议 `derive_q2_from_h1_q1`；禁止 `_v2`）。另加纯函数把结果格式化成注入文本（例如 `format_q2_derivation_block`）。禁止平行实现把旧 `return header+table` 留在旁边。

### 输入

- `statement_kind`：`income` | `cashflow` | `balance`
- 已按有效公告日过滤后的表（行里要有报告期列，按列名取，禁止 `iloc` 切片）
- 可选：`effective_map`（用于写入两期生效公告日；没有就只写报告期）

定位同年 H1=`YYYY0630` 与 Q1=`YYYY0331`。以 **latest 报告期所在年** 为准：latest 不是 `0630` 时，本卡不派生（不要对 latest=Q3/年度顺手派生）。

### 拒绝（必须有稳定 `reason` 码，写入注入块，不得静默、不得填 0）

| reason | 条件 |
|---|---|
| `not_h1_latest` | latest 不是 `YYYY0630` |
| `balance_is_stock` | `statement_kind=="balance"`（即使两行都在） |
| `missing_q1` | 过滤后没有同年 `0331` |
| `q1_not_public` | 有 Q1 行但无有效公告日 / 不在过滤结果里 |
| `scope_mismatch` | 任一侧存在 `合并范围`/`币种`/`单位`/`会计口径`（按列名命中即可）且两边不等，或只一侧有值 |
| `no_eligible_fields` | 没有可减的金额字段（见下） |
| `percent_only` | 候选字段全是同比/环比/`%`/百分比，减了会得到无意义点数 |

缺列上报：可减字段不在表里就 skip 该字段，进 `missing` 列表；**禁止当 0**。全部 skip → `no_eligible_fields`。

缺可比性列（两边都没有范围/币种/单位/口径）：**允许**对下面金额白名单做减法。这是「同一次拉表、同一套列」的默认，不是证明合并范围相同。列一旦出现就必须比对。

### 金额白名单（按列名，禁止位置切片）

只对 **两边都能 `pd.to_numeric` 且有限** 的值做 `H1 − Q1`。

利润表（与 `utils.py` income 优先级对齐，去掉日期列）：

`营业总收入` `营业收入` `营业总成本` `营业成本` `税金及附加` `营业税金及附加` `销售费用` `管理费用` `研发费用` `财务费用` `营业利润` `利润总额` `所得税费用` `净利润` `归属于母公司所有者的净利润` `基本每股收益` `稀释每股收益`

现金流：**可以**减流量/净额；**禁止**减存量。至少排除列名含 `期末` / `期初` 的字段（例如 `期末现金及现金等价物余额` 不得 H1−Q1）。允许：`销售商品、提供劳务收到的现金`、`经营活动现金流入小计`、`购买商品、接受劳务支付的现金`、`支付给职工以及为职工支付的现金`、`支付的各项税费`、`经营活动现金流出小计`、`经营活动产生的现金流量净额`、`购建固定资产、无形资产和其他长期资产所支付的现金`、`投资活动产生的现金流量净额`、`吸收投资收到的现金`、`取得借款收到的现金`、`分配股利、利润或偿付利息支付的现金`、`筹资活动产生的现金流量净额`、`现金及现金等价物净增加额`。

列名含 `同比` `环比` `%` `百分比` `增长率` 的，即使碰巧在白名单别名里也拒绝该字段。

### 成功时的结构（frozen dataclass）

- `reported_period_label`：`{year}Q2`（**仅派生块**允许 Q2）
- `period_kind`：`single_quarter_derived`
- `derivation_formula`：`H1-Q1`（测试锁死这一字面量）
- `h1_period` / `q1_period`：YYYYMMDD
- `values`：`{列名: 差值}`，至少测一个净利润类字段
- `missing`：白名单里缺的列
- `reason`：成功时为空或 `ok`

### 注入文本

`format_q2_derivation_block` 必须让模型看见机器可读片段：

成功：

- `period_kind=single_quarter_derived`
- `reported_period_label=YYYYQ2`
- `derivation_formula=H1-Q1`
- 至少一个金额（如 `归属于母公司所有者的净利润=150` 或 `净利润=150`）
- 中文：这是 H1 累计减 Q1 得到的 Q2 单季，不是报表原始 Q2 行

失败：

- `period_kind=unknown` 或明确 `Q2_single_quarter=N/A`
- `reason=<上表码>`
- 中文：**禁止把 H1 累计当作 Q2 单季**
- **不得**出现编造的 Q2 金额

### Header / classify（回归锁）

- `classify_financial_period_kind(..., "income")` 对 `0630` 仍是 `half_year_cumulative` + `derivation_formula=not_derived`
- `financial_cutoff_header(..., statement_kind="income")` 对 H1 latest **仍含** `derivation_formula=not_derived`
- 不要把 H1 header 改成 `H1-Q1`

### Provider

`_financial_report_sina` 的 **Sina 成功** 与 **backup 成功** 两处，在 `header + table` 之后追加派生块（`statement_kind in {income, cashflow}`）。空表失败路径不派生。资产负债表走拒绝块或完全不追加（须测试锁死：balance 文本无 `single_quarter_derived` 金额）。

不要改 `_shrink_table`、公告日截断、摘要 header、资金流。

## 允许修改

- `tradingagents/dataflows/financial_announce.py`
- `tradingagents/dataflows/providers/cn_akshare_provider.py`（仅 `_financial_report_sina` 成功返回处追加派生块 + 必要 import）
- `tests/test_financial_period_kind.py`（加派生用例；**不要**改掉 P0-3a 的 `not_derived` 期望）
- `tests/test_financial_announce_cutoff.py`（生产路径；不要改既有 cutoff / `2024-08-20` 的 H1 header 期望，只追加断言）

## 禁止修改

- `resolve_effective_announce_date` / `LATE_FILING_GRACE_DAYS` 判定结果
- 资金 `period_kind`、`fund_flow_evidence.py`、`data_collector.py`、`evidence_verifier.py`
- 社交、P0-4、P0-5、前端、schema
- `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 主干快进、部署
- Q3/Q4 派生、无人调用的 `EvidenceRecord` 模块

## 阅读纪律

1. **完整读** `financial_announce.py`，grep 调用点。
2. **完整读** `_financial_report_sina` 与三个 wrapper；grep `financial_cutoff_header(`。
3. 改原路径。

## 测试（TDD）

先在 **`ba47284` 上写会失败的测试**，再改产品代码。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_period_kind.py \
  tests/test_financial_announce_cutoff.py \
  -q --tb=short
```

必须覆盖：

1. **同口径金额**：H1 净利润 250、Q1 100 → 派生 150，`period_kind=single_quarter_derived`，`derivation_formula=H1-Q1`，label `2024Q2`。
2. **无 Q1**：不派生金额；reason=`missing_q1`（或你们锁死的等价码）。
3. **范围/单位不一致**：拒绝，无差值。
4. **同比百分比**：不把 10%−8% 写成 2。
5. **资产负债表**：不派生流量差值。
6. **现金流期末余额**：有 `期末现金及现金等价物余额` 时不得出现该字段的 H1−Q1。
7. **P0-3a 回归**：`0630` 分类仍是 `half_year_cumulative` + `not_derived`；header 仍 `not_derived`。
8. **生产路径**：
   - 现有 `_FinFixtureProvider(_three_tables())` + `get_income_statement(..., curr_date="2024-08-20")`：仍含 `period_kind=half_year_cumulative`，**无**编造 Q2 金额，且含 Q2 N/A / `missing_q1` /「禁止把 H1」类声明。
   - **新夹具**（不要改坏 `_three_tables` 既有 cutoff 期望）：同年 `20240331`+`20240630` 利润表金额可减时，`get_income_statement` 文本含 `period_kind=single_quarter_derived` 与正确差值。`get_balance_sheet` 同日不得含该派生金额。

不要 `assert result is not None`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `ba47284e3284fbed70fc104901fbe354907c7bee`
- 精确 pytest 数字
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」。**不准予部署。**
- 不要 @项目调度助手催工。
