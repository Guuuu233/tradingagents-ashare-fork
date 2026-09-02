# Decisions

记录跨会话持续有效的决定。临时进度放在 `PROJECT_STATE.md`；实现细节仍以代码、测试和当前 issue 为准。

## D-009：决策语义四元拆分优先于继续堆局部闸（已采纳）

- 日期：2026-08-27
- 状态：有效（P0/P1/P2-Gate4 与 Track A5–A12 已在主干 `98fe5d1`；生产未部署）
- 决定：
  1. 采纳 `work/2026-08-27-audit-decision-semantics-plan.md` 为**决策语义 / PIT / 回测污染**权威施工设计；日常派工入口为 `work/2026-08-27-decision-semantics-workflow.md`。
  2. 禁止把「上游失败 / 前视 / 证据冲突 / 方向未确认」坍缩为 Neutral、HOLD 或合格的 `completed` 样本。必须拆分 `analysis_status`、`direction`、`trade_action`、`risk_status`（及 `confirmation_state`）。
  3. 施工顺序：**P0（状态机 + EvidenceRecord + period_kind + 资金语义/cluster + 去人格化）→ P1（事件覆盖 / capitulation / 回测校准隔离 / provider 红灯）→ P2（社交 Task 5–15）**。与 `unified-final-plan` Track A 冲突时以本决定与审计稿为准。
  4. 社交基建可并行，但不得与 P0/P1 混 commit；active / 删 `legacy_proxy` 仍走既有 Gate；未过 Gate 不得宣称社交接入完成。
  5. 回测与校准只接收 `analysis_status=VALID` 且动作语义明确的样本；`INVALID/ABSTAIN/NO_TRADE/WAIT` 必须排除并计数。禁止价格不足时缩短 `hold_days`。
  6. R1/R2/R3 离线 fixture 齐备并通过前，不得声称历史案例“已修复”；只能声称设计可执行。
- 原因：本地核验 `300433.SZ@2026-05-06` 报告 `f8724342` 七分析师全 502 仍落库 `completed/HOLD/25`；`api/main` 仅认 BUY/SELL/HOLD；资金流 guard 写 `direction=中性`；校准只筛 lifecycle `completed`。局部闸无法消除统计污染。
- 影响：P0/P1/P2-Gate4 与 Track A5–A12 已合入至 tip `98fe5d199e8874ae829d2b492882d82339c836f0`（生产未部署）。加权仍保持关闭（`credit_weighting_enabled=False`），不改 3/1 轮次与用户模型绑定。主干合入仍严格执行 D-010 独立审核员 + Cursor「准予合入」流水线；未过 Cursor「准予部署」不得上线。旧 `decision` 字段可兼容，统计主键切新状态。

## D-010：主干合入与部署的最终验收权在 Cursor（已采纳；2026-08-30 补强审核闸）

- 日期：2026-08-28（补强：2026-08-30）
- 状态：有效
- 决定：
  1. David 指定 Cursor 为总控。Multica「项目主管」或「独立代码审核员」单独通过 **不足以** Fast-Forward `codex/dav-4-p2a-trunk` 或生产部署。
  2. **合入前强制流水线（缺一不可）**：
     1. 开发：隔离分支 + 单关注点 commit + 定向 pytest 证据 → `in_review`
     2. **独立代码审核员**（`aa01a41a-c3da-4021-9e45-a592ac77166c`）：对**完整 40 位候选 SHA**只读复审，书面给出 ✅通过 / ⚠️有条件通过 / ❌打回（须含文件路径与行号证据）
     3. **Cursor**：在独立 worktree 对**同一 SHA**复跑测试并做契约/白名单复核；仅当评论同时写出完整 40 位 SHA 与「准予合入」或「准予部署」时，才可开运维 FF/部署卡
  3. 独立审核员 PASS、项目主管「建议合入」、运维 pytest 绿，**均不得**直接 FF。禁止跳过步骤 2 直接由 Cursor「准予合入」代替独立审核员（紧急热修须在评论中显式写「跳过独立审核的理由」并经 David 口头确认——默认不允许）。
  4. 独立审核员与开发者不得互相改对方分支；审核卡只读。打回则开返修卡，禁止带病 FF。
  5. DAV-462 / DAV-464 在 Cursor 复审前已 FF，属过程事故；P2-T5…T11 曾缩成「仅 Cursor 隔离复测」——自本补强起恢复独立审核员闸，不作为免审先例。
- 原因：P0-1 曾在复审前被合入；近几刀社交卡为赶进度跳过独立审核员，削弱第二双眼睛的质量保障。
- 影响：调度助手不得把审核员 PASS 升级成合入。当前 **禁止部署**。下一张编码卡起必须挂独立审核步骤。

## D-008：社交 archive 时间分层与 append-only 快照

