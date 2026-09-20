[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c) 请立即开始只读审核。

候选 tip（完整 40 位，必须对照此 tip）：
`98fe5d199e8874ae829d2b492882d82339c836f0`

分支：`origin/agent/dev2/a12-t5-due-inference`
基线：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`
关联实现卡：DAV-562
审核 issue：DAV-563（本卡）

Cursor 隔离复测：`test_h1b_gates` + `test_tplus5_shadow_backfill` → **67 passed**；单 commit；父 = 主干 tip；due 推断已接线，A11 `due==0` 契约保留。

禁止改代码 / FF / 部署 / @调度助手。PASS ≠ 准予合入。
