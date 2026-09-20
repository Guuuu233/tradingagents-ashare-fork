编排侧独立复跑 `.venv310`：`tests/test_evaluation_contracts.py` 当前 **6 failed / 7 passed**。请就地修，勿另开平行实现。

## 根因（已复现）

1. **Pydantic 类型错位（主因，拖垮 roundtrip / weekly 校验）**  
   `quadrant_3_debate_quality.challenge_metrics.challenge_evidence_status` 被建成 `MetricValueModel`（`status`/`note` 期望 int），实际写入的是 `status='valid'|'zero_denominator'`、`note=str|None`。  
   → `ValidationError: status Input should be a valid integer`。  
   **修法**：该字段改用独立 status 结构（或复用已有非 MetricValue 的 status 模型），不要塞进 `MetricValueModel`。

2. **断言过严**  
   `seven_reports_utilization` / `evidence_recycling_rate` 在 mock 缺分母时返回 `zero_denominator`，测试却断言 `valid`。要么补齐 mock 分母，要么断言接受 `zero_denominator`（契约语义：零分母不得伪造 rate）。

3. **`render_weekly_summary_markdown` NameError**  
   使用了未定义的 `struct_total`（约 L1792）。改为与聚合结果一致的变量名。

## 约束提醒

- 基线 tip `93212809a099ebc6e5787bc0468e63969f5496f8`
- 禁止改业务辩论主链 / 默认打开 `credit_weighting_enabled` / 自行 FF / 重启
- 绿后贴精确 SHA + `.venv310` pytest 证据，再交独立审核

[@资深开发1](agent://6050b57e-f551-4756-8ad9-3af522d7d4e3)
