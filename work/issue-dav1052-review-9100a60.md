# DAV-1052 返修候选 `9100a607`：代码审核员同 SHA 只读复审

## 复审目标

请只读复审候选 `9100a6070d0bcb32b8d16467fd9ac2811e4dc648`，不要复用 DAV-1051 对旧 SHA `7a3034741740aec83f70f0b9a4327fa87873006c` 的结论。候选必须以其直接父 `7a3034741740aec83f70f0b9a4327fa87873006c` 为对照。

本次返修只针对上次复审指出的两项合同缺口：

1. `OfflineSnapshotPriceDataProvider.is_st()` 在 metadata 存在但同时缺少 `st` 与 `st_dates` 时，必须返回 `None`，上游必须形成 `offline_metadata_unavailable` typed-missing；不能把未知状态当作 `False`。
2. `_check_date_bound()` 必须严格拒绝非 `YYYY-MM-DD` 或不存在的日历日期，并覆盖 `trade_dates`、`bars[].date` 和 `metadata.st_dates`，不能靠 `[:10]` 截断绕过。

同时确认显式 `st: false`、显式 `st: true`、合法日期、T+1/T+5、forward OOS 上界和既有 PIT 语义没有被放宽。

## 固定审查边界

- 直接父：`7a3034741740aec83f70f0b9a4327fa87873006c`
- 候选：`9100a6070d0bcb32b8d16467fd9ac2811e4dc648`
- 允许改动且必须逐项核对：
  - `tradingagents/eval/v03_return_measure.py`
  - `tests/test_v03_offline_guard.py`
- 不得出现其他文件、生产 API、provider 路由、数据库、部署配置、H1b、社交功能或个人模型/凭据配置改动。

## 必查证据

使用固定解释器：

```text
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
```

核验完整 40 位 SHA、直接父、远端分支、改动白名单和 `git diff --check`。运行新增离线护栏测试及既有 `tests/test_v03_return_measure.py`，给出精确计数；行为测试必须证明删除两处修复后会失败，不能只检查日志或状态字段。产品代码改动还须在隔离临时库跑 RT-FULL（`-q -p no:randomly`，沿用已记录的 deselect），报告候选与父版本的实际失败集合；不得写生产库、调用真实模型或真实供应商。

请将结论限定为同 SHA 代码审查结果：逐项列出 🔴/🟡/🟢、是否 PASS、测试范围和未覆盖边界。审查只读，不改代码、不提交、不合入、不部署，不创建后续任务，也不要在评论中 @ 项目调度助手。
