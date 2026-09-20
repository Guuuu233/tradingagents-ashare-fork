# TradingAgents-AShare 执行者交接书

更新时间：2026-07-31  
交接对象：下一位实现/执行者  
项目目录：`/Users/davidliu/Documents/TradingAgents-AShare`  
当前分支：`main`  
当前 HEAD：`ca13c8ad0d856c9364a5443d77605d69061a448c`

---

## 0. 你的角色

你接手的是“实现者/执行者”角色。

你的职责是：

- 根据用户和审查方批准的范围修改代码。
- 编写确定性长期测试。
- 运行与改动风险相称的测试。
- 提供完整 diff、原始测试输出、运行证据和清理回读。
- 在每个约定停点停下，等待审查方裁定。

你不是审查者。不要替自己的实现作最终通过裁定。

未经明确指令，不要：

- push。
- amend 已有提交。
- 修改 Git 作者身份。
- 自动进入下一阶段。
- 把两个独立问题混成一个大提交。
- 清理、恢复或提交工作区内来源不明的脏文件。
- 重跑四格真实 A/B。
- 修改真实用户提示词或开关。
- 重启容器。

建议接手时先向用户报告：

> 我已按执行者角色接手。先只读核验当前仓库和交接材料，不改代码、不切开关、不跑真实分析。随后我会把两个质量问题拆成独立方案，先提交方案和验收条件给审查方，获批后一次只实现一个关注点。

---

## 1. 必读材料

开始前完整阅读：

1. 当前仓库规则：
   `/Users/davidliu/Documents/TradingAgents-AShare/AGENTS.md`
2. 审查者交接书：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/2026-07-31-reviewer-handoff.md`
3. Phase C 原始详细交接：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/2026-07-31-phase-c-handoff.md`
4. Phase E 证据报告：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/phase_e_ab_2026-07-31/PHASE_E_REPORT.md`
5. 更早总体交接和原始 Phase E 计划：
   `/Users/davidliu/Documents/TradingAgents-AShare/docs/HANDOFF_2026-07-30.md`
6. 本文件：
   `/Users/davidliu/Documents/TradingAgents-AShare/work/2026-07-31-implementer-handoff.md`

注意：

- `AGENTS.md` 当前自身是 modified 状态；必须读取工作区当前内容并遵守，但不要擅自把它纳入本任务提交。
- 用户最新指令优先。
- 本文件提供执行边界，不替代审查方对具体方案的批准。

---

## 2. 接手时的只读核验

先运行：

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git branch --show-current
git rev-parse HEAD
git log -4 --oneline --decorate
git diff --cached --name-only
```

本交接创建前核验到：

```text
branch=main
HEAD=ca13c8ad0d856c9364a5443d77605d69061a448c
staging=empty
```

当前未过滤 `git status --short`：

```text
 M .gitignore
 M AGENTS.md
 M tests/test_api_smoke.py
 M tests/test_intent_parser.py
 M tests/test_portfolio_import.py
?? CLAUDE.md
?? GEMINI.md
?? locks/
?? scripts/work_smoke_commit2.py
?? tradingagents.db-journal
?? work/
?? work_analysis_collect_probe.json
?? work_smoke_commit2.py
```

与上一份审查者交接相比，以下项目后来出现：

```text
 M .gitignore
 M AGENTS.md
?? CLAUDE.md
?? GEMINI.md
?? locks/
```

来源在本交接中没有确认。

执行要求：

- 不恢复。
- 不删除。
- 不格式化。
- 不顺手修改。
- 不纳入 stage。
- 若你的拟修改路径与这些文件重叠，先停下向用户和审查方说明。

本交接文件位于已有 untracked 的 `work/` 下，不应自动提交。

---

## 3. 已完成状态

### 3.1 Phase B

```text
f016cf0 feat: persist custom analysis prompts server-side
56f6359 docs: record that custom prompt history is unrecoverable
```

### 3.2 Phase C

正式提交：

```text
ca13c8ad0d856c9364a5443d77605d69061a448c
feat: inject custom prompts into research debate pipeline
```

准确包含 10 个文件：

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

统计：

```text
1022 insertions(+), 18 deletions(-)
```

