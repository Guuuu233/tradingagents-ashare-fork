# TradingAgents-AShare Phase C 交接书

更新时间：2026-07-31（Australia/Perth）

用途：供新的 VS Code Codex 任务接续当前工作。本文是本地工作交接文件，不属于 Phase C 产品代码，**不得 stage、不得提交**。

## 0. 接手后的第一条原则

先完整阅读仓库根目录的 `AGENTS.md`，再执行任何操作。

当前任务仍受以下边界约束：

- 当前只处理 TradingAgents-AShare，不处理 TradingAgents-CN。
- 不擅自进入 Phase E。
- 不运行新的真实股票分析，除非 David 明确要求。
- 不改动正式自定义提示词，不打开注入开关，除非 David 明确要求。
- 不使用 `git add -A`、`git add .` 或其他会把无关脏文件一起暂存的命令。
- 不修改、恢复、删除现有无关脏文件。
- 不提交，直到 David 在新任务中明确授权 commit。
- 一个 commit 只包含当前获批的 Phase C 关注点。

## 1. 当前仓库与 Git 状态

仓库：

```text
/Users/davidliu/Documents/TradingAgents-AShare
```

分支：

```text
main
```

当前 HEAD：

```text
56f63591a2d91a0cd86a3b60565efaf3d0700711
56f6359 docs: record that custom prompt history is unrecoverable
```

紧邻的 Phase B 功能提交：

```text
f016cf0 feat: persist custom analysis prompts server-side
```

Phase C 尚未提交，但获批的 10 个文件已经暂存。

本交接书创建后会作为 `work/` 下的额外 untracked 文件出现；它不属于 Phase C。

## 2. 当前已经暂存并获审查批准的 10 个文件

8 个已跟踪修改文件：

```text
M  api/main.py
M  api/services/report_service.py
M  tradingagents/agents/managers/research_manager.py
M  tradingagents/agents/researchers/bear_researcher.py
M  tradingagents/agents/researchers/bull_researcher.py
M  tradingagents/graph/setup.py
M  tradingagents/graph/trading_graph.py
M  tradingagents/prompts/zh.py
```

2 个新增文件：

```text
A  tests/test_custom_prompt_injection.py
A  tradingagents/agents/utils/prompt_injection.py
```

注意：之前的一份人工汇报曾把
`tradingagents/agents/utils/prompt_injection.py` 写成 `M`，实际
`git diff --cached --name-status` 已核实为 `A`。

暂存统计：

```text
10 files changed, 1022 insertions(+), 18 deletions(-)
```

完整 staged diff：

```text
/private/tmp/phase-c-staged.diff
```

核实信息：

```text
lines=1329
bytes=61101
sha256=536c6c269231d03542ab2780e1db7e705f7f3a70016555b5ea6c61db59235c9b
git diff --cached --check: PASS
```

审查时重新从暂存区生成 diff，并与上述文件做了逐字节比较：

```text
staged-diff-match=PASS
approved-paths-unstaged-drift=NONE
```

因此，交接时的暂存内容就是已经完成最终代码审查的版本。

## 3. 当前未暂存的无关或预存脏文件

最后一次核查时，以下内容不属于 Phase C 暂存集：

```text
 M tests/test_api_smoke.py
 M tests/test_intent_parser.py
 M tests/test_portfolio_import.py
?? scripts/work_smoke_commit2.py
?? tradingagents.db-journal
?? work/
?? work_analysis_collect_probe.json
?? work_smoke_commit2.py
```

这些文件或目录必须保持原样，不得被本次提交带入。不要根据文件名猜测来源，也不要清理。

