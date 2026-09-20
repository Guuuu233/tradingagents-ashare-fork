# 收口 df753841 聚合回归残留的四个既有失败

## 基线与边界

- 固定基线：`df7538413ba7bb55593b1757feaf90b0bd514d1c`
- 目标是逐项消除或正确重分类当前 RT-FULL-OFFLINE 保留的四个失败；不得把“无新增失败”当作完成。
- 不部署、不重启、不写生产库、不启用 H1b、不使用社交 Cookie。
- 不使用 Sonnet 路由。

## 四个问题

1. `tests/test_game_theory_integration.py::test_rt10_real_graph_builder_routing_reachability_and_execution`
   - 测试声明真实图必须产生可用博弈论结果；核对 `mock_collector` 是否真的注入节点。
   - 若产品接线契约要求可达且可执行，应把确定性 fixture 正确接入；若该测试实际验证的是降级路径，必须改成明确的 typed degradation 契约，不能保留互相矛盾的断言。
2. `tests/test_h1b_gates.py::TestH1bV2OnlySampleFilteringAndIndustry::test_verify_h1b_gates_script_runs_and_verifies_v2_only`
   - 保留 D-009 §5：`analysis_status IS NULL` 必须归入 `legacy_null`，不得放宽过滤逻辑。
   - 查清脚本的 fallback/load 路径为什么返回 0 而 golden 目录有 3 个样本；优先修确定性 fixture/路径隔离或测试 setup。
3. `tests/test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param`
   - `cn_akshare.get_cninfo_announcement_content` 必须明确：补齐真实日期参数并 fail-closed，或有充分证据证明它是无日期的纯 transport 并加入诚实白名单。
   - 不得用宽泛 whitelist 隐藏前视风险；需要代码变更时同步添加定点测试。
4. `tests/test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json`
   - `--use-mock` 必须真正使用 60 条确定性 mock，即使环境里存在生产库/其他输入。
   - 修加载优先级或测试隔离，不能把期望值改成 2，也不能让生产库数据影响 mock CLI。

## 验收

- 四个失败逐项给出根因、改动文件和契约依据；不以删断言、放宽断言、跳过测试结案。
- 定点测试全过，并用同一 Python 3.10.20 环境跑受影响文件；报告精确计数。
- 若触及产品代码，必须说明默认生产行为、日期/PIT 语义和 fail-closed 结果；若只是测试修复，也要说明为什么没有改变生产契约。
- 交付完整候选 SHA、直接父、diff-check、改动白名单和日志路径；不合入、不部署。

## 审查

交付后只派 `代码审核员` 或 `代码审核员2` 做同 SHA 只读审查，禁止实现者自审。
