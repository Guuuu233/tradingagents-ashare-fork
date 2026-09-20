## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `e7b02006bbf546b28d5f6e25af85cc97750df279`  
**父 tip：** `d6f75dddb396d35e5102c66b67a5e13f8d0650bb`（与 C-05d 同父；合入顺序：先 FF `b42bb50`，再 cherry-pick 本提交，保持 blob 一致）  
**分支：** `origin/agent/1/4eca31d2d738`

DAV-629 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav628-e7b0200`：仅新增 `work/2026-09-05-c04-pit-raw-dividend-eval.md`；`pytest -k tushare` **32 passed**。文档冻结 raw vs 前复权、`adj_factor` 不得回填历史、`dividend` 旁证、`price_basis` 对接。无 `tradingagents/` 改动、无 token。残留：markdown 引用行尾空白，`git diff --check` 非 0，不挡文档合入。

**准予合入。** 在 C-05d FF 之后 cherry-pick。禁止 merge。禁止部署。禁止实现复权引擎。
