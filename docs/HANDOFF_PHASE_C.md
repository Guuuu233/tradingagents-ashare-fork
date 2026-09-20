# TradingAgents-AShare 交接书：阶段 B 收尾 + 阶段 C/D/E

> 给接手实现工作的 Claude Code（终端或 VS Code 扩展）。**开始前完整读一遍。**
> 这份文件替代一次工具切换丢失的全部会话上下文，不要假设你能从别处读到这些内容。
> 本文件在 `.gitignore` 内（`docs/` 整个被忽略，只有 `KNOWN_ISSUES.md` 被跟踪），不会进提交。

---

## 1. 角色分工（先读这一节）

这个项目有三个 Claude，但**只有两个在同一条回路上**：

| 角色 | 是谁 | 职责 | 碰仓库吗 |
|---|---|---|---|
| **实现方** | **你**（终端 / VS Code 的 Claude Code） | 读代码、写代码、跑测试、在真容器里实测 | 是 |
| **审查方** | 另一个独立实例，用户手动转发 | 只看你的报告和 diff，挑错、定方向 | **否，刻意的** |
| 统计方 | Cowork 里的实例，目前已退出 | 阶段 E 之后做校准度统计和报表 | 阶段 C/D/E 期间不参与 |

阶段 B 的实现是 Cowork 那个实例做的（它文件读写是宿主机真实的，但 shell 被隔离在沙箱 VM 里，
没有 Docker、没有 `/app/.venv/bin/python`）。**交接给你的唯一原因就是你有真 shell。**

### 这个分工是刻意的，因为它真的抓到过东西

审查方抓到过：一个测试用例结果和探查数据矛盾、一个「安全」判断其实方向反了（是泄漏）、
一处错误的法条引用、一个过宽的 `except`、缓存键语义错误、三次范围溢出。

**实现方也驳倒过审查方三次，三次都对：**

1. 它说「157 万字符的模型输出物理上不可能」—— 它把字符数当 token 数，重复空格的 BPE
   压缩率约 127:1，实际约 13,200 token，完全合理。
2. 它假设「研究经理收到一大片空白所以看不到基本面」—— `research_manager_prompt` 里根本没有
   `{fundamentals_report}` 占位符，那条路径不存在。
3. 它假设「输入脏是输出退化的根因」—— commit 5 清理输入后照样退化，根因在提示词要求的宽 Markdown 表。

**三次都是靠实测把它驳回来的。请继续这样做。**

它会给你很具体的指令和判断，其中一部分是错的。**遇到你能实测验证的地方，先测再说；测出来
和它说的不一致，直接说不一致并附数据。**顺从地执行一个错误判断，是这个分工里最坏的失败模式。

**同时不要走另一个极端**：它要求的验证步骤（跑冒烟、多标的对照、打印实际 prompt）不要跳过
或用「我判断没问题」代替。这个项目已经因为「没验证就下结论」出过多次事故。

现在你有真容器了，**它要求的那些验证不再有环境借口**，可以要求得更硬。

### 汇报格式（每个阶段/commit 结束后停下）

- 改了哪些文件、每个文件改了什么
- **没改哪些本来可能相关的文件**（这条经常暴露遗漏）
- 你**不确定**的地方
- 测试结果（数量 + 哪些是新增的）
- 线上冒烟结果（**真实调用的原始输出**，不只是「通过」）
- 完整 diff

**不要写「已彻底修复」「完美解决」。没有测试覆盖的修复叫推测。**

用户会把你的汇报转给审查方，意见再转回来。中间可能有延迟，
**不要在等待期间自行推进下一步。**

---

## 2. 环境（会踩的坑）

- **VS Code 用普通本地窗口打开，不要用 Dev Containers / Remote。** 否则 shell 落在容器里，
  路径和挂载都不一样。
- **容器里的真实解释器是 `/app/.venv/bin/python`。** 本机和容器默认的 `python` 都缺依赖。
  之前有一轮验证卡住就是因为这个，不是代码问题。
- **bind mount 只有 `api/`、`tradingagents/`、`tests/` 三个目录**。`scripts/` 可能不在容器里，
  跑 `scripts/` 下的东西前先 `docker compose exec <服务名> ls scripts/` 看一眼，
  没有就 `docker cp`。
- **工作区有三个长期未提交的脏 test 文件，不要动、不要 stage**：
  `tests/test_api_smoke.py`、`tests/test_intent_parser.py`、`tests/test_portfolio_import.py`
