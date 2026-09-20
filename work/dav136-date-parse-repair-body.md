# DAV-136 精确返工：严格拒绝污染日期

## 精确基线

只读远端 `target` 分支 `agent/1/6c30927a`，基线 SHA：`a80eb2fcf36acd8a6d6922296820ed1909d6ad98`。该 SHA 已通过定向实现测试，但只读复审复现了阻塞缺陷。不要读取 DAV-119/DAV-118 历史，不复用旧 run，不修改主干、配置、providers、模型绑定、API Key、个人设置或凭据。

## 唯一阻塞问题

`tradingagents/dataflows/fund_flow_evidence.py:_normalise_date_text` 目前只检查前 10 个字符并截断后缀。复现输入 `2026-08-07garbage` 会被当成 `2026-08-07`，使五日窗口进入 `available` 并累计资金流。该输入必须 fail-closed，不能静默求和。

## 只允许的范围

- `tradingagents/dataflows/fund_flow_evidence.py`
- `tests/test_fund_flow_evidence.py`

## 实现与验收

1. 日期解析不得按前缀截断；只接受仓库当前约定的完整、可验证日期格式。非法后缀、非法日期和污染文本必须拒绝，并返回既有结构化 `data_conflict`/`partial` 语义。
2. 新增最小回归：五条交易日记录中一条为 `2026-08-07garbage` 时，结果不能是 `available`，`netamount`/`r0_net` 不得累计；同时保留并验证正常五日窗口通过。
3. 不改变交易日历、来源族/算法组隔离、legacy Web、字段资格或累计窗口之外的任何语义。本子任务不处理 source metadata 混组建议，也不修改 provider/collector/analyst。
4. 从上述精确 SHA 产生并推送新的远端 branch/SHA；不要推送主干。

## 验证

- 优先运行：`env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_fund_flow_evidence.py`；若 `.venv310` 不存在，必须明确报告未执行，不能用其他解释器冒充 `.venv310` 证据。
- 对改动模块执行 `env -u PYTHONPATH .venv310/bin/python -m compileall -q tradingagents/dataflows/fund_flow_evidence.py`（环境缺失如实记录）。
- 执行 `git diff --check`。
- 交付 branch/SHA、实际改动文件、精确测试结果、结构化失败证据和未覆盖限制。新 SHA 经远端核验和复审前，不合入、不重启、不解锁 DAV-122 或后续阶段。

若再次 context-window 400 或网关断连，立即停止该 run，由项目主管改派资深开发2；不要重放本任务上下文。
