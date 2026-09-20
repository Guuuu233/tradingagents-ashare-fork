# DAV-283 阶段三：历史案例学习闭环（方案 §5.4）

**基线父提交必须是 `dcc871dff13878803881bdbb9aed55f7cc10dbeb`。禁止基于 `8866494` 开工。不必等宿主部署。禁止自己推主干。**

对照《项目加强方案》§5.4：每次分析后记录「预测 vs 实际」，形成错误案例库，下次分析注入相似历史案例。

## 产品契约

1. 分析 **completed** 后落库一条案例：`symbol`、`trade_date`、`direction`/`decision`、关键 claims（从辩论/裁决抽取，缺则空列表）、运行 SHA。禁止用 LLM 另写一套预测。
2. 实际涨跌：用已有行情接口按 **trade_date 之后的下一交易日（或 T+1 收盘）** 对比，`as_of` 必须 ≤ 评估日；日历/行情失败写 `【数据缺失】`，禁止填 0 或今天。
3. 下次分析：按同一行业或同一 symbol 检索最多 N 条相似案例，注入宏观/基本面 prompt；未命中写 `【历史案例未命中】`。禁止编造案例。
4. 禁止 `_v2`；禁止改辩论轮次默认值、`.env`、providers、role_bindings、资金流证据、setup.py 拓扑、INDUSTRY_LINKAGE_MAP、RAG 检索器核心打分。
5. 不得把案例库当训练语料外传。

## 允许修改

- 新建 `tradingagents/knowledge/historical_cases.py`（或同目录等价单文件）
- `api/services/report_service.py` 完成路径挂钩（只在 completed 落库，失败不得假案例）
- `tradingagents/agents/utils/knowledge_context.py` 增加案例注入（可复用 RAG 格式化风格）
- `tests/` 追加：落库、行情缺失、注入未命中

SQLite 表可加在现有 DB，迁移必须幂等；禁止碰用户配置表。

## 验收

独立 worktree。`.venv310` 定向新测试 + compileall + diff-check。
推送独立分支精确 SHA，不合主干。不得 @项目调度助手。
