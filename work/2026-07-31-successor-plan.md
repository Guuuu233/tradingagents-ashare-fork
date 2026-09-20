# TradingAgents-AShare 继任团队执行规划方案

版本：2026-07-31
适用目录：/Users/davidliu/Documents/TradingAgents-AShare

> 本规划是执行顺序和验收门槛，不是对所有阶段的自动授权。遇到上游核心、数据库 schema、认证行为、网络策略或其他根本性选择，必须停在对应 gate 等 David 决定。

## 1. 总目标

把项目推进到一个“数据有证据、日期有语义、缺口可见、模型不乱猜、任务可追溯”的 A 股 AI 投研系统，同时最大限度保留原版成熟架构和前端。

最终必须满足：

1. 标的解析不会把自定义提示词中的普通英文词当成股票。
2. 完整日线、盘中实时快照、历史日期数据严格分开。
3. 外部源失败、正常无记录、历史不适用、实时不可得各自有明确语义。
4. data_gaps 能覆盖全链路显式数据缺口。
5. report-level confidence、claim-level confidence、probability 三者单位和含义不冲突。
6. 认证、用户归属、OTP、SSRF 和任务稳定性不再 fail-open。
7. 所有修复都有确定性测试、focused diff、独立提交和可回滚边界。
8. 不把一次随机 LLM 真实分析包装成统计因果结论。

## 2. 当前基线

### 已完成并应保护

- 数据日期、公告日、交易日护栏和历史 snapshot 拒绝。
- 财务表按列名清洗，stale active report 启动恢复。
- Phase B：自定义提示词服务端持久化。
- Phase C：三个角色的 after_data 注入、冻结 bundle、完整 prompt snapshot、结构化 validator。
- Phase D：真实 prompt 注入机械验证和长期测试。
- 前端原版基本可用，暂不以改 UI 为主线。

### 已完成但未提交

P2A 只改：

- api/main.py
- tests/test_chat_symbol_extraction.py

语义：标的、日期、LLM 意图解析和本地名称回退只读取前端追加标记之前的原始问题；完整文本仍进入 AnalyzeRequest.query 和 user_intent.raw_query。

证据：

~~~text
tests/test_chat_symbol_extraction.py -q: 12 passed, 1 warning
tests/test_api_smoke.py -k chat -q: 3 passed, 1 warning
api/main.py py_compile: 通过
独立审查：PASS
~~~

### 未完成

- P2B：实时快照和完整日线分离。
- P3：全链路 data_gaps 确定性汇总。
- P2：提示词语义修复。
- P0/P1：认证 fail-closed、OTP、SSRF、资源归属、回测和多用户运行稳定性。
- 长期：结构化 evidence ledger、裁决层证据摘要、typed vendor result。

## 3. 总纪律

每个任务固定经过：

~~~text
只读核验
 → 复述问题、范围、风险
 → 方案和测试矩阵
 → David/审查者批准
 → 读完整文件并 grep 所有调用点
 → 先写能复现旧 bug 的测试
 → 最小原路径实现
 → 容器验证
 → focused diff、diff check、未改文件清单
 → 独立审查
 → David 允许后才 stage/commit
~~~

禁止：

- git add .、git add -A、git reset --hard。
- 恢复、删除或格式化来源不明的脏文件。
- 修改 TradingAgents-CN。
- 把两个关注点混成一个提交。
- 新增 _v2、_new、_fixed 并行实现。
- 把原始 DataFrame、连续 NaN 或无关大表塞进 prompt。
- 用空字符串/None/DataFrame 表示数据获取失败。
- 未授权运行真实分析、切开关、PATCH 正式提示词或重启容器。
- 把密钥、JWT、token、SMTP 密码写入日志或文档。

建议提交顺序：

1. P2A 输入边界。
2. P2B 完整日线/实时快照。
3. P3 data_gaps 确定性聚合。
4. P2 提示词语义。
5. P0 认证。
6. P0 secret/OTP。
7. P0 SSRF。
8. P1 资源归属。
9. P1 回测。
10. P1 多用户运行时隔离。

每个提交只解决一个关注点；未获 David 提交指令前只展示 staged diff。

## 4. 团队分工

### Manager

维护当前阶段和 gate，给执行者明确文件范围，收集 focused diff、测试和未改文件，交给独立 inspector，不把 stale agent 当已完成，遇到产品选择立即停下。

### P2A 执行者

只在 api 和 focused tests 范围复核并收口标的边界；保留 AAPL、A 股代码和中文名称；不改 tradingagents、frontend、正式 prompt。

### P2B 数据核心执行者

先读 provider、DataCollector、Market Analyst、API 传播；获授权后才修改 tradingagents；分离完整日线和 realtime snapshot；逐项补 provider、collector、analyst、API 测试。

### Data quality 执行者

实现 API 层 data_gaps 确定性聚合，只识别严格机器可读失败标记，保留正常无事件和 not_applicable 的区别，统一 single/dual horizon。

