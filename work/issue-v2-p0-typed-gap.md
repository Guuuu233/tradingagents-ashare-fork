## 真实生产复现

- trunk/service：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 真实账户报告：`af8f029a1ab842eca7ba80d43ec882d8` / `601899.SH`
- `market_data_context.data_failure_ledger` 中 shareholder_count 已是结构化：status=`failed`；`source_provenance.shareholder_count` 也有 requested_as_of/status/gap。
- 但最终顶层 `data_gaps` 仍出现裸文案：`【数据获取失败】shareholder_count：provider call failed`。
- 其他条目已能显示 `data source refused` / `data source unavailable` / `未返回可验证数据日期`。

## 目标与边界

从当前 target trunk 开新分支，严格 TDD。只允许修改：
- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector.py`（或当前同一 gap 合并契约测试文件，仅一个）

禁止修改 report_service、providers、API、debate、数据库 schema、前端、配置、用户模型/providers/role bindings/API Key、主干和服务。

## RED 测试

用 production-reachable fixture 复现：结构化 ledger/provenance 的 source=`shareholder_count`, status=`failed`, reason 为上游异常类别或明确失败原因。最终合并后的 gap 必须：
1. 不含裸英文 `provider call failed`；
2. 保留 source 名称；
3. 输出 typed、可理解的失败类别/原因（如 provider_failed/数据源调用失败 + 原始可审计 reason）；
4. 不把 failed 改成 refused/unavailable/empty；
5. 既有 refused/unavailable/as-of gap 不回归；
6. ledger 与 provenance 原结构不被覆盖。

先运行测试确认 RED，再最小修复 GREEN。不得仅在该股票硬编码；修复统一 gap 格式化/合并源头。

## 验收

- RED→GREEN 证据；
- 完整 `tests/test_data_collector.py` 与相关 data gap tests 全绿；
- compileall、git diff-check；
- 推送远端 branch/SHA，等待独立复审；不合入、不重启、不上线。
- 明确 changed files、测试计数和未完成状态。不要 mention 项目调度助手。