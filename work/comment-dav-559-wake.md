[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) 请立即开工 Track A11（本卡 DAV-559）。

基线 tip（完整 40 位）：`ccda1be9c96e4d9a5f334fa03280342badeb4306`  
分支建议：`agent/dev2/a11-t5-no-vacuous-pass`

问题：`due_t5_count==0` 时若 N≥60 虚高记完整率 100% 并 PASS。详见本卡描述。

单关注点；先 push；完整 tip + pytest → `in_review`。禁止 FF / 部署 / 开加权 / 动脏文件 trio。