### Security 执行者

分开处理认证、OTP、SSRF、归属和回测。密钥轮换不在本任务内。先确认本地免登录模式、SSRF allowlist 和 OTP 限流存储。

### Inspector / QA

独立读完整文件和 diff，输出 PASS/FAIL、文件行号、影响和缺失证据；不替实现者修复，不未经授权跑真实分析。

## 5. 阶段 0：只读重新基线

运行：

~~~bash
cd /Users/davidliu/Documents/TradingAgents-AShare
git status --short
git branch --show-current
git rev-parse HEAD
git log -6 --oneline --decorate
git diff --cached --name-status
git diff --check -- api/main.py
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
~~~

阅读顺序：

1. AGENTS.md
2. 2026-07-31-successor-handoff.md
3. 本规划
4. implementer/reviewer/phase-c handoff
5. docs/HANDOFF_2026-07-30.md
6. PHASE_E_REPORT.md
7. KNOWN_ISSUES.md、PROVIDERS.md、MODEL_ROUTING.md
8. 当前代码和调用点

Gate 0：只输出基线；若 HEAD、暂存区或 P2A 与交接不一致，停下报告。

## 6. 阶段 1：P2A 收口

目标：用户问市场问题且 custom 含 Agent 时，不再产生 AGENT。

验收：

- 无标的原始问题 + custom Agent 返回不可识别/400。
- 600519、带后缀 A 股、AAPL、中文名称不回归。
- sync/stream 一致。
- 正文内普通 [分析要求] 不误截断。
- custom 中的 AAPL/日期不污染原始问题。
- AnalyzeRequest.query 和 user_intent.raw_query 保留完整原文。
- 无真实分析、无数据库写入。

步骤：读完整 api/main.py 和所有 extractor 调用点；读 focused test；复现旧正则；跑容器测试；独立审查；David 允许后只 stage 两个文件并单独提交。

## 7. 阶段 2：P2B 日线与实时分离

这是当前数据准确性主线，必须先取得 David 修改 tradingagents 的明确授权。

### 产品契约

~~~json
{
  "daily": {
    "as_of": "实际最大完整日线日期",
    "completeness": "completed"
  },
  "realtime": {
    "status": "available|unavailable|not_applicable",
    "source": "sina|eastmoney|investoday",
    "quote_as_of": "源时间或 null",
    "retrieved_at": "抓取时间",
    "error": null
  }
}
~~~

口径：

- 指标和 VPA 只使用完整日线。
- realtime 只作为独立 snapshot，不能冒充当天 K 线或完整日成交量。
- Eastmoney 无可信源时间时 quote_as_of 必须为 null，不能猜当前时间。
- 历史日期不访问实时源，status=not_applicable。
- 所有源失败要显式 unavailable，不能用空字典阻断后续 provider。

现状：

~~~text
历史日线 Eastmoney/Sina/Tencent
 → _maybe_append_realtime_row
 → 雪球盘中 OHLCV
 → 同一表计算指标/VPA
~~~

候选文件：

- tradingagents/dataflows/providers/cn_akshare_provider.py
- tradingagents/graph/data_collector.py
- tradingagents/agents/analysts/market_analyst.py
- api/main.py 的单/双周期、SSE、报告传播
- realtime/provider/collector/analyst/report 测试

实施顺序：

1. grep _maybe_append_realtime_row、_fetch_realtime_row、stock_individual_spot_xq 调用点。
2. 先写盘中历史行过滤、Xueqiu 不可用、Sina/Eastmoney source/time/status 的失败测试。
3. 删除旧补行调用和死代码。
4. DataCollector 采独立 snapshot，按排序后的完整日线设置 daily.as_of。
5. Market Analyst prompt 分隔完整日线和实时快照。
6. API/SSE/ReportDB 传播同一 market_data_context，不改 schema。
7. 覆盖历史日期、盘中、收盘后、所有源失败。
8. 独立审查。

Gate 2：授权、旧调用无残留、指标不含 snapshot、source/time/status 明确、round-trip 一致、容器测试通过。

## 8. 阶段 3：P3 data_gaps

推荐 API 层止血，不改 tradingagents 或 schema：

1. 在 api/services/report_service.py 增加纯函数。
2. 按固定字段扫描 analyst report 字符串。
3. 只识别行级 【数据获取失败】。
4. 与 LLM data_gaps 稳定合并、规范化去重。
5. None/未知字段安全忽略。
6. LLM 失败时确定性 gaps 仍保存。
7. single/dual horizon、result/SSE/ReportDB 一致。

行为表：

| 情况 | 是否 gap |
|---|---|
| 超时、HTTP 500、结构异常、严格失败标记 | 是 |
| 正常无龙虎榜/无重大新闻 | 通常否 |
| 历史不适用 | 按字段标记 unavailable 或 not_applicable |
| 同一缺口重复 | 合并 |
| 只有宽泛“失败/缺失” | 否，防误报 |

