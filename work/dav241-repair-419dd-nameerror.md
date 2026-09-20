# DAV-241 单问题返修：419dd337 精确候选导入即失败

## 精确基线
- 被审候选：`target/fix/dav-237-fund-flow-window-fail-closed@419dd337bf0b4a46edb921d69c3e0ec1bba918f2`
- 直接父：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 组合树实测（精确候选checkout、Python 3.10）：
  `NameError: name 'date' is not defined`，位置 `api/services/report_service.py:425`。
- 根因：函数返回注解`Optional[date]`，文件只导入`datetime, timezone`，未导入`date`。
- DAV-238原PASS作废，因为其测试未在精确候选代码上真实执行。

## 唯一任务
从`419dd337`创建独立返修分支，仅解决该导入/运行阻塞，并验证原40项窗口测试确实在候选代码上运行。

## 边界
- 允许修改：`api/services/report_service.py`；如必须补防回归测试，仅修改`tests/test_report_service_fund_flow.py`。
- 禁止改其它逻辑、api/main、provider、collector、Prompt、配置、Key、数据库。
- 不得回退DAV-237已有fail-closed逻辑。

## 验证
在返修checkout目录、宿主`.venv310/bin/python`下实际运行：
1. `python -c 'import api.services.report_service'`
2. `tests/test_report_service_fund_flow.py`（预期40项）
3. 指定资金流相关测试
4. `TUSHARE_TOKEN=''`全量tests
5. compileall、git diff-check

## 交付
- 新远端分支/SHA，父必须为419dd337；
- diff只能是最小导入修复（及必要测试）；
- 给真实测试数；明确未合入、未部署。

立即施工，不询问。