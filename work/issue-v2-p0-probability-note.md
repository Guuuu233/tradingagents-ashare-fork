## 真实生产复现

- trunk/service：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 真实账户报告：`af8f029a1ab842eca7ba80d43ec882d8` / `601899.SH` / `2026-08-21`
- 报告 completed，confidence=68、target=36.8、stop=33.0，但 probability=NULL。
- DB 原始 trader_investment_plan、final_trade_decision、judge_decision、manager_verdict 均没有“概率/胜率”文本，NULL 本身语义允许。
- 阻断：result_data 与顶层均没有 `extraction_warning` 或 `extraction_note`，属于静默字段缺失，不符合“语义允许为空必须有 note，提取失败必须有 warning”。

## 目标与边界

从当前 target trunk 精确基线开新分支，严格 TDD。只允许修改：
- `api/services/report_service.py`
- `tests/test_verdict_extraction.py`（或一个现有同功能 report_service 测试文件）

禁止修改 data_collector、providers、debate、API main、数据库 schema、前端、配置、用户模型/providers/role bindings/API Key、主干和服务。

## RED 测试

构造与真实报告相同语义：
1. decision=BUY，confidence 可提取，target/stop 可提取；
2. 所有正式文本均无 probability/胜率；
3. 预期 probability 保持 None；
4. result_data/structured fields 必须写明确 note 或 warning，至少指出“概率未提供/未提取”，不得写默认 0、不得从 confidence 映射；
5. confidence 已成功时不得误写“置信度也缺失”；
6. 现有 HOLD 无目标价 note 行为不回归。

先运行测试并记录预期 RED，再做最小实现 GREEN。需要追踪 structured fields 如何持久化进 result_data，修在源头，不只改顶层 DB 列。

## 验收

- 新回归测试 RED→GREEN 证据；
- `tests/test_verdict_extraction.py`、相关 report_service/字段语义测试全绿；
- compileall、git diff-check；
- 推送远端 branch/SHA，等待独立复审；不合入、不重启、不上线。
- 交付必须明确 changed files、测试计数和未完成状态。不要 mention 项目调度助手。