# Known Issues

## Legacy report English direction on secondary surfaces

**Status:** Known audited surfaces are fixed in DAV-887 and DAV-914. Current release `6cc4e15` predates DAV-914, so the source fix is in the target mainline but the live bundle must be rebuilt and re-verified by the next release.
**Discovered:** 2026-08-04 during M5 wrap-up

### Symptom

Pre-Chinese-localization reports store their direction in English (`BULLISH` /
`LEAN_BEARISH` / `NEUTRAL` / …). The main report surfaces now handle this — the
report list and detail view show a「旧版报告」badge, and the DecisionCard maps
English directions to Chinese via `DIRECTION_ALIAS` (`localizeDirection` in
`frontend/src/utils/reportText.ts`). The audit found these historical secondary
surfaces that could render a report's raw `direction` field:

- `TrackingBoardPanel`（跟踪看板）— renders `analysis.direction` directly
- `Portfolio`（持仓页）— renders `report.direction` in the latest-report line
- `ChatCopilotPanel`（分析完成/断线恢复/浏览器通知）— rendered completion direction directly
- `AgentCollaboration`（协作图完成节点）— rendered verdict direction directly
- `HistoricalDebateDrawer`（历史总监裁决）— rendered manager verdict direction directly

DAV-887 applies the existing `localizeDirection` mapping to the first two
surfaces; DAV-914 applies it to the latter three. The target mainline has
independent coverage of 17 test files/171 tests and a successful production
build; this does not cover browser interaction, login, or a real analysis run.
The current release's live bundle check predates DAV-914, so the next release
must rebuild and re-check its assets.

### Suggested fix

The source changes are complete and add component coverage. DAV-914 passed
`npm test -- --run` (17 files / 171 tests) and `npm run build`; no data migration
is required. The current live-bundle evidence is still the earlier
DAV-887/D-021 release evidence, so see
`work/2026-09-14-p2-direction-localization.md` for the source merge and
`work/2026-09-14-frontend-live-bundle.md` for the older live check.

---

## Vendor chain collapses three outcomes into two behaviors

**Status:** Core result-type redesign fixed in DAV-69. The remaining provider
boundaries are listed below; DAV-889, DAV-895 and DAV-898 are resolved in the
current release `63d5648`.
**Discovered:** 2026-07-29 during historical-news refusal work

### Symptom

`route_to_vendor` only advances to the next provider on **exceptions**.
A provider that returns an ordinary failure / empty / refusal **string** is treated as a successful hit and **stops the chain**.

Three logical outcomes are therefore folded into two runtime behaviors:

| Semantic outcome | Desired behavior | Current behavior |
|---|---|---|
| **Refuse** — this source cannot serve the request (snapshot-only, near-window-only, missing date field, etc.) | Stop the chain *or* skip only equivalent weak sources; never pretend success | Returned as a normal string → **chain stops** |
| **Fail** — network / timeout / parse error / temporary API breakage | Try the next vendor | Exception → **fallback** |
| **Confirmed empty** — query succeeded and there is truly no data | Stop and report “confirmed none” | Returned as a normal string → **chain stops** (same as refuse) |

### Why it matters

- A **refusal** implemented only inside one provider can be bypassed if that provider raises (signature mismatch, lock timeout, schema error) and a later vendor has **no date semantics** (classic case: `get_global_news` akshare → yfinance).
- A **near-window empty** returned as `"No news found …"` looks like “confirmed none” to the model, blocks investoday/other historical-capable sources, and is the wrong affirmative conclusion for historical analysis.
- The same shape affects any multi-provider category where some vendors are date-aware and others are live snapshots.

### Affected methods (current inventory)

High risk (multi-provider chain + mixed date semantics):

1. `get_global_news` — mitigated for historical dates by router-level refuse (3b-hotfix); live path still uses chain
2. `get_news` — same mitigation for historical dates (policy A); live path still uses chain
3. `get_insider_transactions` — provider-level snapshot refuse; exception can still fall through to yfinance/AV
4. `get_fundamentals` / three statements — date truncation on CN paths; yfinance path does not use `curr_date`
5. `get_realtime_quotes` — shorter chain; lower risk after snapshot refuse
6. `cn_market_data` / `institutional_risk` tools — config default may fall back to `yfinance` when category unset (also tracked for data_vendors commit)

