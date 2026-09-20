资深开发2：请按本卡描述与仓库 `work/issue-p2-t8-data-collector-social.md` 执行 P2-T8。

基线主干 tip（完整 40 位）：

`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`

硬约束：
1. 社交必须在 `_fetch_all` **之后**独立超时调用；不得进入市场 `ThreadPoolExecutor` / `tasks`
2. `pool["news"]` 与 `social_data_context` 独立键
3. 从 `zt_pool`/`hot_stocks` 建 `market_data_context.market_attention`（保留 status/as_of）
4. 默认 mode 仍 disabled；不改 Graph/API/analyst；不删 legacy_proxy；不部署
5. TDD；单 commit；推隔离分支后 `in_review`，写完整 SHA + pytest 数字
6. 不要 @项目调度助手；不要自行 FF

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
