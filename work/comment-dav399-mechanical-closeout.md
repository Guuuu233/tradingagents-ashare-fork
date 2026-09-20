当前行为门已满足：Opening专项16 passed；核心协议/P1-M矩阵原始输出确认248 passed, 1 warning。现在停止行为修改，只做机械收口：

1. 确认并报告prompt/custom矩阵186项的真实终态；若无完整终态，冻结编辑后重跑该同一命令。
2. 删除`tradingagents/agents/utils/debate_utils.py` EOF多余空行；`git diff --check ccc4c53..工作树`必须无输出。
3. 冻结编辑后重跑：
   - Opening 16项；
   - 核心248项同一命令；
   - prompt/custom 186项同一命令；
   - `env -u PYTHONPATH .../.venv310/bin/python tests/golden/audit_20260823/replay_verifier.py`；
   - compileall。
4. 确认changed files仅9个允许路径（8 tracked + Opening测试），`api/main.py`/`conditional_logic.py`/setup.py/manager/DB/config均未改。
5. 创建新分支（不要覆盖旧ref），提交并推送；回报远端branch、精确SHA、直接父ccc4c53、各测试终态。明确未合入/未重启/未上线，3/1未改。

禁止继续重构、增加功能或弱化测试；不要mention项目调度助手。