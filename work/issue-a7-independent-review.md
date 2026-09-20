# Track A7 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/dev2/a7-persist-report-industry`
- 候选 tip SHA：`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`
- 父 / 基线：`503aa1606161918ba25e77dad40ec2e8df652461`
- 关联：DAV-547
- brief：`work/issue-a7-persist-report-industry.md`

## 期望

- completed 写路径把可验证 industry 写入 `result_data.instrument_context.industry`（或与 A6 提取一致的槽位）
- 缺失 → None；禁止硬编「未知行业」
- **无** `ReportDB.industry` 列 / migration
- 可选 dry-run 回填脚本幂等
- 不开加权；不部署（交付文案若写「生产已具备」视为无效声明）

## 动作

detached checkout tip；diff 对照范围；`.venv310` 跑 `tests/test_report_industry_persistence.py`（及 h1b/shadow 相关）。

## 禁止

改代码 / FF / 部署 / @调度助手催合入；PASS ≠ 准予合入。

## 交付

✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。
