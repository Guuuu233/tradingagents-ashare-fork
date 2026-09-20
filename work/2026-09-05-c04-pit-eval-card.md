# C-04 切片 1（只读）：dividend + raw daily 的 PIT 风险与落点

**基线：** `d6f75dddb396d35e5102c66b67a5e13f8d0650bb`  
**一个关注点：** 冻结「如何用私有网关 `dividend` + raw daily 做 PIT/RAW，以及为何不能用当前 `adj_factor` 回填历史」。  
**禁止：** 改 `tradingagents/`、改 token、改 providers、重启服务、实现复权引擎、混 C-09。

依据 `work/2026-09-05-tushare-private-gateway-matrix.md`。只新增一个文档：`work/2026-09-05-c04-pit-raw-dividend-eval.md`。

必须写清：raw 收盘价通道 vs 前复权；`adj_factor` 只可核验或自今日起归档；`dividend` 作除权事件旁证；现有 `price_basis`（DAV-606）标签如何对接。禁止打印 token。一个 commit，push，40 位 SHA。
