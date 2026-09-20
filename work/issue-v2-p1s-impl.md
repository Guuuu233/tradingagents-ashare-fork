# P1-S 实施：H1a 影子指标写入（零加权）

## 基线

- fresh isolated checkout：`target/codex/dav-4-p2a-trunk@0554216305b3c860cbe893681335b6b1a29e17ef`
- 禁止宿主脏树；独立 worktree；宿主 `.venv310` 跑相关测试。
- 不得合入主干、不得重启、不得改 3/1。

## 允许文件

- 新增模块（建议）`tradingagents/agents/utils/shadow_credit.py`：只读输入、纯函数计算、可重算。
- 在报告落库前填充 `shadow_credit_metrics` 的最小挂钩。优先 `api/services/report_service.py` 的 canonicalize / persist 路径；若必须动 `tradingagents/graph/trading_graph.py`，只允许在已有 `shadow_credit_metrics` 赋值点调用纯函数，禁止改辩论拓扑。
- 对应单测：`tests/test_shadow_credit*.py`，以及必要时扩展 `tests/test_debate_protocol_metadata.py` / `tests/test_debate_state_persistence.py` 的既有空 dict 断言为“可填充但不加权”。

禁止修改：

- `debate_utils.py`、`bull_researcher.py`、`bear_researcher.py`、`research_manager.py`、`conditional_logic.py`、`zh.py`/`en.py`
- `frontend/**`
- 用户配置、DB schema / 新表、`credit_weighting_enabled` 默认值（必须保持 false）

`api/main.py` 原则上不动。若发现非改不可，先停并报告。

## 指标（每份报告）

`shadow_credit_metrics` 必须含：

- `schema_version`（例如 `h1a_json_v1`）
- `credit_weighting_enabled=false`
- bull_verified_rate / bear_verified_rate
- bull_challenge_adoption_rate / bear_challenge_adoption_rate
- analyst_utilization_by_role
- manager_evidence_coverage
- manager_consistency_gate_triggered
- t_plus_5_direction_hit：窗口未到为 `null`，不得记失败
- sample_count
- protocol_version
- model_id × stance（缺模型则 typed 缺失，禁止编造）

计算必须可对同一 `result_data` 重放得到相同 JSON（忽略运行时间戳）。

## 硬规则测试

先 RED 再 GREEN：

1. 填充后 `feature_flags.credit_weighting_enabled is False`。
2. manager prompt / 裁决输入不含信用分数字（对 manager 节点做断言或 prompt 捕获）。
3. T+5 未到 → `t_plus_5_direction_hit is None`。
4. 缺行情 → typed gap，不得填 0。
5. 同一份 fixture 关闭采集 vs 开启采集：`final_trade_decision` / 方向 / 仓位字段一致。
6. 旧报告缺字段 canonicalize 不崩，metrics 可为 `{}`。

不要跑无关全量；相关 pytest 必须绿。

## 交付

推独立远端 branch + 精确 SHA。评论含基线/候选 SHA、文件、测试、未合入/未重启。不要 mention 项目调度助手。完成后 mention：

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)
