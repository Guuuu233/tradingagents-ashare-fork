# P2-T15b / Gate 4：删除 legacy_proxy（用户已批准「开 Gate4」）

## 授权

David / Cursor 于 2026-09-02 明确 **「开 Gate4」**。  
权威：`docs/social_data/implementation_plan.md` Gate 4 + Task 15 删除项 + 矩阵 **T-H4**。

## 基线

优先以 **A5 合入后的主干 tip** 为父（预期 `d2f8aa05579d0520abe942972889a82060ea65e7` 或其已 FF 后的 `codex/dav-4-p2a-trunk` tip）。  
开工前 `git ls-remote` 核验；若 A5 尚未上主干，从 `origin/agent/dev2/a5-tplus5-shadow-backfill` @ `d2f8aa0…` 建分支，勿基于过时的 `46a6dfe…` 丢 A5。

分支建议：`agent/dev2/p2-t15b-gate4-remove-legacy`。  
**独立单 commit**：`refactor(social): remove legacy social proxy after activation`

## 只做这件事

1. **删除** `legacy_proxy` 适配臂与符号（产品代码中不得再出现 `source_mode="legacy_proxy"` / legacy 新闻冒充社交路径）
2. `TA_SOCIAL_MODE=disabled` → 社交 **`not_applicable` / 不可用**，**不再**回退 news / zt_pool / hot_stocks
3. 清掉旧 social prompt 中把新闻当社交的措辞（`prompts/zh.py` / `en.py`、`social_media_analyst.py` 相关）
4. 更新测试：凡断言 disabled/shadow → `legacy_proxy` 的用例改为 Gate4 后契约（T-H4：legacy 符号不存在；disabled=`not_applicable`）
5. 保留 mode 开关与 shadow/active/canary 能力；**不要**默认改成 production active 全量（默认仍可为 `disabled`，只是语义变了）

## 主要文件（预期）

- `tradingagents/dataflows/social/analyst_adapter.py`
- `tradingagents/agents/analysts/social_media_analyst.py`
- `tradingagents/prompts/zh.py` / `en.py`（仅社交/新闻分离相关）
- `tests/test_social_rollout_modes.py`
- `tests/test_social_analyst_separation.py`
- `tests/test_social_e2e_acceptance.py`
- 其它被 `legacy_proxy` 字符串绑死的测例

## 验收

- `rg legacy_proxy tradingagents/` → 无产品路径命中（测试若需可仅出现在「禁止回归」注释/负向断言）
- disabled：不触 archive；status/`not_applicable`；无新闻替代社交
- shadow：仍可读 archive / 持久化 bundle，但 **不得**再用 legacy 新闻正文冒充；`direction_allowed=false`
- active 行为不回退新闻
- 定向社交 pytest 全绿；报告精确数字

## 禁止

- 部署 / 「准予部署」
- 开 `credit_weighting_enabled`
- 改辩论 3/1、用户模型绑定
- 脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 混入 A5/校准无关改动
- @调度助手催合入

## 交付

先 push；完整 40 位 tip + pytest → `in_review`。  
必须独立审核 + Cursor「准予合入」后才 FF。
