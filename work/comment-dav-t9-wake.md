资深开发2：请按本卡描述与仓库 `work/issue-p2-t9-social-state-wiring.md` 执行 P2-T9。

基线主干 tip（完整 40 位）：

`a375bdc9cf3d07584eb6c28c637bde9bca867876`

硬约束：
1. `AgentState` / `create_initial_state` / graph propagate 贯穿 `social_data_context`
2. `api/main.py` **三处** `create_initial_state` 全传（约 2977 / 3529 / 3717，以 tip 上 grep 为准）
3. `_build_horizon_result` 合并 ledger→data_gaps 遵守 §5.5（insufficient/empty 不进 failed gaps）
4. 不改 ToolNode/analyst/prompts；不删 legacy_proxy；不部署；默认 mode 仍 disabled
5. TDD；单 commit；推隔离分支后 `in_review`，写完整 SHA + pytest 数字
6. 不要 @项目调度助手；不要自行 FF

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
