# Track A11：线性 FF 主干并回归（aa2750f，不准部署）

## 授权

Cursor 已在 DAV-559 **准予合入**；独立审核 DAV-560 ✅。

## 目标 tip（唯一）

`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`

分支：`origin/agent/dev2/a11-t5-no-vacuous-pass`  
父：`ccda1be9c96e4d9a5f334fa03280342badeb4306`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_h1b_gates.py`（可附带 `test_tplus5_shadow_backfill.py`）。

## 禁止

merge 改写 / 部署 / 开加权。

完成后评论主干 tip SHA → `done`。
