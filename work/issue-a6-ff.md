# Track A6：线性 FF 主干并回归（46a6dfe，不准部署）

## 授权

Cursor 已在 DAV-453 **准予合入**；独立审核 DAV-538 ✅。

## 目标 tip（唯一）

`46a6dfee9601ca679b8d22c0c86a931fccb59b63`

分支：`origin/agent/cursor/a6-h1b-gates-v2-only`  
父：`9d5d53dd89bed6dcbfa7dd3aa991771993dddfea`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only** 到上述 tip。  
回归：`tests/test_h1b_gates.py`（及相关信用测可选）。

## 禁止

- merge / rebase 改写历史
- 部署 / 重启生产
- 开 `credit_weighting_enabled`
- Gate4 / 删 `legacy_proxy`

完成后评论主干 tip SHA，卡 → `done`。
