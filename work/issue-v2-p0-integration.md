# TradingAgents-AShare v2 Phase 0 组合树集成（唯一 writer）

## 固定事实基线

- 仓库：`https://github.com/Guuuu233/1.git`
- 目标主干：`target/codex/dav-4-p2a-trunk`
- 当前目标主干：`45821dd4f21a5f65578dbf54f5d916970ae835c0`
- A：`79caee2ea10145d0117b60dfc00eae6429c0a02f`
- B：`a6ef47136da7f49ca0618857c5b36a16ccc41a04`
- C：`64f9167c4dcee4f74ce84a90f5454e32674124e1`
- DE：`59f7253821a644c2d2326f90363d2e7f01094afa`，已是当前 trunk 内容，禁止重复 cherry-pick
- DAV-346/bundle wash：已在当前 trunk，禁止重复 cherry-pick
- 宿主环境：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310`，Python 3.10.20
- 宿主当前没有监听 8000；数据库 reports 当前无 pending/running；真实用户 3/1 已核验且不得修改

## 目标

在独立 detached worktree 或临时 clone 里，以 `45821dd` 为基线，按 A -> B -> C 构造可重复的 Phase 0 combined tree。不得在用户宿主脏树直接 cherry-pick。不得进入 Phase 1，不得修改 prompt、协议、前端或个人配置。

## 组合规则

1. 先记录基线 SHA、`git status --short`、定向测试结果。
2. 只 cherry-pick A、B、C；DE 与 DAV-346 已在 trunk，仅核验，不重复应用。
3. B/C 都写 `tests/golden/audit_20260823/`，冲突时保留 DB 完整直导夹具；保留 C 的 replay/verifier/fairness/zh.py 内容与 B 的 verdict extraction 测试。
4. 每步执行 `git diff --check` 和 `env -u PYTHONPATH .venv310/bin/python -m compileall -q api tradingagents`。
5. 任何冲突必须记录文件、解决理由和最终 diff；禁止 `-X ours/theirs` 粗暴覆盖。
6. 不改 `.env`、数据库、providers、role bindings、模型绑定、API Key、目标主干和运行服务。

## 必测

- 基线：`env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_swimlane_de_audit.py tests/test_debate_bundle_wash.py -q`
- 组合定向：规格列出的测试存在才运行；缺失路径必须列为“基线不存在”，不可伪报通过：
  `tests/test_financial_as_of.py tests/test_verdict_extraction.py tests/test_evidence_verifier_fairness.py tests/test_debate_prompts_deep_reasoning.py tests/test_debate_bundle_wash.py tests/test_swimlane_de_audit.py tests/test_research_manager_claim_evidence_coverage_gate.py tests/test_debate_e2e_protocol_repair.py tests/test_debate_seven_reports_and_protocol_gate.py`
- 若 C 的 replay harness 存在，运行 `env -u PYTHONPATH .venv310/bin/python tests/golden/audit_20260823/replay_verifier.py`
- 宿主 `.venv310` 全量 pytest；基线/组合树逐项分类，不能把环境/API 失败算代码 PASS。

## 交付格式

任务、基线 SHA、组合 SHA、分支/临时 checkout、变更文件、冲突处理、测试命令和真实结果、compileall/diff-check、已验证功能、已知限制。必须明确：未合入=是、未重启=是、未上线=是。提交后停止同树开发，等待独立精确 SHA 审核。