- `tests/test_api_smoke.py::TestPortfolioOverviewEndpoint::test_overview_returns_watchlist_...`
  **本来就是红的**（断言 `'600519.SH' == '贵州茅台'`，持仓名称映射问题）。已用 `git stash`
  验证过与近期改动无关。不要顺手修。
- `tests/test_dashboard_tracking.py` 有两个用例失败，**已定案与阶段 B 无关**（做法见第 4 节）。
  不要修，不要重验。
- **`docs/` 整个在 `.gitignore` 里**，只有 `docs/KNOWN_ISSUES.md` 被跟踪。往 `docs/` 写东西
  不会进版本库、也传不到审查方。要让审查方看到就写 `KNOWN_ISSUES.md`，或 `git add -f`。
- 调度器已停用（`active_scheduled=0`），不会自动生成新任务。
- 仓库根有 **`AGENTS.md`**，**先完整读一遍并严格遵守**。核心：读完再改、不新增并行路径、
  删死代码、一次提交一个关注点、**不自行提交（展示 diff 等确认）**、不确定就停下来问、
  外部数据源的行序列序字段存在性值可解析性都不可信。
- 用户曾在对话里明文粘贴过本地 `X-API-Key`（用于 `127.0.0.1:8001`）。建议提醒他轮换一次。

---

## 3. 项目是什么 / 终极目标

`KylinMountain/TradingAgents-AShare` 的本地分支。A 股多智能体投研系统：
7 个分析师 → 多空研究员辩论 → 研究经理裁决 → 交易员 → 风控三方 → 风控总监。
FastAPI（`api/`）+ Vue/TS（`frontend/`）+ 核心逻辑（`tradingagents/`）+ 定时任务（`scheduler/`）+ SQLite。

**用户的最终目标是用历史日期分析的结果统计校准度**（系统说「上涨概率 60%」时，实际上涨比例
是否接近 60%）。这个目标决定了所有优先级：**历史分析绝不能读到该日期之后才存在的信息**，
否则统计出来的数字毫无意义。

已完成的主线是清掉七类前视偏差（按位置取列/取行、字符串比日期、日期未规整、快照源当历史源、
降级链绕过拒绝、prompt 里的墙钟时间）。相关机制在
`tradingagents/dataflows/utils.py`（`take_latest` / `chronological`）、
`trade_calendar.py`（`normalize_to_trading_day` 只向前回溯、日历不可用时硬失败）、
`financial_announce.py`（A4 策略，最复杂，`LATE_FILING_GRACE_DAYS=120` 的双峰依据在 `docs/`）。
护栏在 `tests/test_provider_date_guards.py`，其中「双历史日对比」那条能抓出
**「参数传了但函数体不用」——本项目反复出现的失效模式**。

---

## 4. 阶段 B 现状：实现已完成，只剩验证 + 提交

**详细清单见 `docs/PHASE_B_HANDOFF.md`，先读那份，别重复劳动。** 摘要：

`feat: persist custom analysis prompts server-side` —— 自定义提示词此前只在浏览器
localStorage（`Settings.tsx`），只在 `ChatCopilotPanel.tsx` 里拼进 chat 层，
**分析师、研究员、研究经理完全不使用它，它空转了半个多月**。阶段 B 只把存储搬到服务端，
**不碰注入**。

新增 `user_custom_prompts` 表 + `UserCustomPromptDB`、
`api/services/custom_prompt_service.py`、6 个端点、19 个单元测试（全过）、
真实库冒烟脚本 `scripts/smoke_custom_prompts.py`、
`UserDB.prompt_injection_enabled` 总开关（默认 False）。

**你要做的只有两件事**（命令、绊线、回滚路径、两条 commit 信息全文都在 `PHASE_B_HANDOFF.md`）：

1. 在真容器里跑单元测试 + 冒烟。
   - ⚠️ **冒烟脚本里的 `init_db()` 会触发 `ALTER TABLE users ADD COLUMN`，
     直接改那个装着 188 条报告的生产库。**
   - **备份必须是第一条涉及数据库的命令。** 不要在备份之前跑任何 `import api.database`
     的命令 —— 包括那条看起来只是读配置的
     `python -c "from api.database import DATABASE_URL"`。若该模块有 import 时副作用
     （`create_all()` / `_ensure_*_schema()`），这条"只读"命令就会在没有备份的情况下
     执行迁移。用 shell (`ls -la data/*.db` + `cp`) 先备份，之后再确认 `DATABASE_URL`。
   - SQLite 3.35 之前没有 `DROP COLUMN`，**这个 ALTER TABLE 基本不可逆，备份是唯一退路。**
     出现任何异常（绊线触发、断言失败、`database is locked`、输出看不懂）→
     **立即停下 → `cp` 备份回去 → 贴输出给用户 → 不要向前修。**
