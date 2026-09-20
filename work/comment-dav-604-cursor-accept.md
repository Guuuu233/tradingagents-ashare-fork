## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `0dfb5c5e8507cf994e05000c954ccc57538d01c1`  
**父 tip：** `2a82b11d1157d61e7ecea7cac01225b8d020caca`  
**分支：** `origin/agent/1/c9eb020f0758`  
**旧 SHA `2434382…` 作废。**

独立审核员已对 **本 SHA** 出具 ✅（评论在 DAV-604）。Cursor 隔离 `/tmp/iso-dav604-0dfb5c5` 复测：

```
pytest tests/test_cohort_metadata_persistence.py tests/test_h1b_gates.py tests/test_shadow_credit.py tests/test_report_industry_persistence.py
# 106 passed, exit 0
git diff --check  # 0
```

抽核：`update_report_partial` / `create_report` 仅当 `prior_status != "completed"` 才写入 cohort。已 completed 缺键历史行 + `update_report_partial(status="completed")` 的 RED 用例存在且断言四键不出现。

changed files 仅：
- `api/services/report_service.py`
- `tests/test_cohort_metadata_persistence.py`

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。禁止回填旧 121。
