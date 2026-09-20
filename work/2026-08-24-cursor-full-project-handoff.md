# TradingAgents-AShare 项目完整交接书（交给 Cursor 新接管者）

> **用途**：这是 Cursor 新接管者的独立工作手册。假设接手者不了解历史对话、项目内核、Multica 团队、部署方式和质量门禁。
>
> **编制时间**：2026-08-24 23:17 AWST（UTC+08:00）附近。
>
> **项目目录**：`/Users/davidliu/Documents/TradingAgents-AShare`
>
> **工作组远端**：Git remote `target`，目标分支 `codex/dav-4-p2a-trunk`。
>
> **最高原则**：本文是时间点快照。接手后的第一件事不是继续写代码，而是重新核验 Git、Multica、测试进程、服务和数据库的实时状态。

---

# 0. 接管后先做什么：不要重新施工

当前工作已经到 **P1-B Stage 4：宿主全量回归、合入、部署**。B1 Opening、B2 Challenge、B3 Tiebreak/Manager 已经完成开发和独立复审。

接手后的首要操作顺序：

1. 完整阅读：
   - `AGENTS.md`
   - `PROJECT_STATE.md`
   - `DECISIONS.md`
   - 本交接书
   - 顶层规格：`/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md`
2. 检查正在运行的宿主全量 pytest，**不要重复启动第二份全量**：
   - 当前 pytest PID：`28686`
   - 命令：
     ```bash
     env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/ -q
     ```
   - 当前输出文件：
     ```text
     /private/tmp/claude-501/-Users-davidliu-multica-workspaces-desktop-api-multica-ai-a9f7c79e-936b-441c-9895-d70c6ff76b54-16f8c54b04be-workdir/55592827-aebc-4d1a-88a0-541ee2bdeb1e/tasks/bszfdn2il.output
     ```
   - 最后核验时约到 65%，进程仍在运行，但原始输出已出现多组 `F`；尚无最终失败详情和计数。不得把它写成通过，也不得在终态前中断或重复启动。
   - 查询命令：
     ```bash
     ps -o pid,ppid,%cpu,%mem,etime,state,command -p 28686
     python3 - <<'PY'
     p='/private/tmp/claude-501/-Users-davidliu-multica-workspaces-desktop-api-multica-ai-a9f7c79e-936b-441c-9895-d70c6ff76b54-16f8c54b04be-workdir/55592827-aebc-4d1a-88a0-541ee2bdeb1e/tasks/bszfdn2il.output'
     s=open(p,errors='replace').read()
     print(s[-5000:])
     PY
     ```
3. 查询 Multica Stage 4 卡：
   ```bash
   multica issue runs DAV-397 --full-id
   multica issue get DAV-397 --output json
   multica issue comment list DAV-397 --output json
   ```
4. **即使全量通过，也不得直接合入或重启**。Hermes 在交接前发现两个新的合入前 BLOCK，已评论到 DAV-397：
   - 候选 diff 存在 EOF 多余空行；
   - 真实 HTTP 请求的 `config_overrides` allowlist 不接受 `v2_debate_enabled`，DAV-398 无法按规定单次开启 v2。
5. 先完成两个微返修及精确 SHA 复审，再重新组合、跑最终全量、fast-forward、部署和真实 3/3 验收。

---

# 1. 项目是什么

这是一个面向 **A 股盘后投研** 的多智能体系统，基于上游 TradingAgents fork 扩展。

系统不是简单的“让多个模型各写一段报告”，而是由数据采集、七类分析师、多空研究员、研究总监、交易员、风险辩论和风险总监组成的状态图工作流。

最终产品目标：

> 把“多头和空头轮流生成胜负文案”升级为“结构化分歧、证据化盘问、可校准决策”。

核心产品原则：

1. 数据失败必须显式上报，不能用空值诱导模型脑补。
2. 历史分析必须防前视，所有来源要有真实 `as_of`。
3. 多空不能只重复第一轮内容，必须独立立论、交叉盘问和必要时加赛。
4. claim、challenge、证据状态和总监裁决必须结构化、可复算、可审计。
5. 测试全绿不等于产品完成；必须以真实 API、运行进程和数据库落库验收。

---

# 2. 系统内核与真实调用链

## 2.1 主要目录

