# DAV-282 补漏：方案 §4.5 京东方A 上线后真实 3/3

**前置：宿主 healthz 必须已是 `dcc871dff13878803881bdbb9aed55f7cc10dbeb`。若仍是 `8866494`：停止并报告，不要在旧进程上发分析。**

对照《项目加强方案》§4.5 验证 2–3。现有三份 3/3 报告生成于 `0b10041`，早于 5 行业部署、两阶段拓扑、27 行业与 RAG，**不能**当作 §4.5 端到端。

## 操作

指定账户 `davidliu022305@gmail.com` / `429163f7-50b6-4982-8bdf-96ae99506843`。
标的：`000725.SZ`（京东方A）。
**不要填 trade_date**（收盘后默认当天交易日）。
单次请求 `config_overrides`：`max_debate_rounds=3` 且 `max_risk_discuss_rounds=3`。
**禁止写回**持久配置（库内必须仍为 3/1）。
禁止改 `.env` / providers / role_bindings。

## 验收（核库，不以 completed 文案为准）

1. 新报告 `user_id` 正确、`status=completed`、运行 SHA/`healthz` 为 `dcc871`。
2. `investment_debate_state.count=6` 且 `risk_debate_state.count=9`，双方 judge 非空。
3. DataCollector 映射消费电子；`macro_report` 或注入痕迹含产业链（`【产业链联想数据】` 或 LME 铜价/【数据缺失】显式标注）。
4. 基本面有上游/下游或议价权表述；不得把缺失指标写成具体数字。
5. 持久配置事后仍为 3/1。

交付：report_id、trade_date、count=6/9、关键词计数、产业链是否注入。不得 @项目调度助手。
