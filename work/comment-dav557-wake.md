[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c) 请立即开始只读审核。

候选 tip（完整 40 位，必须对照此 tip）：
`ccda1be9c96e4d9a5f334fa03280342badeb4306`

分支：`origin/agent/dev2/a10-backfill-db-path`
基线：`4493177eeefa4a7aabfc05904c156ccb0106d06e`
关联实现卡：DAV-556
审核 issue：DAV-557（本卡）

Cursor 隔离复测：industry + tplus5 + h1b_gates → **80 passed**；单 commit；父 = 主干 tip；两 backfill 脚本 `--db-path` 优先打开目标库，坏路径不落 golden。

禁止改代码 / FF / 部署 / @调度助手。PASS ≠ 准予合入。