### Suggested fix (implemented in DAV-69)

1. Introduce an explicit result type (or exception hierarchy), e.g.:
   - `VendorRefuse(reason)` — do not fall through to date-blind vendors; optional allow-list for same-semantics peers
   - `VendorFail(error)` — try next vendor
   - `VendorEmpty(confirmed=True)` — stop; prompt says confirmed none
   - `VendorOk(payload)`
2. Until that lands, **date-blind / near-window capabilities must be refused at `route_to_vendor` (or category policy)**, not only inside a single provider.
3. Re-enabling investoday (or any historical-capable source) for news must be an **explicit same-source whitelist for both live and historical modes**, never a silent fallback hit.

Implementation notes (DAV-69):

- `VendorResult` / `VendorOk` / `VendorRefuse` / `VendorEmpty` / `VendorFail` live in
  `tradingagents/dataflows/vendor_result.py`; `result_to_prompt()` unwraps a typed
  result back to a prompt string for direct callers.
- `route_to_vendor` now interprets: plain value or `VendorOk` = hit; `VendorRefuse` =
  stop (continue only through `allow_peers`); `VendorEmpty` = confirmed none, stop;
  `VendorFail` or exception = fall through to the next vendor.
- Providers signal the new semantics: cn_akshare `get_global_news` (sina fail →
  `VendorFail`, sina empty → `VendorEmpty`), cn_akshare `get_news` empty →
  `VendorEmpty`, yfinance news/global/insider error strings → `VendorFail` and
  "No ... found" → `VendorEmpty`. Plain strings remain backward-compatible hits.
- Regression tests: `tests/test_vendor_chain_semantics.py`.

### Regression guard

3c-3 e2e guards (missing `curr_date` hard-fail, dual historical-date upper-bound comparison, provider signature whitelist) are intended to catch silent reintroduction of undated live data on historical paths. They do **not** replace a typed vendor-result redesign.

---

## Adjudicators previously lacked first-hand access to analyst reports

**Status:** Resolved for bounded first-hand evidence access in P1-D/DAV-901.
`trader` and `risk_manager` now receive the same deterministic seven-source summary;
full report passthrough remains intentionally out of scope.
**Discovered:** 2026-07-29 during 1.58MB output investigation

### Historical symptom

Before P1-D, `research_manager`, `risk_manager`, and `trader` did not receive
bounded first-hand analyst evidence directly in their prompts.  The reports were
available in state and some fields were used for memory retrieval, but that did not
provide an evidence block for adjudication.

### Prompt template coverage map (from `tradingagents/prompts/zh.py`)

`✓(摘要)` = the report is passed as a bounded first-hand **evidence summary**
(`build_evidence_summary`, ≤300 chars) rather than the full report.

| Agent | market | sentiment | news | fundamentals | smart_money | volume_price | macro |
|---|---|---|---|---|---|---|---|
| bull_researcher | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| bear_researcher | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| aggressive_debator | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| conservative_debator | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| neutral_debator | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| **research_manager** | ✓(摘要) | ✓ | ✓(摘要) | ✓(摘要) | ✓ | ✓ | ✓(摘要) |
| **trader** | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) |
| **risk_manager** | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) | ✓(七源摘要) |

`research_manager` receives `smart_money_report`, `volume_price_report`, and
`sentiment_report` as raw data for its "expected-value gap analysis", plus bounded
evidence summaries of market/news/fundamentals/macro for evidence-level cross-checks.
The macro analyst's report — previously consumed by nobody — is now wired in.
`trader` and `risk_manager` receive the deterministic output of
`build_seven_source_evidence_summary`: fixed source order, per-source limits and a
2400-character total ceiling.  They do not receive full analyst reports.

### Why it matters

