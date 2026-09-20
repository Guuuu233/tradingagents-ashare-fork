# 数据缺口 / 裁决偏多 / T+5 准确度 — 修补方案

日期：2026-08-27  
状态：**方案稿，未改产品代码**（已并入统一总纲 `work/2026-08-27-unified-final-plan.md`；施工顺序以总纲 §5 为准）  
窗口：数据与裁决审计（与舆情/社交窗口并行，合入规则见文末）

相关证据：

- `work/2026-08-26-bull-bear-skew-audit.md`（早期偏多观感）
- 本会话对 51 场用户 v2 报告的 `data_gaps` / `dispute_map` 统计
- 16 场 T+5 已到期样本的 baostock 前复权回测（符号命中 8/16 = 50%）

---

## 0. 现在不要做什么

1. **不要开** `credit_weighting_enabled`（D-006 / D-007，门槛仍 KEEP_FALSE）。
2. **不要取消** 历史日对快照源的 `snapshot_historical_refusal`（板块资金流、雪球热搜、质押、股东截面）。那是防前视，不是这次的 bug。
3. **不要** 仅因「dispute 资金流对打却判 bull」就全局改成中性/空头。16 场反事实里，能对上该规则的 2 场（中际旭创、寒武纪）T+5 都涨了；改掉后符号命中 50% → 37.5%。
4. **不要** 把本方案与舆情/社交（MediaCrawler）改动打进同一个 commit。路径几乎无重叠，但工作树已有双方脏文件。

---

## 1. 已确认的问题（按层）

### 1.1 数据账本：假缺口

`data_collector._build_source_provenance`：源文本抽不出 `YYYY-MM-DD` 的 `as_of`，就把 `fundamentals` / `balance_sheet` / `income_statement` / `cashflow` 记成「未返回可验证数据日期」。

实测：50/51 场仍有数千字财报正文和数字。失败的是日期戳，不是没拉到表。

### 1.2 数据账本：真缺口（设计如此）

全部补样都是历史交易日（相对 2026-08-27，连 08-25 也算历史）。下列接口无历史截面，**每场都会 refused**：

- `fund_flow_board`、`insider_transactions`、`hot_stocks`、`share_pledge`

`northbound_flow` 为 2024-08 后制度性停更（`unavailable`），不是本次网络故障。

个股资金流 `get_individual_fund_flow` **没有**这条拒绝，所以报告里仍有「主力净流出 xx 亿」。板块层缺、个股层往往有。

### 1.3 数据：真失败且 fail-open

中芯国际 688981 @ 2026-07-22 市场报告写明日线 OHLCV 缺失，仍给出 bear。核心行情不可用时不得出方向 winner。

### 1.4 裁决：程序对称、结果偏多

- 主张结构：几乎每场 3 bull / 3 bear claim
- `dispute_map`：bull 65 / bear 26 / tie 2（约 70% 判多方）
- 最终 winner：约 30 bull / 14 bear / 7 tie
- **不是红利股选择能解释的**：科技历史日同样偏多

机制（生成阶段，不是解析器默认 bull）：

1. 七个分析师 + 研究经理的 few-shot 全是 `direction: 看多`；`MANAGER_VERDICT` 范例 `winner: bull` 且资金流「多方占优」。
2. 「数据有方向倾向时必须选偏多或偏空」+ Hold 严格限制 → 不确定时写偏多；`normalize_winner` 把偏多/增持映射成 `bull`。
3. 资金流分单对打（有进有出）常被解读成吸筹。此规则在 16 场 T+5 里只命中 2 场，且那 2 场方向碰巧对。

亏钱的多头（歌尔、韦尔 06-09、立讯）用的是「突破 / 地量见底 / 扫筹」，**不是**对打资金流模板。无泄漏规则能事先把它们改成空头。

### 1.5 准确度：低，但是混合物

16 场 T+5 符号命中 50%。8 次 miss：

- 3 场错多（歌尔 -11.6%、韦尔 -5.5%、立讯 -1.8%）
- 3 场错空（含工业富联 **+28%**）
- 2 场中性后大动（蓝思 +23%、中建 +2.5%）

先知把 3 场错多改成空：上限约 11/16 = 69%。剩下仍有空头踏空和中性踏空。机械把所有多头改中性/空头，符号命中不会系统性变好。

`/v1/calibration` 当前 `sample_size=0`，v2 `manager_verdict` 尚未接入 T+5 校准管线。

---

## 2. 修补方案（建议拆 commit，未实施）

每个 commit 一个关注点。未获确认前不改 `tradingagents/`。

### P1 — 账本假缺口（数据清洗，单独 commit）

文件：`tradingagents/graph/data_collector.py`（`_extract_source_as_of` / `_build_source_provenance`）

- 财报类源：正文已有可解析数字、且分类器未判 failed/refused 时，**不要**因缺 ISO 日期写成「获取失败」。
- 可记 `status=available_unverified_as_of` 或等价，缺口文案区分「没数据」和「有数据无日期戳」。
- 测试：复现 50/51 假缺口的夹具；有数字无日期不得进 `data_gaps` 失败列表。
- **不改** snapshot refusal。

