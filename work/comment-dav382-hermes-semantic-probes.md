Hermes在你当前GREEN worktree执行了两条纯函数实测，发现必须在提交前修复的语义BLOCK：

## BLOCK 1：合法空probability note未进入metrics输入

实测：
```text
resolved={confidence:68,target:28.03,stop:26.8,extraction_note:'概率未提供/未提取'}
_apply_structured_report_fields(...)
_mount_or_refresh_protocol_metadata_and_metrics(...)

result.extraction_note = None
field_completeness = 3/4 partial
missing_fields=['probability']
```

根因：`_apply_structured_report_fields`未把`resolved['extraction_note']`/`resolved['extraction_warning']`写回result，刷新helper看不到合法空值契约。最小修复仍在api/main：把这两个resolved字段写入result（遵守现有report_service契约），然后刷新metrics。测试必须覆盖probability=None + extraction_note，期望4/4、legitimate_omissions含probability；不要只测probability=0.8。

## BLOCK 2：dual聚合顶层metrics从空容器计算

实测：primary horizon包含confidence/probability/target/stop且field completeness=4/4；调用：
```python
_mount_or_refresh_protocol_metadata_and_metrics(aggregate, source_state=primary)
```
实际顶层仍为0/4，因为helper固定`calculate_all_debate_metrics(result)`，result是只含short_term/medium_term的聚合容器。

修复：当提供`source_state`时，metrics应从真正primary completed horizon计算，再挂到aggregate；无source_state时才从result计算。确保：
- 多horizon顶层metrics等于primary horizon刷新后的metrics；
- query hoist在structured后以最终result刷新；
- single horizon structured后以最终result刷新；
- nested new-state同步同一metrics；legacy nested不变。

请先新增两条RED并确认失败，再最小GREEN。继续同一两文件、当前run；禁止提交当前半成品、禁止全量/P1-B/配置/主干/服务。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
