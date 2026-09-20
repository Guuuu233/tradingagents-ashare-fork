# DAV-304 P1：§6 报告质量闸（关键词 + 禁止改写失败）

**基线父提交：`ab3cda62bc676562c9421f809c0f1fa95d62f34e`。独立分支，不合主干。**

## 缺口

方案 §6 风险1：Prompt 不够时要后处理检查「传导/联动」等；当前无落库闸。失败外盘曾被 LLM 静默省略。

## 契约

1. 新建 `tradingagents/graph/report_quality_gate.py`（不要 `_v2`）。对 **macro_report**（必要时 fundamentals_report）检查：
   - 至少命中：`传导` 与（`联动` 或 `外溢` 或 `时滞`）之一；
   - 若 `market_data_context.global_indices` 为 failed/partial，正文必须出现 `【数据缺失】` 或 `全球核心指数`/`标普`/`恒生` 之一，**禁止**仅有「外围平稳/外围中性」而无点位或缺失标注。
2. 不通过：写入 `data_failure_ledger` 条目 `source=report_quality_gate`（reason 明确），**不阻断** completed（避免因文风误杀整份分析）；可选对宏观 **最多 1 次** 重试（有则做，没有重试钩子就只记 ledger）。
3. 挂钩点必须是现有完成路径上的**一处**（`trading_graph` 或 `report_service.update_report_partial` 在 status=completed 前）。禁止平行流水线。
4. 不改辩论轮次、不改 Prompt 长文（本卡不是再写一篇 prompt）。

## 白名单

- `tradingagents/graph/report_quality_gate.py`（新建）
- `tradingagents/graph/trading_graph.py` **或** `api/services/report_service.py`（只允许一个挂钩文件）
- `tests/test_report_quality_gate.py`（新建）

## 验收

单测：合格正文 pass；缺「传导」记 ledger；「外围平稳」且无指数/缺失标注记 ledger。compileall + git diff --check。推独立分支。不得 @项目调度助手。
