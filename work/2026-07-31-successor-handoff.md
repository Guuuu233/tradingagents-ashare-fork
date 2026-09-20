# TradingAgents-AShare 继任团队项目交接书

更新时间：2026-07-31（Australia/Perth）  
交接原因：原管理/执行团队退休，由新 agent 团队接替。  
项目目录：/Users/davidliu/Documents/TradingAgents-AShare  
当前分支：main  
当前 HEAD：ca13c8ad0d856c9364a5443d77605d69061a448c

> 本文是事实底稿，不是授权书。任何代码、数据库、正式提示词、开关、真实分析、提交或发布动作，都必须服从用户最新指令和仓库根目录 AGENTS.md。

---

## 1. 一句话结论

这是一个基于成熟开源 TradingAgents 架构改造的 A 股多智能体投研系统。目标不是预测股票必涨必跌，而是把多来源市场数据、基本面、新闻、情绪、宏观、主力资金和量价指标交给多个角色进行结构化分析、红蓝辩论和风险裁决，并在数据不可用时明确暴露缺口，避免模型编造。

当前状态：

1. 原版多智能体流程、数据日期护栏、财务表清洗、服务端自定义提示词注入机制已经完成相当一部分。
2. Phase C 已提交，Phase D 机械链路验收通过。
3. Phase E 四格真实 A/B 证明开关、身份、快照和报告落库机械链路成立，但质量验收失败，不能宣称自定义提示词项目完成。
4. 用户发现的“当天交易时段大量取不到数据”已经定位为两个不同问题：
   - chat 输入中的自定义提示词把 Agent 误识别成股票代码 AGENT；P2A 已在工作区完成最小修复，但尚未提交。
   - 盘中日线数据与实时快照混用；P2B 已完成只读诊断，实施需要明确授权修改 tradingagents/ 上游核心。
5. 结构化 data_gaps 仍不是全链路汇总；新闻、主力资金、量价等缺口会漏报。
6. 认证 fail-open、OTP、SSRF、资源归属和回测等安全/稳定性问题已由独立审查确认，但尚未修复。

---

## 2. 产品定位和不可改变的方向

### 2.1 产品闭环

~~~text
自然语言问题
 → 标的/日期/周期/用户约束解析
 → 数据采集与供应商降级
 → 7 个分析师分别分析
 → Bull/Bear 多空辩论
 → Research Manager 裁决
 → Trader 投资计划
 → 激进/中性/保守风险辩论
 → Risk Manager/组合决策
 → 结构化报告、SSE、数据库、前端
~~~

当前代码实际角色是 15 个，不是 README 里的 14 个：

~~~text
analysts: market, social, news, fundamentals, macro, smart_money, volume_price
researchers: bull_researcher, bear_researcher
manager: research_manager
trader: trader
risk: aggressive_analyst, neutral_analyst, conservative_analyst, risk_manager
~~~

权威角色清单是 api/services/role_routing_service.py 的 ALL_ROLES。不要拿 api/main.py 的 ANALYST_AGENT_NAMES 当构造角色枚举，它是 SSE 展示短名，命名不同会导致风控角色静默错位。

### 2.2 产品不是什么

- 不是保证收益的预测器。
- 不是自动下单系统。
- 不是把缺失数据留白后交给 LLM 自己猜的聊天机器人。
- 不是用当天快照伪装完整历史日线的回测工具。
- 不是只看 BUY/SELL 标签就能完成质量评价的黑箱。

David 早期自建工具回测平均方向命中约 50.5%，因此产品定位已经转为信息与决策辅助。坚持：有数据就标来源、数据日期和语义；没有数据就写不可用；正常无事件不等于接口失败；接口失败不等于零风险。

### 2.3 质量原则

代码负责确定性计算、日期过滤、字段清洗、失败分类、结构化校验和溯源；LLM 负责解释、比较、假设和文字组织。不要把可确定问题交给模型自由发挥，不要用一次随机真实分析替代确定性测试。

---

## 3. 项目前因后果

