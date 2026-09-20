环境纠偏：当前运行记录显示，Multica checkout 内没有项目专用 Python，刚才使用的是系统 Python 3.14；该次 `tests/test_confirmation_gate.py` 结果不计入验收证据，也不能据此判断代码状态。

请在同一工作树中改用宿主机已核验的 Python 3.10 解释器：

`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly ...`

若该绝对路径在执行环境不可见，立即在卡内报告为环境阻塞并停止重复测试；不得用 Python 3.14、其他 checkout 或“等价环境”冒充 DAV-854 的 RT-7/RT-FULL 证据。代码范围和验收口径不变。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
