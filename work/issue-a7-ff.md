# Track A7：线性 FF 主干并回归（97f7c26，不准部署）

## 授权

Cursor 已在 DAV-547 **准予合入**；独立审核 DAV-548 ✅。

## 目标 tip（唯一）

`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`

分支：`origin/agent/dev2/a7-persist-report-industry`  
父：`503aa1606161918ba25e77dad40ec2e8df652461`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_report_industry_persistence.py`（及相关 h1b 可选）。

## 禁止

merge 改写 / 部署 / 开加权 / schema 列。

完成后评论主干 tip SHA → `done`。
