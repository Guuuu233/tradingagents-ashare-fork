# DAV-243 修正后的三提交统一集成与组合树回归

## 精确基线和提交链

- 基线主干：`target/codex/dav-4-p2a-trunk@394e3efe08fef60f728345cc8eb9300c8ad0d693`
- A：`a3271882736c407391c276983a8bddcf16f55bda`，父为394e3ef；单horizon状态累计；DAV-236终审PASS。
- B：`419dd337bf0b4a46edb921d69c3e0ec1bba918f2`，父为394e3ef；资金窗口fail-closed主体。
- C：`09cee5d765b80396763a17b3e682ac96cf04ead1`，父为419dd337；补`datetime.date`导入；DAV-242精确复审PASS。

**固定集成顺序**：从394e3ef开始，依次cherry-pick A → B → C。禁止只挑C而遗漏B。

## 隔离和边界

1. 使用任务自己的独立clone/worktree，禁止在宿主主干直接cherry-pick、commit或push。
2. 创建全新分支`integration/dav233-dav237-v2`，禁止重写旧DAV-240临时分支。
3. 任一cherry-pick冲突则停止并报告，不允许手工改业务逻辑。
4. 不运行真实LLM、不部署、不重启、不修改用户配置/Key/Provider。

## 组合树必须验证

在最终统一HEAD运行：

1. import smoke：`import api.main`、`import api.services.report_service`。
2. 辩论/API定向：
   - `tests/test_debate_state_persistence.py`
   - `tests/test_debate_rounds_configuration.py`
   - `tests/test_api_smoke.py`
   - `tests/test_dual_horizon_e2e.py`
3. 资金流：
   - `tests/test_report_service_fund_flow.py`
   - `tests/test_fund_flow_evidence.py`
   - `tests/test_smart_money_fund_flow_semantics.py`
   - `tests/test_cn_akshare_backup_sources.py`
   - `tests/test_sina_historical_fund_flow.py`
4. `TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q`
5. `compileall -q api tradingagents tests`
6. `git diff --check 394e3ef..HEAD`

## 静态验收

- 最终diff只能包含：
  - `api/main.py`
  - `api/services/report_service.py`
  - `tests/test_debate_state_persistence.py`
  - `tests/test_report_service_fund_flow.py`
- 确认`report_service.py`含`from datetime import date, datetime, timezone`。
- 确认single-horizon累计逻辑与40项窗口测试同时存在。
- 父链应为：`394e3ef → <A重放> → <B重放> → <C重放>`。

## 交付

- 推送全新远端集成分支及最终精确SHA；
- 记录cherry-pick映射、父链、4文件diff、真实测试结果；
- 明确未进主干、未部署、未复验。

立即执行机械集成，不做环境考古或方案讨论。