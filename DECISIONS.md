# Decisions

记录跨会话持续有效的决定。临时进度放在 `PROJECT_STATE.md`；实现细节仍以代码、测试和当前 issue 为准。

## 当前有效决定与执行口径（2026-09-14）

以下条款覆盖本文件中较早的施工快照；旧 SHA、旧服务状态和旧角色名称只作历史记录。

### D-013：合入、部署和状态收口由总工按证据放行（有效）

- David 已明确将合入、部署和状态收口的放行权交给总工；这不是降低门禁，也不是允许 agent 自授权。
- 每次放行仍必须核对完整候选 SHA、直接父、白名单、`git diff --check`、同 SHA **代码审核员**复审、RT-FULL、远端回读；部署还必须有 SQLite 备份、完整性/计数核对、`/healthz` 精确回读和启动恢复证据。
- 生产库写入、真实社交采集/Cookie、启用社交 active、凭据轮换、历史重写和信用加权仍是独立红线；本决定不包含这些授权。

### D-014：代码审查统一派给代码审核员（有效）

- 后续代码审查、交付审查和合入前质量审查派给 `代码审核员`（必要时 `代码审核员2`），不得派给「独立代码审核员」。
- 实施者与审查者必须分离；完整 SHA、隔离 worktree、白名单、红队场景和 RT-FULL 门禁不变。

### D-015/D-016：custom_prompt 六项契约及 DAV-808 实施（有效，已完成）

- custom_prompt 纳入 E-02 硬约束：保存/启动/研究经理三层共用确定性判定；命中时保存拒绝，已存或绕过入口的文本使整次任务 `NO_TRADE`，五个可注入角色均不再接收该文本；不自动删库、不静默改写。
- 现有全局提示词保留并记录 hash；DAV-808 已完成审查、合入和部署。该决定不解锁 H1b、信用加权或真实社交采集。

### D-017：V-03a provenance 返修（有效，已合入并部署）

- DAV-866 的 provenance 返修已合入；随后随 E-04/Fuyao 修复部署到 `63d5648bca7c49f57e1211d848cbc1d02ff6b3a5`。
- V-03a 仍只能作为隔离副本上的半成品进度基线，不能写成正式收益结论；详见 `PROJECT_STATE.md` 和 `work/2026-09-14-v03a-readonly-63d.md`。

### D-018：P1-D bounded 七源一手证据摘要（有效，已合入并部署）

- trader 与最终 `risk_manager` 只接收由代码确定性组合的七源证据摘要：固定来源顺序、单源/总长度上限、空/失败状态和来源标签均保留；不把七份报告全文透传给模型，不把分析师数量当作票数。
- 候选完整 SHA `9d702e7522c94bf3ac983cb1ede10943cfca1a4b`，直接父 `51b8b155ef3d47e99dead8ce4960562cd9a77c9e`；DAV-901 已由**代码审核员**对同一 SHA 只读通过，关联集合 160 项通过；对线上 `63d5648` 的 RT-FULL 为双方各 18 failed、候选 4415 passed、基线 4406 passed，失败集合双向差集为 0。
- 上线前已备份并校验生产 SQLite；上线后 `/healthz` 精确回读 `9d702e7`，provider health、只读行情和 social disabled 烟测通过，reports 计数与数据库 SHA 未变。本决定不授权真实分析、生产数据写入、社交采集、信用加权或历史重写。

### D-019：P1-E Fuyao 财务披露日 PIT fail-closed（有效，代码已合入）

- DAV-902 候选完整 SHA 为 `cd7456012fe2e0b03bd33333e6972301c1c76ddc`，直接父为
  `251fd00aa8a9584850cae5d4ab5adfbd3c5d3b94`；白名单严格为
  `tradingagents/dataflows/providers/cn_fuyao_provider.py` 与
  `tests/test_cn_fuyao_provider.py`。
- DAV-903 已由**代码审核员**对同一完整 SHA 只读 PASS。固定 Python 3.10 的专项关联集合为
  `91 passed` 与 `295 passed, 3 deselected`；没有把开发方 Python 3.14 的输出当作证据。
- 同口径 RT-FULL 使用 `env -u PYTHONPATH .../.venv310/bin/python -m pytest -q`：基线
  `9d702e7` 为 `18 failed / 4415 passed`，候选为 `18 failed / 4426 passed`，双方失败集合
  双向差集均为空，新增失败为 0。
- 候选已从 `251fd00` 线性合入 `cd7456`；治理文档随后作为线性后代落账。此决定不把代码合入
  视为部署：线上仍运行 `9d702e7`，发布前必须重新备份 SQLite、核对完整性/计数、受控启动、
  `/healthz` 精确回读和只读烟测。
- 本决定不授权真实分析、生产数据写入、真实采集、Cookie、信用加权或历史重写；P1-E 只改变
  Fuyao provider 的可见性判断和对应测试。

### D-020：P1-E 受控发布（有效，已完成）

