# DAV-808 红队场景覆盖面复核报告（第二轮·D-012 §5b）

这是 D-012 §5b 的只读覆盖面复核，不是代码审查，不改代码、不改卡面、不派开发、不合入、不部署、不触发调度助手。

- **复核对象**：`work/dav808-implementation-card-20260913.md`（DAV-856 实施卡）中 RT-1 至 RT-16
- **目标基线**：`54077b6ad4a287bbd8c43d386e91427662a5e786`
- **复核角色**：项目评估师（ID: `2c03cc8f-6628-4464-954a-84c47079fdf3`）
- **核验日期**：2026-09-13
- **轮次**：第二轮（第一轮 `NEEDS_ADD` 增补后的复核）

---

## 评估结论

**COVERED（全部覆盖 / 准予解锁）**：DAV-856 实施卡已完成对 RT-10 的范围收窄，并已完整增补 RT-13 至 RT-16 共 4 条强制红队场景。当前 RT-1 至 RT-16 已全面闭合 D-015 六项产品契约、D-012 §5b 多入口覆盖要求与三层防护体系，无遗漏盲区，**正式确认足以解锁后续代码审查**。

---

## 评估依据（按维度关键判断）

1. **三层防护体系独立验证（D-015 §2）**：
   - **Layer 1（保存/迁移入口原子性）**：RT-4 覆盖保存入口 resolved 候选判定与回滚；RT-13 覆盖保存入口歧义文本拦截；RT-14 覆盖机读标识零容忍保存拒绝；RT-16 独立覆盖 `migrate_legacy_prompt` 迁移入口原子拦截与零增量回滚。
   - **Layer 2（任务启动解析冻结层）**：RT-1~3 覆盖 manager 三级配置；RT-5 覆盖四非 manager 角色；RT-10 聚焦既有旧数据库行启动冻结拦截（不改写原行、`NO_TRADE`、0 LLM）；RT-13 覆盖既有旧行歧义文本运行时整单停机。
   - **Layer 3（研究经理组装处最后一道 fail-closed 兜底）**：RT-15 独立覆盖直接调用 `create_research_manager` 绕过 API 与冻结层的情形，严格断言返回 blocked/no-trade 结果、0 LLM 调用且不静默删句。
2. **确定性三态状态机完备性（D-015 §4）**：
   - `VIOLATION`：RT-1~5、RT-10、RT-14、RT-15、RT-16 全面覆盖；
   - `SAFE_NEGATION_OR_UNRELATED`：RT-8 覆盖明确否定句豁免，RT-9 覆盖金融无关权重词豁免；
   - `AMBIGUOUS`：RT-13 完整覆盖保存入口 422 提示改写与运行时既有旧行整单 fail-closed。
3. **零容忍机读标识覆盖（D-015 §4）**：
   - RT-14 独立覆盖无动作动词、仅出现 `cluster_id` 或 `independent_cluster_count` 的情形，确保零容忍规则不退化为普通双词匹配。
4. **五角色与时序联动契约（D-015 §3, §5）**：
   - RT-5 覆盖 bull、bear、trader、risk_manager 任一角色违规时的前置拦截，确保多空辩论不先跑、五角色均 `injected=False`。
5. **系统与向后兼容契约（D-015 §3, §6）**：
   - RT-6 覆盖两种 placement 位置；RT-7 覆盖开关关闭时 byte-identical 零影响；RT-11 覆盖持久化、reason/hash 结构化字段与报告可回读；RT-12 覆盖 42 项既有测试全绿回归。

---

## RT-1 至 RT-16 逐条复核结论清单

