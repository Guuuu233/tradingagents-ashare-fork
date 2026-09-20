# Track A9：线性 FF 主干并回归（4493177，不准部署）

## 授权

Cursor 已在 DAV-553 **准予合入**；独立审核 DAV-554 ✅。

## 目标 tip（唯一）

`4493177eeefa4a7aabfc05904c156ccb0106d06e`

分支：`origin/agent/dev2/a9-h1b-gates-db-path`  
父：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_h1b_gates.py`（可附带 calibration/decision_status）。

## 禁止

merge 改写 / 部署 / 开加权。

完成后评论主干 tip SHA → `done`。