### 3.1 从旧工具转向成熟开源方案

项目最初是 Python + AkShare + pandas + DeepSeek 的自建看盘工具，后来旧系统损坏且不可用。于是转向 GitHub 的成熟方案 KylinMountain/TradingAgents-AShare，保留原版多智能体、辩论、前端和 API，仅围绕数据准确性、溯源、提示词接入和后端稳定性做窄改。

除非出现已验证缺陷，不要重写原作者核心逻辑，不要大幅魔改前端。

### 3.2 数据事故形成的硬规则

历史上发生过：

- 按位置 iloc[:, :18] 取错财报列，普通企业收到大量 NaN。
- 用 head()/iloc[0] 假定第一行是最新。
- 日期未规整为有效交易日，历史查询越界。
- 日期解析失败填当前时间，把可检测失败变成假答案。

因此 AGENTS.md 要求：按列名取数、显式缺列、显式排序、日期转 datetime、失败不得返回空白、禁止默认填当前时间、外部调用必须超时/重试/区分失败类型。

### 3.3 Phase B：提示词服务端持久化

早期设置页的自定义分析提示主要在浏览器 localStorage，分析师/研究员/研究经理没有真正收到。已完成：

- f016cf0 feat: persist custom analysis prompts server-side
- 56f6359 docs: record that custom prompt history is unrecoverable

提示词更新是删除后重建，旧文本会丢失；因此 Phase C 选择把当次完整 resolved 文本写进报告快照，而不是新增历史表。

### 3.4 Phase C：注入三类角色

当前 HEAD ca13c8a：

~~~text
feat: inject custom prompts into research debate pipeline
~~~

只注入：

- bull_researcher
- bear_researcher
- research_manager

默认位置是 after_data：数据之后、原有写作/输出要求之前。共享注入逻辑在 tradingagents/agents/utils/prompt_injection.py，GraphSetup、TradingAgentsGraph 和 API 统一传递。

不可回归行为：

- 开关关闭：零 resolver 读取，原 prompt 逐字节不变。
- 开关开启：初始化时解析一次，任务内冻结 prompt bundle。
- 双周期复用同一 bundle。
- 两条报告保存路径都写 custom_prompt_snapshot 深拷贝。
- 快照含 resolved_text、hash、length、placement、enabled、injected。
- probability 只接受 0.00–1.00；报告级 confidence 是 0–100 整数；无依据不猜。
- Bull/Bear 的 DEBATE_STATE、Research Manager 的 VERDICT 必须可解析。

Phase C focused 回归曾为 67 passed, 2 warnings。没有新缺陷证据不要重构。

### 3.5 Phase D：机械链路通过

临时 [PROMPT-OK] 验证确认 Bull、Bear、Research Manager 收到最终注入；DEBATE_STATE/VERDICT 没坏；JWT 身份正确；快照完整；finally 清除了临时标记、恢复正式提示词并关闭开关。临时脚本和标记已经删除，不要恢复。

### 3.6 Phase E：机械完成、质量失败

四格真实 A/B 针对 600519.SH（贵州茅台）：

| 组别 | 日期 | 开关 |
|---|---|---|
| current_off | 2026-07-29 | false |
| current_on | 2026-07-29 | true |
| historical_off | 2026-04-30 | false |
| historical_on | 2026-04-30 | true |

机械上四格 completed，身份/开关/快照正确。质量上：

- 开启组 confidence 反而升高：当日 75→85，历史 40→75；单次运行不能作因果结论，但没有看到期待的保守效果。
- 四格 probability 都是 null，符合无明确主周期/基准价/定量依据时不猜。
- 新闻、主力资金、龙虎榜、VWMA/成交量报告有明确缺口，但当日报告 data_gaps=[]，历史报告只记录基本面缺口。
- Bull 在主力资金失败时把价格高于 EMA/VWMA 表述成“市场资金持续流入”。
- 报告级 confidence 0–100 与机读 claim-level confidence 0.00–1.00 同名冲突。
- Bear 有时把 probability 写成下跌概率；有时模型额外打印正文级 probability/confidence。
- Research Manager 与最终风控可以分歧；最终 confidence 不是三名注入角色的直接服从指标。