| 目录 | 职责 |
|---|---|
| `tradingagents/` | 多智能体核心、图、状态、Prompt、数据流、证据核验 |
| `api/` | FastAPI、认证、任务、报告持久化、设置、流式入口 |
| `frontend/` | React/TypeScript 前端 |
| `scheduler/` | 定时分析与后台调度 |
| `tests/` | 单元、集成、协议、API、回归测试 |
| `tests/golden/audit_20260823/` | 三只金标准报告与 replay verifier |
| `data/tradingagents.db` | 真实 SQLite 数据库 |
| `work/` | 施工规格、交接、审核证据、冻结补丁 |

## 2.2 从 HTTP 请求到报告落库

典型链路：

```text
POST /v1/analyze 或流式/Chat入口
  → api/main.py 认证与请求解析
  → _build_runtime_config(request.config_overrides, user_id)
  → TradingAgentsGraph(config)
  → GraphSetup / LangGraph
  → DataCollector 获取并冻结共享数据上下文
  → 七类分析师并行生成报告
  → Bull/Bear 投资辩论
  → Research Manager 裁决
  → Trader 计划
  → 风险辩论与 Risk Manager
  → 最终结构化字段解析
  → api/main.py 构造 result payload
  → report_service.create_report(...)
  → reports.result_data JSON 落库
  → API/前端读取历史报告
```

重要：报告有多条构造路径，不能只修 Graph 内部结果：

- stream-events 单 horizon；
- non-stream 单 horizon；
- query/manual hoist；
- dual-horizon aggregate。

P1-M 曾出现过“Graph helper正确、全量绿，但真实Web流式报告完全缺metadata/metrics”的事故。原因是 `api/main.py::_build_result_payload(final_state)` 绕过 Graph 的 horizon result helper。因此任何结果字段改动都要审所有入口和真实落库。

## 2.3 Graph 与状态

关键文件：

- `tradingagents/graph/trading_graph.py`
- `tradingagents/graph/setup.py`
- `tradingagents/graph/propagation.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/agents/utils/agent_states.py`

`Propagator.create_initial_state(..., runtime_config=...)`负责初始化状态。P1-B通过请求运行时配置决定：

```text
v1_legacy（默认）
或
v2_structured_disagreement（单次请求启用）
```

投资辩论状态至少包括：

- `protocol_version`
- `protocol_stage`
- `feature_flags`
- `claims`
- `challenges`
- `round_messages`
- `attempts`
- `claim_counter`
- `challenge_counter`
- `challenge_verification`
- `belief_trajectory`
- `tiebreak_skipped`
- `debate_degenerate`
- `data_utilization_metrics`

## 2.4 模型与角色解析

`TradingAgentsGraph.__init__`从 runtime config 读取`user_id`，再通过：

```text
role_bindings → model_profiles → providers
```

为15个角色分别创建LLM客户端。

个人配置边界：

- 模型选择、角色绑定、provider、backend URL、API Key由用户自己管理；
- 不要因为代码测试或上游故障自动换模型；
- 不得擅自修改 `user_llm_configs`、`role_bindings`、`providers`、密钥；
- 如需建议，先实测并告诉用户在设置页改什么。

## 2.5 数据层

数据层包括 AkShare、Tushare、东方财富、同花顺、Fuyao、BaoStock、新浪等路径和 fallback。

铁律：

- 按列名取数，禁止按位置切片；
- 取“最新”必须先排序；
- 日期转 datetime 后比较；
- 数据失败返回 typed gap，不返回空串/None/0；
- `requested_as_of`和`actual_as_of`分开；
- 主力资金不同算法族不得混算；
- 新浪 Web legacy只作参考，不得冒充当前算法组；
- 真实来源以 `source_provenance`、`data_failure_ledger`、provider trace和报告DB为准。

---

# 3. Legacy 与 v2 协议边界

## 3.1 Legacy

默认 `v2_debate_enabled=false`：

- 维持旧 Prompt；
- 维持图拓扑和Bull/Bear绑定；
- 维持6条辩论消息；
- 维持旧 Check B/C/D 与逐claim去重；
- 旧报告可读取；
- challenge指标应显示`legacy_no_data`，不能伪造0%。

## 3.2 v2 三段式

用户持久轮数必须仍是3/1，v2把三轮映射为：

1. **Opening**：Bull、Bear各一次独立双盲立论；
2. **Challenge**：Bull、Bear各一次交叉盘问；
3. **Tiebreak**：仅存在未决分歧时每方最多一次，否则跳过并进入总监。

### Opening