交接后应先运行：

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git diff --cached --name-status
git diff --cached --check
```

若暂存区与第 2 节不一致，先停下汇报，不要自行修正。

## 4. Phase C 的目标与最终设计

Phase C 的目标是把 Phase B 已经存入服务端的自定义分析提示词，真正注入以下三个角色的最终 LLM prompt：

```text
bull_researcher
bear_researcher
research_manager
```

本阶段不向其余 12 个角色注入。

最终位置选择：

```text
DEFAULT_PLACEMENT = "after_data"
```

含义：

- bull/bear：自定义文案位于最后一个数据字段之后、`写作要求：`之前。
- research_manager：自定义文案位于最后一个数据字段之后、`输出要求：`之前。
- 模型先看到当前任务数据，再看到用户自定义指令，最后看到角色内建的输出格式约束。

唯一默认值定义在：

```text
tradingagents/agents/utils/prompt_injection.py
```

三个 factory、`GraphSetup`、`TradingAgentsGraph` 和 `api/main.py` 共用这一默认值。

只允许：

```text
before_data
after_data
```

只有 `None` 可以使用默认值；空字符串或其他非法 placement 不会静默回退，
而会被 `build_injection_slots()` 明确拒绝。

## 5. 10 个文件的具体作用

### `tradingagents/agents/utils/prompt_injection.py`

新增的共享注入工具。

- 定义 `Placement`。
- 定义唯一的 `DEFAULT_PLACEMENT = "after_data"`。
- `build_injection_slots(custom_prompt, placement, role_key)` 是唯一决定两个模板槽位值的函数。
- 返回：

```python
{
    "custom_prompt_before_data": "...",
    "custom_prompt_after_data": "...",
}
```

- 空文本时两个槽位都为空。
- 非空文本统一追加两个换行作为分隔。
- 日志只写 role、placement、是否注入、长度和 hash，不记录提示词正文。
- 非法 placement 抛出 `ValueError`。

### `tradingagents/prompts/zh.py`

三个模板各增加两个槽位：

```text
{custom_prompt_before_data}
{custom_prompt_after_data}
```

共 6 个占位符。

空槽位格式化后保留原模板的原有换行结构；开关关闭时不会插入自定义文本。

### 三个角色 factory

文件：

```text
tradingagents/agents/researchers/bull_researcher.py
tradingagents/agents/researchers/bear_researcher.py
tradingagents/agents/managers/research_manager.py
```

共同变化：

- factory 签名新增 `custom_prompt` 和 `placement`。
- 使用共享的 `DEFAULT_PLACEMENT`。
- 节点构建 prompt 时调用 `build_injection_slots()`。
- 两个槽位通过 `**injection_slots` 传入 `.format()`。
- factory 内没有各自复制 placement 条件判断。

### `tradingagents/graph/setup.py`

- `GraphSetup.__init__` 接收 `custom_prompts` 与 `custom_prompt_placement`。
- 只有 `None` 使用默认 placement。
- `setup_graph()` 只把对应角色文本传给 bull、bear、research_manager 三个 factory。
- 其他角色 factory 不接收 `custom_prompt`。

### `tradingagents/graph/trading_graph.py`

- `TradingAgentsGraph.__init__` 新增显式的 `custom_prompts` 和 `custom_prompt_placement` 参数。
- 这些值不写进通用 config。
- 直接传给 `GraphSetup`。

### `api/main.py`

新增/使用以下生产函数：

```text
_resolve_and_freeze_custom_prompts()
_build_custom_prompt_snapshot()
_attach_custom_prompt_snapshot()
```

行为：

1. 初始化报告后，在构造任何 `TradingAgentsGraph` 之前读取用户开关。
2. 开关关闭时不调用角色提示词解析器，三个角色得到空 bundle。
3. 开关打开时只调用一次 `resolve_all_roles_prompts()`。
4. 从全部角色解析结果中取本阶段三个目标角色。
5. 对合并后的 resolved text 执行 6000 字符上限检查。
6. 为当前任务冻结文本、hash、长度和 injected 状态。
7. 单周期和双周期构图都复用同一份冻结 bundle。
8. 两条正式结果保存路径均通过同一个 helper 写入
   `custom_prompt_snapshot`，并使用 `deepcopy`。

快照结构：

```json
{
  "enabled": true,
  "placement": "after_data",
  "roles": {
    "bull_researcher": {
      "resolved_text": "...",
      "resolved_hash": "...",
      "resolved_length": 123,
      "injected": true
    },
    "bear_researcher": {},
    "research_manager": {}
  }
}
```

完整 `resolved_text` 被保存，保证旧报告不会因用户之后修改提示词而失去当时使用的原文。

### `api/services/report_service.py`

本阶段同时收紧结构化字段语义：

`probability`：

- 必须是 `0.00–1.00`。
- `70` 一类百分比整数拒绝为 `None`，不自动除以 100。
- bool、NaN、无效文本、负数、超范围值均拒绝。
- 每条拒绝路径记录 WARNING，包含字段、原值和原因。

`confidence`：

- 必须是 `0–100` 整数。
- bool 拒绝。
- `75.9` 拒绝，不静默截断为 `75`。
- `75.0` 可接受为 `75`。
- 无效或越界值拒绝为 `None`，并记录 WARNING。

结构化提取 prompt 明确说明：

- confidence 不是上涨概率。
- probability 不得从 confidence 换算或代填。
- probability 只表示系统指定主周期内，期末价格高于明确分析基准价的概率。
- 缺乏主周期、基准价或定量依据时应为 `null`。

### `tests/test_custom_prompt_injection.py`

新增 T1–T20 长期回归测试。

重点覆盖：

- 关闭开关时零角色解析调用。
- 三个目标角色的注入范围和位置。
- 其他角色不接收自定义提示词。
- 一个任务只解析一次，双图复用冻结结果。
- 任务运行期间数据库变化不影响已冻结 bundle。
- 超长 resolved text 明确失败。
- 快照结构和 deepcopy 隔离。
- 两条保存分支都调用统一快照 helper。
- probability/confidence 的接受、拒绝和 WARNING 日志。
- bull/bear/research_manager 节点实际发送给 LLM 的 prompt 中，注入文本只出现一次且位于 after_data。
- bull 和 bear 的实际节点返回状态可正确更新 claims/round_summary。
- research_manager 的实际 `investment_plan` 可解析 `VERDICT`。
- `[PROMPT-OK]` 不污染 `DEBATE_STATE` 或 `VERDICT` 机读内容。

## 6. 测试与卫生检查

最终 focused 回归命令：

```bash
docker compose exec -T app /app/.venv/bin/python -m pytest -q \
  tests/test_custom_prompt_injection.py \
  tests/test_trading_graph_multi_horizon.py \
  tests/test_agent_states.py \
  tests/test_email_report_service.py \
  tests/test_market_analyst.py
