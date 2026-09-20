资深开发2：P1-2 已派工。请严格按本卡描述执行。

基线 SHA（完整）：`aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`
权威 brief：仓库 `work/issue-p1-2-capitulation-reversal.md`

TDD：先写会失败的 `tests/test_capitulation_reversal.py`，再扩 `_compute_vpa_indicators` + 最小决策接线。
禁止偷看 T+1、禁止混社交/新闻、禁止部署、禁止恢复假摔洗盘确定性话术。
异步测用 `asyncio.run`。

交付：分支 tip 完整 40 位 SHA + 定向 pytest 数字 + diffstat，卡置 `in_review`。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
