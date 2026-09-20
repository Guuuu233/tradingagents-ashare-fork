## Cursor 同 SHA 验收：不准予合入

**候选 SHA：** `627c8a914c765727414a091a2eb4b59511cad837`  
独立审核 ✅。隔离复测 19 passed。

**打回原因：** 生产 `get_cninfo_*` 仍消费 AKShare 已丢掉 `adjunctUrl` 的 DataFrame，live record 无法 hashed。测试靠夹具塞列。相对路径补 `static.cninfo.com.cn` 前缀可以保留，但字段必须先从同一 query 留下来。

返修卡已开。禁止 FF。禁止部署。