提交前批准的 staged diff SHA-256：

```text
536c6c269231d03542ab2780e1db7e705f7f3a70016555b5ea6c61db59235c9b
```

最终相关测试：

```text
67 passed, 2 warnings
```

Phase C 已关闭，不要在没有新缺陷证据的情况下重构它。

### 3.3 Phase D

最终验收通过：

- Bull 首行 `[PROMPT-OK]`。
- Bear 首行 `[PROMPT-OK]`。
- research_manager 首行 `[PROMPT-OK]`。
- `DEBATE_STATE` 和 `VERDICT` 正常解析。
- JWT 身份正确。
- prompt snapshot 保存完整 resolved text。
- finally 恢复正式提示词和关闭状态。

Phase D 已关闭。

---

## 4. Phase C 的不可回归约束

任何后续修改都必须保持：

### 4.1 注入角色范围

只注入：

- `bull_researcher`
- `bear_researcher`
- `research_manager`

其他角色没有自动纳入 Phase C 范围。

### 4.2 注入位置

```text
DEFAULT_PLACEMENT = "after_data"
```

自定义提示词位于：

```text
数据块之后
写作要求/输出要求之前
```

### 4.3 placement 校验

- 只有 `None` 使用默认值。
- 非法 placement 明确拒绝。
- 不允许用空字符串静默回退。

### 4.4 switch-off

- 开关关闭时不执行 resolver 数据库查询。
- 注入槽位为空。
- 原始 prompt 逐字节不变。

### 4.5 prompt bundle

- 任务初始化阶段一次解析。
- 同一次任务内冻结。
- 数据库后续变化不影响已启动任务。

### 4.6 snapshot

- 两条报告落库路径都附加。
- 使用深拷贝。
- 保存完整 `resolved_text`、hash、length、placement、enabled 和角色 injected 状态。

### 4.7 validator

- `probability` 只接受 0.00–1.00。
- 百分比整数拒绝为 null。
- `confidence` 只接受 0–100 整数语义。
- bool 拒绝。
- 非整数 float 拒绝，不截断。
- 拒绝路径有 WARNING 日志。
- 不从 confidence 代填 probability。

### 4.8 机读块

不得破坏：

- Bull/Bear 的 `DEBATE_STATE`。
- research_manager 的 `VERDICT`。
- 风险角色已有的 `RISK_STATE`。

---

## 5. 永久披露的结构性限制

`research_manager` 看不到：

- `fundamentals_report`
- `market_report`
- `news_report`

这三份正文只用于 `curr_situation` memory 检索，不进入 research_manager prompt。

因此：

- 它不能独立核对 Bull/Bear 对这三份一手材料的引用。
- 不能宣称它已完整执行一手证据分级。
- 本轮后续修复若不专门改变数据链，就必须继续披露该限制。

不要把这项结构性修复偷偷夹入下面两个质量修复。

---

## 6. Phase E 现有证据的性质

四格真实 A/B 已经存在：

| label | trade_date | switch |
|---|---|---:|
| current_off | 2026-07-29 | false |
| current_on | 2026-07-29 | true |
| historical_off | 2026-04-30 | false |
| historical_on | 2026-04-30 | true |

证据位于：

```text
/Users/davidliu/Documents/TradingAgents-AShare/work/phase_e_ab_2026-07-31/
```

但是这些任务是上一位审查者越过角色边界亲自运行的。

你可以：

- 把文件当作已有诊断证据阅读。
- 从中提炼确定性回归条件。

你不可以：

- 把它包装成合规的实现方交付。
- 自动再跑一遍。
- 用每格一次的随机结果作因果结论。

机械链路已有证据：

- 四任务 completed。
- 日期与开关分组正确。
- JWT 用户正确。
- prompt snapshot 正确。
- finally 清理有回读。

质量验收失败：

- 开启组 confidence 没有表现得更保守。
- `data_gaps` 漏掉新闻和主力资金失败。
- Bull 在资金数据失败时仍声称资金持续流入。
- report confidence 与 claim confidence 单位冲突。
- Bear 把 probability 写成下跌概率。
- Bull/Bear 有时新增正文级 probability/confidence 字段。

---