2. **贴完整原始输出 → 停下等人工确认 → 才提交。** 不要自行提交（`AGENTS.md` 铁律 5）。
   用户说过"做完就提交"，但那句话以验证通过为条件；绊线触发时提交是错的。
   确认后拆两个 commit（功能 8 个路径 + `docs/KNOWN_ISSUES.md`）。

### 三处已定案，不要「顺手优化」

1. **6002 边界**：global(4000) + `\n\n` + role(2000) = 6002，超 6000 上限被拒。刻意不加余量：
   6000 是「注入文本」的字面上限。报错已指名角色和两个数字。
2. **主开关在 `UserDB.prompt_injection_enabled`**，不在 `user_llm_configs`（那张表正被
   `migrate_legacy_user_llm_config` 拆解）、也不用表内 `target_type='switch'`（污染枚举语义）。
3. **`test_dashboard_tracking.py` 那两个失败与阶段 B 无关**。验证方法：把仓库复制两份到 `/tmp`，
   一份用 `git show HEAD:` 还原成改动前，同环境跑同一测试文件 → 改动前后都是 `2 failed, 2 passed`，
   失败位置同为 `:199`。**没用 `git stash`（有丢工作区风险）。不要修，不要重验。**

---

## 5. ⚠️ 阶段 C 之前必读：下游到底看得见什么数据

**这一节是本次交接新查证的，和 `docs/KNOWN_ISSUES.md` 已记录的内容不一致，以本节为准。**

`KNOWN_ISSUES.md` 记的是「`research_manager`/`risk_manager`/`trader` 都不接收
`fundamentals_report`，整个后半段管线只跑在辩论文本上」。前半句对，**后半句太强**。

逐个模板核对 `tradingagents/prompts/zh.py` 的占位符、并区分「进了 prompt」和
「只用于 memory 检索的 `curr_situation`」之后的真实情况：

| agent | 进 prompt 的分析师报告 | 只用于 memory 检索 | 完全没读 |
|---|---|---|---|
| `bull_researcher` | market, sentiment, news, fundamentals, volume_price | — | macro, smart_money |
| `bear_researcher` | market, sentiment, news, fundamentals, volume_price | — | macro, smart_money |
| `research_manager` | sentiment, smart_money, volume_price | market, news, fundamentals | macro |
| `trader` | **（无）** | market, sentiment, news, fundamentals | macro, smart_money, volume_price |
| `aggressive_debator` | market, sentiment, news, fundamentals | — | macro, smart_money, volume_price |
| `conservative_debator` | market, sentiment, news, fundamentals | — | macro, smart_money, volume_price |
| `neutral_debator` | market, sentiment, news, fundamentals | — | macro, smart_money, volume_price |
| `risk_manager` | **（无）** | market, sentiment, news, fundamentals | macro, smart_money, volume_price |

复核方法（可自行重跑）：提取每个模板的 `{...}` 占位符集合，与各 agent 文件里
`.format(...)` 实际传入的 kwarg 名交叉比对。**注意占位符名不等于 state 键名** ——
`state["market_report"]` 传进去时叫 `market_research_report`，
按 state 键名 grep `{market_report}` 会得到 0 次并误判成「没人用」。
（本次第一遍就是这么扫错的，第二遍才对上。）

### 由此得出三条对阶段 C 直接有影响的结论

1. **`macro_report` 被下游完全无人读取。** 宏观分析师照常运行、消耗 token 和时间，
   产出的报告进不了任何 prompt，也不参与任何 memory 检索。这是**错数据链路，不是缺数据** ——
   任何人看仪表盘会以为宏观维度已被纳入决策。
   **`KNOWN_ISSUES.md` 里没有这一条。不要顺手修，也不要塞进阶段 B 的两个 commit。**
   建议阶段 B 提交完之后单独记录、单独处理。
2. **`smart_money_report` 只到达 `research_manager`，多空双方从未见过。** 主力资金分析
   进不了辩论。审查简报里那句「多头说资金流入 5.5 亿」实际来自新闻里的二手数字，
   不是主力资金分析师的一手数据 —— 这正好是阶段 E 要重点观察的现象（见第 8 节）。
