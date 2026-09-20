# DAV-621 返修：生产路径必须保留 hisAnnouncement 的 adjunctUrl

**打回 SHA（exact，从此分叉）：** `627c8a914c765727414a091a2eb4b59511cad837`  
**父 tip：** `141c702dd796f54e8a33c4f3767ba9240c5abea3`  
**分支：** `origin/agent/2/103c5565bb98`

Cursor 隔离 `/tmp/iso-dav621-627c8a9`：`tests/test_cninfo_disclosure_metadata.py` **19 passed**。独立审核 ✅不能代替合入。本卡 **不准予合入**。

## 阻塞

AKShare 1.18.30 `stock_zh_a_disclosure_report_cninfo` / `_relation_cninfo` **return 前丢掉** `adjunctUrl`。现网 `CnAkshareProvider.get_cninfo_announcements` / `get_cninfo_ir_surveys` 仍直接吃该 DataFrame。因此生产 envelope 的 `adjunct_url` 恒为 None，`qualify_cninfo_content` 只能 `unavailable`。测试是自己往 DF 里塞 `adjunctUrl` 才绿的。

C-05b 要求：对**同一** `hisAnnouncement/query` 响应按字段名保留 `adjunctUrl`。哈希层本身不够。

## 本返修只做这一件事

让 `get_cninfo_announcements` / `get_cninfo_ir_surveys` 生产路径上的 record 带上响应里的官方 `adjunctUrl`（相对路径可解析为完整 URL；**路径本身必须来自字段**，禁止用 announcementId+日期拼接 `finalpage/{date}/{id}.PDF`）。

允许：包一层同一 query、或在 parse 前把 AKShare 丢掉的列补回。禁止改 site-packages。禁止 `anns_d`。禁止聚类/manifest。禁止自动 FF。

## RED（修复前必须失败）

1. Mock `ak.stock_zh_a_disclosure_report_cninfo` 返回 **1.18.30 形状**（仅 `代码/简称/公告标题/公告时间/公告链接`，无 `adjunctUrl` 列），但底层 query JSON 有 `adjunctUrl=finalpage/2026-07-28/1220000001.PDF`（或等价 patch 点）→ `get_cninfo_announcements` 的 record.`adjunct_url` 非空且 **不是** 用 id 公式编出来的。
2. 底层无 `adjunctUrl` 字段 → `adjunct_url is None`，qualify 为 `unavailable`，不得编 static 路径。
3. 既有 19 个测试保持绿（含 hashed/403/无 ID）。

一个 commit，基于 `627c8a914c765727414a091a2eb4b59511cad837` 继续，push，评论新的 40 位 SHA。
