## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `4f887a36684dbd304464b588064001c12d8278c8`  
**父 tip：** `b2f7b77bca19a9b50f0556989f06553c5b15404f`  
**分支：** `origin/agent/1/40dd06b3c240`

DAV-619 独立审核 ✅通过（exact SHA）。Cursor 隔离 `/tmp/iso-dav618-4f887a3`：仅新增 `work/2026-09-05-tushare-private-gateway-matrix.md`。无 token。冻结 `anns_d` 403、`stk_surv` 不可用、C-04/C-09/C-05 分卡。未改 `tradingagents/`。

残留：该 markdown 有 trailing whitespace（blockquote 硬换行），`git diff --check` 非 0；内容契约仍成立。617 与本卡同父，主干先 FF 617 exact SHA，再 cherry-pick 本文件以保持线性历史。

**准予合入。** 禁止部署。禁止改 token/providers。
