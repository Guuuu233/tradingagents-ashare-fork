修复卡已核对，现直接启动实现。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3) 请从线上主干 `codex/dav-4-p2a-trunk` 的完整基线 `7810f19875890725cd26e414cde272063fb3606a` 开始，在隔离分支完成 DAV-854：

1. 普通命题被反驳且没有 PIT/前视失败时，保持 `confirmation_state=UNRESOLVED`，恢复 `trade_action=WAIT`；
2. PIT/前视失败路径继续保持 `ABSTAIN / NO_TRADE / BLOCKED`，不得放宽 DAV-850 的其他硬闸；
3. 只改 issue 白名单中的判断逻辑和测试：`tradingagents/agents/utils/decision_status.py`、`tests/test_confirmation_gate.py`（只能新增用例，不得修改或删除既有断言），必要时新增专项测试；不得碰 `evidence_verifier.py`、`research_manager.py`、配置、数据库、部署或 DAV-808；
4. 交付前跑 RT-1 至 RT-7、`git diff --check`，并按卡面要求完成候选版本与第一父版本的 RT-FULL 全量对照：候选失败数回到 19，且失败集合与线上 `70b5b47bcff0618e0db1258a438b0875440d4ac5` 的 19 项逐项一致；
5. 推送完整 40 位候选 SHA，并在卡内报告父提交、白名单、测试原始结果、工作树和远端回读；交付后置 `in_review`，不要自行合入或部署。

审查阶段固定派给 `代码审核员`，必须对同一完整 SHA 只读审查；当前不启动审查，不启动部署。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
