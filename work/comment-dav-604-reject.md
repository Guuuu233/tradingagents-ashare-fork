## Cursor 打回（exact SHA）

**打回 SHA：** `243438299d8f0465aa1fc08f46e15f2337613944`  
**父 tip 不变：** `2a82b11d1157d61e7ecea7cac01225b8d020caca`  
**不准予合入。未 FF。未部署。**

DAV-605 最终结论 ❌ 与 Cursor 专项问题一致：104 passed 不能掩盖缺键盲区。

### 缺陷

旧 121 样本的 `result_data` **没有** `decision_model_version` 键。  
`ensure_report_cohort_persisted` 只挡住 legacy 字符串和显式 `None`。缺键会走 `not in` 并写成 `decision_model.v1`。  
`update_report_partial(..., status="completed")` 对**已经 completed** 的旧行也会调用该函数，等于回填。

### 返修合同（一个 commit，原路径）

1. **RED 测试必须先失败：** 库中已有 `status=completed` 且 `result_data` 完全没有 cohort 四键的报告；再 `update_report_partial(status="completed")`（可不传新 result_data）。断言四键仍不存在，且不得变成 `decision_model.v1`。
2. **新分析首次完成**（`init_report` 后 pending→completed，或 `create_report(status=completed)` 新行）仍必须写入 v1 三元组 + SHA。
3. **已 completed 的历史行**：缺键视为 unlabeled / `legacy_unversioned`，禁止写成 v1。
4. 禁止回填旧 121；禁止改 `evidence_verifier.py`；禁止部署；禁止开加权。
5. 贴 **新** 40 字符 SHA；DAV-605 改审新 SHA，本 SHA 作废。
