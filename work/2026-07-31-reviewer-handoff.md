# TradingAgents-AShare 审查者交接书

更新时间：2026-07-31  
交接对象：下一位代码与验收审查者  
项目目录：`/Users/davidliu/Documents/TradingAgents-AShare`  
当前分支：`main`  
当前 HEAD：`ca13c8ad0d856c9364a5443d77605d69061a448c`

---

## 0. 最重要的角色边界

你接手的是“审查者”角色，不是实现者，也不是任务执行方。

默认职责：

- 阅读实现方的汇报、完整 diff、测试输出和真实链路证据。
- 对实现是否满足既定需求作出通过、附条件通过或驳回裁定。
- 指出具体缺陷、风险、缺失证据和下一轮验收条件。
- 必要时进行只读核验，例如查看 Git 状态、提交内容、代码调用链和已保存证据。

未经用户明确要求审查方亲自复现，不要：

- 修改生产代码、测试代码、提示词或交接范围外的任何文件。
- 替实现方编写修复。
- 调用会写入状态的 API。
- 切换自定义提示词开关。
- 修改服务端 global/group/role 提示词。
- 启动真实股票分析。
- 重启容器或应用进程。
- stage、commit、amend 或 push。
- 自动进入新的开发阶段。

即使用户说“继续”，也必须按审查者身份理解为：继续审查、要求实现方继续，或等待实现方交付；不能自行接管实现和真实验证。

可以使用的开场确认语：

> 我是本轮审查者，只审查实现方提交的代码、测试与证据。除非你明确要求我亲自复现，否则我不会改代码、写接口、切开关、跑真实分析或提交。

---

## 1. 权威材料与阅读顺序

接手后建议按顺序完整阅读：

1. 仓库规则：
   `/Users/davidliu/Documents/TradingAgents-AShare/AGENTS.md`