- message 1/2；
- 双方看相同七报告，但看不到对方本场输出和历史提示答案；
- `history/current_response/opponent claims/past_memory`隔离；
- 每方恰好3条claim；
- 覆盖至少3个不同battlefield；
- `responded_claim_ids=[]`；
- `target_claim_ids=[]`。

battlefield枚举：

- `capital_flow`
- `sentiment_theme`
- `price_volume`
- `macro_policy`
- `fundamentals`

### Challenge

- message 3/4；
- `new_claims=[]`；
- 每方至少1条challenge；
- target必须是对手、存在、未resolved的claim；
- weakest_point非空且≤500字符；
- evidence至少1条；
- severity为`fatal|major|minor`；
- `self_win_prob`必填且0..1；
- accepted挑战分配CH ID并进账本；
- 失败attempt不推进count/stage/CH counter；
- 同speaker、同target、同/高相似weakest_point拒收。

### Tiebreak / Manager

- Challenge完成后，无未决分歧：count=4直接Research Manager，`tiebreak_skipped=true`；
- 有未决分歧：每方最多一次加赛；
- 总监Prompt根据真实stage/message count参数化，不得写死“基于3轮”；
- 输出dispute map；
- 未验证fatal challenge不能否决verified claim；
- contradicted fatal必须驳回；
- 记录belief trajectory和degenerate标记。

---

# 4. 已完成阶段和精确证据

## 4.1 Phase 0：已完成并部署

Phase 0把as-of、防前视、confidence/probability、证据公平、密钥脱敏、逐claim防捆绑洗白等修复组合进主干并完成真实smoke。

关键证据：

- 组合回归曾为`1852 passed, 1 skipped`；
- Phase 0主干曾锁定`50e115347b49bcb9e767c593296045a356099006`；
- 经过PID/cwd/SHA/DB/真实账户验收；
- 用户3/1保持不变。

## 4.2 P1-M：已完成、合入并部署

P1-M实现：

- protocol metadata；
- 默认关闭feature flags；
- 数据利用与辩论指标纯函数；
- offline A/B harness；
- 所有报告构造路径写入metadata/flags/metrics；
- HOLD合法空目标价、warning/note与field completeness一致；
- dual/query/stream持久化语义正确。

最终主干和现网P1-M SHA：

```text
ccc4c53a4985f8db32353586e6cb4317aa34f8cd
```

全量证据：

```text
1899 passed, 1 skipped, 0 failed
```

最终真实报告之一：

```text
report_id: a25670bf53e648b5a796775da9b88b3a
symbol: 600919.SH
status: completed
error: null
```

其真实DB验收证明：protocol、flags、metrics、legacy六消息、LLM日志关联和字段完整率均落库。

## 4.3 P1-B/B1 Opening：完成并独立复审PASS

链路：

```text
ccc4c53
  → f8ffe4764773276c04e1228d2a76eb5013c51369
  → a2b7515c7a549a642d4c6438cab36132bb0228b5
```

B1锁定基线：

```text
a2b7515c7a549a642d4c6438cab36132bb0228b5
branch: agent/2/1f3eb19a5a03
```

能力：

- 请求级state启用；
- Opening双盲；
- 恰好3条claim、至少3战场；
- Bear message2不触发legacy Check B/C；
- 静态Prompt和动态state零泄漏；
- Bear opening后authoritative stage切challenge；
- legacy最终Prompt保持兼容。

## 4.4 P1-B/B2 Challenge：完成并独立复审PASS

C1 Foundation：

```text
91b4cf51b80fb3edae0d6a2df02ae9928dd665a8
branch: agent/2/128052b9e828
```

C2最终：

```text
6aa1f92e345da718a9a51cacf1535201f7e946a9
branch: agent/2/37cc17580e66
parent: 91b4cf51b80fb3edae0d6a2df02ae9928dd665a8
```

独立复审：DAV-394 PASS。

测试：

```text
143 passed
replay PASS
compileall 0 error
```

能力：Challenge schema、sanitizer、硬闸、CH账本、duplicate、失败attempt隔离、权威stage恢复语义。

## 4.5 P1-B/B3：完成并独立复审PASS

**真实可验证远端候选**：

```text
branch: agent/1/93fd11c25df0
SHA: ec3e9030d3424fea9bb55058e2a2f0bc8705f16a
parent: 6aa1f92e345da718a9a51cacf1535201f7e946a9
```

Hermes已用`git ls-remote target`核验该ref存在；本地Git可解析其单亲父链。

变更文件共8个：

