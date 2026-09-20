# DAV-244 统一树分文件回归、挂起定位与远端推送

## 已构造的精确统一树

- 路径：`/private/tmp/ta-dav243-integration`
- 分支：`integration/dav233-dav237-v2`
- HEAD：`c956e43`；父链：
  `394e3ef → 825b0cc(stream) → be009f3(window) → c956e43(date import)`
- 4文件范围已确认；`import api.main`与`import api.services.report_service`已PASS。
- 上一run将4个辩论/API测试一起执行后，pytest PID 20899超过3分钟无返回，已kill；原因未分类。禁止把它写成代码失败，也禁止直接重跑整组。

## 任务性质
只读测试/集成交付；不得修改代码。若发现确定性代码失败，记录并blocked，不自行修。

## 分文件隔离（每条前台硬超时120秒）

在统一树cwd下，使用：
`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest <file> -q --tb=short`

依次单独运行并记录耗时/结果：
1. `tests/test_debate_state_persistence.py`
2. `tests/test_debate_rounds_configuration.py`
3. `tests/test_api_smoke.py`
4. `tests/test_dual_horizon_e2e.py`
5. `tests/test_report_service_fund_flow.py`
6. `tests/test_fund_flow_evidence.py`
7. `tests/test_smart_money_fund_flow_semantics.py`
8. `tests/test_cn_akshare_backup_sources.py`
9. `tests/test_sina_historical_fund_flow.py`

任一文件超时：记录精确文件、最后输出、进程栈/活动状态，停止；不得继续全量或宣称失败根因。

## 组合全量门

所有分文件通过后：
- `TUSHARE_TOKEN='' env -u PYTHONPATH <.venv310/python> -m pytest tests -q`，前台硬超时600秒；
- compileall；
- `git diff --check 394e3ef..HEAD`；
- `git diff --name-only`必须仅4个预期文件。

## 推送

全部门通过后，将现有分支推到：
`target/integration/dav233-dav237-v2`
并用`git ls-remote target`回读精确SHA。

## 交付

- 分文件结果与耗时；
- 全量结果；
- 最终远端SHA/父链/文件范围；
- 明确未合主干、未部署、未运行真实LLM。

不要环境考古，不要读旧issue，不要修改宿主/WIP。立即执行。