- 日期：2026-08-27
- 状态：有效（方案层；产品代码尚未实施）
- 决定：
  1. MediaCrawler `add_ts` 只映射为 `first_seen_at`；`last_modify_ts` 只映射为 `snapshot_at`。二者都是爬虫库务时间，不得解释为平台正文时间。
  2. 平台源时间：小红书用 `time` / `last_update_time`；抖音用 `create_time`。`last_update_time` 的可靠性单独验证，验证通过前不参与历史资格。
  3. 互动指标资格一律 `snapshot_at <= cutoff`。`ingest_at` 只用于导入审计，永不参与资格判断，也不得回填缺失时间。
  4. TradingAgents social archive 必须 append-only snapshot，不继承 MediaCrawler 对工作行的 update-in-place。
- 原因：钉住 SHA `d6f7c5bb` 下，`last_modify_ts` 由爬虫写入、注释写明是 DB 记录更新时间；XHS `update_content` 更新互动数与 `last_update_time` 但不更新 `desc`；DY 对已存在行逐字段覆盖。把库务时间当成源内容时间会把后补抓取和未来互动数带进历史分析。
- 影响：实施方案见 `docs/social_data/implementation_plan.md`。废止「`content_observed_at=add_ts` / `metric_observed_at=last_modify_ts` 当作源时间」的映射。未确认前不派 Multica。

## D-007：信用加权 flag 用户已预批准，但仍受门槛门禁

- 日期：2026-08-26
- 状态：有效
- 决定：用户口头批准开启 `credit_weighting_enabled`；**在 `verify_h1b_gates` 输出 `ELIGIBLE_FOR_ACTIVATION` 之前，生产端 flag 必须保持 `False`**（当前实测 `KEEP_FALSE`）。门槛通过后，无需再次征询即可把 flag 置为 `True` 并部署。
- 原因：D-006 与门槛草案要求系统级门槛全部通过后才允许加权；当前库 689 份报告仍未过 N/分侧/时间/平衡等多维门槛（单标的占比约 45%、行业数 0、多头占比约 87% 等）。
- 影响：批准记入决策账本；不改变默认 flag；继续积累合格周评样本与 `h1b_gate_samples` 注入路径。

## D-006：P3 H1b 激活门槛与分层隔离（已批准）

- 日期：2026-08-26
- 状态：有效
- 决定：采用 `work/p3-h1b-activation-gates-draft.md` 推荐默认值；架构取分层隔离（系统级门槛不过则全员 shadow；单模型偏置仅 clamp 该模型权重为 1.0，异常模型占比 >50% 才全局回 shadow）。`credit_weighting_enabled` 默认 false。
- 量化门槛摘要：N≥60 / 标的≥20 / 行业≥5；bull·bear 各≥25；≥45 自然日且≥30 交易日；T+5 完整率≥95%；多空比例∈[40%,60%]；Δverified≤18%；权重系数∈[0.85,1.15]。
- 原因：规格 §11.1 要求书面批准后方可加权；评估师 Conditional Pass 推荐路径 B 以兼顾可用性与鲁棒性。
- 影响：解锁 H1b 实施卡；未过门槛或关 flag 时不得影响总监裁决。

## D-001：Hermes 保持原位并作为原始历史来源

- 日期：2026-08-24
- 状态：有效
- 决定：不迁移、不覆盖、不清理 `~/.hermes` 中的 memory、session、SQLite 数据库或配置。Cursor 只读取仓库内整理后的共享上下文和脱敏归档。
- 原因：保持 Hermes 原有 session、memory 和 Multica 调度工作流不变，同时降低迁移损坏和隐私泄露风险。

## D-002：共享上下文采用分层读取

- 日期：2026-08-24
- 状态：有效
- 决定：固定读取顺序为 `AGENTS.md` → `PROJECT_STATE.md` → `DECISIONS.md`。历史会话仅在需要追溯时按关键词检索。
- 原因：让 Cursor 获得连续性，同时避免把完整历史放进 every-turn prompt，减少上下文噪声和旧指令污染。

## D-003：项目历史只保存相关、脱敏、可检索的副本

- 日期：2026-08-24
- 状态：有效
- 决定：只归档与 TradingAgents/Multica 工作直接相关的 Hermes 会话；排除 cron 和无关对话。导出必须使用 Hermes 脱敏，并在本地再次扫描常见凭据格式。
- 原因：共享项目证据与私人历史应严格分离。原始完整记录继续由 Hermes 数据和独立备份保存。

## D-004：不在进行中的 Multica 流水线上切换运行时

- 日期：2026-08-24
- 状态：有效
- 决定：本次只确认 Cursor runtime 可用，不改现有 Agent 绑定，不重启 daemon。任何切换必须等相关任务结束后另行明确授权并单独验证。
- 原因：运行时切换可能中断当前 issue 或改变执行环境，不能作为上下文迁移的附带操作。

## D-005：实时状态优先于交接文档

- 日期：2026-08-24
- 状态：有效
- 决定：`PROJECT_STATE.md` 只提供最近核验快照。分支、HEAD、脏工作树、issue、Agent 和 runtime 状态必须在每次开工前重新查询。
- 原因：避免后续 Agent 根据过期状态继续执行或覆盖他人工作。
