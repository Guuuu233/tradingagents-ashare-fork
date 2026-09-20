# DAV-808 决策简报：custom_prompt 与 E-02 守卫

更新：2026-09-12（按远端施工主干刷新）

## 核验对象

- 精确主干：`7810f19875890725cd26e414cde272063fb3606a`
- 直接父提交：`1ef79a736334a0bad5022e510387dead69c69666`
- 说明：旧简报中的 `41b77dc` 已不是当前实施基线；以下代码位置按 `7810f198` 回读。
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`
- 本轮只读；只更新本地决策简报，未修改代码、提示词、配置或数据库。

## 已确认事实

1. `tradingagents/agents/managers/research_manager.py:189-214` 的
   `_apply_relation_prompt_guard` 只接收内置 `research_manager_prompt` 模板，做固定片段替换和残留禁词检查。
2. 当前主干的 `research_manager.py:747-753` 先准备注入槽并执行该内置模板守卫，
   `795-799` 再通过
   `prompt_template.format(..., **injection_slots)` 注入 `custom_prompt`；因此自定义文本不经过现有 E-02 守卫。
3. `api/services/custom_prompt_service.py:53-85` 当前只校验 target 类型、长度和 key；
   `113-149` 按 `role > group > global` 解析，但 global 文本会与 override 拼接，不会被覆盖删除。
4. `api/main.py:2720-2755` 在任务启动时冻结五个可注入角色的 resolved prompt；开关关闭时不解析，开启时只做长度检查。
5. `research_manager` 属于 `arbiter` 组（`api/services/role_routing_service.py:17-26`），所以 global、group=`arbiter`、role=`research_manager` 都能影响该节点。

6. 当前主干的保存入口仍只把请求交给 `custom_prompt_service.replace_custom_prompts`，将其抛出的
   `ValueError` 转成 `422`；服务本身的 `_validate_item` 仍是类型、键和长度校验，不是 E-02 语义扫描。

## 当前实测

- DAV-824 记录的基线测试为：`tests/test_custom_prompt_injection.py` 的 `42 passed`，
  以及 E-02 模板守卫的 `7 passed, 29 deselected`；它们证明注入机制和内置模板守卫正常，
  **不证明 custom prompt 已被 E-02 守卫覆盖**。
- DAV-824 的运行时探针记录：违规文本
  `按分析师权重计票，并按 cluster_id 汇总` 进入提示词 1 次，LLM 调用 1 次，未出现
  `e02_relation_prompt_guard_failed`。本次对 `7810f198` 的静态回读确认该代码顺序仍存在；
  尚未把这条旁路探针冒充成当前生产运行证据。

## 推荐规格（仅建议，未视为政策批准）

### 覆盖范围

将进入 `research_manager` 的 resolved custom prompt 纳入 E-02 硬约束；不把普通角色中与关系图无关的“权重”用法误判为 E-02 违规。

### 守卫位置

采用“三层但单一纯函数”的策略：

1. 保存/迁移时调用纯 linter，给新配置即时错误提示；
2. `_resolve_and_freeze_custom_prompts` 对最终的 `research_manager` resolved 文本再次检查，覆盖旧行、直接数据库写入和所有 global/group/role 组合；
3. `research_manager` 组装处保留最后一道 fail-closed 防线，覆盖绕过 API 直接调用的路径。

不能只放在保存时，也不能只改组装处：前者漏旧数据和非 API 入口，后者会在前置分析师已经消耗 LLM 调用后才阻断。

### 命中处置

- 不静默剔除或改写用户文本。
- 运行时返回现有 `ABSTAIN / NO_TRADE / BLOCKED` 语义，附结构化 reason code 和审计 hash；不发起 research-manager LLM 调用。
- 新保存/迁移请求可直接返回可读的 4xx 校验错误；已有违规行不自动删除、不自动改库。
- 注入开关关闭时保持现有零解析、零注入行为。

### 误报策略

纯确定性三态 linter：`VIOLATION`、`SAFE_NEGATION_OR_UNRELATED`、`AMBIGUOUS`。

- 直接命令（按分析师/claim/cluster 加权、计票、按 cluster_id 汇总）判 `VIOLATION`；
- “不要/禁止/严禁按权重计票”等明确否定句判安全；
- 只谈一般统计、价格权重或非研究经理裁决且无受限动作的文本判无关；
- 无法确定意图时 fail-closed，提示用户修改，而不是让 LLM 自行解释。

## 实现前必须列入的红队场景

1. global 级违规文本进入 research_manager；
2. group=`arbiter` 级违规文本；
3. role=`research_manager` 级违规文本；
4. global 合法 + group 违规、global 违规 + role 合法的拼接组合；
5. 同一用户同时给多个角色提示词，仅 manager 违规时只阻断 manager 语义；
6. `before_data` 与 `after_data` 两种 placement；
7. 注入开关关闭时不解析、不调用 linter、不发起阻断；
8. 明确否定句不误判；
9. 与关系贡献无关的“权重”不误判；
10. 旧数据库行、迁移入口、直接 factory 调用均 fail-closed；
11. 阻断结果含 reason code/hash，且 LLM 调用次数为 0；
12. 合法 custom prompt 的现有注入行为保持不变。

## 待 David 裁定的六项产品契约

这些是 DAV-808 进入实现前的具体语义选择，不是泛化的“施工政策”。括号内是 DAV-824
给出的建议默认值，仍未视为批准：

1. **约束范围**：custom prompt 是否正式纳入 E-02 硬约束？（建议：纳入。）
2. **守卫落点**：仅研究经理组装处，还是保存校验 + 最终解析冻结 + 研究经理组装的分层守卫？
   （建议：分层，但复用同一个纯 linter；组装处保留最终 fail-closed。）
3. **命中处置**：保存入口是否返回可读 `422`；运行时是否使用 `ABSTAIN/NO_TRADE/BLOCKED`、
   reason code 和审计 hash，并保证研究经理 LLM 调用次数为 0？（建议：是；不静默剔除或改写。）
4. **误报策略**：是否采用“`cluster_id` / `independent_cluster_count` 零容忍 + 关系贡献上下文
   识别 + 明确否定句豁免 + 不确定时 fail-closed”？（建议：是。）
5. **角色范围**：先只约束 `research_manager`，还是一并约束当前五个可注入角色？
   （建议：先只约束 `research_manager`，其他角色另行定契约。）
6. **V-03 基线**：当前 global prompt hash `5489166b29c2` 是纳入基线并先做合规认证，还是在
   V-03 运行前禁用？（这是实验基线选择，不能由 DAV-808 实现默认代替。）

## 获批后才可开卡的实现边界

仅在上述契约明确后，才创建 DAV-808 实施卡。候选白名单暂定为：

- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/utils/prompt_injection.py`（只有需要抽出扫描器时）
- `api/services/custom_prompt_service.py`
- `api/main.py`（只有需要在解析冻结层接入时）
- `tests/test_claim_cluster.py`、`tests/test_custom_prompt_injection.py` 及新增聚焦测试

不得顺手改内置提示词、数据库 schema/数据或 `work/v03-freeze-sheet-20260909.md`。交付时必须
覆盖上方 12 条红队场景，并由“代码审核员”对**同一完整 SHA**独立复核；审核、合入、部署、
重启仍是四个独立动作。

在契约裁定前，不开实现卡，不改提示词，不改生产库，不部署。
