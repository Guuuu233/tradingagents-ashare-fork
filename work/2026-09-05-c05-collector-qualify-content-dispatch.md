# C-05 切片 9：`_fetch_all` 对已取到的巨潮 envelope 调用 `qualify_cninfo_content`

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `9a2878c8e94a0bf5ccab3ba222067d9f47d89138`（或其线性后代）。  
**一个关注点：** 公告与 IR envelope 已经进 `results`。本卡只把已有 `CnAkshareProvider.qualify_cninfo_content`（即 `cninfo_disclosure.qualify_cninfo_content`）接到 `_fetch_all` 归一化 envelope **之后**、`cninfo_record_to_evidence` **之前**，让分析路径带上 `content_status` / `content_sha256`。  
**禁止：** 改 `qualify_cninfo_content` 资格规则；改 `get_cninfo_announcements` / `get_cninfo_ir_surveys` / `adjunctUrl` 附着；解析 PDF 正文或财报数字；把 `content_bytes` 写入 record/日志/prompt/测试黄金文件；接线 `anns_d`；把 qualify 加进 `TOOLS_CATEGORIES` / `route_to_vendor`；编造 `static.cninfo.com.cn` URL；C-09-3；社交 AUTH；打印 token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

切片 8 之后标题级元数据会进 `event_coverage`，但 collector 从不 qualify，C-05b 的 hash 契约在分析主路径上是死代码。

## 允许改

- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector_cninfo_qualify.py`（新建）

用 vendor 链**同一份** `_registry.get("cn_akshare")`。对 `cninfo_announcements` 与 `cninfo_ir_surveys`：仅当对象已是 envelope 且 `status` 不是 `provider_failure` 时，对 `records` 逐条调用 `cn_provider.qualify_cninfo_content(record, cutoff=规整 trade_date)`（已有 `timeout` 默认即可）。失败/缺附件/无 announcementId 必须保持函数现有 `unavailable`，不得改成 `confirmed_empty` 或「确认无公告」。不要新写下载器，不要并行 `_v2`。

## 契约

1. `_fetch_all` 结束后，ok envelope 里被 qualify 过的 record 不再全是默认 `not_attempted`（缺 URL/缺 id 应为 `unavailable`；mock PDF 成功应为 `hashed` + 64 hex）。
2. `provider_failure` envelope **不得**被改成 ok，也不得把失败写成确认无公告。
3. 测试禁止真打 `.cninfo.com.cn`。mock `qualify_cninfo_content` 或底层 fetch，至少：hashed 一条、缺 adjunct → unavailable、provider_failure 不被 qualify 成空成功。必须走 `_fetch_all`。
4. 测试不得包含 PDF 全文；只断言 status 与 sha256。
5. `env -u PYTHONPATH` + `.venv310/bin/python -m pytest tests/test_data_collector_cninfo_qualify.py -q`

一个 commit，push 功能分支，评论 40 位 SHA。不要自建审核卡、不要 @独立代码审核员。
