# DAV-205 精确 SHA 独立终审

## 固定输入

- 目标主干基线：`target/codex/dav-4-p2a-trunk@c39d975793f907bc21200c7efc5dd2676d2e0833`
- 候选分支：`target/agent/1/01a01f04`
- 候选 SHA：`2d7bc5b8a0b45b52fb61a71d9cd6a7649e3578bd`
- 变更范围应仅包含：
  - `api/main.py`
  - `tradingagents/graph/trading_graph.py`
  - `tests/test_debate_state_persistence.py`

## 审核目标

只读审核候选精确 SHA，不修改代码、不重新开发：

1. 核验候选基于当前目标主干，列出完整 diff 与实际变更文件；
2. 核验单周期 `_build_result_payload` 保留 `investment_debate_state`、`risk_debate_state`；
3. 核验 `_build_horizon_result` 保留两类 debate state 和 `risk_feedback_state`；
4. 核验双周期 primary hoist 不丢失 debate state；
5. 核验 `report_service` 原有 canonicalization 不会剥离新增 JSON 键；
6. 核验没有改 DB schema、用户配置、provider、模型、URL/Key；
7. 审查测试真实性：测试必须从生产函数构造状态，断言 history/count/judge/claims，不得只断言键存在或使用不可能 fixture；
8. 独立运行：
   - `env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_debate_state_persistence.py -q`
   - 相关报告持久化测试；
   - 全量 `pytest tests/ -q`
   - `compileall`、`git diff --check`；
9. 输出 PASS/FAIL。PASS需给精确 SHA与测试；FAIL需给可复现文件:行号和最小返修点。

## 约束

- 不接受开发者自报测试作为审核结论；
- 不合入、不部署、不重启；
- 不把旧的2/1轮报告宣称为3轮；
- DAV-199与DAV-200继续锁定。
