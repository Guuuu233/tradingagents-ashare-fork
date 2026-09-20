# P2-G1 只读：多空模型同档告警（不改绑定）

## 基线

`4fa2e24`。规格 10.2。禁止自动改 role_bindings / 换模 / 改 3/1。

## 允许文件

- 新增只读校验模块（建议 `tradingagents/agents/utils/model_tier_warning.py` 或现有 config 校验处的最小挂钩）
- 对应测试
- 警告写入 result_data 元数据（model_id × stance、provider、warning 列表）
- 不得改 `api/main.py` 除非无挂钩点；先停并报告

## 行为

- bull/bear 同一模型 → 可 warning“同模自我辩论”
- 不同模型但无法证明同档 → warning
- 明显跨档 → warning
- **只 warning，不修改用户配置**

TDD：三用例（同模、同档异模、跨档）。候选独立分支，不自行 FF。

完成后 mention 独立代码审核员。不要 mention 项目调度助手。
