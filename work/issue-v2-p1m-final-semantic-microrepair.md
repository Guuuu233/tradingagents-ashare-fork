## 固定基线与两个已复现BLOCK

- 父WIP远端：`agent/2/dav382-semantic-wip@3a4cab0cfdb30cc46c15551b69b71cb2d7b914bf`
- 目标主干/运行服务仍为：`f89b6009544a60727499a02f2e7c585502802801`
- 3a4cab0已包含单/双/query路径挂载，但不可合入。

Hermes在精确3a4cab0、宿主`.venv310`真实复现：

### RED A：合法空probability被误算缺失

```text
_apply_structured_report_fields(result, structured=None,
 resolved={confidence:68,target_price:28.03,stop_loss_price:26.8,
 extraction_note:'概率未提供/未提取', extraction_warning:None})
_mount_or_refresh_protocol_metadata_and_metrics(result)

result.extraction_note=None
field_completeness=3/4 partial
missing_fields=['probability']
```

根因：`_apply_structured_report_fields`未将resolved的`extraction_note`、`extraction_warning`写回result。

### RED B：dual顶层从空聚合容器算metrics

primary horizon含confidence/probability/target/stop，`calculate_all_debate_metrics(primary).field_completeness=4/4`；但：

```text
_mount_or_refresh_protocol_metadata_and_metrics(aggregate, source_state=primary)
top field_completeness=0/4
```

根因：helper固定`calculate_all_debate_metrics(result)`，忽略source_state。

## 严格范围

只允许修改：
- `api/main.py`
- `tests/test_debate_state_persistence.py`

禁止其他文件、P1-B、配置/DB/服务/主干、prompt/graph/provider/frontend。

## TDD

1. fresh checkout精确3a4cab0。
2. 在现有测试中先写两条RED并用宿主：
   `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest ...`
   - probability=None + extraction_note必须field completeness=4/4，legitimate_omissions含probability；result和saved result均有note。
   - aggregate source_state=primary时顶层metrics必须与primary一致，至少field completeness与evidence recycling逐项相等；不得从空aggregate算0。
3. 最小GREEN：
   - `_apply_structured_report_fields`显式写入`extraction_note`/`extraction_warning`（遵循resolved值，不猜默认）；
   - helper的metrics输入：有source_state时基于source_state的内容，但必须叠加result中已经结构化写入的confidence/probability/target/stop/note/warning及报告字段，确保query/single最终刷新不丢新字段；可构造独立临时mapping，禁止修改source_state；
   - dual aggregate顶层使用primary completed horizon的真实metrics；nested new-state同步同一metrics；legacy nested逐字保持。
4. 用宿主3.10跑：整个state persistence、192项矩阵、replay、compileall、diff-check；不跑全量。
5. changed files恰好2个；推新远端branch/SHA，报告父SHA、RED/GREEN、测试。
6. 未合入/未重启/未上线，3/1不变，P1-B锁定。

不要 mention项目调度助手。不要复用/覆盖原`agent/2/4a1c766107bb`分支。