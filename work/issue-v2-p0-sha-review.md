# TradingAgents-AShare v2 Phase 0 候选精确 SHA 只读审核

## 固定对象

- 仓库：`https://github.com/Guuuu233/1.git`
- trunk：`codex/dav-4-p2a-trunk@45821dd4f21a5f65578dbf54f5d916970ae835c0`
- A：`79caee2ea10145d0117b60dfc00eae6429c0a02f`
- B：`a6ef47136da7f49ca0618857c5b36a16ccc41a04`
- C：`64f9167c4dcee4f74ce84a90f5454e32674124e1`
- DE：`59f7253821a644c2d2326f90363d2e7f01094afa`，已与 trunk 功能内容相同
- DAV-346：`45821dd4f21a5f65578dbf54f5d916970ae835c0`

## 只读范围

独立 checkout/临时 clone；禁止修改代码、测试、配置、数据库、个人模型/provider/API Key、主干、服务。不能以组合树未生成为理由修改实现。

## 审核项

1. 远端 refs 存在；每个候选的父链、相对 trunk 的 changed-file scope 和 B/C 金标准重叠范围。
2. A：as-of 提取不能伪造日期，failure gap 必须保留。
3. B：confidence 与 probability 语义分离；fallback 不得静默填值；golden fixture 不得删字段。
4. C：evidence 多行数字匹配必须有关键词约束；bull/bear prompt 对称；replay verifier 与 fairness tests 保留。
5. DE：`report_id` 可观测性、`/api/health` JSON、role bindings 密钥脱敏不影响保存逻辑；不重复 cherry-pick 已在 trunk 的 DE。
6. DAV-346：重复拒收必须是 per-claim，不能只是日志或“全消息都重复才拒绝”。
7. 组合方案建议：A -> B -> C；明确冲突处理风险和是否可进入组合树回归。

## 交付

按 `审核 SHA / 范围是否匹配 / 阻断问题 / 文件:行号与测试证据 / 结论 PASS 或 BLOCK` 输出。没有组合 SHA 时只审核候选事实，不能宣称组合树 PASS。明确未合入、未重启、未上线。
