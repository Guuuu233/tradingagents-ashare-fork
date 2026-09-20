[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c) 请立即开始只读审核。

候选 tip（完整 40 位，必须对照此 tip）：
`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`

分支：`origin/agent/dev2/a11-t5-no-vacuous-pass`
基线：`ccda1be9c96e4d9a5f334fa03280342badeb4306`
关联实现卡：DAV-559
审核 issue：DAV-560（本卡）

Cursor 隔离复测：`test_h1b_gates` + `test_tplus5_shadow_backfill` → **60 passed**；单 commit；父 = 主干 tip；`due==0` → rate=0 / FAIL / `reason=no_due_samples`。

禁止改代码 / FF / 部署 / @调度助手。PASS ≠ 准予合入。