四格每格只运行一次，不能作统计因果实验。Phase E 不能宣称质量通过。

---

## 4. 代码和数据架构

### 4.1 目录职责

| 目录 | 职责 | 注意 |
|---|---|---|
| tradingagents/ | 上游多智能体、provider、graph、prompt | 修改前必须获 David 授权 |
| api/ | FastAPI、认证、任务、报告、SSE | 当前主要后端改动区 |
| frontend/ | React/Vite/TypeScript、chat、设置、报告 | 原版基本可用，不要无故改 |
| scheduler/ | 定时分析和并发调度 | 可能与 API 同容器运行 |
| tests/ | 单测和回归 | bug 必须有复现测试 |
| work/ | 交接、证据、分析产物 | untracked，不能全量 stage |
| docs/ | 历史方案和已知问题 | 可能是历史快照 |

### 4.2 任务生命周期

api/main.py 主要入口：

- POST /v1/chat/completions：解析自然语言、创建任务。
- POST /v1/analyze：结构化分析入口。
- GET /v1/jobs/{job_id}、/events、/result：状态、SSE、结果。
- /v1/reports：历史研报。
- 自选股、定时分析、持仓、看板、模型配置、提示词 API 也主要在 api/main.py 和 api/services/。

典型链路：

~~~text
请求
 → _ai_extract_symbol_and_date / streaming
 → AnalyzeRequest
 → job store
 → _run_job / TradingAgentsGraph
 → DataCollector
 → analysts
 → debate / trader / risk
 → extract_structured_data
 → result / SSE / ReportDB
~~~

### 4.3 数据源和语义

| 类型 | 来源 | 注意 |
|---|---|---|
| A 股日线 | AkShare/Eastmoney、Sina、Tencent、BaoStock | 需排序、日期截断，不能混入盘中快照 |
| 独立实时 | Sina，失败时 Eastmoney | 已有 get_realtime_quotes，当前主要给跟踪看板 |
| 雪球 | stock_individual_spot_xq | 盘中补行失败点，不应再混入日线指标 |
| 财报 | AkShare/Sina | 按列名；按公告日过滤 |
| 新闻 | AkShare/Investoday 等 | 历史日期禁止只支持实时/近窗的源 |
| 资金/龙虎榜/涨停池 | AkShare 等 | 缺失显式报告；正常无上榜不是接口失败 |
| 全球/美港股 | YFinance/AlphaVantage | 日期语义和市场范围必须单独验证 |

路由在 tradingagents/dataflows/interface.py，BaseProvider/DataResult 在 tradingagents/dataflows/providers/base.py，注册中心在 providers/registry.py。DataResult 应区分 ok、refuse、fail、confirmed empty；当前部分 router 仍把失败字符串当成功，详见 docs/KNOWN_ISSUES.md。

---

## 5. 已验证问题

### 5.1 P2A：Agent 被解析成 AGENT（已在工作区修复，未提交）

前端 ChatCopilotPanel.tsx 会追加：

~~~text
<原始问题>\n\n[分析要求] <custom prompt>
~~~

旧 api/main.py 对完整文本执行任意 1–6 个大写字母 ticker 正则。自定义提示词中的 Agent 被转成 AGENT，随后被送入分析任务。无标的市场问题因此可能被错误当成一只股票，最终报告得到“无 K 线/无技术指标”的误导性结果。

P2A 当前修复：

- 只让标的、日期、LLM prompt 和本地名称回退读取 [分析要求] 之前的原始问题。
- 完整文本仍保留在 AnalyzeRequest.query 和 user_intent.raw_query。
- 不封杀既有 AAPL、A 股代码契约，不使用 AGENT 黑名单。
- 支持 LF/CRLF 和标记行空白，正文内联同名字面量不截断。

工作区文件：

