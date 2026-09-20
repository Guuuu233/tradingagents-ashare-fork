# DAV-310 P1：Tushare 产业链指标补 actual_as_of provenance

**基线：`7a21a597ddec6699301790cb173726f53fe0cbb3`。独立分支，不合主干。**

## 真实验收缺口

线上新能源汽车碳酸锂已返回：source=tushare、symbol=LC.GFE、current_value=158680、confidence=高；但结构化字典没有 `requested_as_of` / `actual_as_of`。目前只有 note 文本，无法审计防前视实际日期。

## 契约

1. `IndustryLinkageProvider` 的 Tushare 成功结果必须输出：
   - `requested_as_of`（传入日期）
   - `actual_as_of`（选中 row.trade_date，YYYY-MM-DD）
   - `retrieved_at`（UTC ISO）
   - `transport_provider=tushare`、`api_name=fut_daily|index_global`
2. 必须满足 `actual_as_of <= requested_as_of`；不满足时 fail-closed。
3. 失败结果也保留 requested_as_of、api_name 和类型化 category；不打印 token。
4. 不改图谱映射、不改 `.env`、不改 `cn_akshare_provider.py`。

## 白名单

- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tests/test_industry_linkage_provider.py`

## 验收

定向 pytest；真实脱敏 smoke 只打印字段名/source/symbol/actual_as_of，不打印完整行情或 token；compileall、diff-check。推精确 SHA。不得 @项目调度助手。
