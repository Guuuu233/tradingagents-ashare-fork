## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`503aa1606161918ba25e77dad40ec2e8df652461`  
**父 / 基线**：`d2f8aa05579d0520abe942972889a82060ea65e7`  
**分支**：`origin/agent/dev2/p2-t15b-gate4-remove-legacy`  
**独立审核员 DAV-544**：✅通过  
**授权**：用户已明确「开 Gate4」

### Cursor 证据

1. 远端 tip 可达；单 commit；6 文件；`tradingagents/` 无 `legacy_proxy`。
2. 隔离复测：**67 passed**（rollout / separation / e2e / downstream / report / collector / toolnode）。
3. 独立审核员另报 174 + 35 passed。
4. T-H4：disabled → `not_applicable`；shadow `direction_allowed=false`。

### 决定

**准予合入** `503aa1606161918ba25e77dad40ec2e8df652461`。  
**不准予部署。** 不开加权。默认 mode 仍可为 `disabled`（语义已变，不再新闻冒充）。

请运维仅对该 SHA 线性 FF。
