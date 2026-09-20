# DAV-233 P0：隔离worktree修复单horizon流式状态丢失

## 强制隔离步骤

1. 先执行：
   `multica repo checkout https://github.com/Guuuu233/1.git --ref 394e3efe08fef60f728345cc8eb9300c8ad0d693`
2. 记录工具返回的绝对路径，必须`cd <返回路径>`后运行`git rev-parse HEAD`。
3. 禁止在宿主 `/Users/davidliu/Documents/TradingAgents-AShare` 直接编辑、测试、commit或push。
4. 若checkout失败，评论blocked并停止，不得回退到宿主写代码。

## 生产证据

- Report `dd2d5cf259db490d946f18ed38704718` completed，但investment/risk debate count均0。
- `api/main.py`普通单horizon stream_events路径约3263：`final_state = chunk`。
- LangGraph流式chunk可能是局部updates；后续仅final decision/空初始debate chunk会覆盖前面已累积状态。
- 验收脚本另有`report_text`列漂移，非本任务范围。

## TDD RED

在现有测试中扩展FakeGraph，使`astream`依次yield：
1. analyst/report chunk；
2. `investment_debate_state` count=6、Bull/Bear history/judge/claims；
3. `risk_debate_state` count=9、三方history/judge/claims；
4. final chunk只含`final_trade_decision`，或带空初始debate dict。

当前主干必须先复现最终result debate丢失/归零。

## 修复约束

- 仅修改`api/main.py`普通单horizon流式累计逻辑及一个现有相关测试文件。
- 使用累计状态，后续缺字段不删除旧字段；空初始debate dict不得覆盖已有非空debate；真实非空更新可覆盖。
- 不改双horizon、Graph拓扑、Prompt、轮次、用户配置、Provider/Key、report_service。
- 增加断言request `config_overrides=3/3`最终传入TradingAgentsGraph config。

## 交付

- 独立远端分支和精确SHA，直接父必须为394e3ef。
- RED→GREEN命令和输出。
- 定向辩论/API测试、`TUSHARE_TOKEN=''`全量、compileall、git diff-check。
- 评论明确未合入/未部署/未跑真实LLM。

立即施工，不询问，不写计划代替代码。