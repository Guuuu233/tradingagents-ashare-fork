## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `effa20f6bbeed6e74716db71189380ef7f743245`  
**父：** `627c8a914c765727414a091a2eb4b59511cad837`（C-05b 哈希层）  
**主干祖先：** `141c702dd796f54e8a33c4f3767ba9240c5abea3`  
**分支：** `origin/agent/2/9797d1ab3f30`

DAV-624 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav623-effa20f`：`pytest tests/test_cninfo_disclosure_metadata.py` **23 passed**。打回点已补：AKShare 丢列后从同一 `hisAnnouncement/query` 捕获或 fallback JSON 保留官方 `adjunctUrl`；无字段则 None，qualify=`unavailable`；自定义相对路径不被 id 公式覆盖。

残留（不挡合入）：全局 `requests.post` 猴子补丁有并发交织风险；`test_dav623_same_query_response_captured_without_duplicate_query` 未断言 `post_call_count`；测试文件 EOF 空行导致 `git diff --check` 非 0。

**准予合入。** 线性 FF `effa20f`（含已修的 627c8a9）。禁止 merge。禁止部署。
