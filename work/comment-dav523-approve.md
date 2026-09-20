## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`c1ec33e7adf95b2889e015038b78dd4ac2233fd5`  
**父提交**：`7db2882d43276c22dd87e259b259dd1f500c6bd0`  
**分支**：`origin/agent/dev2/p2-t15a-social-e2e-acceptance`  
**独立审核员 DAV-524**：✅通过

### Cursor 证据

1. 远端可达；祖先含 T14 tip；**仅** `tests/test_social_e2e_acceptance.py`（+1010），零产品代码改动。
2. 隔离 worktree `@ c1ec33e`，无代理：
   - 新增 E2E：**9 passed** in 0.28s
   - 社交扩展矩阵：**176 passed**, 4 warnings in 32.61s
3. 约束：未删 `legacy_proxy`；未改默认 mode；未部署。

### 决定

**准予合入** `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`。  
**不准予部署。** Gate 4 / T15b 仍未开。

请运维仅对该 SHA 线性 FF。