### P2 — 缺 K 线 fail-closed（单独 commit）

文件：市场分析师路径 + `research_manager` / `extract_and_validate_manager_verdict`

- 日线 OHLCV 明确失败时：禁止 `winner=bull|bear`，最多 `tie` + 明示不可裁决。
- 测试：用 688981 类「行情数据缺失」夹具，修复前允许方向、修复后必须 tie/拒绝。

### P3 — 提示词范例去偏（提示词，单独 commit）

文件：`tradingagents/prompts/zh.py`（及 en 对应块，若仍在用）

- 分析师 `VERDICT` few-shot 不要全是「看多」；至少中性或轮换空头范例。
- `MANAGER_VERDICT` 范例不要写死 `winner: bull` + 「资金流向多方占优」。
- Hold 限制改为：数据不足或分单冲突时 **允许且鼓励** 中性，禁止把中性当偷懒，也禁止把冲突资金流默认成偏多。
- **验收不是 T+5 立刻上升**（见 §1.5）。验收是：新跑样本的 `dispute_map` 多方占比下降、资金流对打更多 `tie`。

### P4 — 资金流分歧默认 tie（裁决规则，单独 commit）

文件：`evidence_verifier.extract_and_validate_manager_verdict` 或 manager 后处理（改原路径，禁止 `_v2`）

- 同一 `data_point` 同时含流入与流出（或超大单 vs 大单对打），`dispute_map.winner` 不得为 bull/bear，应为 `tie`；冲突项不得单独支撑最终 bull。
- 禁止「流出 = 吸筹」作为 `evidence_decision` 的通过条件（可与 smart_money 已有「仅有 netamount 不得写主力吸筹」对齐）。
- 测试：对打资金流夹具 → 该条 dispute 必须 tie。
- 明确：**不承诺** 16 场历史回测变好；上线后用新样本再测。

### P5 — T+5 校准接线（评估，单独 commit）

- 把 v2 `manager_verdict.winner` 写入 `shadow_credit_metrics.t_plus_5_direction_hit`（到期后填）。
- `/v1/calibration` 对 v2 样本 `sample_size > 0`。
- 等 2026-08-24 / 08-25 批次 T+5 到期后再报准确度，不拿 n=16 拍板。

### P6 — 门槛脚本口径（可选，单独 commit）

`scripts/verify_h1b_gates.py` 目前扫全库 completed，旧报告无 v2 winner → 分侧 0/0、行业 0。应只计 `v2_structured_disagreement`，并补 `industry` 元数据。这不修裁决质量，只修门槛数字可信度。

---

## 3. 本窗口进度（2026-08-27）

| 项 | 状态 |
|---|---|
| 宿主 tip / healthz | `aa41f44992674144eb2e320fc1962f7b0022a795`，3/1 轮次未改 |
| `credit_weighting_enabled` | 仍 False |
| 用户账号 v2 completed | 约 51–55 场（含 batch1–4 与 502 重试） |
| T+5 可验证 | 16 场，符号命中 50% |
| 前端 chat 走 v2 | `frontend/src/services/api.ts` 已加 `config_overrides.v2_debate_enabled`，**未提交** |
| 本方案 | 仅此 md，**未改** `tradingagents/` |

补样脚本（均在 `work/`，不入库逻辑）：

- `run_h1b_sample_fill.py` batch1：08-25 防御股，9/9 bull
- `run_h1b_sample_fill_batch2.py`：08-24 bear_tilt
- `run_h1b_sample_fill_batch3.py`：多日期 + 失败重试
- `run_h1b_sample_fill_batch4.py`：5–7 月 TMT 历史日（准 T+5）
- `run_h1b_sample_fill_retry502.py`：5 场 502/429 已补齐

---

## 4. 与舆情/社交窗口合入

另一窗口：MediaCrawler 社交接入，决策 **D-008**（时间分层 + archive append-only）。`PROJECT_STATE.md` / `DECISIONS.md` 指向 `docs/social_data/implementation_plan.md`；**本工作树截至 2026-08-27 ~02:52 尚未看到该文件**（可能仍在另一窗口未保存或未写入）。**未派 Multica、未改产品代码。**

合入纪律：

| | 数据/裁决窗口 | 舆情/社交窗口 |
|---|---|---|
| 预期改动 | `data_collector.py`、`prompts/zh.py`、`evidence_verifier.py`、`frontend/src/services/api.ts` | `docs/social_data/`、后续 archive/provider/collector/prompt 社交路径 |
| 禁止 | 改社交时间语义、碰 D-008 | 改 H1b flag、改 snapshot refusal、改 manager few-shot |
| 提交 | 每项 P1–P6 单独 commit；先展示 diff | 社交单独 commit；Issue 标题写「修订后 Task N」 |
| 冲突文件 | `PROJECT_STATE.md`、`AGENTS.md`、`work/h1b_gates_report.json` | 同左；合入前对这三份做一次手工合并 |

建议顺序：两边都先 **停手展示 diff** → 先合不碰 `tradingagents/` 的文档 → 再合前端 v2 override → 最后才动 collector/prompt（需用户点头 P1 起）。

HEAD 仍为 `aa41f449` 时，任何一边都还没上主干。