测试：标准/自由格式失败、多报告去重、LLM None、正常无事件、single/dual、job.completed、DB round-trip、旧报告恢复、不影响旧结构化字段。

Gate 3：无宽泛关键词误报、两条保存路径一致、无 schema 偷改、独立审查通过。

## 9. 阶段 4：提示词语义

目标：

1. report-level StructuredReport.confidence = 0–100 整数。
2. DEBATE_STATE/RISK_STATE claim confidence = 0.00–1.00。
3. probability 永远为上涨概率，Bear 也不改成下跌概率。
4. 模板未要求时不增加正文级字段。
5. EMA/VWMA/价格趋势不是主力资金净流入。

先给 David/审查者：

- 推荐方案及与 global 文案、应用护栏、角色包装的比较。
- 完整文案、chars、hash、三角色 preview。
- 拟改文件和测试。
- 随机模型行为的不可证明边界。

未经授权不要 PATCH 正式提示词。测试 prompt 语义、机读块解析、switch-off 不变；不把一次真实输出作为证明。

## 10. 阶段 5：P0 安全

### 认证

当前无凭证/无效 JWT/API token 会落 local-default-user。先选：

- 全部受保护接口 401。
- 默认关闭、严格限定本地部署的单用户模式。

选择后写无凭证/无效凭证/owner-missing 测试，删除静默异常，闭合报告、job、model、token、回测权限。密钥不轮换。

### secret/OTP

不做密钥运维轮换。另行确认生产缺 secret 是否 fail-fast、ENV/APP_ENV 统一、生产不泄露 OTP、申请/校验限流和失败锁定。需要 schema/Redis 选择时停。

### SSRF

先选固定 host allowlist，或严格校验的本地代理 URL。实现 scheme、DNS/IP、私网、回环、元数据、重定向和错误正文保护。

每个关注点独立 diff、测试、审查、提交。

## 11. 阶段 6：P1 稳定性与多用户

按独立关注点：

1. backtest 提交 TypeError。
2. sample_interval 最小值和日期循环。
3. backtest list/get/delete 认证与归属。
4. job owner 缺失 fail-closed。
5. process-global trading config 竞态。
6. Redis/SSE terminal event、任务超时。
7. vendor typed semantics：Ok/Fail/Refuse/ConfirmedEmpty。
8. yfinance 日期、Investoday 参数、Alpha Vantage timeout。

每项先复现测试，再改原路径；不顺手做 schema 重构。

## 12. 长期架构（另立批准）

### evidence ledger

建议 DataCollector/AgentState 记录 source、status、failure_type、as_of、reason，由 Python 聚合 data_gaps，LLM 只补充解释。会改 tradingagents/state/序列化，另立项目。

### 裁决层证据摘要

Research Manager 当前看不到 fundamentals_report、market_report、news_report 正文。建议 analyst 输出短的、只含事实数字日期的 evidence summary，裁决层接收摘要而非完整大报告。

### typed vendor result

统一区分 VendorOk、VendorFail、VendorRefuse、VendorEmpty，避免 router 把历史拒绝/接口失败/确认无事件都当成功字符串。

## 13. 验证和真实分析规则

每个 bug 必须有修复前失败测试；数据适配器至少正常/接口失败/结构异常三用例；结果类改动覆盖 API/SSE/ReportDB。

优先容器：

~~~text
docker exec tradingagents-ashare /app/.venv/bin/python ...
~~~

默认不跑真实股票分析。若 David 明确授权，先提交实验卡：目标、标的、日期、horizon、分析师、JWT、是否写库、deterministic 判据、失败处理、finally 清理和回读项。不能使用 server-level API key 代替用户 JWT，不能自动重跑失败任务。

## 14. 完工交付

每个关注点交付：

1. 根因和问题定义。
2. 修改文件与未修改相关文件。
3. 行为变化和不变契约。
4. 完整 focused diff。
5. 测试命令、收集数、通过/失败/跳过/warnings 原始输出。
6. 容器/宿主环境。
7. 数据库、SSE、配置、开关是否写入。
8. 未验证项和随机模型局限。
9. 独立审查 PASS/FAIL。
10. stage 前路径清单和提交授权状态。

最终完工还需验证：关键数据源健康、日线/实时/历史语义、缺口可见、权限闭合、结构化报告可追溯、前端契约一致、改动可回滚。

## 15. 下一轮的直接动作

1. 重新核对 Git、容器、P2A diff。
2. 审查并等待 David 批准 P2A 提交。
3. 向 David 请求 P2B 修改 tradingagents 的授权。
4. 提交 P2B 方案和测试矩阵，不立即写大改动。
5. P2B 完成后再做 P3 data_gaps。
6. 提示词语义、P0/P1 按独立关注点推进。
7. 每一 gate 停下，不自动进入下一阶段。

当前停止点：不提交 P2A，不改 tradingagents，不跑真实分析，不切开关，不改 schema，不轮换密钥，不清理工作区。

