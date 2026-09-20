# P1：接入 Tushare Shibor 3M 与 LPR 1Y 两项 A 类真值

**基线：`1eb280b35f436c2ff1ece00a448ad7483c86eff9`。独立分支，不合主干。DAV-314 已核定仅这两项属于同口径 A 类真值。**

## 已验证能力

- `shibor`：code=0，字段 `date,on,1w,2w,1m,3m,6m,9m,1y`；目标取 `3m`。
- `shibor_lpr`：code=0，字段 `date,1y,5y`；目标取 `1y`。
- 当前图谱：`银行间同业拆借利率Shibor` symbol=`Shibor_3M`、`贷款市场报价利率LPR_1Y` 尚为 pending_api。

## 契约

1. 图谱两项改为 `source="tushare"`，明确 metadata：`api_name`、`value_field`、单位%、非价格行情。
2. provider 支持 Tushare 宏观利率时序，不得把它硬塞进 `fut_daily/close`：
   - `shibor` 使用 date + 3m；
   - `shibor_lpr` 使用 date + 1y。
3. 结构化输出完整：current_value、趋势/月/季变化（利率变化建议同时保留百分点或明确现有百分比算法语义）、requested_as_of、actual_as_of、retrieved_at、transport_provider=tushare、api_name、value_field、status。
4. 防前视：actual_as_of<=requested_as_of；无数据/403/字段缺失/日期异常 fail-closed。
5. 不动 18 个 B 类代理、不改10年国债D类、不改其他63项，不改 `.env`/用户配置。
6. 测试隔离宿主 token；用 fixture 验证两个接口字段与周末回退。

## 白名单

- `tradingagents/dataflows/industry_linkage.py`
- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tests/test_industry_linkage.py`
- `tests/test_industry_linkage_provider.py`

## 验收

`.venv310/bin/python -m pytest tests/test_industry_linkage.py tests/test_industry_linkage_provider.py -q`；compileall、diff-check；脱敏真实探针只打印 source/api/value_field/requested/actual/status，不打印 token。推远端精确 SHA。禁止 @项目调度助手。
