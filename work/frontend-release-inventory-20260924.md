# DAV-1236 前端发布准备——08-28 后 10 个前端提交变更清单与影响评估（只读）

- 评估基线：后端锚定 `22a1ff3`（`22a1ff32b77614cd2a8d3593c20e7bae077e539b`），前端生产 dist 为 08-28 构建（`index-BUNRF3hH.js`）。
- 全部 10 个提交均为 `22a1ff3` 的祖先（`git merge-base --is-ancestor` 逐个验证），即这些提交的后端配套改动已包含在评估基线内。
- 评估日期：2026-09-24。只读评估，未部署、未改生产目录、未调模型、未写生产库。

## 1. 逐提交清单

### a09ca2c — wip(p0-1): snapshot run-integrity state machine for independent repair（2026-08-28）

- **改动文件**：`api/database.py`、`api/main.py`、`api/services/calibration_service.py`、`api/services/report_service.py`、`frontend/src/components/DecisionCard.tsx(.test)`、`frontend/src/pages/Analysis.tsx`、`frontend/src/pages/Reports.tsx`、`frontend/src/types/index.ts`、`frontend/src/utils/reportText.ts` + 5 个后端测试文件。
- **用户可见变化**：DecisionCard 与 Reports 列表引入新决策语义——`invalid`（无效运行）、`no_trade`（不交易）、`watch`（观望）；`INVALID_RUN`/`DATA_ERROR`/`ABSTAIN`/`PARTIAL` 状态优先于 BUY/SELL 文本判定；非可执行状态下隐藏目标价/止损/置信度并显示「非可执行状态」提示条；DecisionCard 改为「裁决摘要」折叠区、风险等级常显。
- **API/字段**：消费报告字段 `analysis_status`、`trade_action`（读取侧，非新增请求）。
- **后端支持（22a1ff3）**：✅ 同一提交内落库 `ReportDB.analysis_status`/`trade_action`（`api/database.py:448-449`，含列迁移声明），`api/services/report_service.py:47-48,1630-1635` 读写透传。
- **是否改变分析生成行为**：否（展示层 + 后端状态机字段；生成行为改动在配套后端，属该提交自身范围，非前端重建引入）。
- **风险等级**：中——见 §3 专项（wip 提交，依赖后续修复 bc2b5b2）。

### e368362 — feat(frontend): 强制 chatCompletion 携带 config_overrides.v2_debate_enabled (Track A0)（2026-09-02）

- **改动文件**：`frontend/src/services/api.ts`（+1 行）。
- **用户可见变化**：无直接 UI 变化；但所有聊天入口发起的分析内部走 v2 辩论协议。
- **API/字段**：`POST /v1/chat/completions` 请求体新增 `config_overrides: { v2_debate_enabled: true }`（无条件硬编码）。
- **后端支持（22a1ff3）**：✅ `ChatCompletionRequest.config_overrides`（`api/main.py:1286`），allowlist 含 `v2_debate_enabled`（`api/main.py:652-660`），`_build_runtime_config` 消费（`api/main.py:5263`）。
- **是否改变分析生成行为**：**是**——聊天入口全部切换到 v2 三阶段辩论协议。详见 §2。
- **风险等级**：**高**（D-037 冻结范围 + cohort 拆分）。

### c62ff8e — feat(frontend): 交互选档接线与契约隔离 (H-03b, DAV-679)（2026-09-06）

