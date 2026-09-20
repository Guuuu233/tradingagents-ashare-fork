## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `d6f75dddb396d35e5102c66b67a5e13f8d0650bb`  
**父 tip：** `effa20f6bbeed6e74716db71189380ef7f743245`  
**分支：** `origin/agent/2/f98069e27499`

DAV-626 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav625-d6f75dd`：`pytest tests/test_news_event_coverage.py` **26 passed**，`git diff --check` 0。白名单 3 文件。同 `cninfo:{id}` 并簇；异 id 不因标题并簇；markdown 解析不发明 id。未改 `get_news` / recall_status。

**准予合入。** 线性 FF。禁止 merge。禁止部署。
