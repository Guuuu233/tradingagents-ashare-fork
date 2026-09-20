# P2 只读审计：65 个 pending_api 的 Tushare 可替代矩阵

**只读，不改代码、不改 `.env`、不打印 token。基线 `2ee1fb8`。**

用户规则：Tushare 已付费且功能较全；有条件时优先 Tushare，能出真值的不得继续 pending_api 或默认 yfinance。

## 工作

审计 `tradingagents/dataflows/industry_linkage.py` 中全部 65 个 `source="pending_api"` 指标。基于：
1. Tushare 官方文档；
2. 本机已配置 token 的脱敏直接 HTTP 探针；
3. 不猜 API，不按字段名臆测。

输出 `work/dav314-tushare-pending-api-matrix.md`，每项一行：
- 行业/指标/频率
- Tushare api_name、参数/ts_code（若有）
- 权限探针 code/row_count/field names/requested_as_of/actual_as_of（不打印值和 token）
- 分类：A 可直接真值替代 / B 可用近似代理但需用户批准 / C Tushare无对应接口 / D 有接口但当前权限不足
- 推荐优先级

至少重点核实：Shibor、LPR、10年国债、白糖、玉米、豆粕、氧化铝、燃料油、沥青、煤炭、钢材/铁矿石、汽车销量/产量、工业机器人产量、家电出口等。

严禁把期货价格直接冒充现货价格；代理指标必须列为 B，不得算 A。最终只交审计文档和探针摘要，0 代码改动、0 测试。推文档分支/SHA。

禁止 @项目调度助手。