3. **审查方建议阶段 C 先只注入多头/空头/研究经理三个角色，这个选择是站得住的** ——
   这三个角色确实拿得到分析师数据。但要注意：研究经理**看不到** fundamentals/market/news
   的正文（只进了 memory 检索），所以用户提示词里给裁决角色写的
   「按证据可信度分级判断（A 级证据 > C 级观点）」**在研究经理身上仍然无法完全执行**，
   它手里没有那几份一手证据可对照。**这一条要在阶段 C 的汇报里明确写给审查方**，
   不要默认注入了提示词该要求就生效了。

---

## 5.1 待核查：那张表的形状可能指向一次不完整的集成

**这是审查方的推测，不是结论。按第 11 节的规矩，核完再采信。**
**核查时机：阶段 B 提交完之后。不要现在做，不要混进任何 commit。**

把第 5 节那张覆盖表按「上游原版的 4 个分析师」和「本分支新增的 3 个」重排：

| | market | social | news | fundamentals | | volume_price | smart_money | macro |
|---|---|---|---|---|---|---|---|---|
| | 原版 | 原版 | 原版 | 原版 | | 新增 | 新增 | 新增 |
| 接线情况 | 一致 | 一致 | 一致 | 一致 | | 部分接入 | 只到裁决者 | 无人读 |

**原版四个在各处接线一致，乱的全是新增三个。**

审查方的推测：这不是三个独立缺陷，而是**一次不完整的集成** —— fork 加了三个分析师，
只把它们接进了一部分 prompt 模板。若成立，这是**一次修而不是三次修**，而且能一起验证。

### 核查方法（不要直接采信推测）

```bash
# 三个 analyst 文件是什么时候加进来的、是不是同一批
git log --follow --oneline -- tradingagents/agents/analysts/macro_analyst.py
git log --follow --oneline -- tradingagents/agents/analysts/smart_money_analyst.py
git log --follow --oneline -- tradingagents/agents/analysts/volume_price_analyst.py

# 引入它们的那些 commit 里，有没有同步改提示词模板
git show --stat <引入的commit> | grep -E "prompts/(zh|en)\.py"
```

### 判据

- **假设成立**：三个是同一批（或紧邻的几个 commit）加进来的，且那批 commit
  **没有**或**只部分**改 `tradingagents/prompts/zh.py` → 按「一次不完整集成」处理，
  一次性把三个都接好，一起验证。
- **审查方猜错**：它们是分批加入的，且各自都同步改过模板 → 那就是三个独立问题，
  各自单独处理。**这种情况要明确回复审查方"推测不成立"并附 git log 输出**，
  不要为了顺着它的判断而含糊过去（见第 1 节：顺从地执行一个错误判断是最坏的失败模式）。

无论哪种结论，都只是**加进 backlog**，不在阶段 C 顺手改 —— 阶段 C 的关注点是注入。

---

## 6. 阶段 C：注入（当前主线）

`feat: inject custom prompts into agent construction`

### 硬性要求

- **在 agent 构造/统一装配处注入，不要散在每个 analyst 文件里。**
  相关位置：`tradingagents/graph/setup.py` 的 `_load_agent_factories()` / `setup_graph()`，
  以及各 `create_*(llm, ...)` 工厂。
- **必须可开关，默认关闭。** 注入前先读 `users.prompt_injection_enabled`（阶段 B 已加，默认 False）。
  关闭时**完全不拼接、不读表**。
- **取文本只能调用 `api/services/custom_prompt_service.resolve_role_prompt()` /
  `resolve_all_roles_prompts()`**，不要在调用点自己拼 `global + override`，否则两份实现会漂移。
- **角色 key 唯一来源是 `api/services/role_routing_service.ALL_ROLES`（15 个，不是 14）。**
  见第 7 节的命名陷阱。
- **位置先各试一次**：内置系统提示之后 / 数据块之前 or 之后。
  **把两种位置下的完整实际 prompt 打印出来给审查方定**，不要自己选一个就往下走。
- **日志记录每次注入的长度和 hash。**
- **先只注入 `bull_researcher`、`bear_researcher`、`research_manager` 三个角色**，
  不要一次上全部 15 个。理由：内置提示词里一个宽表要求就能把分析师打崩
  （见第 9 节退化事故），再叠三千字外部指令风险面更大且难定位。分析师是提取任务、
  行为已知、刚出过退化事故，别动。