## 7. 当前正式服务端提示词

最后一次验证的 global 文案：

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

上次清理后值：

```text
chars=523
hash=e8b3a71c826b
prompt row enabled=true
injection switch=false
placement=after_data
```

这是“上次验证值”，不是本文件创建时重新查询得到的实时状态。

不要直接 PATCH。

在未来获批写入前必须：

1. 使用目标账号 JWT，不使用 server-level `TA_API_KEY`。
2. 确认目标账号：

   ```text
   user_id=429163f7-50b6-4982-8bdf-96ae99506843
   email=davidliu022305@gmail.com
   ```

3. 先 GET 全部已有配置。
4. 明确 PATCH 的整体替换语义。
5. 展示拟写入的完整原文、字符数和 hash。
6. 展示三个角色的 resolved preview。
7. 获用户/审查方明确确认。
8. 写入后回读并本地重算 hash。
9. 开关保持 false，除非进入明确批准的真实验证步骤。

---

## 8. 下一阶段必须拆成两个独立关注点

不要同时实现。

先向审查方提交两个方案，等待选择先做哪一个。

建议顺序：

1. 提示词语义修复。
2. 全链路 `data_gaps` 汇总。

每个关注点应有：

- 独立问题定义。
- 独立文件范围。
- 独立长期测试。
- 独立 staged diff。
- 独立提交。

除非用户另行批准，不要把正式服务端提示词写入与代码修改混在一个不可审查步骤中。

---

## 9. 关注点 A：提示词语义修复

### 9.1 已确认的问题

正式 global 文案说：

```text
confidence 使用 0–100 整数
```

但现有模板中的机器块明确使用：

```json
{"new_claims": [{"confidence": 0.72}]}
```

涉及：

- `DEBATE_STATE.new_claims[].confidence`
- `RISK_STATE.new_claims[].confidence`

这些是 claim-level confidence，既有单位是 0.00–1.00。

另外：

- Bear 曾把 probability 写成“下跌概率”。
- Bull/Bear 曾新增正文级 probability/confidence 字段。
- Bull 曾把股价高于 EMA/VWMA 描述成“市场资金持续流入”。

### 9.2 目标语义

修订方案至少要表达：

1. 报告级 `StructuredReport.confidence`
   - 0–100 整数。
   - 表示最终报告结论可靠性。
2. claim-level confidence
   - `DEBATE_STATE` / `RISK_STATE` 中保持 0.00–1.00。
   - 不受报告级 0–100 定义覆盖。
3. `probability`
   - 始终表示上涨概率。
   - Bear 也不能改成下跌概率。
4. 角色输出格式
   - 模板没有要求时，不新增 JSON、表格或正文级字段。
   - 不能为了响应 global 文案额外打印 probability/confidence。
5. 资金流表述
   - EMA、VWMA、价格、量价趋势不是已观测资金净流入。
   - 主力资金数据失败时必须明确不可判断，不能改写成持续流入。

### 9.3 先诊断再选实现位置

不要默认问题一定要通过修改 `zh.py` 解决，也不要默认只改数据库文案就足够。

先比较至少三种边界：

#### 方案 A：只修订存储的 global 文案

优点：

- 不扩大 Phase C 代码范围。
- 符合“用户提示词定义语义”的原设计。

风险：

- global 文案同时注入三个角色，仍可能存在角色上下文差异。
- 用户未来可以再次改成冲突文案。
- 只能通过提示词约束，不能完全保证随机模型行为。

#### 方案 B：在应用层增加固定的系统语义护栏

优点：

- 对 claim/report 字段单位可建立更稳定的命名空间。

风险：

- 可能改变自定义提示词优先级。
- 可能扩大产品语义和 Phase C 范围。
- 必须解释用户自定义文本与固定护栏冲突时谁优先。

#### 方案 C：角色特定包装或解析侧保护

优点：

- 可针对 Bull/Bear 的机器块和资金措辞。

风险：

- 容易在三个 factory 重复 placement/包装判断。
- 不得破坏 `build_injection_slots()` 作为唯一槽位决策函数的约束。
- 解析侧只能保护结构，不一定能修复正文语义。

