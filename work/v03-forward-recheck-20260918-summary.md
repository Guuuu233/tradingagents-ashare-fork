# V-03 FORWARD_OOS 触发条件复核（DAV-1044，只读，2026-09-18）

## 结论

**未达到现行协议钉死的可评测规模，且当前工具无法在无外连供应商条件下给出 V-03 T+5 前向结果。** 冻结表（`work/v03-freeze-sheet-20260909.md`）没有为 FORWARD_OOS 钉死最小样本数阈值，因此不能自行补一个数字。当前 `FORWARD_OOS >= 2026-09-09` 口径下有 2 条 completed，其中仅 1 条已持久化 T+1 实际值，另 1 条评估日未收盘而 pending。即使测量工具可运行，n=1 也不能作为实验结论。

## 运行基线

- 实际代码 SHA：`6ee148699339efefc2f7f7548eb286be485524e1`（worktree `/private/tmp/ta-serve-6ee1486`；主 checkout 当前 HEAD 为 `4fa7681`，未使用）
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20），已清理 `PYTHONPATH` 与 http(s)/all_proxy 变量
- 副本：`/private/tmp/ta-serve-6ee1486/work/v03-forward-recheck-20260918-replica.db`，SHA-256 `35bd0e07c7d4873f00afd1a73a72bb82fc111ab2412a74280a879065f0b7c971`，`quick_check=ok`
- 生产库文件 SHA：备份前后及复核结束时均为 `e884d3ceb6b44e4af712f64dd17d5387a966ec4f7d3b190ba9c1f3d26dc47518`；逻辑计数仍为 `1410 / 794 completed / 616 failed`

## 09-09 之后样本盘点（账号 `429163f7-50b6-4982-8bdf-96ae99506843`）

| report_id | trade_date | symbol | status | historical_cases 实际值 | 判定 |
|---|---|---|---|---|---|
| `6350b7449b8e4aeabec61914f6a3270b` | 2026-09-10 | `600873.SH` | completed | `eval_date=2026-09-11`，`actual_change_pct=-2.07%` | 已评估（1） |
| `f8c59465b4934717ac3327c7abfc70af` | 2026-09-17 | `600519.SH` | completed | `eval_date=2026-09-18`，`actual_change_pct=NULL`，`【数据缺失】`，claims 含 `eval_date_not_closed` | pending（1） |

- completed（`trade_date >= 2026-09-09`）：**2**；已评估：**1**；pending：**1**；failed：0。
- run_sha 溯源：09-10 样本 `4a5206f6`，09-17 样本 `df753841`；两者与历史样本生成 SHA `a6d4540` 不同，已如实记录。
- 上表的 `actual_change_pct` 是历史案例当前已持久化的实际值；它不是 V-03 默认 T+5 收益测量结果。两个报告的 `shadow_credit_metrics.t_plus_5` 均为 `None`。

## 日期口径与工具缺口

- 引擎默认 `DEFAULT_FORWARD_OOS_END_DATE="2026-09-09"` 是上界参数，不是任务的前向起点；任务口径为 `FORWARD_OOS >= 2026-09-09`。
- 先前以默认上界产出的 `report.json` 显示 `FORWARD_OOS=0`，只是边界参数造成的机械结果，已作废，不作为本次日期复跑结论。
- 使用 `--forward-oos-end-date 2026-09-18` 后，`--offline` 只关闭 healthz 探针，并没有关闭 `VendorPriceDataProvider` 的供应商取价。测量阶段需要外连供应商，临时进程在有界等待后未产出 T+5 结果即停止。
- 因此当前能确认的是样本盘点和持久化回填状态，不能声称已经得到 V-03 T+5 前向收益。

## 生产库与边界

- 生产库通过 `mode=ro` 连接执行 SQLite `.backup()`；复核过程中生产库文件 SHA 保持不变，`quick_check=ok`。
- 未写 `data/tradingagents.db`，未调用分析 API，未产生新报告，未启用社交采集，未使用 Cookie，未修改 `credit_weighting_enabled`，未部署，未合入。

## 下一触发条件

1. 先由总工明确 V-03 的“可评测规模”阈值；现行冻结协议没有钉死该数字。
2. 账号在 `trade_date >= 2026-09-09` 的 completed 样本及已实际回填的有效评测达到该阈值；`600519.SH` 的 `eval_date=2026-09-18` 必须等收盘后实际回填，不能提前计算。
3. V-03 重跑需要允许外连供应商取价，或另开工程卡，给测量引擎增加真正的离线价格数据源/离线开关，并保留 PIT 与 T+5 的证据边界。

## 产物

完整临时产物位于 `/private/tmp/ta-serve-6ee1486/work/v03-forward-recheck-20260918-*`；其中第一次使用默认上界生成的 `report.md/json` 已标作废，本 summary 才是本次可采信的结论。
