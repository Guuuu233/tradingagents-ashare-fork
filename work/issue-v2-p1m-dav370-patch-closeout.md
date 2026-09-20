## 固定输入

- 父候选：`33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- 已冻结补丁：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav370-uncommitted.patch`
- 补丁 SHA-256：`f564809a561c6a5f3c8dd049f2c8e072295d65d96e05c7626182fdeef4c57b2e`
- 补丁大小：15,233 bytes
- 原开发已产生真实 RED→GREEN，并通过：protocol 9、辩论矩阵79、graph/horizon71、debate通配97、analyst/adjudication103、trading/report/p2b109、compileall、replay。无完整全量终态，最终 sibling组合统一全量。

本卡只做机械交付收口，不重新设计、不继续扩测试。

## 操作

1. 必须 fresh checkout：`multica repo checkout https://github.com/Guuuu233/1.git --ref 33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`。
2. 在独立 checkout 校验补丁 SHA-256 后执行 `git apply --check` 和 `git apply`。禁止进入/修改宿主项目树。
3. 变更必须恰好3文件：
   - `tradingagents/graph/propagation.py`
   - `tradingagents/graph/trading_graph.py`
   - `tests/test_debate_protocol_metadata.py`
4. 只允许机械删除 protocol test EOF 多余空行；禁止其他代码/测试变化。
5. 用宿主 `.venv310` 跑：
   - `tests/test_debate_protocol_metadata.py`（9）
   - `tests/test_debate_state_persistence.py`
   - `tests/test_trading_graph_multi_horizon.py`
   - `tests/test_p2b_data_separation.py`
   - replay verifier
   - compileall、完整 diff-check
6. 不跑全量、不跑API/calibration/数据源矩阵。
7. 提交推送新的远端 branch/SHA，报告精确 SHA、三文件范围、命令结果；明确未合入/未重启/未上线，待 DAV-372 sibling组合全量。
8. 禁止修改主干、服务、DB、配置、用户设置、原P1-M六文件、P1-B核心。

不要 mention 项目调度助手。