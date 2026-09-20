## Cursor 隔离复测 — DAV-565 / SHA e368362

**候选**：`e3683628479b204f9839c0424294fc5bde50ba96`  
**父**：`98fe5d199e8874ae829d2b492882d82339c836f0`（线性）

白名单：仅 `frontend/src/services/api.ts` +1 行  
`config_overrides: { v2_debate_enabled: true }`（约 L113）。

隔离 worktree 已核对 diff。等独立审核员（DAV-570）对**同一 SHA**书面结论后，Cursor 再写「准予合入」。

**不准予部署。**
