# P2-LOW：线性 FF 主干并回归（9d5d53d，不准部署）

## 授权

Cursor 已在 DAV-535 **准予合入**；独立审核 DAV-536 ✅。

## 目标 tip（唯一）

`9d5d53dd89bed6dcbfa7dd3aa991771993dddfea`

分支：`origin/agent/dev2/p2-low-social-cleanup`  
父：`0cda99b6072116874b7a458432d0c7bc7b0a29e3`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only** 到上述 tip。  
回归相关社交测即可。

## 禁止

- merge / rebase 改写历史
- 部署 / 重启生产
- Gate4 / 删 `legacy_proxy`

完成后评论主干 tip SHA，卡 → `done`。
