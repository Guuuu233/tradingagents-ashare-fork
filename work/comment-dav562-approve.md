## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`98fe5d199e8874ae829d2b492882d82339c836f0`  
**父 / 基线**：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`  
**分支**：`origin/agent/dev2/a12-t5-due-inference`  
**独立审核员 DAV-563**：✅通过（LOW：测试类边界错位，非阻断）

### Cursor 证据

1. 远端 tip 可达；单 commit；4 文件；父 = 当前主干 tip。
2. 隔离复测：`test_h1b_gates` + `test_tplus5_shadow_backfill` → **67 passed**。
3. `is_t_plus_5_due is None` 时用 `t_plus_5_date` / `trade_date→calculate_t_plus_5_date` 相对 `as_of` 推断 due；解析失败不臆造；A11 `due==0` 契约保留。

### 决定

**准予合入** `98fe5d199e8874ae829d2b492882d82339c836f0`。  
**不准予部署。** 不开加权。不对生产库实写回填。

请运维仅对该 SHA 线性 FF。
