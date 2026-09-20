# Track A5：线性 FF 主干并回归（d2f8aa0，不准部署）

## 授权

Cursor 已在 DAV-540 **准予合入**；独立审核 DAV-541 ✅。

## 目标 tip（唯一）

`d2f8aa05579d0520abe942972889a82060ea65e7`

分支：`origin/agent/dev2/a5-tplus5-shadow-backfill`  
父：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_tplus5_shadow_backfill.py`（及相关 shadow/h1b 可选）。

## 禁止

merge 改写 / 部署 / 开加权 / Gate4 删 legacy（Gate4 另卡）。

完成后评论主干 tip SHA → `done`。
