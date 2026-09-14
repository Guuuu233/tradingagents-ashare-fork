# P1-D：交易员与最终风控官的一手证据访问设计

日期：2026-09-14（Australia/Perth）  
基线核验：远端 `origin/codex/dav-4-p2a-trunk` =
`a9293ffa7e4e4ceac1be5a3999168505b714a311`；业务运行 SHA =
`63d5648bca7c49f57e1211d848cbc1d02ff6b3a5`。

当前状态（2026-09-14 后续回写）：设计契约已冻结；DAV-900 已实施，DAV-901 已由**代码审核员**
对同一 SHA `9d702e7522c94bf3ac983cb1ede10943cfca1a4b` 只读通过，随后已合入并随
`026349614a3f1b92a95dc06c0515f10ebec193bc` 受控发布。本文件以下内容保留为冻结设计与验收依据；
实施、回归和发布的事实以 `PROJECT_STATE.md`、`DECISIONS.md` 及对应证据文件为准。

## 1. 结论与问题边界

当前代码中，研究经理已经收到由 `build_evidence_summary()` 确定性提取的市场、新闻、基本面和宏观证据摘要；交易员和最终 `risk_manager` 只收到结构化标的/市场/用户上下文、上游方案、风控反馈、辩论状态和记忆内容。两者读取完整 analyst report 只用于 memory 查询，不会把报告内容写进自己的最终 prompt。

因此 P1-D 的目标不是把七份全文继续向下游复制，而是给交易员和最终风控官各加一份相同来源、可追溯、有上限的一手证据摘要，供它们独立核验上游方案。辩论分析师节点继续沿用现有输入，不在本卡扩大范围。

## 2. 冻结的输入契约

### 2.1 唯一来源

摘要只能从当前 LangGraph state 的以下七个 report 字段确定性生成，不能从 LLM 输出的交易方案、辩论 prose 或记忆反向生成“证据”：

| 顺序 | state 字段 | 展示标签 | 建议单项上限 |
|---|---|---|---:|
| 1 | `market_report` | 市场技术 | 300 字符 |
| 2 | `news_report` | 新闻 | 240 字符 |
| 3 | `fundamentals_report` | 基本面 | 300 字符 |
| 4 | `macro_report` | 宏观/板块 | 240 字符 |
| 5 | `sentiment_report` | 市场情绪 | 180 字符 |
| 6 | `smart_money_report` | 主力资金 | 260 字符 |
| 7 | `volume_price_report` | 量价 | 260 字符 |

`build_evidence_summary()` 是现有摘要提取器；实施时应复用它，不能为 trader/risk 另写一套关键词筛选。上限是摘要正文上限，方向前缀和固定标签另计；实现必须再对最终组合做硬上限检查，目标总长度不超过 2,400 字符。不得按“只保留看多的报告”或“只取胜方”筛选。

### 2.2 输出形态

实现应提供一个共享的、无 LLM 的组合函数（名称可由实施者按仓库惯例确定），由 trader 和最终 risk_manager 调用同一函数。输出至少包含：

```text
【一手证据摘要（代码提取，非完整报告；不得按分析师数量计票）】
市场技术证据：...
新闻证据：...
基本面证据：...
宏观/板块证据：...
市场情绪证据：...
主力资金证据：...
量价证据：...
```

每一行必须保留来源标签；报告为空或缺失时不生成“无数据即确认没有”的事实性句子。可以在独立的状态元数据中标记 `missing` / `failed`，但不能把状态占位符伪装成证据。已存在的 `report_manifest` / `run_integrity` 若可用，应只用于标记可用性和失败原因，不得替换真实摘要。

### 2.3 语义与安全约束

- 摘要中的 analyst `VERDICT` 只能作为带来源标签的观察，不是票数；提示词必须明确“禁止按分析师数量、角色数量或摘要数量计票”。
- 摘要是原报告事实行的确定性摘录，不是 LLM 的二次总结；不能补数字、补日期、补来源、补方向理由。
- 摘要提取应继续剥离 `VERDICT`、`DEBATE_STATE`、`RISK_STATE`、`RISK_JUDGE` 等机器块；不得把机器块中的 reason 当作一手事实。
- 0、负数、百分比和日期等原文事实不得因关键词筛选被改写；缺失、失败、未来日期和 provider refusal 必须保留原有状态语义。
- 不把 `claim_evidence_summary`、经理方案或交易员方案当作一手报告的替代品；它们仍按现有字段单独传递。
- 不改变 `analysis_status`、`decision_status`、`trade_action`、`confirmation_state` 或任何统计口径；P1-D 只改变可见输入。
- 不把完整七份报告、`build_dense_report_input(..., 1800)` 的大段文本、memory 命中内容或用户自定义提示词放进这份新证据块。

