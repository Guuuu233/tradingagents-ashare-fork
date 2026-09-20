# Track A12：独立代码审核（只读）tip 98fe5d1

## 候选 tip（完整 40 位，必须对照）

`98fe5d199e8874ae829d2b492882d82339c836f0`

分支：`origin/agent/dev2/a12-t5-due-inference`  
基线：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`  
实现卡：DAV-562

## 审核范围

- `tradingagents/agents/utils/shadow_credit.py`
- `scripts/verify_h1b_gates.py`
- `scripts/backfill_tplus5_shadow.py`
- `tests/test_h1b_gates.py`

## 契约

1. `is_t_plus_5_due is None` 时可用 `t_plus_5_date` / `trade_date→calculate_t_plus_5_date` 相对 `as_of` 推断 due。
2. 解析失败不臆造 due；显式 false / pending / suspension 仍排除。
3. due 且 hit=None → 进分母、不进 completed。
4. A11：真正 `due_count==0` → rate=0 / FAIL / `no_due_samples`。
5. 不开加权、不缩短 hold、不改 0.95 阈值。

## 禁止

改代码 / FF / 部署 / @调度助手合入。PASS ≠ 准予合入。

书面 ✅ / ⚠️ / ❌，含路径与行号。