先向审查方提交推荐方案、理由、文件范围和不选其他方案的原因。

### 9.4 预期涉及的文件

只能作为候选，不是授权清单：

```text
tradingagents/prompts/zh.py
tradingagents/agents/utils/prompt_injection.py
tests/test_custom_prompt_injection.py
api/services/report_service.py
```

如果方案只改服务端存储文案，可能没有代码 diff，但仍需预览和写入审批。

如果需要触碰其他文件，先说明原因。

### 9.5 确定性测试要求

不要用“再跑一次 LLM 看是否听话”代替长期测试。

至少应覆盖：

- 最终 Bull prompt 中，自定义文案仍只出现一次。
- after_data 顺序不变。
- `DEBATE_STATE` 示例仍明确是 0.00–1.00 claim confidence。
- `RISK_STATE` 示例仍明确是 0.00–1.00 claim confidence。
- 修订语义明确声明 report confidence 与 claim confidence 不同。
- probability 定义中明确是上涨概率，且适用于 Bear。
- 不要求模板未定义的角色额外输出正文级字段。
- fake Bull/Bear 输出的机读块仍可解析。
- fake research_manager 输出的 `VERDICT` 仍可解析。
- switch-off prompt 逐字节不变。

可以测试“prompt 中存在禁止推断资金流的明确规则”，但不能把字符串断言包装成模型必然服从的证明。

### 9.6 第一停点

先提供：

- 推荐方案。
- 完整拟定文案。
- 文案 chars。
- 文案 `sha256[:12]`。
- 三角色完整 resolved preview 或可复现 preview。
- 拟修改文件列表。
- 新增/修改测试列表。
- 已知无法由单元测试证明的随机模型行为。

然后停下等待审查，不写真实服务。

---

## 10. 关注点 B：全链路 `data_gaps` 汇总

### 10.1 已确认的直接根因

当前函数签名：

```python
extract_structured_data(
    final_trade_decision: str,
    fundamentals_report: str = "",
    config: Optional[Dict[str, Any]] = None,
)
```

当前 extraction prompt 只看到：

```text
final_trade_decision[:3000]
fundamentals_report[:1000]
```

因此它看不到：

- `market_report`
- `sentiment_report`
- `news_report`
- `macro_report`
- `smart_money_report`
- `volume_price_report`
- 其他明确数据失败证据

`api/main.py` 有两条调用路径：

- dual-horizon 路径。
- single-horizon 路径。

两条路径目前都只传：

```text
final_trade_decision
fundamentals_report
config
```

两条路径都必须一致修复。

### 10.2 当前 result 中已有的数据

至少可用字段包括：

```text
market_report
sentiment_report
news_report
fundamentals_report
macro_report
smart_money_report
volume_price_report
analyst_traces
final_trade_decision
investment_plan
trader_investment_plan
```

dual-horizon 路径会把 primary horizon 的这些报告提升到 top level。

执行者应完整核验 `_build_result_payload()`、multi-horizon result 构造、保存和 SSE 输出，不要只改一个调用点。

### 10.3 要先决定的设计

至少比较：

#### 方案 A：把全部报告传给结构化 LLM

优点：

- LLM 可结合上下文归纳缺口。

风险：

- token 体积。
- 截断策略。
- 随机漏报。
- 报告中的“无事件”可能被误判为数据失败。
- 结构化提取成本和延迟上升。

#### 方案 B：Python 侧确定性汇总显式失败标记

优点：

- 可测试。
- 不依赖 LLM 随机服从。
- 可保证显式接口失败进入结果。

风险：

- 中文失败措辞多样。
- 简单关键词容易误报。
- “龙虎榜无数据”可能表示当日没有上榜，不等于接口坏。

#### 方案 C：混合

示例边界：

- Python 汇总明确的 source failure。
- LLM 继续提取报告中更语义化的缺口。
- 两者去重后合并。

风险：

- 合并、归一化和优先级需要明确。

不要直接选字符串大杂烩。先提交行为表。

### 10.4 必须定义的行为表

方案中至少回答：