- **改动文件**：新增 `AnalysisHorizonSelector.tsx(.test)`；`ChatCopilotPanel.tsx`、`api.ts`、`api.test.ts`、`types/index.ts`。
- **用户可见变化**：聊天输入区新增「短线/中线/双档」选档器；API 错误提示可透传后端 `detail`。
- **API/字段**：`POST /v1/chat/completions` 新增 `horizons` 字段；新增 `api.analyze()` helper（`POST /v1/analyze` 带 `horizons`，默认 `['short']`）。
- **后端支持（22a1ff3）**：⚠️ **部分**。`/v1/analyze` 的 `horizons` 完整支持（`api/main.py:1205-1230` 校验解析）；但 **`ChatCompletionRequest` 没有 `horizons` 字段**（`api/main.py:1279-1288`），pydantic 默认忽略多余字段——聊天选档器选「中线/双档」时该字段被静默丢弃，聊天路径只能从用户文本 NLP 提取 horizon（`api/main.py:5292-5295`，`explicit=False`）。`api.analyze()` 在当前前端无页面调用方（仅测试覆盖）。**即：聊天选档 UI 在 22a1ff3 上是 dead control**（HEAD 88442fc 同样未加该字段）。
- **是否改变分析生成行为**：否（字段被忽略则行为不变；若后端日后支持则会改变）。
- **风险等级**：中——UI 承诺与实际行为不符（选中线仍跑短线），属功能性 bug 而非破坏。

### 8325943 — feat(frontend): 组合页定时选档与契约隔离 (H-03c, DAV-681)（2026-09-06）

- **改动文件**：`Portfolio.tsx`、`Portfolio.test.tsx`、`api.test.ts`。
- **用户可见变化**：组合页持仓行的「定时」按钮旁新增短线/中线 HorizonSwitch；开启定时分析时按所选档位创建。
- **API/字段**：`POST /v1/scheduled` 携带 `horizon`（`createScheduled(symbol, horizon, '20:00')`）。
- **后端支持（22a1ff3）**：✅ `POST /v1/scheduled` 的 `horizon` 参数（`api/main.py:7332-7355`，`scheduled_service._validate_horizon`）。
- **是否改变分析生成行为**：否（horizon 是既有后端能力，只是把档位选择权暴露到 UI）。
- **风险等级**：低。

### 9590f4c — feat(calibration): 校准面板小样本熔断与可靠性曲线保护 (DAV-758)（2026-09-08）

- **改动文件**：`api/services/calibration_service.py`、`CalibrationPanel.tsx(.test)`、`types/index.ts`、后端测试 ×2。
- **用户可见变化**：校准面板新增「已评估样本 (n)」「排除样本 (Excluded)」指标；样本不足时显示警告横幅并隐藏可靠性曲线柱体（熔断占位）。
- **API/字段**：读取校准响应新字段 `sample_sufficient`、`min_sample_size`、`insufficient_reason`、`probability_sample_size`、`excluded_counts`（`CalibrationResponse`/`CalibrationExcludedCounts` 类型）。
- **后端支持（22a1ff3）**：✅ 同提交后端实现（`api/services/calibration_service.py:152,1138-1146`，`DEFAULT_MIN_CALIBRATION_SAMPLE_SIZE=30`）。
- **是否改变分析生成行为**：否。
- **风险等级**：低（字段均为可选，旧后端亦可降级渲染）。

### 85a32f7 — fix(calibration): 统一前端校准熔断概率样本口径并隔离全部样本展示 (DAV-765)

- **改动文件**：`CalibrationPanel.tsx(.test)`、`types/index.ts`（仅前端）。
- **用户可见变化**：熔断判定改用「概率校准样本 n」口径，与全样本 `sample_size` 分列展示；无有效概率样本时的提示文案区分。
- **API/字段**：读取 `probability_sample_size`（可选字段，带 `?? 0` 回退）。
- **后端支持（22a1ff3）**：✅（同上，`calibration_service.py` 已返回该字段）。
- **是否改变分析生成行为**：否。
- **风险等级**：低。

### 4e6266b — fix(frontend): localize direction on tracking board and portfolio surfaces (DAV-887)

- **改动文件**：`TrackingBoardPanel.tsx(.test)`、`Portfolio.tsx/.test`、`reportText.test.ts`（仅前端）。
- **用户可见变化**：追踪看板与组合页的方向字段由英文原始值本地化为中文显示。
- **API/字段**：无变化。
- **后端支持（22a1ff3）**：不适用（纯展示层）。
- **是否改变分析生成行为**：否。
- **风险等级**：低。

