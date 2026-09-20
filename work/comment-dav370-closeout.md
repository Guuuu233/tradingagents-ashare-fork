停止重复全量测试；单分支全量无终态不计证据，最终 sibling 组合树统一跑全量。当前已验证 79 项辩论矩阵、71 项 graph/horizon、compileall、replay。立即只做收口：

1. 删除 `tests/test_debate_protocol_metadata.py` EOF 多余空行，完整 `git diff --check 33c6e6b..工作树` 必须无输出；
2. 复跑 9 项 protocol test 与既有 state persistence 定向即可，不再跑全量；
3. 核验 changed files 恰好 propagation.py、trading_graph.py、protocol test 三个；
4. 提交并推送 `agent/agent/77d841694f3f`，发布精确 SHA 与 RED/GREEN/定向证据；明确单分支全量未取终态，组合树待统一全量；
5. 禁止再修改实现范围、主干、服务和用户配置。

[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff)