| 输入情形 | 是否 data gap | 原因 |
|---|---:|---|
| 接口请求失败/超时/500 | 是 | 明确无法取得数据 |
| 返回 `error: None, status: completed` 但正文是失败文案 | 需要明确规则 | 不能只看任务状态 |
| 当日无龙虎榜记录 | 通常不是接口失败 | 可能是事实上的无上榜事件 |
| 新闻检索失败 | 是 | 来源不可用 |
| 当日确实没有重大新闻 | 通常不是接口失败 | “无事件”不等于“无数据” |
| 主力资金字段缺失 | 是 | 无法判断净流入 |
| 技术指标因成交量缺失无法计算 | 是 | 分析维度不完整 |
| 报告明确说某字段不可得 | 是 | 显式缺口 |
| 同一缺口被多个报告重复描述 | 合并 | 避免重复 |
| LLM 已输出同义 gap | 合并或保留更具体项 | 需有确定规则 |

### 10.5 合并语义

实现前明确：

- 顺序是否稳定。
- 同义项怎样去重。
- 是否保留数据源/报告名称。
- 是否限制最大条数。
- LLM extraction 失败时 Python gaps 是否仍保存。
- Python 汇总异常时是否影响主任务。
- single/dual horizon 是否完全一致。
- 保存到 ReportDB、job result 和 SSE 的值是否一致。
- 旧报告读取是否兼容。

建议 gap 文案包含报告或数据源名称，避免只有“数据缺失”这种不可操作文本。

### 10.6 预期候选文件

可能涉及：

```text
api/services/report_service.py
api/main.py
tests/test_report_recovery.py
tests/test_custom_prompt_injection.py
```

更理想的是新增一个聚焦 data-gaps 的测试文件，避免继续把不相干逻辑堆入 700 多行的 `test_custom_prompt_injection.py`。

如果修改数据库 schema，应先停下说明，因为当前需求原则上不需要 schema 变化。

### 10.7 确定性测试要求

至少覆盖：

- single-horizon 把全部需要的报告交给汇总逻辑。
- dual-horizon 把 primary horizon 的全部需要报告交给汇总逻辑。
- 明确新闻接口失败进入 `data_gaps`。
- 明确主力资金失败进入 `data_gaps`。
- 明确量价计算失败进入 `data_gaps`。
- 正常的“没有龙虎榜记录”不被误报为接口失败。
- 正常的“无重大新闻”不被误报。
- 同一缺口不重复。
- 已有 LLM `data_gaps` 不丢失。
- LLM structured extraction 返回 None 时，确定性 gaps 仍可保存。
- job result、SSE payload、ReportDB 保存一致。
- 两条保存路径行为一致。
- 现有报告字段恢复测试不回归。
- 不改变 `probability`、confidence、target/stop-loss 的既有行为。

### 10.8 第二停点

实现前先提交：

- 推荐架构。
- 行为表。
- 完整调用链。
- 拟修改文件。
- 数据量/token 预算。
- 去重规则。
- 失败降级策略。
- 测试矩阵。

等待审查通过后再写代码。

---

## 11. 测试和运行环境

项目运行测试优先使用容器：

```text
tradingagents-ashare
/app/.venv/bin/python
```

宿主机 Python 可能缺少依赖。

执行前先查看：

- 当前容器是否运行。
- 代码是否通过 volume 映射立即生效。
- uvicorn 是否启用 reload。

不要因为测试需要就擅自重启 app；若必须重启，先说明：

- 为什么当前进程未加载代码。
- 是否有 running/pending 任务。
- 重启影响。
- 恢复方案。

建议每个关注点至少执行：

```bash
git diff --check
```

以及：

- 新增的聚焦测试。
- Phase C 自定义提示词测试。
- 报告恢复/保存相关测试。
- 机读块解析相关测试。
- multi-horizon 相关测试。

测试汇报必须给：

- 完整命令。
- collected 数量。
- passed/failed/skipped/warnings。
- 原始失败输出。
- 是否在容器内运行。
- 对警告的归属说明。

不要只写“全部通过”。

---

## 12. 真实分析规则

默认不要跑。

现有四格证据已经足够定位问题，且每格单次 LLM 运行不能作统计因果判断。

若修复后确需真实验证，必须先让审查方批准：

