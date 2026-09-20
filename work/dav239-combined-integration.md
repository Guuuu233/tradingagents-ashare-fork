# DAV-239 两P0统一集成分支与组合树回归

## 精确输入

- 目标基线：`target/codex/dav-4-p2a-trunk@394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 候选A：`target/agent/worker1/01a020e5@a3271882736c407391c276983a8bddcf16f55bda`
  - 修改：`api/main.py`、`tests/test_debate_state_persistence.py`
  - DAV-236独立终审PASS：93定向、1434全量/1 skipped、compileall/diff-check通过。
- 候选B：`target/fix/dav-237-fund-flow-window-fail-closed@419dd337bf0b4a46edb921d69c3e0ec1bba918f2`
  - 修改：`api/services/report_service.py`、`tests/test_report_service_fund_flow.py`
  - DAV-238独立终审PASS：40专项、43关联、1471全量/1 skipped、compileall/diff-check通过。
- 两提交均直接父为394e3ef，文件不重叠。

## 唯一owner与隔离要求

1. 必须先创建独立checkout/worktree，不得直接编辑宿主主干。
2. 从394e3ef创建新的远端集成分支，例如`integration/dav233-dav237`。
3. 固定顺序：先合入`a327188`，再合入`419dd337`；可使用cherry-pick，禁止强推主干。
4. 如果出现冲突，停止并报告；不得手工改业务逻辑绕过冲突。

## 组合树验证

在统一集成SHA上运行：

1. `tests/test_debate_state_persistence.py`
2. `tests/test_debate_rounds_configuration.py`
3. `tests/test_api_smoke.py`
4. `tests/test_dual_horizon_e2e.py`
5. `tests/test_report_service_fund_flow.py`
6. `tests/test_fund_flow_evidence.py`
7. `tests/test_smart_money_fund_flow_semantics.py`
8. `tests/test_cn_akshare_backup_sources.py`
9. `tests/test_sina_historical_fund_flow.py`
10. `TUSHARE_TOKEN='' env -u PYTHONPATH <宿主.venv310/python> -m pytest tests -q`
11. compileall、git diff-check。

## 静态验收

- 组合diff只能包含四个业务/测试文件组：`api/main.py`、`api/services/report_service.py`及两份对应测试；不得夹带配置、Prompt、Provider、用户设置、密钥。
- 核验stream状态累计和资金窗口校验均在统一树存在。
- 不运行真实LLM、不部署、不重启、不改用户配置。

## 交付

- 推送新的远端集成分支与精确SHA；
- 给出父链、合入顺序、changed files、测试结果；
- 明确尚未进入主干、未部署、未复验。

立即执行。不得只输出合入建议。