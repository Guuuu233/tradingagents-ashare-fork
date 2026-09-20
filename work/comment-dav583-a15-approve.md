## Cursor 隔离核验 + 准予合入（Track A15）

**候选 SHA（完整 40 字符）**：`31c32f0f877e86fc3c06eb58a34b4dc08a453044`  
**父 tip**：`03cfb47c2f01981a6f80254fba8ac6a857b4d987`  
**远端分支**：`origin/codex/dav-a15-industry-backfill-ensure`  
**独立审核**：DAV-584 ✅ 通过（同 SHA）

### 隔离证据
- ancestry：tip 是该 SHA 祖先
- diff 范围仅 2 文件：`scripts/backfill_report_industry.py`、`tests/test_report_industry_persistence.py`
- 隔离 worktree pytest：`100 passed in 31.17s`（`test_report_industry_persistence` + `test_h1b_gates` + `test_tplus5_shadow_backfill`）
- 实现与 `backfill_tplus5_shadow.py` 默认路径对齐；缺列迁移失败显式 `RuntimeError`，禁止静默 golden fallback

### 裁决
**准予合入** SHA `31c32f0f877e86fc3c06eb58a34b4dc08a453044` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

约束：不准予部署；不改 `credit_weighting_enabled`；禁止 merge commit。
