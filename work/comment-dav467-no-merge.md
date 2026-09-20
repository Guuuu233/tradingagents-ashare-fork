Cursor 独立复审 `80c099f61141909fa0ec074b9bf3d9cde421cc38`：**不准予合入。**

High：`seven_reports['fundamentals_report']` 含未验证 as_of 的同一数值时，`evaluate_single_evidence` 仍返回 verified。现有测试用空 reports，锁不住生产路径。返修见 DAV-468。不要 FF、不要部署。
