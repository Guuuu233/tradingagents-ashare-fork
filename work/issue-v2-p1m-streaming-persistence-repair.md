## 精确基线与真实生产证据

- 当前目标主干/运行服务：`f89b6009544a60727499a02f2e7c585502802801`
- 真实报告：`fa50d0d8e7f744f0b9e03f4c0a1322e3`，`600030.SH`，completed，legacy 6条消息正确。
- 递归DB审计：整份result_data中以下字段全部不存在：protocol_version、protocol_stage、feature_flags、data_utilization_metrics、challenge_verification、shadow_credit_metrics、tiebreak_skipped、debate_degenerate。
- 真实请求路径：`stream_events=True`单horizon → `graph.graph.astream` → `result = _build_result_payload(final_state)` → create_report；未调用`graph._build_horizon_result`，因此P1-M挂载被绕过。
- 现有`tests/test_debate_state_persistence.py::test_single_horizon_persists_debate_states_to_result_data`覆盖该流式落库路径，但未断言P1-M字段。

## 严格范围

只允许修改：
- `api/main.py`
- `tests/test_debate_state_persistence.py`

禁止其他文件、DB schema、配置、用户设置、prompt、researcher、conditional_logic、manager、provider、前端、P1-B协议逻辑。

## TDD

### RED

在现有`test_single_horizon_persists_debate_states_to_result_data`基础上扩展真实可达断言，或新增同文件窄测试：

1. Fake astream final state使用`Propagator.create_initial_state`兼容形状，或显式包含v1默认metadata/flags和真实round_messages；禁止构造生产不可能状态。
2. 在修复前，job result和`capture_create_report`的`result_data`必须因缺以下字段而RED：
   - protocol_version=`v1_legacy`
   - protocol_stage=`opening`
   - feature_flags={v2:false, shadow:true, weighting:false}
   - data_utilization_metrics非空、含evidence_recycling/seven_reports_utilization/field_completeness/challenge_metrics
   - challenge legacy_no_data
   - 6条round_messages保留
3. 旧legacy直接调用`_build_result_payload`且nested state无P1-M key时，nested state必须逐字保持，但顶层默认metadata可读；不能破坏现有测试。
4. 输入`investment_debate_state`原对象不得被P1-M新增逻辑就地修改。

先运行精确测试确认RED，失败必须因P1-M字段缺失，不是fixture/API初始化错误。

### GREEN

最小实现：

- 在`api/main.py`导入并复用现有：
  - `get_protocol_metadata`
  - `calculate_all_debate_metrics`
- `_build_result_payload`先构造当前payload，再对payload做只读规范化与指标计算；实现语义必须与`TradingAgentsGraph._build_horizon_result`一致：
  - 顶层永远挂载规范metadata/flags/metrics；
  - 仅当原nested state已含P1-M keys（生产新state）时，在nested浅拷贝中挂载；legacy fixture无key时nested逐字不变；
  - 不修改`final_state`及原nested state；
  - 计算一次，无LLM/网络/DB；无自嵌套。
- 不复制一整套默认常量或指标算法；只调用既有helpers。

## 验收

- 真实RED→GREEN；
- 精确流式持久化测试、整个`tests/test_debate_state_persistence.py`；
- P1-M 38项、job lifecycle相关流式测试、report/p2b/graph关键矩阵；
- replay、compileall、diff-check；
- 不跑全量，最终组合统一全量；
- changed files恰好2个，推远端branch/SHA；
- 未合入/未重启/未上线；3/1不变，P1-B锁定。

不要 mention 项目调度助手。