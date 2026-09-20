# H1b 门槛复核（2026-09-18）

## 结论

当前仍不得启用 H1b 信用加权：两个明确的 cohort 均未通过 7 维系统门槛，程序建议 `KEEP_FALSE`。本次只读复核没有修改生产库，也没有修改 `credit_weighting_enabled`。

## 复核边界

- 运行代码：`6ee148699339efefc2f7f7548eb286be485524e1`
- Python：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（3.10.20）
- 数据来源：生产库通过 SQLite `.backup()` 生成的只读副本 `/private/tmp/ta-h1b-recheck-20260918.db`
- 生产库当时 SHA256：`e884d3ceb6b44e4af712f64dd17d5387a966ec4f7d3b190ba9c1f3d26dc47518`
- 只读副本 SHA256：`35bd0e07c7d4873f00afd1a73a72bb82fc111ab2412a74280a879065f0b7c971`
- 两端 `PRAGMA quick_check` 均为 `ok`；生产库在备份前后 SHA 保持不变。
- 生产库逻辑计数：`1410` reports，`794 completed`，`616 failed`。

文件级 SHA 是数据库文件布局的证据，不能替代逻辑数据守恒；本次以 `quick_check`、计数及目标行回读共同证明只读复核未改变业务数据。

## 三段台账总量

当前程序从 794 份 completed 报告中得到：

`794 原始样本 → 132 合格 v2 结构化辩论样本 → 9 份 D-009 合格样本`

非 v2 排除 662 份；D-009 进一步排除 123 份，其中 `legacy_null=69`、`abstain=37`、`invalid_run=2`、`data_error=0`、`no_trade=0`、`wait=15`。

## Cohort 1：`legacy_unversioned`

- 分层后样本：`7`
- 7 维系统状态：`FAIL`
- 建议：`KEEP_FALSE`
- 关键失败：样本量 `7/60`、标的数 `7/20`、多空样本 `1/5`（两侧均需至少 25）、Verified Claims `33/36`（两侧均需至少 100）、自然日 `21/45`、交易日 `3/30`、T+5 完整率 `85.7%/95%`、多头占比 `16.7%`（要求 40%–60%）。
- 仅“加权幅度范围”这一维通过；不能据此开启功能。

## Cohort 2：`decision_model.v1:evidence_contract.v1:price_basis.unspecified`

- 分层后样本：`2`
- 7 维系统状态：`FAIL`
- 建议：`KEEP_FALSE`
- 关键失败：样本量 `2/60`、标的数 `1/20`、行业数 `1/5`、多空样本 `0/0`、Verified Claims `9/11`、自然日 `5/45`、交易日 `2/30`、T+5 完整率 `0%/95%`、单标的占比 `100%`。
- 仅“加权幅度范围”这一维通过；不能据此开启功能。

## 可复核产物

- `/private/tmp/ta-h1b-legacy-20260918.json`
- `/private/tmp/ta-h1b-v1-20260918.json`
- `/private/tmp/ta-h1b-legacy-20260918.log`
- `/private/tmp/ta-h1b-v1-20260918.log`

后续动作仍是积累满足协议的真实样本后重新复核；在此之前保持 `credit_weighting_enabled=false`。