### 必须在注入文案里解决的语义冲突

`confidence` 字段已存在（`ReportDB.confidence`，Integer，某次分析给出 70），
而用户提示词要求的是**概率**（`ReportDB.probability`，Float，阶段 B 之前已加）。

- 置信度 = 对这份分析有多确定
- 概率 = 上涨这件事的可能性

**两个语义重叠的数字字段，模型会随手各填一个且不一致。**
必须在注入文案里显式定义两者，或只保留一个。这条不解决，阶段 E 的校准统计
会拿到两个互相矛盾的数字，而校准度算的是概率。

### 阶段 C 必做：把完整 resolved 文本写进报告快照

阶段 B 给每行加了 `prompt_hash`，但 `PATCH /v1/custom-prompts` 是删行重建 ——
**用户一改提示词，旧版本文本就永久消失。**

后果直指终极目标：阶段 E 做 A/B、几个月后回头算校准度时，标着 `hash=abc123` 的那批报告
查不回提示词原文 —— 只知道两批用的提示词不同，不知道旧那版写了什么，
无法归因「提示词的哪处改动影响了校准度」。

**解法不是建历史表**，而是注入时把当次的**完整 resolved 文本本身**写进报告快照
（连同 hash 和长度）。归因自包含，永不需要回查提示词表，也不受用户后续修改影响。
6000 字符对一份本来几百 KB 的报告可以忽略。
（已记录在 `docs/KNOWN_ISSUES.md`，阶段 B 的 commit 2。）

### token 成本

用户提示词约三千字。如果 15 个角色都注入，一次分析里这段文本会被注入 15 次。
**`llm_call_logs.prompt_tokens` 目前全是 None**（CLIProxyAPI 中间层没透传，见第 8 节 backlog 8），
所以**验证成本时要用真实 tokenizer 直接算，不要等那个字段**。
中文经验值 1.5–2 字符/token，但那是估算不是实测，别把估算当结论往下传。

---

## 7. 命名陷阱：两套角色 key，混用会静默错位

**权威（构造 agent 用的）**：`api/services/role_routing_service.ALL_ROLES`，15 个。
三处交叉验证一致：`graph/setup.py` 的 `_get_role_llm()` 调用 15 次、
`ROLE_DEFAULT_TIERS` 15 个 key（`resolve_all_roles` docstring 原文 "all 15 agent roles"）、
前端 `RoleModelConfigSection.tsx` 的 `ROLE_LABELS` 15 个。
文件数也对得上：7 analyst + 2 researcher + 2 manager + 3 risk_mgmt + 1 trader = 15 个 `create_*`。

```
market  social  news  fundamentals  macro  smart_money  volume_price
bull_researcher  bear_researcher  research_manager  trader
aggressive_analyst  neutral_analyst  conservative_analyst  risk_manager
```

之前记成 14 是漏算了 `volume_price`。注意 `GraphSetup.setup_graph` 方法签名自带的默认参数
只列了 6 个 analyst，但生产调用链 `trading_graph.py:59` 传的是 7 个（含 `volume_price`）——
**别拿方法签名的默认值当清单。**

**⚠️ `api/main.py` 的 `ANALYST_AGENT_NAMES` 是另一套 key，只用于 SSE 进度推送和前端展示：**

| 权威（role_llms / role_routing） | 展示层（ANALYST_AGENT_NAMES） |
|---|---|
| `aggressive_analyst` | `aggressive` |
| `neutral_analyst` | `neutral` |
| `conservative_analyst` | `conservative` |
| `risk_manager` | `portfolio_manager`（展示名甚至是 "Portfolio Manager"） |
| `bull_researcher` / `bear_researcher` | `bull` / `bear`（外加 `Bull_Initial`/`Bear_Rebuttal` 等 4 个别名） |

**这套短名不能用于构造 agent 或校验角色 key。**混用会在风控三方和风控总监上静默错位 ——
不报错，只是注入到了错的地方或没注入。阶段 B 的服务层已经用
`import ALL_ROLES` 锚定，阶段 C 照做，不要在新代码里重新定义一份。

---

## 8. 阶段 D / E

### 阶段 D：验证注入真的生效（用户强调不可跳过）

**不要默认它工作。**

