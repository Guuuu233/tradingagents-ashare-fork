## 独立核验

- 关联任务：DAV-437（P3-H2.0 评测契约冻结）
- 当前主干 `origin/codex/dav-4-p2a-trunk` = `93212809a099ebc6e5787bc0468e63969f5496f8`
- 候选分支：`agent/1/df73803fe99d` @ **`d50b0dc3a7b3721aba16ac4474530c1565be79de`**
- 祖先核验：`93212809a099ebc6e5787bc0468e63969f5496f8` 是 `d50b0dc3a7b3721aba16ac4474530c1565be79de` 祖先，可严格线性 fast-forward
- 独立审核：✅ 通过（0 High / 0 Medium）
- 编排侧 `.venv310`：`pytest tests/test_evaluation_contracts.py tests/test_shadow_credit.py tests/test_credit_weighting.py tests/test_h1b_gates.py` → **42 passed**

## 唯一允许动作

远端 `https://github.com/Guuuu233/1.git`（或迁移后的 tradingagents-ashare-fork）：

1. 读回 trunk 仍必须是 `93212809a099ebc6e5787bc0468e63969f5496f8`，候选仍必须是 `d50b0dc3a7b3721aba16ac4474530c1565be79de`，否则 **BLOCK**。
2. `git push <target> d50b0dc3a7b3721aba16ac4474530c1565be79deefs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `d50b0dc3a7b3721aba16ac4474530c1565be79de`

## 约束

- **三禁**：禁止 force、禁止改代码、禁止重启。
- `credit_weighting_enabled` 保持默认 False。
- 合入完成后 mention 项目主管；**不要** mention 项目调度助手。
- 部署另开卡（本卡不做 uvicorn 重启）。
