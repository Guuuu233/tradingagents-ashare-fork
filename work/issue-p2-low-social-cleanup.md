# P2-LOW：社交总审残留清理（L1–L4）

来源：`work/audit-recent-social-20260901.md`（无 HIGH；L5 已在 MED3 R3 消掉）。

## 基线

主干 tip：`0cda99b6072116874b7a458432d0c7bc7b0a29e3`（开工前 `git ls-remote` 核验）。  
分支建议：`agent/dev2/p2-low-social-cleanup`。  
**每个 L 单独 commit。** 禁止 Gate4 / 删 `legacy_proxy` / 部署。

## L1 — 适配层虚构 cutoff 展示

- 现象：`analyst_adapter.py` / `prompt_formatter.py` 在缺 cutoff 时捏造 `{date}T15:59:59Z`；空 bundle 还用 ad-hoc `social_empty_context`
- 期望：未知 cutoff 时用 `compute_as_of_cutoff`（或省略 / 明确 unknown），禁止硬编码 15:59:59；reason 走既有正式码
- 文件：`tradingagents/dataflows/social/analyst_adapter.py`、`prompt_formatter.py` + 测

## L2 — `parse_iso_datetime` 过宽 except

- 现象：`provider.py` 捕获裸 `Exception` 后吞掉
- 期望：仅 `(ValueError, TypeError, OverflowError)`；解析失败仍返回 `None` 并保持可测
- 文件：`tradingagents/dataflows/social/provider.py` + 测

## L3 — 字符串 sort `published_at`

- 现象：`SocialArchiveProvider._sort_and_limit_records` 用字符串排序；aggregator 已用 `parse_iso_datetime`
- 期望：与 aggregator 一致，按解析后的 datetime（失败行不得静默冒充最新）
- 文件：`provider.py` + 测

## L4 — verifier 把 mode 当 status

- 现象：`evidence_verifier.py` 把 `disabled`/`shadow` 塞进 status 集合，与 mode 语义混用
- 期望：status 集合不含 mode token；继续依赖 `direction_allowed` / 真实 status；若需 mode 则读独立字段
- 文件：`tradingagents/agents/utils/evidence_verifier.py` + 测

## 不做

- L5（已修）
- Gate 4 / T15b / 删 legacy
- 部署
- 无关 dirty 文件（`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`）

## 交付

1. 先 push 远端分支，再报每个 commit 的完整 40 位 SHA
2. pytest（相关模块）证据
3. 卡 → `in_review`，等独立审核 + Cursor 准予合入