1. 在提示词末尾**临时**加一句可验证的标记，如「报告首行输出 `[PROMPT-OK]`」
2. 跑一次，确认标记出现在**每个受影响 agent** 的输出里
3. 打印**一个分析师和研究经理的完整最终 prompt**
4. 确认后删掉标记
5. **把这个验证固化成长期测试**

### 阶段 E：A/B

600519，当日和 `2026-04-30`，各跑一次开启和关闭，共四份报告。重点对比：

- 置信度数值（以及第 6 节那个 confidence vs probability 冲突是否真的被消除）
- 是否出现数据缺口声明
- **主力资金数据源失败时，多头还敢不敢引用新闻里的二手资金流数字**
  （结合第 5 节结论 2：多空双方本来就看不到 `smart_money_report`，
  所以「多头引用了资金流数字」几乎必然是二手的，这一点在设计对比时要考虑进去）

---

## 9. Backlog（按优先级，别自己改顺序）

1. **阶段 B 收尾 / C / D / E**（当前主线）
2. **600519 退化回归**：Gemini API 当时返回 500，三次都没跑成。等 API 恢复后跑 3 次
   600519 基本面分析，查 `llm_call_logs` 的 `response_chars` / `degraded` 列。
   600519 是财报体量最大的（147 列），如果修改后只有它还退化，说明 `fc015f0`
   修的是主要触发条件而非全部。
3. **as_of 陈旧降级**：每类数据设最大陈旧阈值（行情 3 天、股东户数 120 天、财报 180 天等），
   超阈值降级为显式警告；`as_of` 必须打进 prompt 正文让模型看得见。
4. **缓存**（一直刻意推迟到返回值稳定之后）：键语义已定 —— **按请求日期做键，不是实际数据日期**
   （回退后按实际日期存的话，下次请求同一天还会重走多次回退，缓存等于失效），
   实际日期存进值里；键带逻辑版本前缀 `v1:`，任何改变返回内容的改动递增它；
   TTL 分档（当日直接成功 → 长 TTL；发生了回退 → 15-30 分钟；失败 → 更短）。
5. **`get_insider_transactions` 语义错误**：它拉的是 `stock_main_stock_holder`（股东持股结构），
   不是增减持。**错数据不是缺数据** —— 分析师会以为减持风险已排查。要么换正确接口，
   要么函数名和文案都改成「股东持股结构」并写明「本项不含增减持信息，减持风险未排查」。
6. **`data_vendors` 配置与 fallback 链**：`cn_market_data`、`institutional_risk` 两类未配置，
   `get_vendor()` 默认回落 **yfinance**（根本没有这些方法），先撞一遍再靠全局 fallback
   摸到 akshare，又慢又误导。
7. **`get_global_news` 签名不兼容**：`takes 2 positional arguments but 4 were given`，
   静默降级到 yfinance。A 股分析的全球新闻从 yfinance 拿，来源和视角都不对。
8. **`finish_reason` / `token_usage` 全为 None**：大概率 CLIProxyAPI 中间层没透传 OpenAI
   格式字段。这让 `llm_call_logs` 少了两个核心字段，下次再出退化仍然区分不了
   「模型停不下来」和「撞输出上限」。做分角色模型配置、算注入 token 成本时也要用到。
9. **数据库清理残余**：清场后 `running=0`、`failed_interrupt=103`、`completed=188`。
   WAL 和 `PRAGMA foreign_keys=ON` 的评估还没做。
10. **小清理**：`y_finance.get_stockstats_indicator` 失败返回空串（违反显式失败原则）、
    `# Duplicate removal if any` 等死代码。

### 阶段 B 提交完之后追加进 backlog 的三条

- **`/v1/dashboard/tracking-board` 在 HEAD 上返回 500。** 干净环境（`/tmp`，无挂载问题）里也复现，
  改动前就存在。`tests/test_dashboard_tracking.py:199` `assert 500 == 200`。
  **单开会话查，不要混进阶段 B/C。**
- **核查「新增三个分析师是不是一次不完整的集成」** —— 见第 5.1 节，方法和判据都写在那里。
  **阶段 B 提交完之后才做，不要现在做，不要混进 commit。** 结论加进 backlog。
- **`macro_report` 下游无人读取**（见第 5 节结论 1）。
  **附带影响：宏观分析师目前是纯浪费** —— 产出既不进 prompt 也不进 memory。
  **在接线修好之前，给它配分角色模型、优化它的数据源都没有意义**，
  做分角色模型配置时不要在它身上花时间。

### 阶段 B 收尾（2026-07-30，commit f016cf0 / 56f6359）实测后追加的四条

