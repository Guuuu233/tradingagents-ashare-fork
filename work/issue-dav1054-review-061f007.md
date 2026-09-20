# DAV-1054 返修候选 `061f007`：代码审核员同 SHA 只读复审

## 复审目标

请只读复审候选 `061f007ebf58692024c78772cb21e1429695a569`，以直接父 `9100a6070d0bcb32b8d16467fd9ac2811e4dc648` 为对照。不要复用 DAV-1053 对 `9100a607` 的打回结论，也不要把前两轮的测试计数当作本轮证据。

本轮唯一修复目标是：`trade_dates` 与 `bars[].date` 必须对未经截断的原始去空白字符串先做严格 `YYYY-MM-DD` 格式和真实日历日期校验，通过后才保存 canonical 日期；带时间、时区或其他后缀的字符串不得再通过 `[:10]` 绕过。确认 DAV-1052 已修好的未知 ST typed-missing、显式 `st:false`/`st:true`、`st_dates`、T+1/T+5、forward OOS 上界与既有 PIT 语义继续成立。

## 固定审查边界

- 直接父：`9100a6070d0bcb32b8d16467fd9ac2811e4dc648`
- 候选：`061f007ebf58692024c78772cb21e1429695a569`
- 允许改动且必须逐项核对：
  - `tradingagents/eval/v03_return_measure.py`
  - `tests/test_v03_offline_guard.py`
- 不得出现其他文件、生产 API、provider 路由、数据库、部署配置、H1b、社交功能或个人模型/凭据配置改动。

## 必查证据

使用固定解释器：

```text
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
```

核验完整 40 位 SHA、直接父、远端分支、改动白名单和 `git diff --check`。运行新增离线护栏套件与既有 `tests/test_v03_return_measure.py`，给出精确计数；至少现场验证带时间后缀的 `trade_dates` 和 `bars[].date` 都被拒绝，并做变异验证证明删除本轮顺序修复后新增测试会失败。产品代码变更须在隔离临时库跑 RT-FULL（`-q -p no:randomly`，沿用 DAV-979 的 deselect），报告候选与直接父的实际失败集合；不得写生产库、调用真实模型或真实供应商。

请将结论限定为同 SHA 代码审查结果：逐项列出 🔴/🟡/🟢、是否 PASS、测试范围和未覆盖边界。审查只读，不改代码、不提交、不合入、不部署，不创建后续任务，也不要在评论中 @ 项目调度助手，避免重复触发。
