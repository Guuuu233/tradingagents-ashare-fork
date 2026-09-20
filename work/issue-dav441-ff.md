## 独立核验

- 关联任务：DAV-441（P3-H2.4）
- 当前主干 `origin/codex/dav-4-p2a-trunk` = `578c01d195ab388615e14372992b2bd64ba18bec`
- 候选 tip：**`52353ed8d28d0dcee50be370b01ec42059df9836`**
- 祖先核验：`578c01d` 是候选直接祖先，可严格线性 fast-forward
- 宿主证据：`pytest tests/test_evaluation_contracts.py tests/test_recalculate_weekly_metrics.py tests/test_h1b_gates.py -q` → **51 passed**
- 本地 `/healthz.commit_sha` 已预部署对齐（部署卡仍需在 FF 后正式核验）

## 唯一允许动作

远端 `https://github.com/Guuuu233/1.git`（或 `tradingagents-ashare-fork`）：

1. fetch 后读回 trunk 仍必须是 `578c01d195ab388615e14372992b2bd64ba18bec`，否则 **BLOCK**。
2. `git push <target> 52353ed8d28d0dcee50be370b01ec42059df9836:refs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `52353ed8d28d0dcee50be370b01ec42059df9836`

## 约束

- **三禁**：禁止 force、禁止改代码、禁止重启。
- `credit_weighting_enabled` 保持默认 False；不改 3/1 / 模型绑定。
- 合入完成后 mention 项目主管；**不要** mention 项目调度助手。
- 部署另开卡（本卡不做 uvicorn 重启）。
