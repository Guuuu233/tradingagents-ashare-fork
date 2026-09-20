# DAV-216：DAV-214/215 候选格式窄返修

## 精确基线

- 远端分支：`target/agent/1/01a01fdf`
- 当前候选：`7c2f19ab3f5d1176b235306bf3421fdbc14570b5`
- 当前主干：`7ef89f63662ce01bacdcda9dd0996060ce903c83`
- 候选是主干后代，ahead 3 / behind 0。

## 唯一任务

`git diff --check 7ef89f63..7c2f19ab`发现3处尾部多余空行：

- `tests/test_debate_state_persistence.py:903`
- `tests/test_fund_flow_evidence.py:459`
- `tests/test_smart_money_fund_flow_semantics.py:157`

只删除这3处文件末尾多余空行，不得修改任何业务代码、测试断言、配置、Prompt、Provider、Key或用户设置。

## 验收

1. 新提交必须直接基于`7c2f19ab3f5d1176b235306bf3421fdbc14570b5`；
2. `git diff <旧SHA>..<新SHA>`只能显示3处EOF空行删除；
3. `git diff --check 7ef89f63..<新SHA>`退出0；
4. 重跑：
   - `tests/test_debate_state_persistence.py`
   - 资金流专项测试
5. 推送到新独立远端分支，不能覆盖当前`agent/1/01a01fdf`；
6. 给出远端分支、新SHA、父SHA和测试结果。

禁止合入、部署、重启或运行真实报告。立即施工。