- 精确验证目标。
- deterministic 判据。
- 股票。
- trade_date。
- 分析师范围。
- horizon。
- 用户身份。
- 是否允许写报告。
- 是否需要切开关。
- 是否需要临时提示词。
- 失败是否重跑。
- finally 清理步骤。

认证必须使用目标 JWT。

不要使用 server-level `TA_API_KEY` 代替用户身份；之前它曾回落到：

```text
local-default-user
```

真实验证临时内容必须：

- 明确标识。
- 不进入生产代码。
- finally 恢复。
- 回读 switch、prompt chars/hash。
- 删除 sentinel/capture。
- 不自动重跑失败任务。

---

## 13. Git 工作流

每个关注点完成后：

1. 先展示未过滤 `git status --short`。
2. 展示完整 focused diff。
3. 运行 `git diff --check`。
4. 展示测试原始输出。
5. 等审查方批准文件范围。
6. 只 stage 获批路径。
7. 展示：

   ```bash
   git diff --cached --name-status
   git diff --cached --stat
   git diff --cached --check
   ```

8. 生成完整 staged diff 文件供审查。
9. 等明确提交指令。
10. 提交后核对 commit 内容与剩余脏文件。

禁止：

- `git add .`
- `git add -A`
- 自动提交。
- 自动 push。
- amend `ca13c8a`。
- 恢复无关脏文件。

每个 commit 只解决一个关注点。

---

## 14. 向审查方汇报的格式

每轮建议按以下结构：

```text
当前阶段：

目标：

只读诊断：

推荐方案：

不选其他方案的原因：

拟修改文件：

不会修改的文件：

确定性验收条件：

测试计划：

真实运行需求：

风险和结构性限制：

停点：
```

实现完成后的汇报：

```text
改动摘要：

完整文件清单：

关键行为：

测试命令与原始结果：

git diff --check：

未过滤 git status：

正式配置回读：

尚未验证：

请求审查：
```

不要只给结论或截断 diff。

---

## 15. 推荐的第一轮行动

第一轮只做只读诊断和方案，不改代码。

建议具体动作：

1. 读完第 1 节材料。
2. 核验 Git 状态和 HEAD。
3. 完整查看以下实现及所有调用点：

   ```text
   api/services/report_service.py
   api/main.py
   tradingagents/prompts/zh.py
   tradingagents/agents/utils/prompt_injection.py
   tradingagents/agents/researchers/bull_researcher.py
   tradingagents/agents/researchers/bear_researcher.py
   tradingagents/agents/managers/research_manager.py
   tests/test_custom_prompt_injection.py
   tests/test_report_recovery.py
   ```

4. 阅读四份 Phase E JSON 和事件证据，不只看摘要。
5. 分别输出关注点 A、B 的方案和确定性验收条件。
6. 推荐先做哪一个。
7. 停下等待审查方选择。

不要在第一轮：

- 修改代码。
- PATCH 正式提示词。
- 跑真实分析。
- stage。
- commit。

---

## 16. 当前停止点

当前事实：

- Phase B 已提交。
- Phase C 已提交。
- Phase D 已通过。
- Phase E 机械链路有证据。
- Phase E 质量验收失败。
- 尚没有获批的新修复方案。
- 尚未开始提示词语义修复。
- 尚未开始全链路 `data_gaps` 修复。
- 暂存区为空。
- 未 push。

你的正确下一步不是直接写代码，而是：

```text
完成只读诊断
拆分两个修复方案
定义确定性验收条件
提交审查
停下等待选择
```

---

## 17. 接手回报模板

```text
已按执行者角色接手。

我已确认当前 HEAD 为 ca13c8a，暂存区为空。工作区存在多项预存或来源未确认的修改/未跟踪文件，我不会恢复、删除或纳入本任务。

Phase C 和 Phase D 已关闭；Phase E 现有证据显示机械链路完成但质量验收失败。下一步至少分成“提示词语义修复”和“全链路 data_gaps 汇总”两个独立关注点。

我本轮先只读核验代码与证据，分别提交方案、文件范围和确定性测试矩阵，等待审查方选择。未获批准前不改代码、不 PATCH 正式提示词、不切开关、不跑真实分析、不 stage、不 commit、不 push。
```