### 8ccecb8 — fix(frontend): localize direction on chat copilot, agent collaboration, and historical debate drawer (DAV-914)

- **改动文件**：`ChatCopilotPanel.tsx`、`AgentCollaboration.tsx`、`HistoricalDebateDrawer.tsx` + 测试（仅前端）。
- **用户可见变化**：聊天副驾完成/恢复消息、浏览器通知、协作面板裁决徽标、历史辩论抽屉经理推荐徽标的方向文案中文化（带 `中性` 等回退）。
- **API/字段**：无变化。
- **后端支持**：不适用。
- **是否改变分析生成行为**：否（commit message 明确「strictly display layer, no data/business mutation」）。
- **风险等级**：低。

### 331a322 — fix(dashboard): prioritize partial analysis status（2026-09-15，cherry-pick）

- **改动文件**：`Dashboard.tsx(.test)`（仅前端）。
- **用户可见变化**：Dashboard 最近报告列表的决策列从「BUY/SELL 字符串匹配染色」改为结构化判定：优先 `analysis_status`（`PARTIAL→观望`），其次 `trade_action`/`decision`；新增 `不交易`/`无效运行` 琥珀色标签。
- **API/字段**：读取 `analysis_status`、`trade_action`（同 a09ca2c，已支持）。
- **后端支持（22a1ff3）**：✅。
- **是否改变分析生成行为**：否。
- **风险等级**：低。

### 49f1e87 — feat(verdict-contract): basis_from_rejected_claim_ids 可选字段契约与前端类型透传

- **改动文件**：`frontend/src/types/index.ts`（`HistoricalDebateManagerVerdict.basis_from_rejected_claim_ids` + `EvidenceBasisProjection`）、`tradingagents/prompts/{en,zh}.py`。
- **用户可见变化**：无（纯类型透传，无 UI 改动）。
- **API/字段**：无新增调用；消费报告内嵌 verdict 的可选字段。
- **后端支持（22a1ff3）**：✅ `evidence_verifier.py:4877-5378` 完整校验链（非法指认只落 warning）。
- **是否改变分析生成行为**：**是（后端侧）**——该提交同包修改了 zh/en research_manager prompt（增补可选字段指认规则），属于 prompt 变更；但**前端部分**（类型透传）不改变生成行为。若仅摘前端文件上线则无影响；整包上线则 prompt 改动属 D-037 相关面。
- **风险等级**：中（prompt 变更随包携带）。

## 2. 专项：e368362（强制 v2_debate_enabled）

- **携带入口**：仅聊天入口。`api.chatCompletion` 全仓唯一调用方是 `ChatCopilotPanel.tsx:722`（用于 Analysis 页与 Settings 页的副驾面板）。`api.analyze()` helper 无页面调用方；`/v1/scheduled` 定时分析在后端构造 `AnalyzeRequest`、不带 `config_overrides`。即主分析=聊天副驾同一条链，定时/批量任务不受影响——**影响面 = 全部经聊天发起的分析**。
- **辩论流程差异**：v1 为 Bull/Bear 交替、按 `max_debate_rounds` 计数后进 Research Manager（`conditional_logic.py:117-124`）；v2 为三阶段协议——opening（Bull→Bear）→ challenge（Bull/Bear 交叉质询，msg3-4）→ 条件 tiebreak（`should_enter_tiebreak`）→ manager（`conditional_logic.py:125-162`），并由 `debate_utils`/`shadow_credit`/`agent_states.py:203` 启用 v2 专属的 degenerate 检测与影子信用计量。
- **报告版本戳**：`protocol_version` 由 `v1_legacy` 变为 `v2_structured_disagreement`（`propagation.py:81-84`、`agent_states.py:139-140`），随 `result_data.protocol_version` 落库（`api/main.py:2081-2097`）。`decision_model_version` 恒为 `decision_model.v1`、`evidence_contract_version` 恒为 `evidence_contract.v2`（`report_service.py:817-828`）——**这两项不受 override 影响，区分 cohort 只能靠 `protocol_version` / `feature_flags.v2_debate_enabled`**。
- **对 H1b clean 池与测量口径**：聊天发起的报告将盖上 `v2_structured_disagreement`，而既有历史与定时任务报告为 `v1_legacy`。若 H1b 取样/清洗未按 `protocol_version` 分层过滤，同一「clean 池」将混入两套辩论协议的样本，测量口径被静默改变；若按 protocol_version 过滤，则聊天样本整体移出 v1 池，影响样本量与可比性。属 D-037 冻结的生成链改造，默认排除。