- 发布版本为 `026349614a3f1b92a95dc06c0515f10ebec193bc`，其代码父为 P1-E 候选
  `cd7456012fe2e0b03bd33333e6972301c1c76ddc`；发布副本为
  `/private/tmp/ta-release-p1e-0263496-20260914`。
- 旧服务 PID `6509` 已正常停止；新服务 PID `19213` 在 8000 运行，工作目录已核对；
  `/healthz` 的完整 `commit_sha`/`build_identity` 均精确匹配发布 SHA。
- provider health、600519.SH 两日只读 K 线、social disabled 三项烟测通过；未调用真实分析入口。
- 生产 SQLite 仍为 285339648 bytes、SHA-256
  `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`，`quick_check=ok`，
  reports `1409/793/616`。发布备份 `work/tradingagents.db.bak-20260914-predeploy-0263496` 的
  `quick_check=ok`、reports `1409/793/616`；其 SQLite 一致性备份 SHA 为
  `df628f4293069a820ef69a136a1acbb0e7a517f6638dd24a9faeea30f5b51b9e`。
- 本决定只完成代码发布和只读运行核验，不授权生产业务写入、真实采集、Cookie、信用加权、
  历史重写或将本次发布宣称为生产财务 PIT 业务证据。

### D-021：当前发布的前端 live bundle 验收（有效，已完成）

- 在发布版本 `026349614a3f1b92a95dc06c0515f10ebec193bc` 的实际运行副本中重建前端：
  `npm test -- --run` 为 15 个文件/144 个测试全过，`npm run build` 成功。
- 8001 预启动和 8000 重启后的实际 HTTP 入口均返回网页 `200`，页面标题为
  `TradingAgents Dashboard`，新 JS 资源返回 `200`；API 未知路径仍返回 `404`。
- 8000 当前服务已加载该 bundle；资源 SHA 与构建命令记录在
  `work/2026-09-14-frontend-live-bundle.md`。后续发布不得沿用本次 hash，必须重建并复验。
- 本决定只覆盖构建物和 HTTP 入口，不等于浏览器交互、登录、真实分析或生产数据库写入授权。

### D-022：P1-F 连板天梯只按固定当前窗口接入（有效，已实施合入，尚未部署）

- 官方 Fuyao `/api/a-share/special-data/limit-up-ladder` 不接受日期参数，只返回固定近 30
  个交易日矩阵；因此内部能力 `get_limit_up_ladder` 必须把请求基准日期用于本地 PIT 门禁，
  不能把当前窗口伪装成历史快照。
- `cn_fuyao` 是唯一来源；不得从 `get_zt_pool` 合成、不得自动回退到其他 provider、不得
  裁剪未来日期或用 `iloc`/日期回退掩盖窗口不匹配。六个板块、来源、窗口、`seal_nextday`
  的未知值和 typed failure/unavailable 语义必须保留。
- 天梯只进入独立的 `market_attention.limit_up_ladder` 背景字段，不接入交易方向、博弈论
  信号、收益评估、H1b、数据库回填或前端；历史分析遇到该能力时 fail-closed，但不阻断整单。
- 设计和红队清单见 `work/2026-09-14-p1f-limit-up-ladder-design.md`。实施卡交付后必须由
  **代码审核员**对同一完整 SHA 只读审查，再跑与当前发布版本同口径 RT-FULL；本决定不授权
  部署、真实分析、生产数据写入或真实社交采集。
- 实施候选为 `6612aea82e0fb3212d3682c5d09835f529ffec16`，直接父为
  `623c37a71f7a50fdf9945f9158f78cf9f57e5b0a`。DAV-908 的**代码审核员**同 SHA 复审 PASS；
  与线上发布基线 `026349614a3f1b92a95dc06c0515f10ebec193bc` 的 RT-FULL 为基线
  `19 failed / 4425 passed / 1 skipped / 3 deselected`、候选
  `19 failed / 4445 passed / 1 skipped / 3 deselected`，失败集合双向差集为空。候选已线性合入
  `origin/codex/dav-4-p2a-trunk`，但本决定仍不授权本次部署；线上服务继续运行 `0263496...`。

### 当前不变的原则

- D-009 的状态拆分、PIT、证据独立性和统计排除原则仍有效；D-012 的红队完备性、逐条实跑和 RT-FULL 仍是行为类候选的硬门槛。
- 当前施工主干已包含 P1-F 候选 `6612aea82e0fb3212d3682c5d09835f529ffec16` 及其后续治理文档；线上服务仍运行发布 SHA `026349614a3f1b92a95dc06c0515f10ebec193bc`。完整远端文档 HEAD、运行态、数据库计数和剩余工作以 `PROJECT_STATE.md` 及发布后回读为准。

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

## D-010：主干合入与部署的最终验收权在 Cursor（已被 D-011/D-013/D-014 取代，仅存档）

> 本节保留历史流水线原文，不得据其中的 Cursor 或「独立代码审核员」名称派发新任务；当前规则见本文件顶部的 D-013/D-014。

- 日期：2026-08-28（补强：2026-08-30）
- 状态：仅存档；隔离分支、单关注点、只读审核和禁止带病 FF 等原则由后续决定继承
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
