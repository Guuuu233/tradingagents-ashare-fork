# DAV-1050 返修：离线快照未知 ST 与非法日期必须 fail-closed

## 固定基线

- 被打回候选：`7a3034741740aec83f70f0b9a4327fa87873006c`
- 该候选直接父：`6ee148699339efefc2f7f7548eb286be485524e1`
- 本返修必须从 `7a3034741740aec83f70f0b9a4327fa87873006c` 直接追加新提交，不得重写旧 SHA，不得把旧审查结论复用到新 SHA。
- 远端分支使用新的返修分支；提交后必须推送并报告完整 40 位 SHA。

## 只修两个问题

### 1. 未知 ST 状态不能当作非 ST

文件：`tradingagents/eval/v03_return_measure.py`

`OfflineSnapshotPriceDataProvider.is_st()` 在 metadata 存在但同时缺少 `st` 与 `st_dates` 时，必须返回 `None`，让上游进入 `offline_metadata_unavailable` 的 typed-missing 路径；不得返回 `False` 把未知状态当作已验证非 ST。

- 明确提供 `st: false` 的既有语义必须保持；明确提供 `st: true` 仍排除。
- 明确提供 `st_dates` 的语义必须保持；不要因为本次返修放宽 ST 排除。
- 未知 ST 的样本必须保留 coverage 分母，不能产生收益或被静默丢掉。

### 2. 快照日期必须严格合法

文件：`tradingagents/eval/v03_return_measure.py`

`_check_date_bound()` 必须 fail-closed 校验严格的 `YYYY-MM-DD` 日历日期（例如使用正则/严格 `datetime.strptime`，且拒绝不存在的月份/日期），再执行 `forward_oos_end_date` 上界判断。

- `trade_dates`、`bars[].date` 必须走同一严格校验；可选 `metadata.st_dates` 也必须拒绝非法日期，不能通过 `[:10]` 截断绕过。
- `2026-99-99`、`2026-02-30`、`2026/09/18`、`2026-0a-10` 等输入必须抛出 `PriceSnapshotValidationError`。
- 合法日期、T+1/T+5 计算、显式 `forward_oos_end_date` 上界和既有 PIT 语义不得改变。

## 测试要求

只在 `tests/test_v03_offline_guard.py` 增加/调整最小行为测试：

1. metadata 没有 `st`/`st_dates` 时结果为 typed-missing、收益为 `None`、coverage 分母不变。
2. 显式 `st: false` 仍可进入正常测量；显式 `st: true` 仍排除。
3. `trade_dates`、bar 日期和（若支持）`st_dates` 的非法日期均被拒绝；合法日期快照仍能完成 T+1/T+5 测量。
4. 删除这两处修复后，上述行为测试应失败；不能只检查日志或状态字段。

禁止修改 `scripts/run_v03_return_measure.py`、生产 API、provider 路由、数据库、部署配置、H1b、社交功能或个人模型/凭据配置。

## 交付门禁

- 改动白名单严格为：`tradingagents/eval/v03_return_measure.py`、`tests/test_v03_offline_guard.py`。
- `git diff --check 7a3034741740aec83f70f0b9a4327fa87873006c..HEAD` 干净，工作树干净。
- 固定解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，版本必须为 Python 3.10.20。
- 定向测试：新增离线护栏套件与既有 `tests/test_v03_return_measure.py`，给出精确计数。
- 产品代码变更必须在隔离临时库上跑 RT-FULL（`-q -p no:randomly`，按已记录的 DAV-979 例外 deselect），并报告实际失败集合；不得写生产库、调用真实模型或真实供应商。
- 完成后只置为 `in_review` 并交付新 SHA；不得自行合入、部署或 @ 项目调度助手，避免重复触发。
