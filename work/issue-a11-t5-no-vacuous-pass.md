# Track A11：T+5 完整率禁止 `due=0` 虚高记 100%

## 背景（Cursor 实测 2026-09-02）

主干 tip：`ccda1be9c96e4d9a5f334fa03280342badeb4306`。

`evaluate_h1b_system_gates`（`tradingagents/agents/utils/shadow_credit.py`）在 `due_t5_count == 0` 时：

```python
t5_completeness_rate = 1.0 if sample_count >= cfg["min_sample_count"] else 0.0
pass_d4 = False if sample_count < cfg["min_sample_count"] else True
```

本地库（`--db-path` 已兑现）实测：合格 v2=69，却打印  
`T+5 完整率: [ PASS ] 完整率=100.0%/95.0% (已评估=0/到期=0)`。

这与 D-006 / `work/p3-h1b-activation-gates-draft.md` §2.3 矛盾：完整率分母是「已满 T+5 的样本数」。分母为 0 时比率未定义，**不得**虚高 PASS。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `ccda1be9c96e4d9a5f334fa03280342badeb4306`
- 分支建议：`agent/dev2/a11-t5-no-vacuous-pass`
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- **单关注点 commit。** 不要 FF / 部署。

## 只做这件事

1. 当 `due_count == 0`：`completeness_rate = 0.0`（或显式 `None` 但矩阵须可测），**`passed=False`**。不得因 `sample_count >= 60` 记 1.0 / PASS。
2. 细节字段保留 `due_count=0`、`completed_count=0`；可选增加 `reason=no_due_samples`（若加字段须测）。
3. `due_count > 0` 时行为不变：`completed/due >= 0.95` 才 PASS。
4. 定向测试：
   - N≥60 但全部未到期 / 无 due → Dimension 4 **FAIL**，rate=0
   - due>0 且完整率≥95% → 仍 PASS（回归）
   - due>0 且完整率<95% → 仍 FAIL（现有用例）

## 明确不做

- 改 `min_t_plus_5_completeness` 阈值本身
- 开加权 / 部署 / schema / A0
- 生产库 T+5 实写回填（另授权）
- 社交 / 脏文件 trio

## 验收

- `tests/test_h1b_gates.py`（及必要 shadow_credit 测）绿
- push → 完整 40 位 tip → `in_review`；D-010

## 权威

D-006 / D-007；H1b 门槛草案 §2.3；A9/A10 门槛诚实性续作。
