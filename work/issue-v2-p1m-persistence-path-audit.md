## P1-M result_data生成/落库路径只读审计

固定基线：`f89b6009544a60727499a02f2e7c585502802801`。DAV-382正在只改api/main.py与test_debate_state_persistence.py；本卡严格只读，禁止代码/测试/DB/服务/配置修改，禁止全量测试。

审计所有报告路径是否调用或绕过P1-M挂载：
1. stream_events=True 单horizon；
2. stream_events=False short/medium；
3. dual-horizon成功与部分失败聚合；
4. dry_run（是否应明确无P1-M或带默认metadata）；
5. 定时/手动入口最终是否统一进入_run_job_inner；
6. create_report前canonicalize/clean是否会剥离未知P1-M字段。

输出路径表：入口→graph执行→result builder→resolve→create_report→job event，标明当前是否有protocol/flags/metrics。检查DAV-382最小方案是否覆盖所有生产报告路径，是否应仅修_build_result_payload即可；若还有阻断，给精确文件/行号和最小测试范围，不实现。

额外检查：
- `_build_result_payload`调用calculate_all_debate_metrics时，final result的confidence/probability是在后续resolve阶段才写入；因此field_completeness可能在结构化字段注入前计算而失真。判断应在何时重新计算/挂载，必须以真实调用顺序为证据。
- dual-horizon result是否把primary_r的P1-M顶层字段hoist到最终result；目前仅hoist debate state/报告字段，可能顶层缺protocol/metrics。
- canonicalize_report_result_data是否保留未知键。

交付PASS/BLOCK与路径矩阵，0 tests/0 changes，未合入/未重启/未上线，P1-B锁定。不要 mention项目调度助手。