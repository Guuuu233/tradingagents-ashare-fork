# D-037 准备线 A：probability 覆盖率与缺失根因诊断报告

- 生成时间（UTC）：2026-09-23 14:56:41Z
- 输入库：`../scratch/tradingagents.db.snap-20260923`（`mode=ro` 只读；quick_check=ok）
- 主样本：真实账户 `429163f7-50b6-4982-8bdf-96ae99506843`；全库作参照
- reports 总数：1816；真实账户：719（completed=489）

## 1. probability 非空覆盖率

#### 真实账户（completed 口径单列）

### 真实账户·按月份（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| 2026-07 | 22 | 0 | 0.00% |
| 2026-08 | 148 | 7 | 4.73% |
| 2026-09 | 319 | 12 | 3.76% |

### 真实账户·按入口（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| api:api | 205 | 16 | 7.80% |
| chat_or_query | 100 | 3 | 3.00% |
| legacy_unknown | 1 | 0 | 0.00% |
| scheduled_likely | 183 | 0 | 0.00% |

### 真实账户·按 cohort 三元组（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| (none) / (none) / (none) | 224 | 8 | 3.57% |
| decision_model.v1 / evidence_contract.v1 / price_basis.unspecified | 262 | 10 | 3.82% |
| decision_model.v1 / evidence_contract.v2 / price_basis.unspecified | 3 | 1 | 33.33% |

### 真实账户·按 analysis_status（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| (null) | 170 | 7 | 4.12% |
| ABSTAIN | 234 | 0 | 0.00% |
| INVALID_RUN | 2 | 0 | 0.00% |
| VALID | 83 | 12 | 14.46% |

#### 全库参照（completed 口径单列）

### 全库参照·按月份（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| 2026-07 | 403 | 0 | 0.00% |
| 2026-08 | 328 | 7 | 2.13% |
| 2026-09 | 321 | 12 | 3.74% |

### 全库参照·按入口（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| api:api | 226 | 16 | 7.08% |
| chat_or_query | 100 | 3 | 3.00% |
| legacy_unknown | 390 | 0 | 0.00% |
| scheduled_confirmed | 139 | 0 | 0.00% |
| scheduled_likely | 197 | 0 | 0.00% |

### 全库参照·按 cohort 三元组（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| (none) / (none) / (none) | 785 | 8 | 1.02% |
| decision_model.v1 / evidence_contract.v1 / price_basis.unspecified | 264 | 10 | 3.79% |
| decision_model.v1 / evidence_contract.v2 / price_basis.unspecified | 3 | 1 | 33.33% |

### 全库参照·按 analysis_status（completed）

| key | reports | probability 非空 | 覆盖率 |
|---|---|---|---|
| (null) | 731 | 7 | 0.96% |
| ABSTAIN | 234 | 0 | 0.00% |
| INVALID_RUN | 4 | 0 | 0.00% |
| VALID | 83 | 12 | 14.46% |

## 2. 非空样本来源回溯

| report_id | 月份 | analysis_status | probability | result_data.probability | 命中字段 | 命中片段 |
|---|---|---|---|---|---|---|
| `083157cd` | 2026-08 | - | 0.3 | 0.3 | trader_investment_plan | 概率10% |
| `14a41362` | 2026-08 | - | 0.65 | 0.65 | final_trade_decision | **上涨概率**：0.65 |
| `65680355` | 2026-08 | - | 0.45 | 0.45 | final_trade_decision | 胜率45% |
| `75f2082e` | 2026-08 | - | 0.28 | 0.28 | final_trade_decision | **Probability**: 0.28 |
| `8fe83e03` | 2026-08 | - | 0.62 | 0.62 | - | - |
| `f0a52c2b` | 2026-08 | - | 0.35 | 0.35 | final_trade_decision | 概率为35% |
| `f8d09a28` | 2026-08 | - | 0.35 | 0.35 | - | - |
| `052af159` | 2026-09 | VALID | 0.32 | 0.32 | - | - |
| `07ee2029` | 2026-09 | VALID | 0.42 | 0.42 | - | - |
| `2548aa7d` | 2026-09 | VALID | 0.28 | 0.28 | - | - |
| `3ce94484` | 2026-09 | VALID | 0.35 | 0.35 | - | - |
| `8a347575` | 2026-09 | VALID | 0.35 | 0.35 | - | - |
| `95e42f65` | 2026-09 | VALID | 0.25 | 0.25 | - | - |
| `96bb2bdc` | 2026-09 | VALID | 0.62 | 0.62 | - | - |
| `ad822503` | 2026-09 | VALID | 0.28 | 0.28 | final_trade_decision | 上涨概率 0.28 |
| `b3c47467` | 2026-09 | VALID | 0.35 | 0.35 | - | - |
| `b66f0b0b` | 2026-09 | VALID | 0.48 | 0.48 | - | - |
| `c78fc9c8` | 2026-09 | VALID | 0.42 | 0.42 | - | - |
| `e79aaed3` | 2026-09 | VALID | 0.48 | 0.48 | - | - |

