生产smoke失败归类为验收脚本取错层级，不是代码失败。Golden文件是外层 wrapper：`{"result_data": {...}}`；你把 wrapper 直接传给 `calculate_*`，所以所有 denominator为0。`OfflineABHarness.compare_golden_fixtures` 已正确使用 `content.get("result_data", content)` 并返回三只标的。

立即按以下方式重跑临时smoke，禁止修改代码或测试：

```python
payload = json.loads(path.read_text(encoding="utf-8"))
rd = payload.get("result_data", payload)
inv = rd.get("investment_debate_state", {})
calculate_evidence_recycling_rate(inv)
calculate_all_debate_metrics(rd)
```

验收：三只 recycling denominator>0，报告利用率分母>0；去污染与合法空值按已有测试/真实结构核验。不要为错误脚本新增兼容代码。纠正smoke通过后立即执行仅一次全量并保存原始输出，然后推送当前组合SHA。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
