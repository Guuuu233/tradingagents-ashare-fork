# C-05 切片 8：`_fetch_all` 接入巨潮 IR/调研 envelope

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `da9a69d6dbf11a0e359732bce3d9b11968b3e1b1`（或其线性后代）。  
**一个关注点：** 用 vendor 链同一份 `cn_akshare` 调用已有 `get_cninfo_ir_surveys`，把 envelope 写入 `results["cninfo_ir_surveys"]`。collector 已有对该 key 的 `cninfo_record_to_evidence` 循环。  
**禁止：** 改 `get_cninfo_ir_surveys` / 公告 `get_cninfo_announcements` / PDF / `adjunctUrl` / 聚类；接线 `anns_d`；把 IR 加进 `TOOLS_CATEGORIES` / `route_to_vendor`；C-09-3；社交 AUTH；打印 token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

切片 7 已接公司公告。IR 仍缺键，循环空转。

## 允许改

- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector_cninfo_ir_collect.py`（新建）

调用 `_registry.get("cn_akshare").get_cninfo_ir_surveys(...)`。窗口与公告/个股新闻一致：`start = trade_date - LONG_DAYS`，`end`/`cutoff` = 规整 `trade_date`，`symbol` = ticker。失败 envelope 原样放入结果，禁止把 KeyError/空表写成「确认无公告」。超时/异常字符串降级为 `provider_failure` envelope，不得静默缺键。不要把空串封装成 `ok` 空表。

## 契约

1. `_fetch_all` 结束后 `results["cninfo_ir_surveys"]` 恒为 envelope（`status`/`records`）。
2. `status=ok` 且有 `canonical_event_id` 时进入 `event_coverage`；旁证不得改写该 ID。
3. `provider_failure` → gap，不得 `confirmed_empty`。
4. 空 records 文本不含「确认无公告」。
5. 单测 mock `get_cninfo_ir_surveys`，禁止真打 `.cninfo.com.cn`。至少：正常一条、provider_failure、空 records。必须走 `_fetch_all`。
6. `env -u PYTHONPATH` + `.venv310/bin/python -m pytest tests/test_data_collector_cninfo_ir_collect.py -q`

一个 commit，push 功能分支，评论 40 位 SHA。不要自建审核卡、不要 @独立代码审核员。