## 3. 反事实测量：正则 fallback 在文本上的可命中率

对全部 completed 报告回放同一组 `_PROBABILITY_PATTERNS`（api/services/report_service.py:1114-1119 (_PROBABILITY_PATTERNS)），统计「文本中本可命中」vs「列值实际非空」：

| 集合 | completed | 列值非空 | 文本可命中(任一字段) | 可命中但列为空 |
|---|---|---|---|---|
| 真实账户 | 489 | 19 | 17 | 11 |
| 全库 | 1052 | 19 | 17 | 11 |


## 4. 根因定位（代码证据）

### 4.1 产生点全图

probability 写入 `reports.probability` 列的全部路径：

1. **生成侧契约（提示词）**
   - `tradingagents/prompts/zh.py:189-190`（bull_prompt 口径约束）、`zh.py:244-245`（bear_prompt）、`zh.py:337-338`（研究经理/裁决段）：只定义 probability **语义**（主周期末价高于基准价的上涨概率，缺条件写 null），**不要求任何机读字段输出 probability**。
   - 机读块 schema 均无 probability 键：VERDICT 只有 direction/reason（`zh.py:393-394`、`zh.py:555` trader_system_prompt 末尾）；DEBATE_STATE new_claims 只有 claim/evidence/confidence/target_claim_ids（`zh.py` bull/bear STAGE_OUTPUT_CONTRACT 段）；MANAGER_VERDICT 模板含 winner/direction/position_pct/entry/target/stop_loss/upside/downside/odds 等，**无 probability**（`zh.py:393`）。
   - `decision_status.py:890` 与 `:1375` 会读 `mv.get("probability")`/`raw.get("probability")`——即下游已预留消费位，但上游契约从不下发该键。
   - 英文 prompts 同样只有语义约束（`en.py:71-72,112-113,184-185`）。

2. **抽取侧**
   - LLM 结构化抽取 `extract_structured_data`（`api/services/report_service.py:1047-1099`）：prompt 第 6 条要求 probability，"报告未明确给出则为 null；禁止用 confidence 换算"。这是 `StructuredReport.probability`（`report_service.py:308`）唯一来源。
   - 正则 fallback `_PROBABILITY_PATTERNS`（`report_service.py:1114-1119`）+ `_extract_probability_regex`（`:1143`），在 `resolve_report_fields`（`:1248-1264`）中按 result_data.probability → final_trade_decision → trader_investment_plan → judge_decision 顺序兜底。
   - 入列：`create_report` `effective_probability = validated_probability or resolved["probability"]`（`report_service.py:1538`），写列 `report_service.py:1626`（更新路径）与 `:1690`（新建路径）。
   - `_apply_structured_report_fields`（`api/main.py:2714`）把 `result["probability"] = structured.probability`——LLM 抽取为 null 时覆盖为 None，再由 create_report 内 resolved 正则兜底。

3. **清零/抑制路径**
   - `apply_decision_status_to_result`（`decision_status.py:1331-1340`）：analysis_status ∈ {INVALID_RUN, DATA_ERROR, ABSTAIN, PARTIAL} 或 trade_action ∈ {NO_TRADE, WAIT} 时 `result["probability"] = None`。
   - `create_report` 双周期/非方向同样置空列值（`report_service.py:1640-1660`）；多周期入口 `api/main.py:3680,3706` 直接传 `probability=None`。
   - 手动接口 `POST /v1/reports`（`api/main.py:5530-5543`）可显式写 probability（非自动管线来源）。

### 4.2 实测结论

