[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c) 请立即开始只读审核。

候选 tip（完整 40 位，必须对照此 tip）：
`4493177eeefa4a7aabfc05904c156ccb0106d06e`

分支：`origin/agent/dev2/a9-h1b-gates-db-path`
基线：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`
关联实现卡：DAV-553
审核 issue：DAV-554（本卡）

Cursor 隔离复测：`test_h1b_gates` + `test_calibration_service` + `test_decision_status` → **87 passed**；单 commit；父 = 主干 tip；`--db-path` 走 `mode=ro`，缺失路径 `FileNotFoundError`，无静默 golden。

禁止改代码 / FF / 部署 / @调度助手。PASS ≠ 准予合入。
