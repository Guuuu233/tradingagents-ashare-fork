## 固定审核对象

- 目标trunk基线：`f89b6009544a60727499a02f2e7c585502802801`
- WIP父链：`3a4cab0cfdb30cc46c15551b69b71cb2d7b914bf`（包含cfaa4b2，尚未主干）
- 远端分支：`agent/2/dav384-semantic-repair`
- 精确SHA：`153fc9bad03c60744407c85a61a7aa52069c84e9`
- 直接父必须为3a4cab0；最终候选相对trunk是f89→cfaa→3a4→153线性3提交。
- 只读复审，0 code changes；禁止主干/服务/DB/配置/P1-B和全量测试。

## 必审

1. 远端SHA、父链、相对3a4仅2文件；相对trunk总范围仍只有`api/main.py`与`tests/test_debate_state_persistence.py`；diff-check。
2. TDD顺序：RED A合法空probability与RED B dual source metrics真实失败；宿主Python3.10 GREEN 27项，111项矩阵，replay/compileall通过。
3. 逐行审核`_mount_or_refresh_protocol_metadata_and_metrics`：
   - source_state存在时使用独立临时mapping，不修改source_state；
   - result中后写入的confidence/probability/target/stop/note/warning和七报告应覆盖source；
   - 不产生`data_utilization_metrics`自嵌套或把aggregate短/中周期整个嵌入metrics输入；
   - dual顶层metrics与primary completed horizon语义一致。
4. `_apply_structured_report_fields`写入resolved extraction_note/warning：与report_service现有契约一致，不覆盖非空有效旧note为错误None；检查BUY/HOLD/缺confidence场景。
5. 所有生产路径：
   - stream_events=True单horizon；
   - stream_events=False short/medium；
   - query hoist；
   - dual multi-horizon；
   - 每个structured apply后刷新；dry_run不动。
6. legacy nested无P1-M key逐字保持；new nested浅拷贝同步；6条round_messages不变；3/1无改动。
7. 使用宿主`.venv310`只跑：`tests/test_debate_state_persistence.py`和必要P1-M窄测试；禁止全量。再独立复跑两条Hermes语义探针。
8. 输出PASS/BLOCK、命令证据、0 changes；未合入/未重启/未上线，P1-B锁定。

不要mention项目调度助手。