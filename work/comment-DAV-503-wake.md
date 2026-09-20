资深开发2：请按本卡描述与仓库 `work/issue-p2-t10-social-toolnode.md` 执行 P2-T10。

基线主干 tip（完整 40 位）：

`46995ac19bb4894dc6cea328f299951eb12698c5`

硬约束：
1. social ToolNode **去掉** `get_news`；news ToolNode 不变
2. **禁止**新增任何 social LangChain data tool / `social_data_tools.py`
3. 不改 analyst/prompts/适配层（Task 11）；不删 legacy_proxy；不部署
4. TDD；单 commit；推隔离分支后 `in_review`，写完整 SHA + pytest 数字
5. 不要 @项目调度助手；不要自行 FF

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
