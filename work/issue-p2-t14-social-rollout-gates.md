# P2-T14：shadow / canary 守卫（修订后 Task 14）

## 目标

把 rollout 边界钉死并用专用测试锁住：

1. `disabled` 不触 archive（Gate 4 前 legacy 适配，不读 bundle）
2. `shadow` 产 bundle，但 **social 正文与最终方向**仍走 legacy；bundle 不得进方向证据
3. `active` + `TA_SOCIAL_CANARY_SYMBOLS` 非空时，**仅白名单**走 active；未命中不得 silently active
4. `active` 覆盖不足 / 失败 → 明确缺口，**禁止**回退 news/legacy

顺带修回顾审计 **M6**：plan §7 要求 shadow 的 trace `source_mode=legacy_proxy`；当前 `analyst_adapter` 写成 `source_mode="shadow"`，与 plan/disabled 语义漂移。本卡改为 **`legacy_proxy`**，并更新任何断言 `source_mode=="shadow"` 的旧测。

本卡**不**删 `legacy_proxy`（Gate 4 / Task 15）、**不**默认开 active、**不**部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`
- **新建**隔离分支，例如 `agent/dev2/p2-t14-social-rollout-gates`
- 禁止 FF / 部署 / `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 14 + §3.1 适配层 + §7 配置表 + Gate 2/3
- D-008 / D-009 / D-010
- 回顾 MED：**M6**（`work/audit-retro-p2-t5-t11.md`）

## 文件白名单

1. `tradingagents/dataflows/social/collector.py`（canary / disabled 不触库的 hardening，最小改）
2. `tradingagents/dataflows/social/analyst_adapter.py`（shadow `source_mode` → `legacy_proxy`；active 无 legacy 回退）
3. `tests/test_social_rollout_modes.py`（**新建**，本卡主测）
4. 若需同步：`tests/test_social_analyst_separation.py`（仅改 shadow `source_mode` 断言）
5. 可选极小：`tests/test_social_data_collector.py`（若 canary 断言需与本卡契约对齐）

禁止：删 legacy 分支、改辩论轮次、改 Gate 4 prompts 大删、部署、扩 MED M1–M5/M7（除非与本卡同一行无冲突的一行修复——默认不要）。

## 行为契约（必须可测）

### A. disabled

- collector：`mode=disabled` → `status=not_applicable`，`direction_allowed=False`；**不**实例化/调用 provider、不 open archive DB（用 mock/spy 证明）。
- adapter：正文仍为 legacy news/zt/hot；`source_mode=legacy_proxy`。

### B. shadow

- collector：可读 archive、聚合并在 context 中带 bundle（或等价持久化字段）。
- adapter：`human_content` **仍**含 legacy 字段（get_news / 涨停池 / 雪球等既有结构）；**不得**把 bundle 四段当唯一正文。
- `direction_allowed=False`。
- **`source_mode` 必须为 `legacy_proxy`**（对齐 plan §7；修 M6）。`mode` 字段仍可为 `shadow`。

### C. active canary

- `TA_SOCIAL_MODE=active` 且 canary 非空：
  - 命中白名单：允许走 archive 采集（仍受路径/数据质量约束）。
  - 未命中：不得 silently active；保持非 active（现有 `not_applicable` + `mode` 降为 disabled/非 active 可接受，但须测死「不会用 active bundle 驱动方向」）。
- canary 为空：表示全部 symbol 可走 active（现有语义，测一下）。

### D. active 不足不回退 legacy

- adapter `mode=active` 且 bundle empty/insufficient/failed：human 正文为缺口/不可判断类文案；**不得**注入 `get_news` / 涨停池 / 雪球 legacy 块。

## 测试要求（`tests/test_social_rollout_modes.py`）

至少：

1. disabled → provider 未被调用
2. shadow → context 有 bundle（或 collector 产出），adapter 正文仍 legacy，`source_mode=legacy_proxy`，`direction_allowed=False`
3. active + canary 未命中 → 非 active / 不 silently 用 bundle 作方向
4. active + canary 命中 → 允许进入采集路径（可用 tmp archive）
5. active + insufficient/empty → adapter 无 legacy news 块
6. 回归：修正后 `test_social_analyst_separation` 中 shadow 断言与 `legacy_proxy` 一致

pytest 命令示例（无代理）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q \
  tests/test_social_rollout_modes.py \
  tests/test_social_analyst_separation.py \
  tests/test_social_data_collector.py
```

## 交付

- 单关注点 commit：`feat(social): add shadow and canary rollout gates`
- **先 push 远端分支**，再评论；`git ls-remote` 必须可见完整 40 位 tip
- 父提交须为 `7876f1c…`
- 完整 SHA + pytest 精确数字 → `in_review`
- 不自行 FF；不 @调度助手合入；等独立审核 + Cursor「准予合入」

## 明确不做

- Task 15 / Gate 4 删除 legacy
- 真实外网 canary 放量
- 部署 / 改默认 `TA_SOCIAL_MODE`