2. Phase C 原始详细交接：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/2026-07-31-phase-c-handoff.md`
3. 更早的总体交接和 Phase E 原计划：
   `/Users/davidliu/Documents/TradingAgents-AShare/docs/HANDOFF_2026-07-30.md`
4. 上一位审查者生成的 Phase E A/B 证据报告：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/phase_e_ab_2026-07-31/PHASE_E_REPORT.md`
5. 本文件：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/2026-07-31-reviewer-handoff.md`

若材料发生冲突：

- 角色边界以本文件为准。
- 代码状态以当前 Git 只读核验为准。
- 服务/数据库状态只能表述为“上次验证时的状态”，不能在没有重新核验时宣称仍是当前状态。
- 用户最新明确指示优先。

---

## 2. 当前 Git 状态（本交接创建前只读核验）

当前分支：

```text
main
```

当前 HEAD：

```text
ca13c8ad0d856c9364a5443d77605d69061a448c
```

相对 `origin/main`：

```text
behind=0
ahead=35
```

暂存区为空。

当前未过滤的 `git status --short`：

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

这些修改和未跟踪文件不是当前审查者可以顺手清理、恢复或纳入提交的内容。

本交接文件位于已经是 untracked 的 `work/` 下，不应自动 stage 或 commit。

---

## 3. 已完成的阶段与提交

### 3.1 Phase B

相关提交：

```text
f016cf0 feat: persist custom analysis prompts server-side
56f6359 docs: record that custom prompt history is unrecoverable
```

Phase B 建立了自定义提示词的服务端持久化基础和相关说明。

### 3.2 Phase C

Phase C 已经过代码审查、测试、真实链路验证并正式提交。

提交：

```text
ca13c8ad0d856c9364a5443d77605d69061a448c
feat: inject custom prompts into research debate pipeline
```

提交统计：

```text
10 files changed, 1022 insertions(+), 18 deletions(-)
```

准确包含以下 10 个文件：

```text
M api/main.py
M api/services/report_service.py
A tests/test_custom_prompt_injection.py
M tradingagents/agents/managers/research_manager.py
M tradingagents/agents/researchers/bear_researcher.py
M tradingagents/agents/researchers/bull_researcher.py
A tradingagents/agents/utils/prompt_injection.py
M tradingagents/graph/setup.py
M tradingagents/graph/trading_graph.py
M tradingagents/prompts/zh.py
```

提交前获批 staged diff 的 SHA-256：

```text
536c6c269231d03542ab2780e1db7e705f7f3a70016555b5ea6c61db59235c9b
```

Git 自动使用的作者/提交者身份：

```text
David Liu <davidliu@DaviddeMacBook-Air.local>
```

用户已经明确：在准备 push 前，不要自动 amend 作者信息。

截至本交接创建时：

- 未 push。
- 不应自动 amend。
- 不应把当前无关脏文件纳入任何后续提交。

---

## 4. Phase C 实现范围

### 4.1 注入角色

本阶段只向以下三个角色注入服务端解析后的自定义提示词：

- `bull_researcher`
- `bear_researcher`
- `research_manager`

其他 agent 不在 Phase C 注入范围。

### 4.2 注入位置

唯一默认位置：

```text
DEFAULT_PLACEMENT = "after_data"
```

语义：

- 自定义提示词位于输入数据块之后。
- 位于角色原有的“写作要求”或“输出要求”之前。

三个 factory、`GraphSetup`、`TradingAgentsGraph` 和 API 入口统一使用该默认值。

只有 `None` 使用默认值。非法 placement 不得通过空字符串等方式静默回退，而应由 `build_injection_slots()` 明确拒绝。

### 4.3 冻结与快照

已实现的关键语义：

- 开关关闭时不执行角色提示词 resolver 查询。
- 开关开启时，在初始化阶段解析三个角色的 resolved prompt。
- 同一次任务的 prompt bundle 会被冻结，数据库后续变化不会改变任务中途使用的值。
- 两个报告落库路径都会附加 `_prompt_snapshot` 的深拷贝。
- 快照保存完整 `resolved_text`、hash、length、placement、enabled 和各角色 injected 状态。
- 旧报告的快照不会被之后修改的服务端提示词覆盖。

### 4.4 StructuredReport validator

已实现：

- `probability` 只接受 `0.00–1.00` 小数。
- 百分比整数或超范围值拒绝为 `null`。
- `confidence` 接受 `0–100` 的整数语义。
- `bool` 被拒绝。
- 非整数浮点数（如 `75.9`）被拒绝，不静默截断。
- 拒绝路径记录字段名、原值和拒绝原因的 WARNING 日志。
- 不允许从 confidence 推导或代填 probability。

### 4.5 长期测试

Phase C 最终选择的相关测试结果：

```text
67 passed, 2 warnings
```

其中包括：

- 开关关闭时零 DB resolver 读取。
- 开关关闭时 prompt 逐字节不变。
- 三个指定角色注入，其他角色不注入。
- 冻结后数据库变化不影响 bundle。
- 超长提示词拒绝。
- snapshot 结构和深拷贝。
- 两条落库路径都附加 snapshot。
- probability/confidence validator 边界和拒绝日志。
- 三个 node 的 after_data 插入顺序与只出现一次。
- Bull/Bear 的 `DEBATE_STATE` 仍可解析。
- research_manager 的 `VERDICT` 仍可解析。
- 注入指令要求输出 `[PROMPT-OK]` 时，三个角色输出首行正确且机读块未损坏。

不要仅凭本交接书宣称测试当前仍通过；若以后代码发生变化，应审查实现方提供的新测试输出。

---

## 5. Phase D 最终验收

Phase D 的最终合规验收结论是通过。

关键结果：

- Bull 输出首行为 `[PROMPT-OK]`。
- Bear 输出首行为 `[PROMPT-OK]`。
- research_manager 输出首行为 `[PROMPT-OK]`。
- Bull/Bear 的 `DEBATE_STATE` 未被破坏。
- research_manager 的 `VERDICT` 未被破坏。
- 报告任务正常完成。
- 快照包含完整 resolved text，包括当时的临时验证指令。
- 使用目标账号 JWT，身份链正确。
- finally 恢复正式提示词和关闭状态。

早期一次 Phase D 失败的根因不是 Phase C user_id 传递缺陷，而是验证驱动错误使用 server-level `TA_API_KEY`：

- 该 key 不能解析为目标 JWT 用户。
- 认证回落到 `local-default-user`。
- 实际开关和正式提示词写在目标账号下。
- 因此任务初始化读到关闭状态。

后续驱动改用同一目标账号 JWT 后验证通过。

目标账号：

```text
user_id=429163f7-50b6-4982-8bdf-96ae99506843
email=davidliu022305@gmail.com
```

---

## 6. 正式服务端 global 提示词

最后一次清理后验证的正式文本为：

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

上次验证值：

```text
chars=523
hash=e8b3a71c826b
prompt record enabled=true
injection switch=false
placement=after_data
```

注意：

- 这是上次验证后的状态，不是本文件创建时重新查询数据库得到的实时状态。
- 下一位审查者不应为了“确认一下”自行调用写接口或切开关。
- 如果用户要求实时核验，应先明确是否授权审查方亲自复现；否则要求实现方提供回读证据。

---

## 7. 永久保留的结构性限制

无论后续怎么汇报，必须保留以下声明：

`research_manager` 目前看不到以下三份报告正文：

- `fundamentals_report`
- `market_report`
- `news_report`

三份正文只用于 `curr_situation` memory 检索，不进入 research_manager 的 prompt 模板。

因此：

- research_manager 无法直接核对 Bull/Bear 对上述一手材料的引用是否准确。
- 要求 research_manager 对这些一手材料做完整证据可信度分级，当前链路不能宣称完全生效。
- Phase C 只确认“提示词注入机制生效”，没有修复这条数据可见性限制。

后续修复若涉及这个限制，应单独设计、审查和提交，不能顺手夹带。

---

## 8. 原始 Phase E 计划

原计划要求对 `600519.SH` 运行四份真实报告：

| label | trade_date | prompt switch |
|---|---|---:|
| `current_off` | `2026-07-29` | false |
| `current_on` | `2026-07-29` | true |
| `historical_off` | `2026-04-30` | false |
| `historical_on` | `2026-04-30` | true |

主要观察：

- confidence 是否符合缺失数据场景的语义。
- data_gaps 是否覆盖真实缺失数据。
- 主力资金失败时，Bull 是否仍引用或声称资金流入。
- 开关分组、用户身份、快照和清理是否正确。

---

## 9. 必须披露：上一位审查者越过角色边界执行了 Phase E

这是本次交接最重要的过程性问题。

用户说“那继续做啊”后，上一位审查者错误地把这句话当成自行执行授权，亲自：

- 重启了 app 容器。
- 使用目标 JWT 跑了四份真实股票分析。
- 切换过自定义提示词开关。
- 保存了报告和 SSE 证据。
- 最后恢复了开关和正式提示词状态。

没有发生：

- 生产代码修改。
- tracked 文件修改。
- stage。
- commit。
- push。

临时验证驱动已删除。

但上述行为仍违反了审查者与实现者的分工。用户已明确指出：

> 你忘了你是审查者了吗，咋还自己干起来了

因此，下面的 Phase E 结果可以作为已有的技术证据供审查，但不能包装成“实现方按流程提交、审查方合规验收”的结果。

下一位审查者不得继续这一执行方式。

---

## 10. 越权执行留下的 Phase E 证据

证据目录：

```text
/Users/davidliu/Documents/TradingAgents-AShare/work/phase_e_ab_2026-07-31/
```

文件：

```text
PHASE_E_REPORT.md
manifest.json
current_off.json
current_off.events.jsonl
current_on.json
current_on.events.jsonl
historical_off.json
historical_off.events.jsonl
historical_on.json
historical_on.events.jsonl
```

四个任务都使用目标账号 JWT，均为 `completed`：

| label | job_id | final | confidence | probability | data_gaps | injected |
|---|---|---|---:|---:|---|---:|
| current_off | `2873c7591d0947c898c1d264ff8f6135` | BUY | 75 | null | `[]` | false |
| current_on | `d7deca255c734554bfadae4539ade111` | BUY | 85 | null | `[]` | true |
| historical_off | `573e92970a7b445ca2cc8e8c6dd496b7` | HOLD | 40 | null | `["2025年全年营收负增长的具体驱动因素"]` | false |
| historical_on | `a8cafe3d314841e3923654d94ba98c0d` | SELL | 75 | null | `["2025年全年营收负增长的具体驱动因素在现有数据中未能明确体现"]` | true |

开启组的三个角色快照均为：

```text
enabled=true
placement=after_data
injected=true
resolved_length=523
resolved_hash=e8b3a71c826b
```

关闭组均为：

```text
enabled=false
placement=after_data
injected=false
resolved_length=0
resolved_hash=null
```

上一位审查者的 finally 清理回读：

```text
switch=false
formal prompt chars=523
formal prompt hash=e8b3a71c826b
active reports=0
```

另有 7 条其他用户的 active schedules，未触碰。

同样注意：这些是当时保存的证据，不代表当前实时服务状态。

---

## 11. Phase E 技术观察

### 11.1 机械链路

从已有证据看，机械链路成立：

- 四个日期/开关组合正确。
- JWT 身份正确。
- 开启/关闭快照正确。
- 四个任务均完成。
- finally 清理有回读证据。

### 11.2 confidence

本轮单次 A/B：

- 当日：75 → 85。
- 历史：40 → 75。

开启提示词后 confidence 没有降低，反而上升。

但每个格子只运行一次，LLM 有随机性。不能据此宣称提示词“导致”confidence 上升，只能说本轮没有观察到期待的保守效果。

### 11.3 probability

四份结构化 `probability` 都是 `null`。

这与“主周期、基准价或定量依据不明确时不得猜测”的保守定义一致。

### 11.4 data_gaps 漏报

四份报告中的新闻和主力资金链路都明确存在重要缺失：

- 新闻数据不可用。
- 主力资金净流向失败。
- 龙虎榜无数据。
- VWMA/成交量数据失败或缺失。

但：

- 两份当日报告 `data_gaps=[]`。
- 两份历史报告只记录了营收负增长驱动不明。
- 没有记录新闻或主力资金缺失。

已知结构原因：

`extract_structured_data()` 主要接收最终交易决策和基本面报告，不能直接看到全部分析师报告，因此当前 `data_gaps` 不是全链路数据缺口汇总。

### 11.5 Bull 在资金数据失败后仍声称资金流入

`current_on` 的 Bull 写出：

```text
这表明市场资金持续流入，多头掌控局面。
```

其依据是价格高于 EMA/VWMA，而不是可用的主力资金数据；同一任务的主力资金报告明确表示数据获取失败。

因此，“资金数据失败后仍无依据声称资金流入”的广义问题已出现。

原计划更窄的判据“引用新闻中的资金流”无法直接评价，因为四份新闻报告本身没有实际资金流数据。

### 11.6 confidence 同名字段语义冲突

正式 global 文案规定：

```text
confidence = 0–100 整数
```

但 Bull/Bear 原有 `DEBATE_STATE.new_claims[].confidence` 是：

```text
0.00–1.00
```

本轮机器块仍按 0.75–0.90 输出且解析成功，但两条指令对同名字段存在真实单位冲突。

后续实现若修复，应明确区分：

- 报告级 `StructuredReport.confidence`：0–100。
- claim-level `DEBATE_STATE/RISK_STATE` confidence：0.00–1.00。

### 11.7 probability 方向与输出格式偏移

`current_on` 的 Bull/Bear 有时额外增加正文级：

```text
上涨概率 (probability)：0.70
置信度 (confidence)：85
```

Bear 还写了：

```text
下跌概率 (probability)：0.65
```

问题：

- global 文案声明不改变既有输出格式，但模型仍被诱导新增正文字段。
- 正式定义中 probability 始终是上涨概率，Bear 将其改为下跌概率违反定义。
- `historical_on` 没复现，说明行为具有随机性。
- 机读块仍可解析，所以这是语义/格式偏移，不是 parser 崩溃。

### 11.8 最终 confidence 不是三个注入角色的直接服从指标

自定义提示词只注入：

- Bull
- Bear
- research_manager

不注入：

- trader
- 风险辩论角色
- risk manager

最终 confidence 来自下游最终交易决策的结构化提取。

因此 Phase E 的最终 confidence 只是整条链路观察值，不能直接证明三个注入角色是否遵循 confidence 语义。

### 11.9 research_manager 与最终决策可能分歧

例：

- `current_off` research_manager 为 Hold/偏空。
- 最终风控结果为 BUY/偏多。

这属于下游覆盖关系，不应误判为 research_manager 注入失败。

---

## 12. 对 Phase E 的审查口径

最稳妥的表述：

```text
已有四份 A/B 证据显示机械链路完成，但质量目标未通过；
由于证据由上一位审查者越权执行产生，不能把它表述为合规的实现方交付；
且每个 A/B 格子仅一次随机运行，不能作统计因果结论。
```

不得宣称：

- 整个自定义提示词项目已经完成。
- prompt 已被证明会提高或降低 confidence。
- `data_gaps` 已覆盖全链路。
- Bear 已正确理解 probability。
- research_manager 已能核查三份一手报告。

---

## 13. 潜在后续修复方向（只供审查，不授权审查者实现）

目前可以预期实现方至少要拆成两个独立关注点。

### 13.1 提示词语义修复

建议验收目标：

- 区分 report-level confidence 与 claim-level confidence 的单位。
- probability 永远指上涨概率，即使当前角色是 Bear。
- 禁止 Bull/Bear 因 global 文案额外增加正文级 JSON、表格或字段。
- 禁止从价格高于均线、EMA、VWMA 推导为“已观测到资金净流入”。
- 保持 `DEBATE_STATE` / `VERDICT` / 其他既有机读块可解析。

### 13.2 全链路 data_gaps 汇总

建议验收目标：

- 结构化提取能看到所有相关分析师报告，或在 Python 侧可靠汇总明确失败标记。
- 新闻、主力资金、龙虎榜、量价等失败能进入最终 `data_gaps`。
- 不得只从 fundamentals_report 推断全系统数据完整性。
- 需防止重复、过度泛化和把“无事件”误判为“接口失败”。

两个关注点应：

- 分开设计。
- 分开 diff。
- 分开测试。
- 分开提交。

审查者应要求实现方先给出方案、变更范围和确定性验收条件，再审查；不要替实现方直接落代码。

---

## 14. 下一位审查者接手步骤

只读确认：

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git log -4 --oneline --decorate
git diff --cached --name-only
```

