# DAV-875 独立补充复核：c23b5ae 的两处额外穿透

候选：`c23b5ae3e8695e4f35c3d2b33b182c2f0440d0d7`

直接父：`f27fed0e8ee2565d7f7269f1fe2365b3eac82c16`

固定解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`

## 1. 非法日历日期仍可生成 numeric baseline

调用 `fundamentals_analyst._extract_baseline_and_revision`，实际值为完整可用结构化记录，`current_date=2024-07-01`，forecast 有 source/type/value/unit/report_period，但 `as_of` 分别使用：

- `2024-02-31` → `baseline.type=management_guidance`、`baseline.value=80.0`、`revision.type=numeric`、无 gap；
- `0000-00-00` → `baseline.type=management_guidance`、`baseline.value=80.0`、`revision.type=numeric`、无 gap。

当前实现只用 `^YYYY-MM-DD$` 正则和字符串比较，没有真正校验公历日期。该结果穿透“非法 as_of 必须阻断 numeric”的返修契约。

## 2. builder 接受伪造 source_hash

调用 `build_fundamentals_expectation_revision`，结构化记录包含真实格式字段但 `source_hash="fin_2024Q2_2024-06-30"`，同时有 `publish_time` 和 `source`：

- `publication.source_hash` 原样保留伪造的 `fin_...`；
- `publication.content_qualification="qualified"`；
- `publication.qualification_status="qualified"`；
- `gaps=[]`。

`validate_expectation_revision` 后续会拒绝 `fin_...`，但 builder 已经先构造出 qualified 结果；如果生产路径在 validator 之前消费或持久化，仍会把伪造指纹当成合格来源。返修契约要求没有真实 source_hash 时保持 gap/unknown，不能由 builder 先放行。

## 当前裁定

这是同一候选上的独立红队发现，候选暂不具备合入或部署条件。待 DAV-875 正式红队报告恢复后合并对照；不得因全量失败数回到 19 就忽略这两项。