- api/main.py（未提交）
- tests/test_chat_symbol_extraction.py（未跟踪）

独立复审 PASS。容器验证：

~~~text
tests/test_chat_symbol_extraction.py -q: 12 passed, 1 warning
tests/test_api_smoke.py -k chat -q: 3 passed, 1 warning
api/main.py py_compile: 通过
~~~

新团队必须先重新查看完整 diff，不能未经用户确认直接提交。

### 5.2 P2B：实时快照与完整日线混用（未实施）

只读复现的当前链路：

~~~text
历史日线 Eastmoney/Sina/Tencent
 → _maybe_append_realtime_row()
 → 雪球盘中 OHLCV 补行
 → 同一表计算指标/VPA
 → Market Analyst
~~~

雪球 token 缺失或返回结构异常时，宽泛捕获只返回历史表，实时状态丢失；成功时盘中价格又混入日线指标。独立 get_realtime_quotes() 已有 Sina→Eastmoney 路径，主要只供 tracking_board_service.py。

已确认的产品口径：

- 指标和 VPA 只用最后一根完整日线。
- 实时价格是独立 snapshot，不冒充当天完整日线。
- snapshot 带 source、quote time（取不到则 null）、retrieved_at 和失败状态。
- 历史分析日期不访问实时源，标记 not_applicable。

候选改动文件：

- tradingagents/dataflows/providers/cn_akshare_provider.py
- tradingagents/graph/data_collector.py
- tradingagents/agents/analysts/market_analyst.py
- api/main.py 的结果/SSE/保存传播
- realtime/provider/collector/analyst/report 相关测试

这会改上游核心，当前停在 David 授权点。不要只删除雪球调用而不补完整的实时状态和失败语义。

### 5.3 P2/P3：data_gaps 漏报（未实施）

当前 extract_structured_data 主要只看：

- final_trade_decision[:3000]
- fundamentals_report[:1000]

所以看不到 market、sentiment、news、macro、smart_money、volume_price 等报告的完整失败证据。result、数据库 JSON、API/SSE 通常忠实传递 extractor 结果，核心问题是 extraction 输入边界和缺少 Python 确定性聚合。

推荐止血方案：

- 在 api/services/report_service.py 增加纯函数，只扫描严格行级 【数据获取失败】。
- 从 result 中已有 analyst report 字段收集，再与 LLM data_gaps 合并。
- 稳定排序、规范化去重、保留首个原文。
- 不扫描宽泛“失败/缺少”，避免把正常无事件误报。
- single/dual horizon 两条收口路径统一。

长期方案是 DataCollector/AgentState 的结构化 evidence ledger，区分 unavailable、normal_absence、not_applicable；会改 tradingagents/，应另立项目。

### 5.4 P2：提示词语义质量（未修复）

下一轮至少要区分：

- 报告级 StructuredReport.confidence：0–100。
- DEBATE_STATE/RISK_STATE.new_claims[].confidence：0.00–1.00。
- probability 永远表示上涨概率，Bear 也不能改成下跌概率。
- 模板没有要求时不新增正文级结构化字段。
- EMA/VWMA/价格趋势不等于主力资金净流入。

提示词语义、data_gaps、P2B 必须分开设计、测试、diff 和提交。

### 5.5 P0/P1：安全和稳定性（未修复）

独立只读审查确认：

1. _require_api_user 无凭证/无效 JWT/API token 会回落 local-default-user，而非 401；存在宽泛异常吞掉。
2. 缺少 TA_APP_SECRET_KEY 时存在固定默认 secret。用户明确暂不轮换密钥，但不能因此描述为“安全”。
3. ENV=prod 与 APP_ENV=production 判断不一致；OTP 无 SMTP 时可能日志/响应泄露，申请和校验缺少限流和失败锁定。
4. /v1/models/fetch 接受任意 base_url，存在 SSRF 和重定向内网探测风险。
5. 回测 list/get/delete 无认证/归属；job owner 缺失时也可能 fail-open。
6. backtest 提交有参数重名 TypeError；sample_interval=0 可能导致日期循环不前进。
7. process-global _config 可能造成多用户运行配置串线。

