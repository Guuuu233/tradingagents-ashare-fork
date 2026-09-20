Wake DAV-605：只读审 exact SHA `243438299d8f0465aa1fc08f46e15f2337613944`，父 `2a82b11d1157d61e7ecea7cac01225b8d020caca`，分支 `origin/agent/1/c9eb020f0758`。

Cursor 已隔离复测（**不等于准予合入**）：
`pytest tests/test_cohort_metadata_persistence.py tests/test_h1b_gates.py tests/test_shadow_credit.py tests/test_report_industry_persistence.py` → 104 passed。

请特别核：旧 121 样本 **没有** `decision_model_version` 键（不是 legacy 标记、也不是显式 None）。`update_report_partial(..., status="completed")` 是否会把缺键旧报告写成 `decision_model.v1`。缺键不得当 v1。

禁止改代码、禁止 FF、禁止部署。通过或打回都写 exact SHA。
