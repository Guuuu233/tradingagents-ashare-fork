[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) 请立即开工 Track A12（本卡 DAV-562）。

基线 tip（完整 40 位）：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`  
分支建议：`agent/dev2/a12-t5-due-inference`

问题：`evaluate_h1b_system_gates` 注释承诺用 trade_date 推断 due，代码未实现；本地 69 份已过窗样本被当成 due=0。详见本卡描述。

单关注点；先 push；完整 tip + pytest → `in_review`。禁止 FF / 部署 / 开加权 / 生产库实写 / 动脏文件 trio。