```

审查方在暂存前独立重跑结果：

```text
67 passed, 2 warnings in 4.68s
```

两个 warning 均来自依赖：

- LangGraph serializer 的 pending deprecation warning。
- `py_mini_racer` 使用 `pkg_resources` 的 deprecation warning。

不是本阶段测试失败。

其他已完成检查：

```text
git diff --check: PASS
git diff --cached --check: PASS
```

生产路径搜索已确认不存在：

```text
phase_d_active
phase_d_verify
临时 prompt capture 写文件逻辑
[PROMPT-OK]
```

`[PROMPT-OK]` 只存在于长期测试 fixture 和断言中。

一次性脚本 `scripts/print_injection_ab_prompts.py` 已删除，不属于最终 diff。

Phase D 驱动脚本 `scripts/phase_d_verify.py` 已删除，不属于最终 diff。

## 7. Phase D 真实链路验证结果

Phase D 最终验收已经通过。

验证过程中曾有一次失败，但已查明不是 Phase C 用户身份透传缺陷：

- 第一次分析请求使用 server-level `TA_API_KEY`。
- 该 key 不能通过用户 token 校验，认证依赖回落为
  `local-default-user`。
- 提示词和开关却通过 JWT 写在 David 的真实账号下。
- 因此任务读取的是默认用户的 `prompt_injection_enabled=False`。
- 驱动脚本改成 JWT 后，身份链一致，第二次验证通过。
- 应用生产代码不需要为此修改。

目标账号：

```text
email=davidliu022305@gmail.com
user_id=429163f7-50b6-4982-8bdf-96ae99506843
```

最终 Phase D 的验收证据：

- bull 输出首行为 `[PROMPT-OK]`。
- bear 输出首行为 `[PROMPT-OK]`。
- research_manager 输出首行为 `[PROMPT-OK]`。
- bull/bear 的 `DEBATE_STATE` 正常。
- research_manager 的 `VERDICT` 正常。
- 报告任务状态为 `completed`。
- `custom_prompt_snapshot` 写入完整 resolved text、hash、长度和 injected 状态。
- 三份 LLM 调用前的最终 prompt 均包含：

```text
数据正文
→ 正式 global 提示词
→ 临时 Phase D 验证指令
→ 写作要求/输出要求
```

- after_data 位置得到直接确认。
- finally 清理成功。

最终验证时的三角色 resolved snapshot：

```text
enabled=True
placement=after_data
resolved_length=614
resolved_hash=7a53c77e9255
injected=True
contains_instruction=True
```

该 614 字版本由 523 字正式文案加临时验证指令组成，只用于 Phase D。

## 8. 正式服务端提示词状态

以下是 Phase D 清理完成后的最后一次确认结果。**这是既有验证记录，不是本交接书创建时重新访问服务端得到的实时读数。**

```text
switch=false
global chars=523
global hash=e8b3a71c826b
sentinel removed=true
```

正式 global 文案：

```text
术语与结构化字段语义定义（只定义语义，不改变当前角色的既有输出格式）：

如果当前角色的模板没有要求显式输出 confidence 或 probability，不要新增 JSON、表格或字段；仅在既有输出格式允许的正文中按以下语义使用。

confidence（置信度）：
- 使用 0–100 的整数
- 表示对本次分析结论可靠程度的主观把握，不表示价格上涨概率
- 80–100：关键数据充分、证据链完整，且不存在足以改变结论的重大缺口
- 50–79：部分数据缺失、证据存在冲突或结论仍有明显争议
- 0–49：关键数据严重缺失或结论高度不确定
- 禁止由 confidence 推导、换算或代填 probability