- `tests/test_debate_b3_protocol.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/agents/utils/debate_utils.py`
- `tradingagents/agents/utils/evidence_verifier.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/prompts/en.py`
- `tradingagents/prompts/zh.py`

独立复审：DAV-396/PASS证据已存在于DAV-395评论链。

测试证据包括：

```text
188 passed（debate矩阵，自报/复审）
215 passed（扩展回归）
replay PASS
compileall 0 error
```

### 必须忽略的冲突SHA

看板后续调度评论曾把B3基线写成：

```text
d43a3f8dc0948657866e721edb9d539f0b080820
branch: agent/1/da91d1a55b7c
```

但交接前Hermes实测：

- `target ls-remote`不存在该ref；
- 本地Git无法解析该SHA；
- B4实际任务启动评论固定的是`ec3e903...`。

因此在新证据出现前，`d43a3f8...`不是有效交付事实，不得用于合入、部署或验收。

---

# 5. 当前实时状态（最重要）

核验时间约：2026-08-24 23:10–23:17 AWST。

## 5.1 三种SHA状态必须分开

### 远端目标主干

```text
target/codex/dav-4-p2a-trunk
ccc4c53a4985f8db32353586e6cb4317aa34f8cd
```

### 宿主工作区HEAD

```text
ec3e9030d3424fea9bb55058e2a2f0bc8705f16a
状态：detached HEAD
```

宿主工作区有大量既有modified/untracked文件，主要是文档、历史现场、`.cursor/`等。**不得清理、reset、stash/drop或批量纳入提交。**

### 运行服务

```text
PID: 21461
port: 127.0.0.1:8000
cwd: /Users/davidliu/Documents/TradingAgents-AShare
DB: /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db
health commit_sha: ccc4c53a4985f8db32353586e6cb4317aa34f8cd
```

即：宿主文件树已切候选，但服务仍是旧进程、旧SHA。不要因为本地HEAD变化就声称上线。

## 5.2 Multica看板

父卡：

```text
DAV-390 P1-B三段式总实施：in_progress
```

阶段：

- B1：完成；
- B2：完成；
- B3：完成；
- B4部署卡`DAV-397`：in_progress；
- B4真实三标的验收`DAV-398`：backlog。

DAV-397真实run：

```text
01a03442-99b9-7bd4-a9b6-16f8c54b04be
owner: 代码运维测试员
status: running
```

## 5.3 当前宿主全量

```text
PID: 28686
command: env -u PYTHONPATH .../.venv310/bin/python -m pytest tests/ -q
candidate tree: ec3e903...
最近进度: 约65%；原始输出已出现多组F，终态和失败详情未知
```

第一次运行`pytest -q`没有指定`tests/`，误收集`work/dav-42-pr13-rework/tests/`历史副本，产生import mismatch；这是测试命令/宿主脏树问题，不是候选代码失败。正确命令是`pytest tests/ -q`。

当前正确全量的原始进度输出在约14%与47%等位置已出现多组失败标记 `F`。这些可能是候选回归、宿主候选树与真实 `.env` 的组合缺陷、测试墙钟/环境问题或既有基线失败；在pytest终态、完整trace和`ccc4c53`对照出来前不能定性。**当前候选一定不可合入。**

## 5.4 数据库与用户配置

真实数据库：

```text
data/tradingagents.db
```

轮次配置表：

```text
user_llm_configs
```

交接前实查真实用户：

```text
max_debate_rounds = 3
max_risk_discuss_rounds = 1
```

数据库当前无pending/running reports。

不要把backend URL、API Key、token或加密字段复制到交接评论/代码/日志；统一记为`[REDACTED]`。

## 5.5 当前两个合入前BLOCK

### BLOCK A：EOF空行

Hermes独立运行：

```bash
git diff --check ccc4c53..ec3e903
```

结果：

```text
tests/test_debate_challenge_protocol.py:1124: new blank line at EOF.
```

B2 reviewer曾把它列为一般建议而仍PASS，但B4要求`diff-check`为0。必须从`ec3e903`开新微返修SHA，仅删EOF多余空行，不amend旧候选。

### BLOCK B：真实HTTP无法按请求启用v2

DAV-398要求：

```json
{"config_overrides":{"v2_debate_enabled":true}}
```

但当前`api/main.py::_CONFIG_OVERRIDES_ALLOWLIST`只允许：

- llm_provider
- deep/quick model
- max debate/risk rounds
- prompt_language

不含`v2_debate_enabled`。`_build_runtime_config`会静默过滤它。

