# DAV-1053 返修：日期严格校验前不得截断

## 固定基线

- 被打回候选：`9100a6070d0bcb32b8d16467fd9ac2811e4dc648`
- 该候选直接父：`7a3034741740aec83f70f0b9a4327fa87873006c`
- 本次返修必须从 `9100a6070d0bcb32b8d16467fd9ac2811e4dc648` 直接追加新提交，不得重写旧 SHA，也不得复用 DAV-1053 的打回结论。
- 使用新的返修分支，提交后推送并报告完整 40 位 SHA。

## 唯一阻塞缺口

代码审核员在 DAV-1053 同 SHA 复审中现场验证：`trade_dates` 和 `bars[].date` 在调用严格日期校验前仍执行 `[:10]`，因此像 `2026-09-10T00:00:00` 这样的非纯 `YYYY-MM-DD` 字符串会被静默接受；`metadata.st_dates` 已正确拒绝同类输入。

修复 `tradingagents/eval/v03_return_measure.py`：对 `trade_dates` 和 `bars[].date` 使用未经截断的原始去空白字符串进行严格格式与真实日历日期校验，校验通过后才保存 canonical `YYYY-MM-DD`。不得放宽既有 `forward_oos_end_date` 上界、T+1/T+5、PIT、显式 `st:false`、显式 `st:true` 或未知 ST typed-missing 语义。

## 测试要求

只在 `tests/test_v03_offline_guard.py` 补最小行为测试：

1. `trade_dates` 拒绝 `2026-09-10T00:00:00` 等带时间后缀字符串；
2. `bars[].date` 拒绝同类带时间后缀字符串；
3. 合法 `YYYY-MM-DD` 仍可完成 T+1/T+5 测量；
4. 保留并继续通过已有未知 ST、显式 ST 和非法日历日期测试；
5. 删除本次“先严格校验、后 canonical 化”的顺序修复后，新增测试必须失败，不能只看日志。

## 交付门禁

- 改动白名单严格为：`tradingagents/eval/v03_return_measure.py`、`tests/test_v03_offline_guard.py`。
- `git diff --check 9100a6070d0bcb32b8d16467fd9ac2811e4dc648..HEAD` 干净，工作树干净。
- 固定解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，版本必须为 Python 3.10.20。
- 运行定向测试并给出精确计数；产品代码变更须在隔离临时库跑 RT-FULL（`-q -p no:randomly`，沿用 DAV-979 的 deselect），报告实际失败集合。不得写生产库、调用真实模型或真实供应商。
- 完成后只置为 `in_review` 并交付新 SHA；不得自行合入、部署或 @ 项目调度助手，避免重复触发。
