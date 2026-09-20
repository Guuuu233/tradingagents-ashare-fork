# DAV-229 P0微任务：普通单horizon astream累加状态

## 精确基线
- `target/codex/dav-4-p2a-trunk@394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 生产报告`dd2d5cf259db490d946f18ed38704718`：completed，但investment/risk debate count均0。
- 宿主审计DAV-226：普通POST /v1/analyze路径可疑代码在`api/main.py`约3261-3265：`async for chunk ... final_state = chunk`；双horizon路径使用累计状态。

## 唯一关注点
只修普通单horizon流式路径的最终状态累加。一个写入者。

## 必须先做RED
在现有`tests/test_debate_state_persistence.py`或最贴近的现有测试文件增加生产可达测试：Fake graph连续yield多个部分chunk：
1. 前一chunk含非空investment_debate_state(count=6)、risk_debate_state(count=9)、judge/claims；
2. 后一chunk只含final_trade_decision或空初始debate状态；
3. 当前代码最终result会丢失/归零，测试必须先失败。

## 实现
- 修改原`api/main.py`单horizon `astream`循环，用累计dict保存每个chunk更新，禁止`final_state = chunk`覆盖历史状态。
- 嵌套state如果后续返回空初始dict，不得覆盖已经非空的debate state；但真实非空更新必须可覆盖。
- 不改双horizon、Graph拓扑、Prompt、轮次默认、用户配置、Provider/Key、report_service。
- 修复后验证request override 3/3仍传入Graph config。

## 交付
- 独立远端分支，新SHA必须直接基于394e3ef；不得覆盖主干。
- RED→GREEN输出。
- 定向测试、相关API/辩论测试、`TUSHARE_TOKEN='' pytest tests -q`、compileall、git diff --check。
- 评论必须给分支、SHA、父SHA、实际测试数、明确未合入/未部署/未跑真实报告。

立即施工，不询问，不写计划代替代码。