## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `b42bb50893ec135c9f365f98f12255a18728f696`  
**父 tip：** `d6f75dddb396d35e5102c66b67a5e13f8d0650bb`  
**分支：** `origin/agent/2/e2fc3b502291`

DAV-630 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav627-b42bb50`：`pytest tests/test_news_event_coverage.py` **32 passed**，`git diff --check` 对三文件为 0。白名单仅 `news_event_evidence.py` / `data_collector.py` / `tests/test_news_event_coverage.py`。无 cninfo 仍 `unknown`+空 manifest；records 进 `query_manifest`；`provider_failure` 不写成 confirmed empty；未改东财 `get_news`。`is_confirmed_empty` 恒为 False（不把单次巨潮空表升成全局确认无新闻）。

**准予合入。** 线性 FF。禁止 merge。禁止部署。