因此：

- 内部Propagator/runtime config测试通过；
- 不等于真实`POST /v1/analyze`能启用v2；
- 如果不修，DAV-398可能生成legacy报告，却被误以为v2验收。

必须新增真实runtime/API RED，最小GREEN只将`v2_debate_enabled`加入安全allowlist，并证明：

1. 默认仍关闭；
2. 单次请求可开启；
3. 不写入`user_llm_configs`，3/1不变；
4. api_key/backend_url/provider敏感键仍被过滤；
5. 真实报告落库`protocol_version=v2_structured_disagreement`。

Hermes已把两条BLOCK评论到DAV-397，并要求全量跑完后禁止直接FF/重启。

---

# 6. Cursor接手后的剩余工作

## Gate 1：收集当前全量终态

- 不重复启动全量；
- 读取PID 28686和输出文件；
- 记录最终passed/skipped/failed/warnings/time；
- 当前已出现失败标记；终态后先保存完整失败节点和trace，再在精确`ccc4c53`基线对照，区分候选回归、宿主脏树、真实`.env`组合缺陷、时间敏感测试或环境问题；
- 即使通过也保持合入BLOCK。

## Gate 2：两个微返修

建议严格串行或不同文件范围并行：

### 微返修A：EOF

- base：`ec3e903...`；
- 只改`tests/test_debate_challenge_protocol.py`；
- 删除EOF多余空行；
- 新SHA；
- `git diff --check`、Challenge专项、相关矩阵；
- 独立只读复审。

### 微返修B：HTTP allowlist

- base应是A的新SHA或确定线性组合基线；
- 允许`api/main.py`和一个真实API/runtime config测试文件；
- 先RED，再GREEN；
- 不改DB schema、不改用户设置；
- 新SHA；
- 独立只读复审。

注意：同一最终链必须线性。不要造两个互不相干的兄弟分支后在宿主手工拼树却没有远端integration SHA。

## Gate 3：最终精确候选验证

必须验证：

```bash
git ls-remote target <final-branch>
git rev-list --parents -n 1 <final-sha>
git diff --name-only ccc4c53..<final-sha>
git diff --check ccc4c53..<final-sha>
git merge-base --is-ancestor ccc4c53 <final-sha>
```

然后在精确干净checkout使用宿主`.venv310`：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/
```

必须保存原始输出，不只写摘要。

## Gate 4：Fast-Forward主干

只有在以下全部满足后：

- 最终SHA远端存在；
- 精确review PASS；
- 全量0 failed；
- diff-check 0；
- HTTP请求级v2启用测试通过；
- DB无active reports；
- 3/1不变。

项目主管只做：

```text
final candidate → target/codex/dav-4-p2a-trunk fast-forward
```

之后Cursor/Hermes独立读回：

```bash
git ls-remote target refs/heads/codex/dav-4-p2a-trunk
```

## Gate 5：部署

1. 再查无active reports；
2. 记录旧PID/cwd/SHA/DB；
3. `kill -9`旧PID；
4. `lsof`确认8000释放；
5. 更新宿主到新trunk；
6. 按正确环境启动；
7. 核对新PID、cwd、DB、SHA、health、3/1。

启动命令必须包含：

- `env -u PYTHONPATH`
- `DATABASE_URL=sqlite:///./data/tradingagents.db`
- 海外代理；
- 国内数据源完整`no_proxy`列表。

不要把本地代理、密钥或URL写入提交；使用已有本机环境配置。

## Gate 6：DAV-398三只全新标的真实3/3验收

禁止使用金标准：

- 600900
- 000333
- 600276

还应避开近期已分析标的，例如600406、600919、600030等，先查DB确保真正全新。

每份报告单次请求启用v2，持久配置仍3/1。每份至少审：

1. completed且error=null；
2. `protocol_version=v2_structured_disagreement`；
3. feature flag真实开启；
4. Opening双方各3条claim、≥3战场；
5. 双盲泄漏0；
6. Challenge两方存在、new_claims为空；
7. 每条challenge有evidence status；
8. unsupported/contradicted fatal未非法否决；
9. tiebreak执行或`tiebreak_skipped=true`；
10. dispute map完整；
11. manager正文、结构字段、置信度一致；
12. confidence/probability/target/stop没有静默null；合法空有note，提取失败有warning；
13. `source_provenance`与`data_failure_ledger`真实；
14. LLM日志关联report_id；
15. 3/1前后不变。

