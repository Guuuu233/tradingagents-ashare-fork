资深开发2：请按本卡描述与仓库 `work/issue-p2-t11-social-analyst-separation.md` 执行 P2-T11。

基线主干 tip（完整 40 位）：

`0cc34278c8024680e0b687bd029295876b6e0c98`

硬约束：
1. 新建适配层为 **唯一** mode 分支；active 只读 social_data_context + market_attention，禁止 news/`get_news` 回退
2. NEWS/SOCIAL sentinel 互不泄漏；更新 deep_framework 期望
3. 不删 legacy_proxy（Gate 4）；不改 ToolNode；不部署；默认 mode 仍 disabled
4. TDD；单 commit；推隔离分支后 `in_review`，写完整 SHA + pytest 数字
5. 不要 @项目调度助手；不要自行 FF

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
