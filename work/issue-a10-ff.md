# Track A10：线性 FF 主干并回归（ccda1be，不准部署）

## 授权

Cursor 已在 DAV-556 **准予合入**；独立审核 DAV-557 ✅。

## 目标 tip（唯一）

`ccda1be9c96e4d9a5f334fa03280342badeb4306`

分支：`origin/agent/dev2/a10-backfill-db-path`  
父：`4493177eeefa4a7aabfc05904c156ccb0106d06e`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_report_industry_persistence.py` + `tests/test_tplus5_shadow_backfill.py`（可附带 `test_h1b_gates.py`）。

## 禁止

merge 改写 / 部署 / 开加权 / 对生产库实写回填。

完成后评论主干 tip SHA → `done`。
