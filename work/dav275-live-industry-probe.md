# DAV-275 阶段二上线后只读探针：产业链注入是否进分析师

只读。禁止改代码、发完整 3/3、改配置。

基线：宿主 healthz 必须是 `8866494f8cae9f648aff0c4c0a2f9f5cf680e8dd`。

对照《项目加强方案》§4.5 验证 1–2（数据采集成功率 / DataCollector 映射），**不做**完整 LLM 回归。

## 必须用宿主 `.venv310` 实测（不臆造）

1. `IndustryLinkageProvider().get_industry_linkage` 对 5 行业：每个至少 1 个指标有真实 `current_value`，或显式 `【数据缺失】`+来源。记录行业、指标名、是否有值、as_of（脱敏，不打印 payload）。
2. DataCollector `_map_stock_to_industry`：000725/300750/688981/601857/600036/000002 映射正确。
3. 可选：对 000725 调 DataCollector 采集路径，确认 `industry_linkage` 键存在且非伪造。

禁止改 `.env`。交付结构化矩阵到评论。不得 @项目调度助手。
