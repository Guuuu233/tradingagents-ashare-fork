## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `b2f7b77bca19a9b50f0556989f06553c5b15404f`  
**父 tip：** `9b3de9b0153e4727896692e42d22223a9b0f4efe`  
**分支：** `origin/agent/2/7467152afe29`

DAV-616 独立审核 ✅通过（exact SHA）。Cursor 隔离 `/tmp/iso-dav615-b2f7b77` 复测同一 SHA：88 passed。`git diff --check` 0。changed files 仅 `cn_akshare_provider.py` 与 `tests/test_vendor_chain_semantics.py`。按列名取 `新闻链接`/`链接`；空/nan/空白不写假 `Link:`。无 `canonical_event_id`。未接 CNINFO。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。
