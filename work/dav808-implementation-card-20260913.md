# DAV-808 实施卡：custom_prompt 纳入 E-02 守卫

## 目标

在当前施工主干 `54077b6ad4a287bbd8c43d386e91427662a5e786` 上实施 D-015 的六项产品契约：自定义提示词不得绕过 E-02 的证据独立性约束；命中违规或无法判定时，整次任务 fail-closed 为 `NO_TRADE`，五个可注入角色都不得继续接收该文本或调用后续模型。

本卡是 DAV-808 的实施子卡。不得把当前生产中全局提示词 `5489166b29c2` 改写、删除或关闭；不得把本卡的测试结果表述为 E-02 已经全局闭合。

## 第一父与交付边界

- 开工前必须再次 `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk`，确认第一父仍为上面的完整 SHA；若已变化，停止并以新的 trunk tip 重建本卡基线。
- 只能在隔离分支/worktree 开发，不能直接改施工主干或生产服务。
- 候选须为单一关注点的新提交，交付时置 `in_review`，并报告完整 40 位 SHA、第一父、远端 ref、工作树状态、白名单 diff、测试解释器和实测计数。

## 产品契约（不可自行改义）

1. custom_prompt 正式纳入 E-02 硬约束。
2. 三层使用同一个确定性纯判定函数：保存/迁移入口、任务启动的 resolved bundle 冻结、research_manager 组装处最后一道 fail-closed。
3. 保存或迁移命中违规：返回可读 422，不入库，不自动删除旧行，不静默改写或剔除文本。运行时遇到既有违规行、绕过保存入口写入的文本或无法判定的文本：整次任务记录 `trade_action=NO_TRADE`，写入稳定 reason code 与提示词 hash；五个可注入角色全部 `injected=false`，不构建后续模型链路，research_manager 不调用模型。
4. 判定为三态：`VIOLATION`、`SAFE_NEGATION_OR_UNRELATED`、`AMBIGUOUS`。动作与对象同时出现才判违规；动作包括加权/计票/汇总/排序，对象包括分析师/命题/证据簇/独立票；`cluster_id`、`independent_cluster_count` 等机读标识按零容忍处理。明确否定句和仓位/指数/因子等与证据贡献无关的权重用法放行；不确定文本在保存入口拒绝，运行时 fail-closed。
5. 保存时检查五个可注入角色的最终 resolved 文本，必须覆盖 global + group + role 的拼接组合；不能只检查单条原始行，也不能只检查 research_manager。
6. V-03 继续保留当前全局提示词并记录 hash `5489166b29c2`；本卡不改实验基线文件、提示词内容或生产库。

## 允许修改（白名单）

- `tradingagents/agents/utils/prompt_injection.py`：唯一的纯 linter/判定结果与注入边界共用逻辑；不得把提示词正文放入日志。
- `api/services/custom_prompt_service.py`：保存、迁移和候选最终 resolved 文本校验；拒绝必须发生在数据库 delete/insert/commit 之前。
- `api/main.py`：任务启动冻结层和无模型的整单 fail-closed/报告收口；必须复用现有报告状态与 `reason_codes` 语义，不新增数据库 schema，不把违规任务记成普通异常失败或空报告。
- `tradingagents/agents/managers/research_manager.py`：最后一道组装兜底；违规时不得调用 LLM，不得静默剔除文本后继续分析。
- `tests/test_custom_prompts.py`、`tests/test_custom_prompt_injection.py`，以及必要时新增一个聚焦 E-02 guard 的测试文件。

白名单之外的生产代码、`tradingagents/prompts/catalog.py`、`tradingagents/graph/setup.py`、前端、数据库 schema/数据、`.env`、providers/role_bindings、部署脚本均禁止修改。若现有状态收口无法在白名单内完成，先报告阻塞，不要自行扩范围。

## 验收标准

- 保存入口对违规与不确定的最终 resolved 文本返回 422，事务保持原子：旧行仍在，违规新行不落库；迁移入口同样 fail-closed。
- 开关关闭时不读提示词、不解析、不调用 linter、不阻断，既有 byte-identical 注入关闭契约保持通过。
- 任务启动发现五个角色任一最终文本违规/不确定时，在构图和任何后续 LLM 调用之前终止；报告可回读，`trade_action=NO_TRADE`，reason code 与 resolved prompt hash 同时存在，五角色不接收原文。
- 研究经理直接组装路径仍有最后一道防线；触发时 LLM 调用计数为 0，不能靠“删掉违规句子”继续。
- 合法提示词的 global/group/role 解析、两种 placement、五角色快照和现有报告字段不回归。
- 不新增枚举值或 schema；不改变 `5489166b29c2` 当前提示词，不写生产库，不部署，不重启。