Adjudicators decide direction and construct the final trade plan, but they can only
compare *how persuasively each debater argued* — they cannot independently verify
the underlying data.  In the 2026-07-29 600519 run, the fundamentals analyst gave
"中性", yet `research_manager` reached "偏空" exclusively from debate text and
volume-price signals; it had no way to cross-check the fundamentals claim.

This is an inherent structural constraint, not a bug per se, but it creates a
situation where strong rhetoric can outweigh weak evidence at the adjudication
layer.

### Fix (implemented in DAV-68 M2)

Adjudicators do **not** receive full analyst reports — that would blow up context.
`tradingagents/agents/utils/evidence_summary.py::build_evidence_summary` builds a
deterministic, bounded (≤300 chars) summary per report that keeps verifiable facts
(numbers, dates, named events), drops argumentation, strips machine-readable
blocks, and prefixes the analyst's own `VERDICT` direction as a labeled fact so the
manager can still tally verdicts.  `research_manager` receives these summaries for
market/news/fundamentals/macro.

The analyst's full report remains available in state for bull/bear to read.

**Golden-output citation-density regression (DAV-68 optimization ②).**
`tests/test_evidence_citation_density.py` drives the real adjudication chain
(research_manager → trader → risk_manager) with golden analyst reports and
golden adjudicator outputs, then asserts a per-hop **citation density** floor:
of the concrete evidence facts present in a node's input prompt, the node's
output must cite at least a minimum fraction. The metric ties the output back
to the wiring — if the evidence summaries (or the plan hand-off) are ever
dropped, the available-fact count collapses and the test fails even though a
mock LLM would still return the fixed golden output.

### Current boundary

Full analyst-report passthrough is not an open wiring bug: bounded summaries are the
deliberate context and provenance boundary.  If full reports are ever considered,
that requires a separate design covering context limits, source provenance and
evidence independence.  The remaining production evidence gap is different: a
real, authorized business path still needs trace, persisted report fields and
readback consistency; offline tests and read-only HTTP smoke tests do not prove it.

---

## Custom prompt history is not retained — old versions are unrecoverable

Status: **Partially resolved in DAV-808; standalone prompt-row history remains a known gap.**

### Symptom

`PATCH /v1/custom-prompts` replaces the user's whole prompt set (delete + insert in
one transaction), mirroring `update_role_bindings`. Each row carries a
`prompt_hash` (sha256[:12]), but the previous standalone row — and therefore the
previous prompt **text** — is gone after any edit.

### Why it matters

This remains relevant to the project's end goal (statistical calibration of
historical-date analyses), not just tidiness. It does not invalidate reports
already produced after DAV-808, because those reports carry a self-contained
resolved snapshot.

In Phase E's A/B runs, each report can be tagged with the prompt hash that produced
it.  Months later, when calibration is computed across a batch of reports, a report
tagged `hash=abc123` cannot be traced back to any prompt text: we will know that two
batches used *different* prompts, but not *what the older prompt said*.  Attributing
a calibration shift to a specific prompt change — which is precisely the question the
custom-prompt work exists to answer — becomes impossible.

### Current implementation and remaining gap

DAV-808 writes the full **resolved prompt text itself** into the report snapshot,
alongside its hash and length. Attribution for those reports is therefore
self-contained: no lookup against `user_custom_prompts` is needed, and later user
edits cannot invalidate the record. The resolved text is capped at 6000 chars
(`custom_prompt_service.RESOLVED_PROMPT_MAX_CHARS`). A future history feature may
retain standalone prompt versions, but it must not replace or silently rewrite
the existing report snapshots.

Retrieve the text via `custom_prompt_service.resolve_role_prompt()` /
`resolve_all_roles_prompts()` — do not re-concatenate global + override at the call
site, or the two implementations will drift.

---

## 同花顺（fuyao.aicubes.cn）数据源接入

**Status:** DAV-83 已接入；存在若干已知边界。  
**Discovered:** 2026-08-05

### 接入情况