三份报告全部通过才关闭P1-B。

---

# 7. 如何领导Multica团队

## 7.1 角色与职责

常用实际owner：

| 角色 | Agent ID | 用途 |
|---|---|---|
| 项目主管 | `4503de74-0fb3-457d-88a7-3db8db04ff8e` | fast-forward、阶段批准、集成决策 |
| 项目调度助手 | `826abb3f-34c0-4d9a-afff-2a0b1a002221` | 编排，不是施工者；有自触发风险 |
| 资深开发1 | `6050b57e-f551-4756-8ad9-3af522d7d4e3` | 核心协议writer |
| 资深开发2 | `5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc` | 精确微返修writer |
| 高级开发·支援 | `04cc525b-70a1-44ee-ad8f-2afc0c6d04ff` | 备用writer |
| 独立代码审核员 | `aa01a41a-c3da-4021-9e45-a592ac77166c` | 精确SHA只读review |
| 代码运维测试员 | `f179edb8-9a81-4dbd-8787-afbfd307eda4` | 全量、部署、运行验签 |
| 项目评估师 | `2c03cc8f-6628-4464-954a-84c47079fdf3` | 真实业务/DB验收 |

Cursor runtime已在Multica注册并online，但交接时Claude runtime仍承载现有Agent绑定。**DAV-397全量运行期间不要重启daemon或切Agent runtime。** 等当前run终态后，如要把施工逐步切到Cursor，单独做runtime切换验证，不要作为B4附带动作。

## 7.2 Issue规格怎么写

每张施工卡必须包含：

1. 精确远端base branch/SHA；
2. 直接父要求；
3. 唯一目标；
4. 允许文件白名单；
5. 禁止区域；
6. RED契约；
7. GREEN验收；
8. 精确解释器；
9. 测试命令；
10. 远端分支/SHA交付格式；
11. 明确未合入/未重启/未上线；
12. 禁止改个人配置。

不要把整段历史评论塞给新agent。长上下文失败后，拆成一文件/一行为的microtask。

## 7.3 标准流水线

```text
规格卡
  → 单一writer fresh checkout
  → RED（真实契约、正确解释器）
  → 最小GREEN
  → 远端新SHA
  → Hermes/Cursor独立ls-remote、parent、范围、diff-check
  → 冻结writer
  → 独立只读reviewer
  → 精确SHA回归
  → 项目主管fast-forward
  → 独立读回trunk
  → 安全部署
  → 真实业务/DB验收
```

每一关都不能用后一关替代：

- issue done ≠ commit pushed；
- commit pushed ≠ review PASS；
- review PASS ≠ full regression；
- tests绿 ≠ trunk已合入；
- trunk变化 ≠ 服务已重启；
- `/healthz=200` ≠ 新SHA上线；
- 报告completed ≠结果字段正确。

## 7.4 单writer与并行原则

严格串行：

- 同一代码树/同一文件范围的实现、返修；
- 实现→review→合入→部署。

可以并行：

- 精确不可变SHA只读review；
- 回归预检；
- ancestry/diff核验；
- DB schema只读审计；
- 环境/provider探针；
- 文档与证据整理。

不要为了“所有人都动起来”派两个coder改同一文件。

## 7.5 唤醒与核验

状态变成`in_progress`不保证有人接单。必须：

```bash
multica issue status DAV-xxx in_progress
multica issue comment add DAV-xxx --content '...[@真实owner](mention://agent/UUID)'
sleep 10
multica issue runs DAV-xxx --full-id
```

看到真实owner的`running`才算开工。

评论mention常产生自动queued副本。如果已有一个有效running，立即取消queued重复：

```bash
multica issue cancel-task <queued-run-full-id>
```

## 7.6 不要只唤醒调度助手

项目调度助手曾出现：

- 自己mention自己；
- 重复输出无新证据；
- 触发重复run；
- context-window 400自循环；
- 把不存在或错误SHA继续向后传播。

有明确owner时直接mention实际coder/reviewer/ops，不要等待调度助手转述。

## 7.7 远端证据

开发自报后必须执行：

```bash
git ls-remote target refs/heads/<branch>
git fetch target <branch>
git rev-list --parents -n 1 <sha>
git diff --name-only <base>..<sha>
git diff --check <base>..<sha>
```

GitHub瞬时网络错误不要解释成分支不存在。使用本地代理并强制HTTP/1.1重试。

## 7.8 Reviewer规则

Reviewer必须：

