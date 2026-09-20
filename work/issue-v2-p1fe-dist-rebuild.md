# 重建 frontend/dist 使 P1-FE 真正上线

## 独立核验

- 远端与宿主 HEAD、`/healthz.commit_sha` 均为 `4fa2e2453de739c0ec10d42a4bac591a6effff53`
- 该 SHA 双亲为 `d9c7014`（P1-S）与 `059f081`（P1-FE）
- API 打开 `data/tradingagents.db`；用户 `davidliu022305@gmail.com` 仍 3/1
- **缺口：** `frontend/dist/assets/index-UuXqNVgd.js` 时间戳 2026-08-23，bundle 内无 `v2_structured_disagreement` / `证据足以裁决` / `isChallengePenetrated`。API 挂载的是这份 dist，因此抽屉 v2 **尚未对用户可见**。

## 唯一允许动作

项目根 `/Users/davidliu/Documents/TradingAgents-AShare`：

1. 在途报告为 0，否则 BLOCK。
2. `cd frontend && npm run build`（不要改源码）。
3. 新 dist 的 JS 必须能 grep 到 `v2_structured_disagreement` 与 `证据足以裁决`。
4. 按既有纪律重启 uvicorn（DATABASE_URL、`.venv310`、代理、完整 no_proxy）。
5. `/healthz.commit_sha` 仍必须是 `4fa2e24…`。
6. 3/1 不变。

禁止改 `tradingagents/`、禁止改 3/1、禁止 force git。评论交付 build 输出摘要、新 bundle 文件名、healthz。不要 mention 项目调度助手。
