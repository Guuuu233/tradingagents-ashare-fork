# Track A12：线性 FF 主干并回归（98fe5d1，不准部署）

## 授权

Cursor 已在 DAV-562 **准予合入**；独立审核 DAV-563 ✅。

## 目标 tip（唯一）

`98fe5d199e8874ae829d2b492882d82339c836f0`

分支：`origin/agent/dev2/a12-t5-due-inference`  
父：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_h1b_gates.py`（可附带 `test_tplus5_shadow_backfill.py`）。

## 禁止

merge 改写 / 部署 / 开加权 / 生产库实写回填。

完成后评论主干 tip SHA → `done`。