- **清理退化报告行 + VACUUM。** 库从 10MB（7-29 早）→ 81MB（本地 7-29 23:11 / UTC 15:11）→
  89.5MB（7-30），一天涨八倍，来自 1.58MB / 1.84MB 的 `fundamentals_report` 退化行。
  清场阶段把 102 条 `running` 更新为 `failed`（1 条 `running` 在清场前已正常 completed）、
  删除另外 17 条 `running` + 2 条 `failed`，之后又新增了 18 条报告记录（15:20–19:52 UTC）。
  库持续增大并不单独证明删行后未回收，因为期间又新增了 18 条报告记录。
  SQLite 删除后的空闲页可在数据库内部复用，但文件通常不会因此缩小；若需要把空间返还给
  操作系统才考虑 VACUUM。清理退化大行后，应对比 `page_count`、`freelist_count` 和文件大小
  再决定是否执行（当前库 page_size=4096，page_count=21859，freelist_count=390；旧备份
  page_count=19890，freelist_count=114）。当时刻意没做，避免和 ALTER TABLE 撞一起。

- **`data/tradingagents.db.bak-cleanup-20260729-231127` 风险已隔离，已改名加 `.UNTRUSTED`
  后缀。** 理由：它是旧式 live-DB `cp` 备份，不是用 `sqlite3 .backup` 做的。
  现有检查未发现结构或时间线不一致：`integrity_check` = ok，且 `MAX(updated_at)` 与换算成
  UTC 的文件 mtime 对齐（mtime 23:11 本地 = 15:11 UTC，`MAX(updated_at)` = 15:11:01 UTC）。
  但由于它来自 live-DB `cp`，无法证明复制期间没有并发写入，因此仍不作为回滚点。
  另外只拷 `.db` 会丢 `-wal` 里已提交未落盘的内容。今天的 `.bak-2026-07-30` 用 `.backup`
  （正确取锁、WAL-safe），已覆盖它作为退路的价值。作为回滚点存在误用风险；若保留，
  只能用于取证，不得用于恢复。

- **教训：判别「连的是哪个库 / 环境是否正确」要用数据存在性，不要用 schema 渲染细节。**
  交接书原来那条绊线（「`PRAGMA table_info` 默认值带引号就停」）两个方向都坏：打印端
  `{default!r}` 的 `repr()` 对字符串永远加引号 → 走 ALTER TABLE 也打印 `'0'` → 照字面每次误停；
  断言端 `strip("'\"")` 剥掉引号再比 → 两条建表路径都过、判别力为零。**同时做到「总是误报」
  和「从不生效」。** 已改为断言 `users`/`reports` 行数 > 0 且能读到一个已有用户
  （新建库没有这些），不受 SQLAlchemy 版本和 `repr()` 影响。
  今后写任何「环境正确性」检查都按这个原则。

- **VS Code 的 Python 测试发现会在仓库根反复建一个 0 字节 `tradingagents.db` + `tradingagents.db-journal`。**
  它自动 import 测试模块收集用例，`tests/` 下的东西 import `api.database`，不带 `DATABASE_URL`
  就按 `api/database.py:12` 的默认值 `sqlite:///./tradingagents.db` 在仓库根建库。
  无害（真库是 `data/` 那个）但会脏工作区，删了还会回来；`tradingagents.db-journal` 是
  untracked，**`git add` 时注意别被通配带进去**。也顺带证实 `api/database.py` 确实有 import
  时副作用 —— 「备份必须是第一条涉及数据库的命令」不是假设性担心。

  ⚠️ **时区注意（本机 AWST +0800）**：`ls`/`stat` 显示的文件时间是本地时间；
  `reports.created_at` / `updated_at` 存的是 UTC（`datetime.now(timezone.utc)`）。
  直接比较两者会差 8 小时，导致「快照缺少早于自身 mtime 的行」这类假阳性结论。
  对时要先换算，或把文件时间用 `date -u` 转成 UTC 再比。

---

## 10. 已知问题（`docs/KNOWN_ISSUES.md`，都不要顺手修）

**A. vendor 链路语义折叠（根因级）**

`route_to_vendor` 只在**抛异常**时 fallback，返回失败文案不算。这把三种语义不同的结果
折叠成了两种行为：

