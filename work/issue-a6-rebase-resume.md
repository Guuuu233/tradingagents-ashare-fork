# Track A6 续作：rebase 到当前主干后重新交付

## 现状（Cursor 核验 2026-09-01）

- 旧交付：`agent/cursor/a6-h1b-gates-v2-only` @ `f2905da538008ec32f01cda395dc5cc5ab4ec8f0`
- 旧基线：`aa41f449…`（已过时）
- **该 commit 不是**当前主干 `codex/dav-4-p2a-trunk` @ `9d5d53dd89bed6dcbfa7dd3aa991771993dddfea` 的祖先 → **未合入**
- 旧独立审核 PASS **不**转移到 rebase 后的新 tip（D-010：精确 SHA）

## 只做这件事

1. 从当前主干 tip `9d5d53dd89bed6dcbfa7dd3aa991771993dddfea` 建/更新隔离分支（可沿用 `agent/cursor/a6-h1b-gates-v2-only` 或 `agent/dev2/a6-h1b-gates-v2-only`）
2. 将 A6 改动 **rebase / cherry-pick** 到该 tip：
   - `scripts/verify_h1b_gates.py`
   - `tradingagents/agents/utils/shadow_credit.py`（仅 v2 过滤 / industry 提取 / 门槛矩阵相关）
   - `tests/test_h1b_gates.py`
3. **尽量不要**把生成物 `work/h1b_gates_report.json` 带进 commit（宿主脏文件敏感）；脚本跑通即可，报告可本地留痕不提交
4. 一个关注点一个 commit（A6 本身）；先 push 再交付完整 40 位 tip SHA
5. 定向 pytest：`tests/test_h1b_gates.py`（及相关若触及）

## 契约（不变）

- 只计 completed + v2 structured / 规范 `manager_verdict.winner`
- 无 v2 winner 的旧报告排除出分母（禁止 0/0、行业 0 假象）
- industry 缺失 → `None`，禁止硬编
- **不开** `credit_weighting_enabled`

## 禁止

- Gate4 / 删 `legacy_proxy` / 部署
- 改 `data_collector.py` / prompts / `evidence_verifier` / frontend
- `git add .`
- 触碰无关 dirty：`AGENTS.md`、`frontend/src/services/api.ts`

## 交付

评论：新 tip 40 位 SHA + `git log --oneline 9d5d53d..<tip>` + pytest 数字 → `in_review`。  
等独立审核 + Cursor 准予合入后再 FF。
