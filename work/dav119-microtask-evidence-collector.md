# DAV-119 微任务：保留 fallback 的结构化 evidence

## 精确基线

远端 target 主干：`codex/dav-4-p2a-trunk`

SHA：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`

只读该精确 SHA；不要读取 DAV-118/DAV-119 历史评论，不复用旧 run，不修改配置、providers、模型绑定、API Key、主干或凭据。

## 唯一问题

当前 `DataCollector._fetch_all()` 在 `fund_flow_individual` 返回 `FundFlowText` 但 evidence 为空时，只重新构造通用 gap：`未返回结构化逐日 netamount/r0_net evidence`，丢失 provider 已保存的 `fund_flow_evidence_meta`，包括 fallback 尝试链、东财 typed gap、最终来源和具体 reason。

当前 `smart_money_analyst.py` 的无 collector fallback 分支也把 `fund_flow_evidence` 直接设为空，无法保留工具返回对象上的结构化 metadata。

## 只做此微任务

只允许修改：

- `tradingagents/graph/data_collector.py`
- `tradingagents/agents/analysts/smart_money_analyst.py`
- 一个已有资金流/collector 测试文件

实现要求：

1. 当 provider 返回带 `fund_flow_evidence_meta` 的 `FundFlowText` 且 evidence 为空时，collector 的 `market_data_context.fund_flow_evidence` 必须保留脱敏 metadata（至少 `source/status/requested_as_of/reason/gap/attempted_sources/fallback_errors/em_typed_gap` 中实际存在的字段），不能覆盖成通用 reason。
2. 同一失败必须继续进入 `data_failure_ledger`，ledger 的 reason/gap 使用保留后的结构化失败信息。
3. 只有工具返回对象确实携带 metadata 时，smart-money fallback 才提取它；不能凭展示文本猜 evidence，也不能引入新来源或改变 fallback 顺序。
4. 不修改资金流 provider、累计窗口、算法字段资格、legacy Web/App manual 语义。
5. 补最小回归：collector/analyst fallback 至少各一条断言，验证 metadata 和 ledger 具体 reason 被保留。

## 验收

必须从上述 SHA 产生并推送新远端 branch/SHA，报告实际改动文件和精确测试结果。执行：

- `.venv310/bin/pytest` 精确测试；
- changed modules `compileall`；
- `git diff --check`；
- 脱敏结构化输出，证明 `reason/gap/attempted_sources/fallback_errors` 没有被通用 gap 覆盖。

如再次 context-window 400，立即停止并改派资深开发2，不重放旧上下文。新 SHA 经远端核验、代码审核、回归和真实 smoke 前，不解锁后续阶段。

当前已确认的未完成质量门：累计窗口重复/交易日连续性、manual_calibration_gap ledger/provenance、真实新算法源可用性；本微任务不得宣称它们已完成。
 in_progress 派工后应立即出现新 run；若无 run，项目主管需点名唤醒。

参考文件：
`tradingagents/graph/data_collector.py:708-742`
`tradingagents/agents/analysts/smart_money_analyst.py:60-75`
`tradingagents/dataflows/fund_flow_evidence.py:66-79,518-543`