- 全库 1816 行中 probability 非空仅 **19 行**，且 **19/19 全部属于真实账户、全部带 custom_prompt_snapshot**（快照内含用户自定义的「probability 与 confidence 字段语义」章节）。默认提示词管线下 **0 条**非空。
- 真实账户 completed 中，custom prompt 含 probability 语义的报告 467 份，仅 19 份产出数值（4.07%）——即便自定义提示词要求，绝大多数报告仍因「主周期/基准价/定量依据不明确 → 写 null」或正文未写数值而缺失。
- 非空样本来源：6/19 能被正则直接在 final_trade_decision/trader_investment_plan 命中（如 `**上涨概率**：0.65`、`胜率45%`、`概率为35%`）；其余 13/19 文本写法为 `上涨概率（Probability）：**0.48**` 这类**正则无法命中**的格式（`（Probability）` 插入打断了 `概率...[:：]0.xx` 模式），只能靠 LLM 抽取器兜底 → **抽取器是主产线，正则是半失效的备胎**。
- 反事实：completed 报告中文本可被现有正则命中但列值为 NULL 的共 11 例，其中 10 例为 2026-08 旧样本（analysis_status 为空，于 2026-09-04 被批量 update——当时抽取链尚不存在/未生效）；另 1 例 `97dcc824…`（2026-09-18，VALID）`result_data.probability=0.35` 但 `reports.probability` 列为 NULL —— **列与 result_data 写路径不一致的实锤样本**（疑为 update_report_partial 或 decision_status 回填只写 JSON 未回写列，待查）。

### 4.3 结论

缺失根因 = **生成侧从未要求输出 probability（主因）+ 抽取侧正则覆盖率不全（次因）+ 个别持久化不一致（零星）**。这不是"抽取失败"型 bug 为主，而是契约设计如此：机读块不携带 probability，正文口径允许写 null。

## 5. 修复建议（只给建议，不施工）

按优先级：

1. **生成侧契约补齐**：在 VERDICT/MANAGER_VERDICT 机读块加 `probability` 键（缺条件仍允许 null，与 L1 语义诚实一致），下游 `decision_status.py:890` 已能直接消费。改动点：`zh.py:393` MANAGER_VERDICT 模板、`zh.py` 各 VERDICT 行模板、对应 en.py、`report_quality_gate.py` 契约校验白名单。风险：改 VERDICT schema 会动所有存量判定/测试 fixture（`tests/test_verdict_extraction.py` 等），需同步；模型乱填概率的幻觉风险需靠"缺条件写 null"纪律+抽检控制。
2. **正则会同步**：`_PROBABILITY_PATTERNS` 增加容忍 `（Probability）`/`**` 穿插的形态（当前 `上涨概率（Probability）：**0.48**` 不命中）。改动点 `report_service.py:1114-1119`，低风险，建议顺手做。
3. **列/JSON 写一致性**：排查 `97dcc824` 类样本（VALID 且 result_data.probability 非空但列 NULL）的写入路径，确认是否存在绕过 `create_report` 的 updater 或 decision_status 回填未同步列。
4. **回填策略**：若决定补齐历史，注意 legacy 列样本（10 例文本可命中）回填需标注 cohort（DAV-604 规则禁止静默回填进 clean cohort）。

## 6. F1「T+10 行业相对概率」与现有 probability 语义对比

- 现有 `reports.probability`："主分析周期期末价格高于分析基准价的**绝对上涨概率**"（`report_service.py:1084-1088` 抽取 prompt；`zh.py:189` 口径）。
- F1 协议目标（`ROADMAP.md:28`）：冻结经验包能否改进 **T+10 行业相对概率**——即相对行业基准的超额口径，且评估 horizon 固定 T+10。
- **语义不同**：绝对 vs 相对（行业基准）、主分析周期（short/medium 不定）vs 固定 T+10。直接复用现有 probability 字段做 F1 会混口径；若 F1 落地需要新字段或明确的映射约定（例如 probability 固定为 short 周期 T+10 口径 + 另存行业相对值）。

## 7. 入口分类方法说明（启发式）

优先级递减：`scheduled_confirmed`（report.id 命中 scheduled_analyses.last_report_id/last_job_id，仅覆盖每个定时任务最近一次）→ `api:*`（result_data.workflow_context.request_source，仅新路径持久化，见 `propagation.py:193`）→ `scheduled_likely`（user_intent 存在但无 raw_query 键，对应 `_build_scheduled_analyze_request` 形态 `api/main.py:244-247`）→ `chat_or_query`（user_intent.raw_query 非空，chat 预解析或 /v1/analyze 带 query，二者无法再细分）→ `legacy_unknown`。scheduled_likely 可能混入早期无 raw_query 的 API 请求，视为上界。