进入 P0 实施前，需重新向 David 确认 API 认证模式、SSRF 网络策略、OTP 限流存储；不能因为“不改密钥”就跳过代码安全问题。

### 5.6 结构性限制：裁决层一手报告不可见

Research Manager、Trader、Risk Manager 主要看到辩论、summary/memory 或有限字段。Research Manager 当前看不到：

- fundamentals_report
- market_report
- news_report

这些正文只用于 curr_situation memory 检索，不进入 Research Manager prompt。因此它不能独立核对多空对上述一手材料的引用。未来若修，优先考虑每个 analyst 输出短结构化 evidence summary，不要把完整报告塞进裁决 prompt；这应独立于自定义提示词和 data_gaps。

---

## 6. 已完成提交和不可回归约束

重要提交（从新到旧）：

| Commit | 内容 |
|---|---|
| ca13c8a | Phase C 注入、快照、结构化 validator |
| 56f6359 | 提示词历史不可恢复说明 |
| f016cf0 | 服务端保存自定义提示词 |
| bd99462 | 七个分析师流式退化中止 |
| fc015f0 | 删除基本面宽 Markdown 表 |
| fee4c36 | 记录裁决层盲点 |
| a9f8504 | 财务表按列名清洗/压缩 |
| 5ae32c2 | stale active report 启动恢复 |
| 1471fd5 | 业绩预告报告期 |
| 00199da | provider 日期参数契约 |
| 11f230d | 显式分析日期 |
| adb2757 | 公告日期比较 |
| 25fac71 | 历史新闻拒绝近窗源 |
| bb3a0d9 | 历史涨停池拒绝近窗源 |
| 39d6db7 | 删除 prompt wall-clock 时间 |
| 3aee89a | 历史分析拒绝 snapshot-only 源 |
| 38ce88f | 公告日财报截断 |
| 1aff852 | 外部数据隐式形状规则 |

Phase C 约束：

- 只注入三个目标角色。
- 默认 after_data，非法 placement 不静默回退。
- switch-off 零 resolver 读取、原 prompt 不变。
- 一个任务只解析一次，bundle 冻结。
- 保存路径都写完整 snapshot 深拷贝。
- confidence 单位不混用。
- 保留 DEBATE_STATE、VERDICT 和风险机读块。

旧 README 仍写 14 个 Agent；旧部署文档可能含 credential-shaped 内容。本交接不复制任何 key/token；运行状态和数据库计数必须重新核验后才能称为当前。

---

## 7. 当前工作区和文件归属

本交接创建前只读 status：

~~~text
 M .gitignore
 M AGENTS.md
 M api/main.py
 M tests/test_api_smoke.py
 M tests/test_intent_parser.py
 M tests/test_portfolio_import.py
?? CLAUDE.md
?? GEMINI.md
?? locks/
?? scripts/work_smoke_commit2.py
?? tests/test_chat_symbol_extraction.py
?? work/
?? work_analysis_collect_probe.json
?? work_smoke_commit2.py
~~~

归属：

- api/main.py 的 P2A 与 tests/test_chat_symbol_extraction.py 是本团队刚完成但未提交，独立审查 PASS。
- .gitignore、AGENTS.md、三个已有测试及其他 untracked 来源未确认；不要恢复、删除、格式化或顺手纳入提交。
- work/ 是证据和交接目录，不能 git add -A。
- 本交接和规划文件也只是交接材料，不属于业务修复提交。

---

## 8. 测试和运行证据

P2A 当前容器证据：

~~~text
tests/test_chat_symbol_extraction.py -q: 12 passed, 1 warning
tests/test_api_smoke.py -k chat -q: 3 passed, 1 warning
api/main.py py_compile: 通过
~~~

历史证据（不是当前全绿保证）：