| 编号 | 场景简述与入口 | 复核结论 | 依据说明 |
|---|---|---|---|
| **RT-1** | global 级违规文本进入 research_manager（任务启动冻结） | `COVERED` | 覆盖 global 级违规进入 manager 的冻结层拦截，验证 `trade_action=NO_TRADE`、reason_code 与 hash 回读及 0 LLM 调用。 |
| **RT-2** | `group=arbiter` 级违规文本（组继承） | `COVERED` | 覆盖组级配置继承（`ROLE_TO_GROUP["research_manager"] == "arbiter"`），验证未配置 global 时 group 级违规同样整单 fail-closed。 |
| **RT-3** | `role=research_manager` 级违规文本（角色专属） | `COVERED` | 覆盖角色级专属 override 违规进入 manager 的拦截路径。 |
| **RT-4** | global 合法 + group 违规；global 违规 + role 合法；三层拼接组合 | `COVERED` | 覆盖多层级组合拼接，验证基于最终 resolved 文本（`global + "\n\n" + override`）校验，并验证保存校验失败时原子回滚、旧行不被删除。 |
| **RT-5** | 同时配置五角色，分别让四个非 manager 角色之一命中 | `COVERED` | 覆盖 4 个非 manager 可注入角色（bull、bear、trader、risk_manager）；验证任一角色违规即整单拦截，五角色均 `injected=False`，辩论不启动。 |
| **RT-6** | `before_data` 与 `after_data` 两种 placement | `COVERED` | 覆盖系统支持的全部两种 placement 位置，验证合法文本位置契约正确、违规文本两种位置均被阻断。 |
| **RT-7** | 注入开关关闭，库中存在违规文本 | `COVERED` | 覆盖 `prompt_injection_enabled=0` 边界，验证零解析、零 linter、零阻断，保持关闭时字节级一致性（byte-identical）。 |
| **RT-8** | 明确否定句：「不要/禁止按权重计票」 | `COVERED` | 覆盖 D-015 §4 否定句防误报规则，验证判定为安全，不阻塞合法保存与注入。 |
| **RT-9** | 仓位权重、指数权重、因子权重等无证据贡献语义 | `COVERED` | 覆盖金融领域通用“权重”用法防误报规则，验证与证据图贡献无关的权重描述判定为安全。 |
| **RT-10** | 已存在的旧数据库行在任务启动时被解析冻结 | `COVERED` | 场景已收窄聚焦：独立验证已有旧数据在任务启动时触发 fail-closed，原行不被改写或删除，报告写入 `NO_TRADE`、reason/hash，零 LLM。 |
| **RT-11** | 阻断任务的报告回读与假 LLM | `COVERED` | 覆盖阻断后的报告生命周期与持久化，验证 report 可正常查询、结构化字段完备、五角色未注入且全链路 0 LLM 调用。 |
| **RT-12** | 合法 custom_prompt 的现有注入链路 | `COVERED` | 覆盖白名单回归测试（42 项既有测试），验证合法多层继承、快照字段及 placement 不退化。 |
| **RT-13** | 三态判定之不确定文本（AMBIGUOUS）分层处置 | `COVERED` | 首轮新增场景：固定输入歧义文本，覆盖保存入口 422 引导改写且不落库、旧行运行时整单 `NO_TRADE`、五角色不注入、零 LLM。 |
| **RT-14** | 仅含 `cluster_id` 或 `independent_cluster_count` 机读标识零容忍 | `COVERED` | 首轮新增场景：固定输入纯机读标识（无动词），覆盖独立判定为 `VIOLATION`，保存入口拒绝，运行时整单 fail-closed。 |
| **RT-15** | 直接调用 `create_research_manager` 绕过 API 与冻结层 | `COVERED` | 首轮新增场景：Layer 3 组装最后兜底，断言返回 blocked/no-trade 结果、reason code 存在、`mock_llm` 调用数严格为 0、不静默删句。 |
| **RT-16** | `migrate_legacy_prompt` 迁移入口输入违规文本 | `COVERED` | 首轮新增场景：固定输入违规文本，覆盖返回 422/ValueError、事务原子回滚、`user_custom_prompts` 表零增量。 |

---

## 审查解锁结论

- **解锁状态**：**准予解锁（UNLOCKED）**
- **结论说明**：
  实施卡中更新后的 16 条红队场景已完全覆盖全部可能绕过 E-02 守卫的入口、组合、层级与边缘状态。每个测试场景均具备明确的固定输入、目标入口与严格的预期断言，满足 D-012 关于“行为审查必须通过红队场景清单逐条实跑”的前提门禁。
- **后续流水线建议**：
  1. 实施方可基于当前 16 条红队场景进入隔离分支开发；
  2. 实现完成后，必须由独立的 `代码审核员` 在同一完整 SHA 上逐条实跑 RT-1 至 RT-16 并报告实际命令与输出；
  3. 本次复核严格遵守不改代码、不改卡面、不派开发、不触发调度助手之约束。