## 3. 两个消费端的固定位置

### trader

在用户 prompt 中增加证据块，位置应紧邻“研究经理方案内容”，但必须保持“上游方案”和“一手证据”两个独立字段。交易员可用摘要核验价格、日期、财务数字、资金与量价约束；不得因摘要数量或某个 analyst `VERDICT` 单独翻转研究经理方向。现有确认闸和 `NO_TRADE`/`WAIT` 保护不变。

### 最终 risk_manager

在最终风控 prompt 中增加同一个证据块，位置应与交易员方案、风险 claim 和风控上下文同级。风控官可用摘要检查交易员提出的价格、仓位、止损、前置条件和降险触发器是否有来源；不得把摘要中的数字当作已经核验的交易许可，也不得绕过上游非可执行状态。

两处必须通过同一共享函数生成，不能一处使用四个报告、另一处使用七个报告，也不能一处得到原始全文、另一处得到摘要。

## 4. 允许修改的范围（实施卡冻结候选）

默认白名单：

1. `tradingagents/agents/utils/evidence_summary.py`：共享组合函数及必要的常量/类型；若现有 `build_evidence_summary()` 足够，不得无理由改动其既有语义。
2. `tradingagents/agents/trader/trader.py`：组装 trader 的证据块。
3. `tradingagents/agents/managers/risk_manager.py`：组装最终 risk_manager 的证据块。
4. `tradingagents/prompts/zh.py`：两处提示词的数据槽位和“不计票/仅供核验”纪律。
5. 对应测试文件：优先扩展 `tests/test_evidence_summary.py`、`tests/test_evidence_citation_density.py` 和现有 trader/risk prompt 测试；不得删除或放宽既有断言。

不得在本卡修改 analyst 节点、辩论协议、数据库 schema、报告持久化、前端、custom_prompt guard、V-03/H1b、社交采集或生产配置。

## 5. 最小验收矩阵

### A. 共享提取器

- 七个来源均按固定标签出现；每个非空摘要来自对应 report，且不超过单项上限。
- 最终证据块不超过 2,400 字符；固定顺序稳定；空报告不产生“确认无数据”的伪事实。
- 机器块被剥离，数字、百分比、日期、负数和合法零值保留；摘要不调用 LLM。
- 同一 state 在 trader 与 risk_manager 得到字节级相同的证据块。

### B. trader / risk_manager 输入

- trader prompt 同时含研究经理方案和七源证据摘要；risk_manager prompt 同时含交易员方案和七源证据摘要。
- 只改 report 内容时，证据块随之改变；只改投资/交易方案时，证据块不被方案文本污染。
- 完整七份报告中的独特事实可在摘要中到达，但不得把完整报告原文全部透传。
- 现有 `tests/test_evidence_citation_density.py` 要升级为分别检查：研究经理、交易员和最终风控官各自能看到来源证据；不能只靠上游方案中的重复数字通过。

### C. 失败与门禁

- 上游 `INVALID_RUN`、`ABSTAIN`、`NO_TRADE`、`WAIT` 等状态仍在进入模型前阻断；新增摘要不能让 trader/risk 把不可执行状态改成 BUY/SELL。
- 资金流 guard、custom_prompt guard、关系图 guard 和既有确认闸测试全部保持通过。
- 不能出现按 analyst 数量加总方向、把 analyst `VERDICT` 当独立投票、或者从摘要自由补造数值的实现。

### D. 证据门

- 候选提交必须是当前主干的直接后代，提交完整 40 位 SHA、直接父、clean worktree、严格白名单和 `git diff --check`。
- 先由**代码审核员**对同一完整 SHA 只读复核；审查者不得改代码、合入、部署或写生产库。
- 通过审查后跑定向测试，再做与当前运行基线的同口径 RT-FULL；新增失败必须为 0。
- 本项不需要生产数据写入，也不自动触发真实分析。若要用生产分析证明 trace/报告/readback，必须另行取得明确的数据写入授权。

## 6. 暂不冻结的实现细节

组合函数的具体返回类型、是否附带 `missing_sources` 元数据、固定标签的中英文文案和总上限内的字符计算方式留给实施者在不改变上述契约的前提下决定。实施卡必须把这些选择写成可测试的具体值后才能开工。

## 7. 当前结论

P1-D 已按本设计完成实施、**代码审核员**同 SHA 复审、全量对照、线性合入和受控发布；当前剩余的是
真实业务路径的 trace/report/readback 证据，不是代码尚未接线。没有明确的数据写入授权时，不补生产样本。