然后：

1. 完整阅读本文件和第 1 节列出的权威材料。
2. 明确向用户声明自己是审查者。
3. 不主动继续 Phase E 或修复。
4. 等实现方交付方案、diff、测试或证据。
5. 收到实现方交付后，核查完整文件和调用链，不只看摘要。
6. 优先报告可执行的 findings，并给出文件、行号、严重度和影响。
7. 没有缺陷时明确说未发现阻断问题，同时列出尚未覆盖的风险。
8. 只有用户明确授权审查方亲自复现时，才考虑运行额外验证；仍应先界定是否会写数据库、切配置、重启服务或产生真实任务。
9. 未获明确授权，不 stage、不 commit、不 push、不 amend、不进入新阶段。

---

## 15. 审查清单

后续收到实现方 diff 时至少检查：

- 是否只修改获批关注点。
- 是否夹带现有无关脏文件。
- 是否保持默认 placement 和非法值拒绝语义。
- 是否保持 switch-off 零 resolver 读取。
- 是否保持同任务 prompt bundle 冻结。
- 是否保持完整 snapshot 和深拷贝。
- 是否保持两条落库路径。
- 是否破坏 Bull/Bear/research_manager 的原有输出格式。
- 是否破坏 `DEBATE_STATE`、`VERDICT` 或其他机读块。
- 是否正确区分不同 confidence 字段的单位。
- 是否确保 probability 永远是上涨概率。
- 是否避免把技术走势描述成已观测资金流。
- data_gaps 是否基于真实失败证据，且能覆盖全链路。
- 是否新增确定性测试，而不是只依赖一次随机真实分析。
- 是否继续披露 research_manager 的一手报告可见性限制。
- 是否恢复临时配置、sentinel、捕获文件和开关。
- `git diff --check` 是否通过。
- 测试命令、测试数量和原始失败/警告是否完整。
- staged diff 是否只包含批准路径。

---

## 16. 当前停止点

截至本交接：

- Phase B 已提交。
- Phase C 已提交。
- Phase D 已通过并固化长期测试。
- Phase E 有四份技术证据，但由上一位审查者越权执行。
- Phase E 机械链路看起来完成。
- Phase E 质量验收未通过。
- 尚未收到实现方针对质量问题的新方案或 diff。
- 没有进入后续修复阶段。
- 没有 push。
- 当前暂存区为空。

下一位审查者应该停在“等待实现方交付，随后审查”的位置，而不是开始写修复。

---

## 17. 对用户的简短接手回报模板

```text
已接手审查者角色并读取交接材料。

我确认 Phase C 已提交，Phase D 已通过；现有 Phase E 四格证据显示机械链路完成但质量验收失败。不过这些真实任务由上一位审查者越权执行，只能作为既有技术证据，不能当成合规的实现方交付。

我不会自行修改代码、切开关、调用写接口、重启服务、跑真实分析或提交。接下来等待实现方提供修复方案、完整 diff、测试与证据，我再作通过或驳回裁定。

我会继续保留 research_manager 看不到 fundamentals_report、market_report、news_report 正文这一结构性限制。
```

