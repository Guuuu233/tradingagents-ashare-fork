# H-04d 投研经理：消费各节点研究档

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `5eed1a2a1ddee1ab34ce676d88fe717b5a2fce28`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只改投研经理节点：本次研究档进入 prompt / 裁决依据；中期任务不得因分析师观察窗是 short 而被当成短期裁决。期限对应核心问题与行动依据，不要求结论必须不同。  
**禁止：** 改七个分析师、`data_collector.py`、`intent_parser.py` 的 `horizon_context_block`、H-04c、H-05、E-03、claim 计权 / `is_credit_weighting_enabled` / `tally_cluster_votes` 算法、`role_bindings`/`providers`、C-04/C-09-3/Track B/H1b/PDF、FF/部署、push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。不要补 H-04c。

计划 v1.1 **H-04d**。H-04b-1…7 已合入。H-04c 在 `5eed1a2` 上 `test_data_collector.py` + `test_horizon_analyst_context.py` **38 passed**，无失败用例证明 collector `get_window` 必须改，本批跳过。现网 `research_manager.py` 未把研究档注入 prompt。

## 允许改

- `tradingagents/agents/managers/research_manager.py`
- 仅 `tradingagents/prompts/zh.py` 与 `en.py` 的 **`research_manager_prompt`**（不要动其它角色模板）
- `tests/test_research_manager_horizon.py`（本卡主验收；可新建。不要改既有 `test_research_manager_*` 除非本卡断言必须引用其中辅助函数——默认不要动）

## 契约

1. 研究档从 `state["horizon"]` 和/或 `state["horizon_run_metadata"]` / H-04a 绑定读取，优先级与分析师冻结方案一致：`state["horizon"]` → metadata `resolved[0]` → `requested[0]` → `get_bound_research_horizon()` → `"short"`。
2. 注入 prompt 时必须同时可见：本次研究档；分析师 traces 里的 `observation_horizon` 只是专业观察窗，不得覆盖研究档。可复用 `build_horizon_context`：第一参数用专业观察窗仅当文档需要对照，**研究档必须走 `research_horizon=`**，不得把观察窗写成整次运行档。
3. 短期 vs 中期：核心问题 / 有效期限 / 失效条件 / 行动依据在 prompt 中按研究档区分（例如 short 近窗交易结构 vs medium 传导与持有条件）。不要求 LLM 输出结论必须不同；测的是注入文本与机读字段，不是真实模型。
4. 若写 `manager_verdict` / traces 类字段：`horizon` = 本次研究档；需要时另存 `research_horizon`。不要发明第二套字段名。不要改资金流 guard、claim cluster 计票、加权开关。
5. 数据/辩论失败路径保持显式 INVALID/ABSTAIN，不编造方向。
6. 不改分析师文件、不改 collector 共享池。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_research_manager_horizon.py tests/test_horizon_analyst_context.py tests/test_research_manager_seven_reports_and_verdict_gate.py
```

至少覆盖：medium 研究档时 prompt 含中期研究档、不得把整次运行写成 short（即使 traces 里 `observation_horizon=short`）；short 研究档时研究档=short；既有七报告/verdict 闸不回归。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
