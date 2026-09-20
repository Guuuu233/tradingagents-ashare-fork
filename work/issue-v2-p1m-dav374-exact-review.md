## 固定审核对象

- 父候选：`33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- 远端分支：`agent/2/2878b47f71c5`
- 精确 SHA：`25e52c02971d7459ec8adb2b38b47e8d6cfa70f8`
- sibling：`8ccd6391404c05d96ff524de8ba5bd02bb371561` 已独立复审 PASS，文件范围零重叠；本卡仅审 25e52c0。
- 严格只读，禁止修改代码/测试/主干/服务/DB/配置；禁止全量测试。

## 必审项

1. ancestry直接父为33c6e6b；changed files恰好 propagation.py、trading_graph.py、protocol metadata test。
2. `Propagator.create_initial_state` 必须深拷贝规范默认元数据；两个 state之间嵌套 dict/list不得共享；不得手写第二套散落默认常量。
3. `TradingAgentsGraph._build_horizon_result`：
   - 新运行状态的顶层和 investment_debate_state 均可见 v1 protocol/meta/feature_flags/真实 metrics；
   - 不修改输入 `investment_debate_state` 原对象；
   - legacy fixture没有新key时，既有 nested state必须保持逐字不变，同时顶层仍可兼容读取默认元数据；
   - metrics只计算一次，无LLM/网络/DB调用，不改图边、prompt、消息、count、history。
4. 检查 `apply_report_quality_gate` 既有可变行为与本提交边界：不得把“整个函数纯函数”写成结论，只核P1-M新增逻辑不额外修改输入 debate state。
5. 数据结构避免自引用或将完整 metrics嵌套进自身导致指数膨胀；检查 `calculate_all_debate_metrics(result)` 输入时 result中的 debate state与输出挂载顺序。
6. feature flag关闭时 legacy节点路由和6条消息行为不变；静态确认未改 conditional_logic/researcher/manager/prompt。
7. 宿主 `.venv310` 复跑：protocol 9、state persistence、trading_graph_multi_horizon、p2b separation、replay、compileall、diff-check；禁止全量/API/calibration矩阵。
8. 0 code changes，给出 PASS/BLOCK、文件:行号、真实命令结果；明确未合入/未重启/未上线。

不要 mention 项目调度助手。