**规格与实施父卡。** 来源：DAV-806 复审卡 DAV-807 的红队场景 RT-13。David 于 2026-09-11 裁定：RT-13 不纳入 DAV-806 验收，另开本卡决定是否把自定义提示词纳入 E-02 硬约束。六项产品契约已由 D-015 定稿，David 于 2026-09-13 明确授权进入实施阶段，实施由子卡 DAV-856 承担。本卡不得表述为「E-02 已全局闭合」，最终仍须经过同 SHA 代码审查、全量对照和独立放行。

## 现状（历史核验；实施基线以 DAV-856 开工时回读为准）

- E-02 提示词守卫 `_apply_relation_prompt_guard`（`tradingagents/agents/managers/research_manager.py`）只检查 `get_prompt("research_manager_prompt")` 返回的内置模板；`tradingagents/prompts/catalog.py` 没有模板覆盖机制。
- 用户自定义提示词经 `build_injection_slots(custom_prompt, placement, role_key="research_manager")`，在守卫之后通过 `.format` 进入提示词，不受守卫。来源链路：`api/main.py` 的 `_resolve_and_freeze_custom_prompts` → `api/services/custom_prompt_service.py` 的 `resolve_all_roles_prompts`（表 `user_custom_prompts`，分 global / group / role 三级）→ `tradingagents/graph/setup.py` 把 `custom_prompts["research_manager"]` 传给研究经理。
- 实测：Claude 探针与 Codex 复审都确认，自定义文本（例如「按权重计票，并按 cluster_id 汇总」）会原样进入 LLM 提示词，且照常发生 1 次 LLM 调用。

## 当前生产库（只读查询，2026-09-11；不得写入）

- `user_custom_prompts` 只有 1 行：`target_type=global`，`enabled=1`，`prompt_hash=5489166b29c2`，2838 字；该用户 `users.prompt_injection_enabled=1`。global 级提示词经 `resolve_all_roles_prompts` 作用于全部角色，研究经理也在内。
- 用「权重|加权|计票|cluster_id|weight|tally|vote」扫描该文本：未命中。这只能说明当前没有实际影响，不能说明代码路径没有缺口。

## 原先待决的问题（已由 D-015 关闭）

1. 是否把自定义提示词纳入 E-02 硬约束。
2. 若纳入，守卫放在哪里：保存校验、运行时解析，还是研究经理组装。
3. 命中后怎么处理：拒绝保存、告警剔除，还是运行时 ABSTAIN / NO_TRADE；如何提示用户。
4. 误报：否定句、与关系贡献无关的「权重」用法。
5. 与 V-03 基线的关系。

## 六项产品契约裁定（2026-09-13，David 点头 + Codex 总工裁定）

> 本节已记入 `DECISIONS.md` 的 D-015；D-016 另行记录了进入实施阶段的授权。

1. **约束范围**：自定义提示词（custom_prompt）正式纳入 E-02 硬约束。
2. **守卫落点**：三层，共用同一个确定性纯判定函数（linter）：① 保存 / 迁移入口；② 任务启动的解析冻结（`api/main.py` 的 `_resolve_and_freeze_custom_prompts`）；③ 研究经理组装处保留最后一道 fail-closed，覆盖绕过 API 的路径。
3. **命中处置（fail-closed）**：
   - 保存入口：返回可读 422，不入库；不自动删除既有行，不静默改写或剔除用户文本。
   - 任务启动时发现违规，或旧数据无法判定：**整次任务记为 `NO_TRADE`**，写入结构化 reason code 与提示词 hash；**五个可注入角色一律不再接收该自定义提示词**，且不构建后续 LLM 链路（研究经理不调用模型）。
   - 注入开关关闭时保持现有零解析、零注入行为。
4. **误报策略**：确定性三态判定。
   - 违规：动作（加权 / 计票 / 汇总 / 排序）与对象（分析师 / 命题 / 证据簇 / 独立票 / `cluster_id` / `independent_cluster_count`）同时出现；机读标识零容忍。
   - 安全：明确否定句（「不要 / 禁止按权重计票」）；与关系贡献无关的权重用法（仓位权重、指数权重、因子权重）。
   - 不确定：保存入口拒绝并提示用户改写；运行时 fail-closed，不留给模型自行解释。
5. **角色范围**：保存时按 global + group + role 拼接后的最终 resolved 文本检查五个可注入角色；运行时对整次任务 fail-closed，其他角色不得带着违规文本继续运行。
6. **V-03 基线**：保留当前全局提示词并在基线中记录 hash `5489166b29c2`，不静默关闭；前提是完成语义合规核验。

### 上线前只读体检（已完成，2026-09-13）

- `user_custom_prompts` 仅 1 行：`target_type=global`、`enabled=1`、hash `5489166b29c2`、2838 字；737 个用户中仅 1 人 `prompt_injection_enabled=1`。
- 最近 60 份 completed 报告的 `custom_prompt_snapshot` 均为 `enabled=true`、`placement=after_data`，五个角色都收到同一段 resolved 文本。
- 以「权重 / 加权 / 计票 / 独立票 / cluster_id / weight / tally / vote」扫描该文本：未命中。
- 结论：守卫生效后不会立即阻断现有账号；若该文本变更，须重新核验并更新 D-015 记录的 hash。

## 实施状态（D-016，2026-09-13）

- 实施子卡：**DAV-856**，已派给 `资深开发1`，状态 `in_progress`。
- D-012 §5b 覆盖面复核：**DAV-857**，已派给 `项目评估师`，只读状态 `todo`，不改代码。
- 实施基线：开工时要求回读 `54077b6ad4a287bbd8c43d386e91427662a5e786`；若 trunk tip 已变化，必须以新 tip 重建第一父。
- 实施范围：仅守卫、解析冻结、研究经理兜底及对应测试；不改提示词正文、数据库 schema/数据、个人模型/厂商配置、前端、部署或生产服务。
- 候选完成后，代码审查固定派给 `代码审核员`，不派给 `独立代码审核员`；审查、合入、部署、重启四个动作仍分别过门禁。

关键词判定只是多一层保险，不能替代「贡献数由代码计算、不由模型自由决定」这一根本约束；不得据本卡宣称 E-02 已全局闭合。H1b 维持 `KEEP_FALSE`，信用加权、真实采集、Cookie、生产数据写入等红线不变。
