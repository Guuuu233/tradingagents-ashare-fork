## 背景

P3-H1b 已知缺口：`research_manager` 调用 `calculate_claim_credit_weights` 时写死 `system_gate_passed=False`，导致即使打开 `credit_weighting_enabled` 仍全员平权。

## 交付（宿主已提交）

- tip 候选：本卡创建后以宿主 commit 为准（`fix(debate): research_manager 按 H1b 门槛实时评估 system_gate_passed`）
- `resolve_claim_credit_weights_for_manager`：空/`h1b_gate_samples` 缺失 → FAIL/KEEP_FALSE；门槛通过 → 非平坦权重
- 辩论状态写入 `credit_weight_audit` 可审计字段
- `tests/test_h1b_gates.py` 新增接线用例；相关套件全绿
- **不**默认打开 flag；不改 3/1 / 模型绑定

## 后续

线性 FF + 部署另卡。运行时需在 state 注入 `h1b_gate_samples`（周评/门槛样本）后 flag 打开才可能真正加权。