- Phase C focused：67 passed, 2 warnings。
- 实时/历史 provider 基线：25 passed, 1 warning。
- inspector 历史审查：Python 140 文件编译通过，tsc --noEmit 通过，Vitest 4 文件/19 测试通过。

优先用容器：

~~~text
docker exec tradingagents-ashare /app/.venv/bin/python ...
~~~

不要为测试擅自重启容器；先确认 reload/volume 和运行中任务。

---

## 9. 真实分析、密钥和隐私边界

默认不要跑真实股票分析、切开关、PATCH 正式提示词或重启容器。若 David 明确授权真实验证，先写清标的、日期、周期、分析师、身份/JWT、是否写库、deterministic 判据、失败处理和 finally 清理。

不要使用 server-level API key 代替目标用户 JWT；历史上它会落到 local-default-user。任何日志、报告或交接书都不得包含 API Key、JWT、token、SMTP 密码或完整用户凭证。旧文档中的 credential-shaped 内容按 [REDACTED_SECRET] 处理。

---

## 10. 权威阅读顺序

1. AGENTS.md
2. 本交接书
3. 2026-07-31-successor-plan.md
4. work/2026-07-31-implementer-handoff.md
5. work/2026-07-31-reviewer-handoff.md
6. work/2026-07-31-phase-c-handoff.md
7. docs/HANDOFF_2026-07-30.md
8. work/phase_e_ab_2026-07-31/PHASE_E_REPORT.md
9. docs/KNOWN_ISSUES.md、docs/PROVIDERS.md、docs/MODEL_ROUTING.md
10. 当前代码、Git 和只读运行状态；实时事实优先于旧文档

旧 Claude 归档在本机迁移库。如需补充旧对话，读取 david-context 的 archive map，再用 search_archive.py；不能假设新 agent 自动拥有旧窗口全文。

---

## 11. 继任团队第一轮动作

只读检查：

~~~bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git branch --show-current
git rev-parse HEAD
git diff --cached --name-status
git diff -- api/main.py tests/test_chat_symbol_extraction.py
~~~

然后：

1. 先审核 P2A diff 与 12 个测试，再由 David 决定是否单独提交。
2. 阅读 P2B 报告，向 David 请求修改 tradingagents/ 的明确授权。
3. P2B 之前不要把 data_gaps、提示词语义或安全大修混进同一个关注点。
4. 每个阶段展示 focused diff、测试原始结果和未改文件，停在约定 gate。

---

## 12. 当前未决事项

必须由 David 决定：

- 是否授权 P2B 修改 provider、DataCollector、Market Analyst 和传播链。
- 受保护 API 是全面 fail-closed，还是保留默认关闭的本地单用户模式。
- SSRF 采用固定服务端 host allowlist，还是严格校验的本地代理 URL。
- OTP 限流使用现有 DB 还是 Redis；若改 schema，先单独确认。
- 是否在前端显示 market_data_context。
- 密钥轮换暂不做，但认证绕过、生产默认 secret、OTP 泄露等代码问题不能描述为已安全。

已有可执行结论：

- P2A 采用“只解析原始问题，完整文本继续用于分析”，不封杀 AAPL，不用 AGENT 黑名单。
- data_gaps 止血优先 API 层严格扫描 【数据获取失败】；长期 evidence ledger 另立项目。
- 前端暂不改，除非 P2B 后端契约需要展示新元数据。

---

## 13. 交接完成标准

继任团队读完本文后，应能回答：

1. 为什么不应重写原版核心。
2. 为什么实时报价和完整日线不是同一个数据对象。
3. 为什么 Agent 会变成 AGENT。
4. 为什么 data_gaps=[] 不能证明全系统数据完整。
5. 为什么一次 A/B 不能证明提示词有因果效果。
6. 为什么 Research Manager 不能独立审计三份一手报告。
7. 为什么“不改密钥”不代表认证/OTP/SSRF 已安全。
8. 哪些文件能改、哪些必须先问、哪些脏文件不能碰。

交接结束。后续以用户最新指令、当前 Git 状态和 AGENTS.md 为准。

