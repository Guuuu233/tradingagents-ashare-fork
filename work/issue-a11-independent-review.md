# Track A11：独立代码审核（只读）tip aa2750f

## 候选 tip（完整 40 位，必须对照）

`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`

分支：`origin/agent/dev2/a11-t5-no-vacuous-pass`  
基线：`ccda1be9c96e4d9a5f334fa03280342badeb4306`  
实现卡：DAV-559

## 审核范围

- `tradingagents/agents/utils/shadow_credit.py`
- `tests/test_h1b_gates.py`

## 契约

1. `due_count == 0` → `completeness_rate=0.0`、`passed=False`；禁止因 N≥60 虚高 100%/PASS。
2. 可选 `reason=no_due_samples`。
3. `due_count > 0` 行为不变（≥95% PASS / <95% FAIL）。
4. 不开加权、不改阈值数值本身。

## 禁止

改代码 / FF / 部署 / @调度助手合入。PASS ≠ 准予合入。

书面 ✅ / ⚠️ / ❌，含路径与行号。