probability（上涨概率）：
- 使用 0.00–1.00 的小数，不使用百分比整数
- 表示在系统明确指定的主分析周期结束时，价格高于报告明确记录的分析基准价的概率
- 例如 60% 应写为 0.60，而不是 60
- 如果主分析周期、分析基准价或定量依据不明确，应填 null，不得自行选择周期、猜测基准价或从 confidence 换算
- 双周期报告只填写系统指定主周期对应的 probability
```

除非 David 明确要求，不要为了“确认”而：

- 打开 switch。
- PATCH 这段文案。
- 添加 sentinel。
- 再跑真实分析。

## 9. 必须保留的结构性限制

`research_manager` 的注入机制已经确认生效，但它当前仍看不到：

```text
fundamentals_report
market_report
news_report
```

这三份报告正文不进入 `research_manager` 的 prompt 模板，只用于构造
`curr_situation` 并查询历史 memory。

因此：

- 不能宣称 research_manager 能对这三份一手材料完整执行证据可信度分级。
- 正式提示词中关于证据链、数据充分性等语义，在 research_manager 身上受到输入可见性限制。
- 这是既有数据链结构限制。
- 不在 Phase C 修复。
- 后续汇报必须继续保留这一声明。

本次 Phase D 捕获还显示，research_manager 能看到的字段取决于已运行分析师；
例如某轮未运行量价分析师时，对应报告字段为空。这也不属于 Phase C 注入缺陷。

## 10. 审查过程与最终裁定

在最终批准前，审查方曾要求修正：

- 不要用测试重新实现生产逻辑制造假阳性。
- 提取可直接测试的生产 resolver/freezer helper。
- T4 通过真实 `GraphSetup.setup_graph()` 验证角色范围。
- T14 检查 `_run_job_inner` 两条保存分支都调用统一 helper。
- T19/T20 从节点实际返回结果验证首行和机读解析效果。
- validator 所有拒绝分支写 WARNING，并用 `caplog` 验证。
- 统一唯一 placement 默认值。
- 删除空字符串 placement 的静默回退。
- 删除全部 Phase D 临时代码。
- 删除一次性 prompt preview 脚本。

这些修正完成后，审查方对 10 文件 focused diff 的最终裁定：

```text
Phase C 代码审查通过，无阻止提交的缺陷。
```

暂存后又完成一次 staged 审查，最终裁定：

```text
暂存内容批准。等待明确指令后再提交，不进入 Phase E。
```

## 11. 新 Codex 的建议接续流程

### 如果 David 只是要求确认交接状态

执行只读检查：

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git diff --cached --name-status
git diff --cached --check
```

确认暂存区仍是第 2 节的 10 个路径，并汇报即可。

### 如果 David 明确授权提交 Phase C

1. 先确认暂存路径仍严格等于第 2 节。
2. 确认获批路径没有 unstaged drift：

```bash
git diff --name-only -- \
  api/main.py \
  api/services/report_service.py \
  tests/test_custom_prompt_injection.py \
  tradingagents/agents/managers/research_manager.py \
  tradingagents/agents/researchers/bear_researcher.py \
  tradingagents/agents/researchers/bull_researcher.py \
  tradingagents/agents/utils/prompt_injection.py \
  tradingagents/graph/setup.py \
  tradingagents/graph/trading_graph.py \
  tradingagents/prompts/zh.py
```

预期无输出。

3. 再运行：

```bash
git diff --cached --check
```

4. 仅在 David 明确给出或确认 commit message 后执行 commit。
5. commit 后核对：

```bash
git show --stat --oneline --decorate HEAD
git status --short
```

6. 明确汇报：

- commit hash。
- commit message。
- 正好包含哪 10 个文件。
- 无关脏文件仍保留。
- 未进入 Phase E。

不要在 commit 前重新 `git add` 全仓库；当前批准内容已经在 index 中。

### 如果 David 要进入 Phase E

不要仅凭本交接书推断 Phase E 的需求。先让 David提供或确认 Phase E 的明确范围，
再按 `AGENTS.md` 先复述目标、拟改文件和风险点，等待确认后开始。

## 12. 关于新窗口能否自动读取旧窗口

不要假设一个全新的 VS Code Codex 任务自动拥有当前 Claude Code/Codex 窗口的完整逐条对话。

可能存在的连续性来源包括：

- 同一 Codex 账号中的任务历史。
- Codex 的本地记忆或历史检索能力。
- 本机仍保存的旧任务记录。
- David 主动让新任务读取旧任务。

但这些能力取决于客户端、登录状态、任务是否为同一线程、工具权限和历史是否可检索；
新任务通常不会把另一个窗口的全部上下文自动放进当前上下文。

因此接手时应以以下优先级为准：

1. 当前仓库与 Git 的实时状态。
2. 本交接书。
3. `/private/tmp/phase-c-staged.diff`（若临时目录文件仍存在）。
4. 旧任务历史或迁移归档，用于补充细节。

如果旧记录与当前 Git 状态冲突，以实时 Git 状态为准，并停下向 David说明差异。

