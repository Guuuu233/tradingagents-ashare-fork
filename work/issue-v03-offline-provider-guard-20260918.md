# DAV-1050：V-03 `--offline` 真离线取价护栏

## 背景与已证实缺口

DAV-1044 在运行代码 `6ee148699339efefc2f7f7548eb286be485524e1` 上复核发现：`scripts/run_v03_return_measure.py --offline` 只跳过 healthz 探针，`VendorPriceDataProvider` 仍会通过 `route_to_vendor`、akshare 或 baostock 取价。前向窗口扩大后，读副本测量会进入外部供应商等待，无法稳定产出 V-03 T+5 结果。

## 目标

让 `--offline` 的含义变成“测量过程不触达任何外部供应商”，并且在没有本地价格快照时快速、可解释地输出 typed-missing，而不是挂在网络等待上。保留在线模式的现有行为；本卡不伪造价格，也不把 typed-missing 算成收益。

## 必须满足

1. `--offline` 下，V-03 测量路径不得调用 `route_to_vendor`、akshare、baostock 或任何非 loopback 外部 socket；不能只关闭 healthz。
2. 无本地价格数据时，结果必须明确记录 provider/network 缺口，`return=NULL`、进入 coverage 分母、不进入收益分子分母；不得返回 0、默认价或静默删除样本。
3. 如实现本地 CSV/JSON 快照入口，必须显式传入路径、记录快照 hash、限制日期不越过 `forward_oos_end_date`，并拒绝缺字段/重复冲突/未来行；不得另造一套收益公式。
4. `--forward-oos-end-date` 必须继续覆盖默认上界；任务的前向起点仍按现行协议 `trade_date >= 2026-09-09` 解释。不能用默认 `2026-09-09` 把真实前向报告机械归为 `FUTURE_DATA`。
5. 在线模式和已有 T+1 Open、T+5、PIT、typed-missing、成本、基准、回归标的隔离语义不得放宽。

## 允许修改

- `tradingagents/eval/v03_return_measure.py`
- `scripts/run_v03_return_measure.py`
- `tests/test_v03_return_measure.py`
- 必要时新增同目录下仅用于离线 provider/CLI 护栏的测试文件

禁止修改 API、生产数据库/schema/历史报告、provider 配置、role_bindings、模型/密钥、`credit_weighting_enabled`、社交 active、DAV-808 及部署脚本。禁止新增第三方依赖。

## 红队验收

- RT-1：在 `--offline` 下 monkeypatch 任意 vendor/HTTP/baostock 入口为“若被调用即失败”，完整测量仍能结束，不进入外部等待。
- RT-2：无本地价格快照时，前向样本为 typed-missing，原因可追溯，收益值为 NULL，coverage 分母守恒。
- RT-3：提供本地快照时，T+1 Open 与 T+5 只读快照取价成功，日期越界/未来 bar/缺列/冲突重复行全部 fail-closed。
- RT-4：显式 `--forward-oos-end-date 2026-09-18` 时，09-10/09-17 样本按 FORWARD_OOS 分类；默认边界行为仍有独立测试。
- RT-5：在线模式仍走既有 provider，离线开关不能改变在线默认路径。
- RT-6：生产库仅通过 SQLite backup 副本读取；备份前后 hash、计数、`quick_check` 一致。
- RT-FULL：候选与直接父使用同一 Python 3.10.20、同一测试口径对照；不得新增失败。

## 交付门禁

- 使用 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，运行时清理 `PYTHONPATH`。
- 交付完整 40 位候选 SHA、直接父、远端分支、白名单 diff、`git diff --check`、定向测试和实际命令输出。
- 不部署、不重启、不写生产库、不跑真实分析、不使用 Cookie、不启用 H1b。
- 交付状态置 `in_review`；后续只读审查固定派给**代码审核员**，禁止派给独立代码审核员，禁止实施者自审。
- 不在交付评论中 @项目调度助手，避免评论触发重复跟进；由总工读取交付状态后单独派审。
