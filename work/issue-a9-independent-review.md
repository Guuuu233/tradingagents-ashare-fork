# Track A9：独立代码审核（只读）tip 4493177

## 候选 tip（完整 40 位，必须对照）

`4493177eeefa4a7aabfc05904c156ccb0106d06e`

分支：`origin/agent/dev2/a9-h1b-gates-db-path`  
基线：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`  
实现卡：DAV-553

## 审核范围

- `scripts/verify_h1b_gates.py`
- `tests/test_h1b_gates.py`（及相关新增用例）

## 契约

1. 传入 `--db-path` / `db_path=` 时必须只读打开该 SQLite，不得静默 golden。
2. 路径不存在 / 无法打开 → 明确失败（非零退出），禁止回退 golden。
3. 未传 `db_path` 时保留原回退链。
4. v2 过滤口径不变；不开加权、不改阈值。

## 禁止

改代码 / FF / 部署 / @调度助手合入。PASS ≠ 准予合入。

书面给出 ✅ / ⚠️ / ❌，含路径与行号。
