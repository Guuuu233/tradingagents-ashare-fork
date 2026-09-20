## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `fba70674bcaa3df5e271d2148ee40dceb6b83a18`  
**父 tip：** `b2f7b77bca19a9b50f0556989f06553c5b15404f`  
**分支：** `origin/agent/senior-dev-2/c05a-cninfo-metadata`

DAV-620 独立审核 ✅通过（exact SHA）。Cursor 隔离 `/tmp/iso-dav617-fba7067`：`pytest tests/test_cninfo_disclosure_metadata.py` **10 passed**，退出码 0。`git diff --check` 0。changed files 仅三份：`cninfo_disclosure.py`、`cn_akshare_provider.py`、`tests/test_cninfo_disclosure_metadata.py`。`canonical_event_id` 仅 `cninfo:{id}` 或 null；KeyError → `provider_failure`。未改 `get_news`/聚类/Tushare。

残留（不挡合入）：`extract_announcement_id` 对 URL 解析有 `except Exception: pass`（失败仍返回 None，fail-closed）；调研夹具代码写成 000001，标题与 ID `1225488095` 仍钉住用户证据。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。
