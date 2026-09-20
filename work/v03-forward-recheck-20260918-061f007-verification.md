# V-03 离线护栏合入后独立核验（2026-09-18）

## 结论

`061f007ebf58692024c78772cb21e1429695a569` 上的 V-03 离线护栏可用，且不需要部署到 8000。它只涉及离线评估脚本、评估模块和测试，不进入 API 服务导入链。

## 独立测试

- 干净 detached worktree：`/private/tmp/ta-verify-v03-061f007`
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`
- 环境：已清理 `PYTHONPATH` 与代理变量；`-p no:randomly`
- 范围：`tests/test_v03_offline_guard.py`
- 结果：**43 passed in 3.52s**

## 生产库只读评估

- 生产库：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`
- 只读备份副本：`/private/tmp/ta-verify-v03-061f007/replica.db`
- 备份前后生产库 SHA-256：`e884d3ceb6b44e4af712f64dd17d5387a966ec4f7d3b190ba9c1f3d26dc47518`，一致
- 生产库 `quick_check=ok`、`integrity_check=ok`，WAL=0
- 生产库报告计数：`1410`（`794 completed` / `616 failed`）
- 离线命令显式使用 `--offline --no-ablations --forward-oos-end-date 2026-09-18`
- 读取 completed 报告：233；评估候选：229；规范化合格：231；入池：0
- `FORWARD_OOS`：2 条；有效收益：0；typed-missing：231；覆盖率：0.00%
- 无本地价格快照时，价格缺口被明确记为 typed-missing，没有外连等待、没有虚构收益

## 运行态回读

- 评估结束后生产库仍为 `1410 / 794 / 616`
- 生产库 SHA、`quick_check`、`integrity_check` 均未变化
- 8000 `/healthz` 仍为 `6ee148699339efefc2f7f7548eb286be485524e1`，`executor_queued=0`
- 本次没有调用分析 API、没有写生产库、没有部署或重启服务

## 尚不能宣称的内容

这证明的是离线工具和零写入边界，不是 V-03 收益结论。正式 FORWARD_OOS 仍需要：有效前向样本达到总工明确的阈值，以及对应的本地 T+5 价格快照（或另行授权在线取价）。