- fresh checkout精确SHA；
- 只读，0 code changes；
- 审范围和真实语义，不只看测试数；
- 用指定宿主解释器；
- 给PASS/BLOCK、文件:行号、复现命令；
- reviewer脚本自身错误不算代码失败；纠正脚本后重跑。

## 7.9 TDD与环境真实性

所有项目Python命令：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python ...
```

原因：Hermes环境PYTHONPATH会污染项目venv。

禁止：

- 裸`pytest`；
- Python 3.14结果冒充宿主3.10；
- `pytest.mark.asyncio`（项目宿主未安装pytest-asyncio）后把执行层错误冒充RED；
- 先写生产代码后补测试；
- 测试通过后删/弱化关键断言。

---

# 8. 运行与部署手册

## 8.1 服务

宿主机运行，不依赖Docker。端口8000。

安全重启：

```bash
# 1. 查active报告
# 2. 记录旧PID/SHA/DB
kill -9 <old_pid>
# 3. 确认端口释放
lsof -nP -iTCP:8000 -sTCP:LISTEN
# 4. 启动新服务（使用现有安全环境变量，不在文档复制密钥）
# 5. 再查新PID拥有端口
# 6. health + real smoke
```

SIGTERM可能不释放端口；必须用`lsof`确认。否则新进程因端口冲突退出，curl仍命中旧进程，造成“新代码没生效”的假象。

## 8.2 数据库

必须显式：

```text
DATABASE_URL=sqlite:///./data/tradingagents.db
```

否则默认可能连接项目根目录的错误空库，表现为历史报告和设置“消失”。

验证：

```bash
lsof -p <pid> | grep tradingagents.db
```

## 8.3 代理

Hermes shell带代理。国内金融源如果没`no_proxy`会经代理出口，东财/新浪/腾讯/同花顺可能失败并静默fallback。

部署时沿用当前已验证完整`no_proxy`启动方式，至少包含localhost和国内数据源域名。不要把密钥写入命令日志或文档。

## 8.4 Health不是终点

至少验证：

- 新PID；
- 新PID cwd；
- 新PID解释器；
- 打开的DB；
- `/healthz.commit_sha`；
- `/api/health` JSON；
- 3/1；
- 一份真实业务报告。

---

# 9. 已知高危陷阱

1. **主干、宿主HEAD、运行服务SHA是三个状态。** 当前正处于三者不同。
2. **看板done不是远端交付。**
3. **不存在SHA传播。** 当前`d43a3f8...`就是实例；只认ls-remote和Git对象。
4. **全量命令要指定`tests/`。** 裸pytest会收集`work/`历史副本。
5. **真实HTTP allowlist。** 内部runtime测试绿不等于请求键未被API过滤。
6. **Graph helper不等于真实Web流式路径。** 必须落库验收。
7. **请求completed不等于数据正确。** 查DB result_data。
8. **HOLD字段时序。** 最终decision之后要resolve note/warning再算metrics。
9. **dual顶层metrics。** 必须来自primary horizon完整source。
10. **stage权威语义。** 显式tiebreak/manager不能因旧count被拉回challenge。
11. **静态Prompt也会泄漏。** 清空动态history不够，要审最终rendered prompt。
12. **测试夹具不可能状态。** 夹具必须生产可达。
13. **Reviewer脚本错误。** 路径/语法错误先修探针，不误判生产代码。
14. **EOF空行仍是diff-check BLOCK。** 当前候选存在该问题。
15. **配置红线。** 不改角色绑定、providers、模型和密钥。
16. **服务启动代理。** 无no_proxy会导致国内源全挂。
17. **用户3/1。** 测试只用单次override，结束后查库仍3/1。
18. **旧后台通知。** 必须按checkout/SHA归属，不能拿旧全量当当前证据。

---

# 10. 关键文件索引

架构/协议：

- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/agents/utils/debate_utils.py`
- `tradingagents/agents/researchers/bull_researcher.py`
- `tradingagents/agents/researchers/bear_researcher.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/utils/evidence_verifier.py`
- `tradingagents/graph/propagation.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/graph/trading_graph.py`
- `tradingagents/graph/setup.py`
- `tradingagents/prompts/zh.py`
- `tradingagents/prompts/en.py`

API/持久化：

- `api/main.py`
- `api/services/report_service.py`
- `api/services/role_routing_service.py`
- `api/database.py`

测试：