- 新增 `cn_fuyao` provider：行情快照、历史日 K（前复权）、三大报表、财务指标（五类能力）、
  涨跌停池（含连板分布）、龙虎榜、交易日历。
- 路由：`fundamental_data` 以 `cn_fuyao` 为主源（失败降级现有弱源）；`get_zt_pool` /
  `get_lhb_detail` 以东财为主、`cn_fuyao` 备用；`core_stock_apis` 中 `cn_fuyao` 排第 5
  （`cn_akshare,cn_baostock,cn_investoday,yfinance,cn_fuyao`，yfinance 之后）；`realtime_data`
  中 `cn_fuyao` 提供第三备用；交易日历在 AKShare 不可用时以 fuyao 近一年日历在线对照。
- 错误码映射：`1001~1004` 参数错误显式报错；`2001/2003` Key 无效显式报错；
  `3001/3002` 标的不存在/数据未就绪、`3004` → `VendorEmpty`；**财务数据路径**
  （`get_fundamentals` / 三大报表）下 `3001` 映射为 `VendorFail`（触发降级到
  cn_akshare / cn_baostock / cn_investoday 等弱源），`3002` 仍为 `VendorEmpty`；
  `4001` 频率超限退避重试后 → `VendorFail`；`5001~5003` 服务端错误 → `VendorFail`。

### 已知边界

- **`/limit-up-ladder` 已合入并受控发布，但尚无真实业务样本**：独立 `cn_fuyao` 连板天梯能力已在
  `6612aea82e0fb3212d3682c5d09835f529ffec16` 实施，DAV-908 的**代码审核员**同 SHA 复审 PASS，
  与线上发布版本的 RT-FULL 失败集合无新增差异；当前服务已运行发布 SHA `6cc4e152...`，只做了
  只读启动/接口烟测，未调用真实天梯上游或真实分析入口。该上游端点没有日期参数，历史分析必须
  fail-closed，且不得从涨停池合成或自动回退到其他来源。
- **`3004` 映射**：`3004` 未在接口文档中单独定义，当前按「确认无数据」处理为
  `VendorEmpty`（与 `3002` 同语义）。若后续接口文档明确 3004 为「目标未覆盖/无权限」，
  应复核其是否应改为 `VendorFail`（参考财务路径对 3001 的分治处理）。
- 三大报表和 `get_fundamentals` 的 Fuyao 路径已在 DAV-902 候选
  `cd7456012fe2e0b03bd33333e6972301c1c76ddc` 增加「报告期不晚于分析日 + 披露日已核验」
  的 fail-closed 可见性约束，并保留不可用行的状态元数据；该代码已由**代码审核员**审查、
  全量回归通过并随 `026349614a3f1b92a95dc06c0515f10ebec193bc` 发布。
- 发布后的只读烟测通过，但尚未调用真实分析入口，因此没有生产历史财务报告的 PIT trace、
  report/readback 证据；不能把本地回归或只读烟测写成生产历史数据已重新整理的证明。
- **`get_fundamentals` 缺 `curr_date`：已由 DAV-889 修复**。Fuyao provider
  对该缺失输入不再回退到当前日期或取实时报告期；相关测试已进入当前发布。
- 龙虎榜 `date` 仅支持一年内（接口约束）；超出返回参数错误。
- **`get_lhb_detail` 首次 4001：已由 DAV-895 修复**。龙虎榜路径不再把
  频率超限误当作日期无数据而静默回退；相关行为和回退边界已有测试。
- **交易日历 fallback 配置优先级：已由 DAV-898 修复**。日历路径现在与
  Fuyao provider 一样优先读取配置中的 `fuyao_api_key`，再回退到环境变量。
- 交易日历 fallback 依赖 `FUYAO_API_KEY`；近一年窗口不足以覆盖更早历史查询。
- 涨跌停/龙虎榜备用链依赖 `cn_akshare.get_zt_pool` / `get_lhb_detail` 在东财失败时返回
  `VendorFail`（已改为显式 VendorFail），否则纯字符串会截断 vendor 链。