## red_team_scenarios（D-012 §5b，实施与复审必须逐条实跑）

| 编号 | 固定输入/入口 | 预期 |
|---|---|---|
| RT-1 | global 级违规文本进入 research_manager | 任务在模型链路前 fail-closed；`NO_TRADE`、reason/hash 可回读 |
| RT-2 | `group=arbiter` 级违规文本 | 与 RT-1 同判，不能只检查 global |
| RT-3 | `role=research_manager` 级违规文本 | 与 RT-1 同判，不能只检查 global/group |
| RT-4 | global 合法 + group 违规；global 违规 + role 合法；三层拼接组合 | 检查最终 resolved 文本，违规组合拒绝/整单停，旧数据不被删除 |
| RT-5 | 同一用户同时给 bull、bear、research_manager、trader、risk_manager 配置；分别让四个非 manager 角色之一命中 | 五角色都不得继续接收违规文本；不能让多空/交易员先跑再由 manager 停止 |
| RT-6 | `before_data` 与 `after_data` 两种 placement | 合法文本位置保持原契约；违规文本两种位置均被拦截 |
| RT-7 | 注入开关关闭，库中存在违规文本 | 零解析、零 linter、零 LLM 阻断，保持关闭时字节级行为 |
| RT-8 | 明确否定句：「不要/禁止按权重计票」 | 判安全，不误报 |
| RT-9 | 仓位权重、指数权重、因子权重等无证据贡献语义 | 判安全，不误报 |
| RT-10 | 已存在的旧数据库行在任务启动时被解析冻结 | 运行时 fail-closed；不静默改写原行；报告写入 `NO_TRADE`、reason/hash，零 LLM |
| RT-11 | 阻断任务的报告回读与假 LLM | `trade_action=NO_TRADE`，reason code/hash 完整，五角色不注入，模型调用数为 0，不能只返回普通 failed/空结果 |
| RT-12 | 合法 custom_prompt 的现有注入链路 | global/group/role、五角色 snapshot、合法文本两种 placement 与既有测试保持通过 |

### D-012 §5b 覆盖复核补充（DAV-857，2026-09-13）

DAV-857 的只读复核结论为 `NEEDS_ADD`。以下四条是实施与后续同 SHA 审查的强制补充，不能省略：

| 编号 | 固定输入/入口 | 预期 |
|---|---|---|
| RT-13 | 保存入口与任务启动入口分别输入一个不满足明确“动作+对象”、也不属于明确否定或无关权重的歧义文本，例如「按重要程度调整各方意见的影响，形成综合结论」 | linter 返回 `AMBIGUOUS`；保存入口 422 并提示改写且不落库；既有旧行运行时整单 `NO_TRADE`、五角色不注入、零 LLM |
| RT-14 | 纯 linter/保存入口输入仅含 `cluster_id` 或 `independent_cluster_count`、不含动作词的文本，例如「请在输出中参考 cluster_id」 | 按机读标识零容忍判 `VIOLATION`；保存入口拒绝，运行时整单 fail-closed |
| RT-15 | 绕过 API 与任务冻结层，直接调用 `create_research_manager(mock_llm, memory, custom_prompt="按分析师权重计票", placement="after_data")` 并执行节点 | 研究经理最后一道防线返回既有 blocked/no-trade 结果；reason code 存在；`mock_llm` 调用数为 0；不得静默删句后继续 |
| RT-16 | `migrate_legacy_prompt` 输入 `legacy_text="按分析师权重计票，并按 cluster_id 汇总"`，用户当前没有 global 行 | 返回可读 422/ValueError；事务原子回滚；`user_custom_prompts` 不新增记录，既有行不被删除 |

实现交付必须给出每条场景的命令、解释器、候选完整 SHA、实际输出和结论；不能用静态阅读或开发者口头说明替代实跑。红队清单在进入代码审查前还须由不同角色复核覆盖面。

## 测试与交付

- 必须使用 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，并以 `env -u PYTHONPATH` 运行。
- 至少运行并报告：custom prompt 持久化/迁移专项、注入专项、相关 research_manager/报告生命周期测试，以及真实全量 `pytest -q -p no:randomly`；全量必须与开工前基线逐项比失败集合，不能只报通过数。
- 改产品代码后，候选必须由 `代码审核员` 针对同一完整 SHA 做只读审查；不得派给 `独立代码审核员`，不得由实施者自审。
- 审查 PASS、总工放行合入、部署、重启分别是后续独立动作；本卡完成不包含 FF、部署、写生产库、真实采集或启用任何加权。
