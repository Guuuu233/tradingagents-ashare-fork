资深开发2：P1-3 已派工。请严格按本卡描述执行。

基线 SHA（完整）：`7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`
权威 brief：仓库 `work/issue-p1-3-backtest-calibration-isolation.md`

主缺口在 `backtest_service._get_price_after` 缩短 hold_days 与 `_classify_decision` 坍缩 HOLD。
校准侧已有过滤则补齐 exclusion 钉，勿整页重写。
TDD + 离线 mock；禁止实网、禁止混社交、禁止部署。

交付：分支 tip 完整 40 位 SHA + 定向 pytest 数字 + diffstat，卡置 `in_review`。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
