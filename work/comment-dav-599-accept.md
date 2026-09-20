## Cursor 验收：R2 仍只有 news fixture

抽核 tip `e10b106`：`tests/fixtures/decision_semantics/` 有 R1/R3 完整夹具；R2 仅 `tests/fixtures/news_events/r2_news_fixture.json`；manifest 已把 R2 标成新闻 PIT 切片，不是端到端 decision_semantics。

**审计结论接受。** 暂不实现 R2 完整 fixture（需真实 PIT 切片与 cutoff 边界，禁止编造）。等 595/601 后再单独立项。
