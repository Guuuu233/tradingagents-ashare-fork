[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) 请立即开工 Track A9（本卡 DAV-553）。

基线 tip（完整 40 位）：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`  
分支建议：`agent/dev2/a9-h1b-gates-db-path`

问题：`verify_h1b_gates --db-path` 未接线，会静默读 golden。详见本卡描述。

单关注点；先 push；完整 tip + pytest → `in_review`。禁止 FF / 部署 / 开加权 / 动脏文件 trio。
