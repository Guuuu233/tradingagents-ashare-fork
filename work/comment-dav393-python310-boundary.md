环境提醒：你刚准备/执行的裸`pytest`不能计为验收证据（系统默认可能是Python3.14）。从现在起所有RED/GREEN与矩阵必须显式使用：

`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest ...`

并在首次有效RED前打印该解释器`Python 3.10.20`。不要用裸pytest或当前worktree内不存在的venv冒充。继续现有fresh a2b7515唯一worktree，不创建第二run、不提交前省略C1→C4垂直RED/GREEN。