# DAV-311 P1：产业链采集结果进入 market_data_context + 报告标题质量闸

**基线：`7a21a597ddec6699301790cb173726f53fe0cbb3`。独立分支，不合主干。**

## 真实验收缺口

京东方报告 `91688a03` completed；美股3/3、传导18/联动2/时滞10均达标。但：
- `result_data.market_data_context` 无 `industry_linkage`；
- 宏观/基本面正文均 0 次 `【产业链联想数据】`；
- 直接 provider 对京东方映射「消费电子」可返回 LME铜价真值，说明不是数据源失败。

根因：`data_collector._fetch_all` 在约 1298 行先构建 `market_data_context`，到约 1355 行才采集 `industry_linkage`；因此产业链永远不会进入 context/provenance/ledger。分析师虽然能从 pool 顶层读到，但模型可删标题，质量闸也不检查产业链。

## 契约

1. 将产业链采集移动到 `market_data_context` 构建之前，或在采集后确定性写入：
   - `market_data_context.industry_linkage` = 结构化 dict（即使部分指标缺失）；
   - `source_provenance.industry_linkage`：requested_as_of、actual_as_of（优先顶层 as_of/cached_at 中真实数据日，不得伪造）、status=available|partial|unavailable；
   - 完全失败进入 `data_failure_ledger`。
2. 保留顶层 `results["industry_linkage"]`，不能破坏宏观/基本面现有读取。
3. `report_quality_gate`：若 context 有 industry_linkage，宏观或基本面至少一份正文必须出现 `【产业链联想数据】` 或明确「产业链」+实际指标/【数据缺失】；否则 ledger 记 `report_quality_gate`，但不把 completed 改 failed。
4. 防前视：产业链实际日期不得 > analysis_baseline_date。
5. 不改 provider、图谱、`.env`、用户配置。

## 白名单
- `tradingagents/graph/data_collector.py`
- `tradingagents/graph/report_quality_gate.py`
- `tests/test_industry_linkage_dataflows.py`
- `tests/test_report_quality_gate.py`

## 验收

- collector fixture：context 与顶层都有 industry_linkage；provenance/ledger 正确。
- quality gate：有 context 但正文删标题时记 ledger；有实际产业链段落时 pass。
- 定向 pytest + compileall + diff-check；推精确 SHA。不得 @项目调度助手。
