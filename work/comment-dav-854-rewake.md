重派精简实现任务（上一运行因使用 Python 3.14 已取消；没有代码交付）。

基线固定为 `7810f19875890725cd26e414cde272063fb3606a`，只做一个缺陷：在 `tradingagents/agents/utils/decision_status.py` 中让普通“被反驳命题、无 PIT/前视失败”回到 `WAIT`；PIT/前视失败仍必须 `ABSTAIN / NO_TRADE / BLOCKED`。测试只允许新增，不得修改或删除既有 `WAIT` 断言；禁止改配置、数据库、部署、DAV-808 和白名单外代码。

先执行并记录：`test -x /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`。后续所有项目测试必须用 `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest`；若该解释器不可见，立即报告环境阻塞，不得改用 3.14。

最小交付：代码改动、RT-1~RT-7 定向测试、`git diff --check`、完整 40 位候选 SHA/父提交/远端回读；全量对照留到代码交付后由总工验收。完成后置 `in_review`，不要合入或部署。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