- `tests/test_debate_opening_protocol.py`
- `tests/test_debate_challenge_foundation.py`
- `tests/test_debate_challenge_protocol.py`
- `tests/test_debate_b3_protocol.py`
- `tests/test_debate_state_persistence.py`
- `tests/test_debate_metrics.py`
- `tests/test_debate_e2e_protocol_repair.py`
- `tests/test_debate_bundle_wash.py`
- `tests/test_debate_information_gain.py`
- `tests/golden/audit_20260823/replay_verifier.py`

施工文档：

- `work/issue-v2-p1b-parent.md`
- `work/issue-v2-p1b-b1-opening.md`
- `work/issue-v2-p1b-b2-c1-foundation.md`
- `work/issue-v2-p1b-b2-c2-protocol-ledger.md`
- `work/issue-v2-p1b-b2-c2-authoritative-stage-closeout.md`
- `work/comment-dav397-premerge-blockers.md`
- `work/dav405-uncommitted.patch`
- `work/dav405-test_debate_challenge_protocol.py`

---

# 11. 项目完成标准

P1-B只有满足全部条件才关闭：

- 最终候选经过精确SHA独立复审；
- 宿主`.venv310`全量0 failed；
- 请求级v2真实HTTP入口可启用；
- target trunk等于最终验收SHA或其确定性后代；
- 服务新PID加载同一SHA和真实DB；
- 3/1不变；
- 三只全新标的真实报告3/3结构硬闸通过；
- legacy旧流程仍兼容；
- 明确列出真实data gaps，不伪造来源；
- 最终报告列出trunk SHA、PID、测试、三个report_id、协议结构、证据状态、字段完整性、未完成项。

不要因为“B1/B2/B3代码完成”提前宣布P1-B完成。当前仍处于B4，且有两个新BLOCK。

---

# 12. 给Cursor的接管提示词（可直接作为首条工作指令）

```text
你接管 /Users/davidliu/Documents/TradingAgents-AShare。

先完整读取 AGENTS.md、PROJECT_STATE.md、DECISIONS.md 和 work/2026-08-24-cursor-full-project-handoff.md。不要立即写代码。

第一步核验：
1. PID 28686 的宿主 .venv310 `pytest tests/ -q` 是否结束，读取原始输出文件并记录终态；该全量约65%时已出现多组F，不得重复启动或提前定性。终态后保存失败节点/trace并与ccc4c53基线分类。
2. `git ls-remote target`确认远端主干仍为 ccc4c53，候选 agent/1/93fd11c25df0 仍为 ec3e903；忽略未能从远端或Git解析的 d43a3f8。
3. 查询DAV-397 runs/comments，确认未fast-forward、未重启。
4. 保持合入BLOCK：
   A. tests/test_debate_challenge_protocol.py EOF多余空行；
   B. api/main.py请求级config override allowlist缺v2_debate_enabled，真实HTTP无法单次开启v2。
5. 用Multica按单writer、严格TDD、精确SHA、独立review方式完成两个微返修，形成一个线性最终候选。
6. 最终候选全量通过后才让项目主管fast-forward；读回远端SHA后再安全重启。
7. 部署后以真实账户、单次v2 override跑三只全新标的，DB递归验收Opening/Challenge/Tiebreak/dispute map/证据状态/字段完整率/日志关联和3/1。

禁止修改用户模型、角色绑定、providers、backend URL、API Key；禁止清理宿主脏工作树；禁止用issue done、自报、HTTP 200代替远端SHA/服务/DB证据。
```

---

# 13. 最后状态摘要

```text
远端主干：ccc4c53a4985f8db32353586e6cb4317aa34f8cd
运行服务：ccc4c53a4985f8db32353586e6cb4317aa34f8cd / PID 21461 / port 8000
宿主HEAD：ec3e9030d3424fea9bb55058e2a2f0bc8705f16a（detached）
B3真实候选：agent/1/93fd11c25df0@ec3e9030d3424fea9bb55058e2a2f0bc8705f16a
B4卡：DAV-397 in_progress
全量：PID 28686，pytest tests/ -q，约65%且已出现多组F；仍在运行，终态未知
真实验收卡：DAV-398 backlog
持久轮次：3/1
active reports：0
已知合入前BLOCK：EOF空行 + HTTP allowlist缺v2_debate_enabled
尚未完成：全量失败分类/修复、两个已知微返修、最终复审、最终全量、FF、重启、三标的3/3验收
```

交接到这里时，**不要重新做B1/B2/B3**；从DAV-397的两个合入前BLOCK和当前全量终态继续。
