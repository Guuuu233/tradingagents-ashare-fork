# H1b flag 批准记录（2026-08-26）

- 用户：批准开启 `credit_weighting_enabled`
- 实时门槛：`scripts/verify_h1b_gates.py` → **KEEP_FALSE / FAIL**
  - N：FAIL（单标的占比 ~45% >15%；行业数 0）
  - Side：FAIL（多/空样本 20/3，verified claims 0）
  - Time：FAIL（交易日 27 <30）
  - Balance：FAIL（多头占比 ~87%）
  - Bias / Magnitude / T+5：PASS
- 处置：按 D-006 **不开 flag**；批准记为 D-007（门槛 PASS 后可直接开）
- tip/healthz：以当时部署 tip 为准
