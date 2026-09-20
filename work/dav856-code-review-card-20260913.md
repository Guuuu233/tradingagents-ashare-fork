# DAV-856 代码审查卡：custom_prompt 三层 E-02 守卫

## 审查对象

- 候选完整 SHA：`b92acd15bc20d6a1bcc699f3ea7723fcb8868b37`
- 第一父：`54077b6ad4a287bbd8c43d386e91427662a5e786`
- 远端 ref：`refs/heads/agent/senior-dev-1/dav856-e02-guard`
- 目标施工主干：`refs/heads/codex/dav-4-p2a-trunk`
- 实施卡：DAV-856；红队覆盖复核：DAV-857（已完成，RT-1～RT-16 全部 COVERED）
- 审查角色：代码审核员

这是同一完整 SHA 的只读审查。不得改代码、改测试、改卡面、提交、推送、合入、部署、重启、写生产库或触发真实采集。不得把实施员的自测结论当作审查结论。

## 必查的产品契约

逐项核对 D-015 六项契约是否由代码真实实现：

1. custom_prompt 纳入 E-02 证据独立性硬约束。
2. 保存/迁移、任务启动 resolved bundle 冻结、research_manager 组装三层共用同一个确定性纯判定函数。
3. 保存/迁移命中违规或歧义时返回可读 422，旧行不删除、不静默改写；运行时遇到既有或绕过入口的违规/歧义时，整单可回读为 `NO_TRADE`，有稳定 reason code 与提示词 hash，五个角色不注入且不构建后续模型链路。
4. `VIOLATION`、`SAFE_NEGATION_OR_UNRELATED`、`AMBIGUOUS` 三态正确区分；动作与证据贡献对象同时出现才判自然语言违规；`cluster_id`、`independent_cluster_count` 等机读标识零容忍；明确否定句、仓位/指数/因子等无关权重放行。
5. 保存时检查五个可注入角色的最终 resolved 文本，覆盖 global + group + role 拼接组合，而非只检查单条原始行或 research_manager。
6. 不改写、不删除当前提示词基线 `5489166b29c2`，不新增 schema/枚举，不写生产库。

## 允许的变更范围

候选只能改下列文件；审查必须用 `git diff 54077b6ad4a287bbd8c43d386e91427662a5e786..b92acd15bc20d6a1bcc699f3ea7723fcb8868b37 --name-status` 逐项回读：

- `tradingagents/agents/utils/prompt_injection.py`
- `api/services/custom_prompt_service.py`
- `api/main.py`
- `tradingagents/agents/managers/research_manager.py`
- `tests/test_custom_prompts.py`
- `tests/test_custom_prompt_injection.py`
- `tests/test_custom_prompt_e02_guard.py`

白名单外出现任何生产代码、配置、提示词正文、数据库 schema/数据或部署文件变更，直接 `NEEDS_CHANGES`。

## 红队实跑（D-012 §5b）

必须在候选完整 SHA 的隔离只读 worktree 中，使用 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python` 并以 `env -u PYTHONPATH` 实跑，逐条给出实际命令、固定输入、实际输出和结论：

- RT-1：global 级违规在模型链路前整单停，报告可回读 `NO_TRADE`、reason/hash、0 LLM。
- RT-2：`group=arbiter` 级违规同样整单停。
- RT-3：`role=research_manager` 级违规同样整单停。
- RT-4：global/group/role 三层合法与违规拼接组合；拒绝原子性和旧行保留。
- RT-5：bull、bear、trader、risk_manager 任一非 manager 角色命中时，五角色都不接收文本且整单停。
- RT-6：`before_data` 与 `after_data` 两种 placement 均覆盖。
- RT-7：开关关闭时零解析、零 linter、零阻断，既有行为不变。
- RT-8：明确否定句安全，不误报。
- RT-9：仓位/指数/因子/等权等无关权重安全，不误报。
- RT-10：既有旧行、迁移入口和绕过 API/service/factory 的路径分别核验；旧行不被静默改写或删除。
- RT-11：阻断任务报告状态、`NO_TRADE`、reason/hash、五角色注入状态、0 LLM 调用全部可回读。
- RT-12：合法 global/group/role、两种 placement、五角色 snapshot 与既有注入行为回归。
- RT-13：`AMBIGUOUS` 保存入口 422，既有运行时行 fail-closed；不得由模型解释放行。
- RT-14：`cluster_id` / `independent_cluster_count` 单独出现也零容忍。
- RT-15：直接调用 research_manager factory 绕过 API/freeze 时仍有最后防线，0 LLM。
- RT-16：`migrate_legacy_prompt` 遇违规原子拒绝，记录增量为 0。

## 测试与审查门禁

- 复核候选专项测试、相关生命周期测试和实施报告的 16 条红队证据；不能只看“通过总数”。
- 独立重跑至少：
  - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_custom_prompt_e02_guard.py tests/test_custom_prompts.py tests/test_custom_prompt_injection.py -q`
  - 相关 research_manager/报告生命周期测试
  - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly`
- 全量结果必须与父提交 `54077b6ad4a287bbd8c43d386e91427662a5e786` 同口径逐项比失败集合；父版本 21 项中由 DAV-850 引入的两项必须在候选中消失，不能新增失败。
- 检查 `git diff --check`、候选工作树洁净、候选父提交准确、远端 ref 可回读。
- 重点审查：异常发生后报告是否真的可回读而非普通 failed/空结果；reason/hash 是否泄漏提示词正文；开关关闭是否短路在解析前；五角色是否存在间接接收违规文本的路径；直接 factory 和迁移入口是否能绕过；是否误改生产基线或引入 schema。

## 交付格式

只读交付必须包含：

1. 审查的完整 SHA、第一父、远端 ref、隔离 worktree 与工作树状态；
2. 白名单逐文件核对和 `git diff --check`；
3. RT-1～RT-16 每条的实跑证据；
4. 测试解释器、完整命令、实际计数和全量失败集合对照；
5. 代码发现按严重度列出，结论只能是 `PASS`、`NEEDS_CHANGES` 或 `BLOCKED`。

只有在同 SHA 代码审查 `PASS` 且所有证据齐全后，才可进入总工的独立合入门禁；本审查卡本身不授权合入或部署。