## 3. 专项：a09ca2c（wip 完整性）

- a09ca2c 自述为 wip 快照（「P0-1 is not a quality-gate commit」，Round-3 review 留有 High：risk revise 把 VALID/BUY 改写为 ABSTAIN/WAIT 后 graph 可循环）。
- **其依赖的后端状态机已在主干**：配套修复 `bc2b5b2`（同日 2026-08-28，`fix(p0-1): resolve risk revise loop, reject direction contract, and calibration exclusions`）已合入且是 `22a1ff3` 祖先；`conditional_logic.py`、`decision_status.py`、`risk_manager.py` 的修复均在基线内。之后还有 `0ff0513`（cohort 元数据持久化）、`0019d6c`（派生结果键隔离）等后续加固。
- **若上线该前端的表现**：报告卡片/列表对 `INVALID_RUN`/`DATA_ERROR`/`ABSTAIN`/`PARTIAL` 显示「无效运行/不交易/观望」并隐藏交易参数。对 22a1ff3 后端是完整闭环；**唯一风险点是若生产后端实际早于 bc2b5b2**（不含状态机修复），老报告无 `analysis_status` 字段时前端回退到旧 `decision` 文本解析，行为等价旧版，仍可降级运行。

## 4. 构建检查（只读，未部署）

- worktree：`git worktree add` 独立检出 `22a1ff32b77614cd2a8d3593c20e7bae077e539b`（detached HEAD）。
- 环境：node v24.18.0 / npm 11.16.0。
- `npm ci`：✅ 383 packages，仅 fsevents install-script allowlist 警告。
- `npm run build`（`tsc && vite build`，vite 8.2.0）：✅ **成功**，tsc 零报错；2785 modules。
- 产物：`dist/index.html` 1.15 kB；`dist/assets/index-DE8a8GuU.css` 168.82 kB (gzip 22.51)；`dist/assets/index-BMvS0Hk0.js` **1,417.83 kB (gzip 412.72)**——新 bundle hash 与生产 `index-BUNRF3hH.js` 不同，符合预期。仅有 >500 kB chunk 体积警告（既有问题）。
- 产物留在隔离 worktree，未触碰任何 `releases/*/frontend/dist` 或生产目录。

## 5. 汇总表

| 提交 | 后端支持@22a1ff3 | 改变生成行为 | 风险 |
|---|---|---|---|
| a09ca2c | ✅（含 bc2b5b2 修复） | 否 | 中 |
| e368362 | ✅ | **是（v2 辩论）** | **高** |
| c62ff8e | ⚠️ chat horizons 被静默忽略 | 否 | 中 |
| 8325943 | ✅ | 否 | 低 |
| 9590f4c | ✅ | 否 | 低 |
| 85a32f7 | ✅ | 否 | 低 |
| 4e6266b | n/a | 否 | 低 |
| 8ccecb8 | n/a | 否 | 低 |
| 331a322 | ✅ | 否 | 低 |
| 49f1e87 | ✅ | 前端否/同包 prompt 是 | 中 |

**建议裁决口径**：默认排除 e368362（D-037）；49f1e87 若整包放行则连带 prompt 变更需单议；c62ff8e 上线前需知悉聊天选档在现后端不生效（UI 先行、后端缺字段）。
