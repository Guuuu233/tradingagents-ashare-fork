# P1-M 单问题返修：将已审核工具接入生产 result_data

## 精确基线

- 父候选：`target/agent/1/01a03077-dav367@33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- 目标主干仍为：`50e115347b49bcb9e767c593296045a356099006`
- 原候选的 6 文件离线工具与测试保留；禁止重写指标算法、禁止复制宿主备份。

## 已复现阻断

在精确 SHA `33c6e6b`：

```python
s = Propagator().create_initial_state('601318.SH','2026-08-21')
inv = s['investment_debate_state']
```

实际得到：`protocol_version/protocol_stage/feature_flags/data_utilization_metrics/...` 全部为 `None`。
全仓搜索显示 `get_protocol_metadata`、`calculate_all_debate_metrics` 只有工具模块与 tests 引用，没有生产挂载点。因此新报告不会实际出现 P1-M 元数据/指标，违反 P1-M 验收。

## 严格允许范围

只允许修改：
- `tradingagents/graph/propagation.py`
- `tradingagents/graph/trading_graph.py`
- `tests/test_debate_protocol_metadata.py`（新增生产可达集成测试）

如需另一测试文件，先停止并报告。禁止修改原 6 文件、api/main.py、researcher、conditional_logic、research_manager、prompt、DB schema、前端、provider、配置和用户设置。

## 严格 TDD

### RED 1：初始状态默认元数据

从父候选 fresh checkout，先写测试：
- `Propagator.create_initial_state` 的 `investment_debate_state` 必须含：
  - protocol_version=`v1_legacy`
  - protocol_stage=`opening`
  - tiebreak_skipped=false
  - debate_degenerate=false
  - data_utilization_metrics={}
  - challenge_verification=[]
  - shadow_credit_metrics={}
  - feature_flags={v2:false, shadow:true, weighting:false}
- 先用宿主 `.venv310` 运行并确认当前候选 RED（实际字段 None/缺失）。

最小 GREEN：复用 `DEFAULT_PROTOCOL_METADATA`/规范 helper 做**深拷贝**初始化，禁止手写第二套散落常量；不得改变 count/history/round_goal/路由。

### RED 2：生产 result_data 挂载

构造最小 completed final_state，直接调用 `TradingAgentsGraph._build_horizon_result`：
- 返回 result 的 `investment_debate_state` 中有规范元数据；
- 顶层/或规范约定位置可读取 `protocol_version=v1_legacy`、feature_flags；
- `data_utilization_metrics` 必须由 `calculate_all_debate_metrics` 的真实纯函数输出，而不是空占位；每个指标有 numerator/denominator/rate/version；
- legacy challenge 为 `legacy_no_data`；
- 不修改 final_state 原对象（observer纯函数）；
- flag关闭时 result的既有字段、message/count/history 和路由相关值逐字保持。

先确认 RED，再最小 GREEN：在 `_build_horizon_result` 终点只读规范化 metadata + 计算 metrics，挂入 result/debate state。不得调用 LLM/网络/DB，不改变图边和 prompt。

## 验收

- 真实 RED→GREEN顺序与输出；
- 新集成测试、原 22 个 P1-M tests、legacy 辩论回归全绿；
- 复跑 `tests/test_debate_rounds_configuration.py`, `tests/test_debate_state_persistence.py`, `tests/test_debate_seven_reports_and_protocol_gate.py`, `tests/test_debate_bundle_wash.py`, `tests/test_debate_e2e_protocol_repair.py`；
- replay、compileall、diff-check；
- 宿主 `.venv310` 全量 `pytest tests/`；
- 推送新远端 branch/SHA，等待独立复审；禁止主干、重启、上线。

变更文件必须恰好 3 个。不要 mention 项目调度助手。