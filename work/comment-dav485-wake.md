资深开发2：P1-1 已派工。请严格按本卡描述执行。

基线 SHA（完整）：`3466e05a6483861cf071d4548b7bee990ac3c774`
权威 brief：仓库 `work/issue-p1-1-news-event-coverage.md`

TDD：先写会失败的 `tests/test_news_event_coverage.py`，再实现 `news_event_evidence.py` + 最小接线。
禁止实网测、禁止混社交、禁止部署、禁止宣称蓝思/R2 案例已修。
异步测用 `asyncio.run`，不要 `@pytest.mark.asyncio`。

交付：分支 tip 完整 40 位 SHA + 定向 pytest 数字 + diffstat，卡置 `in_review`。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
