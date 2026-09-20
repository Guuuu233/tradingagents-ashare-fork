# P0-4b 返修：异步测必须用 asyncio.run；verified_evidence_count 必须接核验

## 目标

返修 DAV-478 候选 `cef3bcf093cf572f8de5fbb0014c3315209ee57a`。Cursor **不准予合入**。

## 基线

- 从 `cef3bcf` 继续，或从主干 `12120c705ab6eb69d2ecac0d669b4d465e5b1abb` 重建同一分支并重放修复
- 分支：`agent/dev2/p0-4b-claim-cluster`（不要新开平行 `_v2` 实现）
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## Cursor 复现（隔离 `/tmp/ta-p04b-cef3bcf` + `.venv310`）

```
1 failed, 24 passed
FAILED test_research_manager_node_produces_claim_cluster_metrics
→ async def 不被原生支持；仓库未安装 pytest-asyncio
```

既有风格：`tests/test_research_manager_run_integrity.py` 用 `asyncio.run(node(state))`，**不要**引入 `pytest-asyncio` 依赖。

## 必须修

1. **异步集成测**：去掉 `@pytest.mark.asyncio`；改成同步测 + `asyncio.run(...)`，与 run_integrity 一致。在无 `pytest-asyncio` 的 `.venv310` 上必须全绿。
2. **`verified_evidence_count`**：接上 `claims_verification`。只有核验状态为 verified（或你们锁死的等价通过码）的证据才计入；contradicted / unsupported / source_unavailable **不得**计入。现在参数传入后未使用，禁止继续虚报「verified」。
3. **总监可消费**：把 `analyst_count` / `independent_cluster_count` / `verified_evidence_count`（或整块 `claim_cluster_metrics` 紧凑摘要）注入 `research_manager` prompt。不要只写进 state 事后字段。中文七位分析师逐一列出（DAV-336）必须仍绿。
4. **inline import**：`update_debate_state_with_payload` 里的 `from ...claim_cluster import assign_claim_cluster` 要么加一行注释说明循环依赖，要么消环。禁止再引入新依赖包。

## 不要改

- P0-4a 资金流行为、财务 Q2、P0-5、社交、3/1、`credit_weighting_enabled`
- 不要为修测装 `pytest-asyncio`

## 允许修改

- `tests/test_claim_cluster.py`
- `tradingagents/agents/utils/claim_cluster.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/utils/debate_utils.py`（仅消环/注释）
- `tradingagents/prompts/zh.py` / `en.py`（仅注入占位所需）

## 测试

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_claim_cluster.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

必须新增/锁死：

- 带 `claims_verification`：verified 计入、contradicted 不计入 `verified_evidence_count`
- 工业富联钉子与 50%≠60% 仍绿
- 异步集成测在无 pytest-asyncio 下通过
- DAV-336 七分析师仍在

## 交付

- 新完整 40 位 SHA，已 push
- `git diff --stat` 相对 `12120c7` 或说明相对 `cef3bcf` 的增量
- 精确 pytest 数字（须与 Cursor 可复现环境一致）
- 不要写「彻底修复」；等 Cursor「准予合入」
- **不准予部署**；不要 @项目调度助手催工