| 结果 | 应有行为 | 现在 |
|---|---|---|
| 拒绝（本源语义上无法服务） | 整类拒绝则停止；仅本源无能力则跳下一个 | 返回字符串 → 停止 |
| 失败（网络/超时） | 尝试下一个 provider | 抛异常 → fallback ✓ |
| 确认为空（查询成功但确实无数据） | 停止并报告「确认无数据」 | 返回字符串 → 停止 |

`get_news` 的问题就是「本源无能力」被写成了和「确认为空」一样的字符串，导致模型读成
「该期无新闻」。真正的重构，暂不做。

**B. 裁决者拿不到一手证据** —— **本次已修正，见第 5 节，以第 5 节为准。**
`KNOWN_ISSUES.md` 里那句「整个后半段管线只跑在辩论文本上」太强。
建议修法（等阶段 E 之后再动）：不要把完整报告塞给裁决者（会撑爆上下文），
而是让每个分析师额外产出一个压缩的「关键证据摘要」（≤300 字，只含 A 级事实和数字），
裁决者接收摘要。

**C. 自定义提示词历史不可恢复** —— 阶段 B 新记录，解法见第 6 节。

**D. `finish_reason` 为 None** —— 见 backlog 8。

**退化输出护栏**（`tradingagents/agents/utils/agent_states.py`）：
`check_llm_output_degraded` / `check_stream_chunk_degraded`，
阈值空白率 > 50% 或字节 > 100KB，触发时不写库、不传下游，改为明确失败文案，
打 WARNING 含前 500 字符。**阶段 C 注入后如果出现退化，先查这里的日志。**

---

## 11. 这个项目最重要的一条经验

**五次出现「自相矛盾或物理上不可能的数字被当成结论往下传」：**

1. 三张表同一天公告，却报出资产负债表滞后 30 天、利润表滞后 390 天（会计上不可能）
   → 查下去发现是同比列刷新，才有了 A4 策略
2. 「科创板真实晚披露 → fallback 正确保守、不泄未来」（方向反了，实际是泄漏）
   → 才有了窗口版 A4
3. 「1.58MB 的构成是 53 行、其中一行约 2500 字符、其余极短」（差两个数量级）
4. 「157 万字符物理上不可能」（审查方把字符当 token，BPE 压缩率算错）
5. **阶段 B 收尾时**：断言 `test_dashboard_tracking` 的失败是「挂载的 disk I/O error」，
   搬到 `/tmp` 后 disk I/O error 消失了但测试照样失败（变成 `assert 500 == 200`）——
   **两个独立问题叠在一起，第一个把第二个掩盖了**，而当时给了个单一原因的解释就往下走了。

**前三次和第五次是实现方，第四次是审查方。两边都会犯。**

还有一次同类的、本次交接里的：按 state 键名 grep `{market_report}` 得到 0 次，
差点写成「market 报告无人使用」，实际模板里用的是 kwarg 名 `{market_research_report}`。
**名字对不上时不要直接下结论，换个角度再核一遍。**

所以：**遇到数字对不上、量级差得离谱、会计或物理上说不通的结果，停下来查证，
不要给一个解释然后继续。**这条比任何具体规则都重要 —— 这个项目的核心问题（前视偏差）
的特征恰恰就是「看起来完全正常」。

---

## 12. 你的第一步（按这个顺序，中间有两个必须停下的闸门）

1. 读 `AGENTS.md`（仓库根）
2. 读 `docs/PHASE_B_HANDOFF.md`（阶段 B 收尾的确切命令、绊线、回滚路径）
3. 用一段话向用户复述：你对任务的理解、打算改哪些文件、你认为的风险点。
   **⛔ 闸门 1：等他确认后再动手。**（`AGENTS.md` 第 1 节要求）
4. 阶段 B 收尾，**备份是第一条涉及数据库的命令**：
   删锁 → `docker compose ps` → **备份（用 shell，不 import 应用代码）** →
   确认 `DATABASE_URL` 指向刚备份的文件 → 单元测试 → 检查 `scripts/` 是否在容器里 → 冒烟
5. **⛔ 闸门 2：贴完整原始输出，停下等人工确认。不要自行提交。**
   任何异常 → 停 → `cp` 备份回去 → 贴输出 → **不要向前修**
6. 确认后提交两个 commit（功能 8 个路径 + `docs/KNOWN_ISSUES.md`），
   `git diff --cached --stat` 自检三个脏 test 文件没被带进去
7. 提交完把三条追加项写进 backlog（见第 9 节末），
   然后停下等审查方意见，**不要自行开始阶段 C**
