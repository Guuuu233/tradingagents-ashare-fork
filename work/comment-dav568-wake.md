## Cursor 派工唤醒 — DAV-568 Ops 本地 T+5 实写

基线 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`  
说明：`work/issue-ops-local-t5-writeback.md`

仅本地库：`data/tradingagents.db`。先 dry-run → 备份 → 实写 → tip 代码复跑 `verify_h1b_gates`。期望 Dim4 due≈69 且 completed>0；总建议仍应 `KEEP_FALSE` 则写明原因。

**禁止** VPS/生产库、**禁止部署**、**禁止开加权**、**禁止改产品代码**。完成后本卡 → `done`。
