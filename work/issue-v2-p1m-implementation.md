# v2 Phase P1-M：协议版本、Feature Flags、指标计算器与离线 A/B 基础设施

## 精确基线

- 项目：`/Users/davidliu/Documents/TradingAgents-AShare`
- 远端目标主干：`target/codex/dav-4-p2a-trunk@50e115347b49bcb9e767c593296045a356099006`
- Phase 0 已闭环：宿主全量 `1852 passed, 1 skipped, 0 failed`；运行服务 SHA=`50e1153`；真实账户保持 3/1。

## 阶段边界

本卡只实施 P1-M，不改变现网辩论 prompt、节点路由、消息数量或 3/1 配置。严禁实施 P1-B opening/challenge/tiebreak，严禁修改用户模型、role bindings、providers、backend URL、API Key。

## 目标数据结构

在 result_data / investment_debate_state 的兼容位置支持：

```json
{
  "protocol_version": "v1_legacy|v2_structured_disagreement",
  "protocol_stage": "opening|challenge|tiebreak|manager",
  "tiebreak_skipped": false,
  "debate_degenerate": false,
  "data_utilization_metrics": {},
  "challenge_verification": [],
  "shadow_credit_metrics": {},
  "feature_flags": {
    "v2_debate_enabled": false,
    "shadow_credit_enabled": true,
    "credit_weighting_enabled": false
  }
}
```

旧报告字段缺失时必须读取为 `v1_legacy`。优先放 result_data，避免过早扩数据库列；如确需 schema 变更，先停止并报告，不得自行扩表。

## 严格 TDD 垂直切片

### M1. 协议元数据默认值与兼容序列化

先写 RED：
- 旧 result_data 无字段时读取为 v1_legacy；
- 新字段序列化/反序列化不丢失；
- feature flags 默认：v2=false、shadow=true、credit_weighting=false；
- flag 关闭时现有 legacy 6 条辩论/现有图路由与输出不变。

再做最小 GREEN。禁止改 prompt、researcher、conditional_logic、research_manager 的 v2 行为。

### M2. 纯函数指标计算器

每项必须输出 `numerator`、`denominator`、`rate`、`version`，分母为 0 时使用 typed note/status，禁止伪造 0%：
- 数字级证据回收率；
- 七报告数据利用率；
- macro 利用率；
- fundamentals 利用率；
- bull/bear verified rate 与差值；
- challenge 数量、采纳率、evidence status（legacy 无数据）；
- 字段完整率（confidence/probability/target/stop，允许为空时识别 note/warning）。

使用手工小 fixture，数值可复算；不得用三只 golden 调参。

### M3. 离线 A/B harness

- 输入同一份完整 result_data；
- 分别运行 legacy evaluator 与 v2 evaluator；
- 模型调用必须 mock/禁用，不调用线上模型；
- 比较结构指标，不做主观文笔评分；
- 三只 golden 只作回放输入，不改变 fixture。

## 允许范围与文件所有权

先完整读调用点，再选择最小文件范围。建议优先：
- `tradingagents/agents/utils/agent_states.py`
- 新增一个明确命名的纯指标模块（不得 `_v2`/`_new` 并行替代旧路径；若新增模块只用于独立 evaluator 可命名 `debate_metrics.py`）
- 报告序列化所在真实文件
- 对应 tests
- 离线 harness 放 `tests/` 或 `scripts/`，不得接线上 API

禁止修改：
- `bull_researcher.py`、`bear_researcher.py`
- `conditional_logic.py`
- `research_manager.py`
- `tradingagents/prompts/zh.py`
- `api/main.py`
- 前端
- 数据 providers
- 用户配置/DB数据

若实际序列化必须改 `api/main.py`，立即停止并回报，由 Hermes另设单一 owner，不能越界。

## 验收

- 严格 RED→GREEN 原始证据；
- 新 P1-M tests 全绿；
- legacy 协议回归测试全绿；
- 原 Phase 0 关键矩阵、replay、diff-check、compileall；
- 宿主 `.venv310` 全量 `pytest tests/`；
- 推送远端 branch/SHA，等待独立复审；
- 不合入主干、不重启、不上线。

统一交付：基线 SHA、分支、候选 SHA、变更文件、测试命令/结果、已验证功能、已知限制、未合入/未重启/未上线。不要 mention 项目调度助手。