## 固定对象

- 父候选远端：`agent/agent/e185bf277a24@f8ffe4764773276c04e1228d2a76eb5013c51369`
- 目标trunk仍为：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- f8ffe47行为测试已完成：Opening 16 passed；prompt/custom 186 passed；核心协议/P1-M 248 passed；replay全绿；compileall全绿。
- 但Hermes独立`git diff --check ccc4c53..f8ffe47`发现两处EOF多余空行，故f8不可复审/合入。

## 唯一任务

从远端精确`f8ffe4764773276c04e1228d2a76eb5013c51369` fresh checkout，仅删除以下两个文件末尾多余空行：

1. `tests/test_debate_opening_protocol.py`
2. `tradingagents/agents/utils/debate_utils.py`

禁止修改任何行为、文本、测试断言、prompt、其他文件。

## 验收

- `git diff --name-only f8ffe47..HEAD`恰好上述2文件；
- `git diff --check ccc4c53..HEAD`无输出；
- 宿主Python3.10：Opening 16项、核心248项、prompt/custom 186项全部复跑并保持原计数；
- replay与compileall通过；
- 新远端分支/SHA，直接父=f8ffe47；禁止amend/force push旧分支；
- 报告远端branch、精确SHA、parent、测试终态；明确未合入/未重启/未上线。

禁止主干、服务、DB、配置、api/main.py、conditional_logic.py、setup.py；不要mention项目调度助手。