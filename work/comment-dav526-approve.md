## Cursor 同 SHA 隔离复测 — 准予合入

**候选 tip（完整 40 位）**：`0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`  
**基线父链**：含 `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`  
**分支**：`origin/agent/dev2/p2-med-social-residuals`  
**独立审核员 DAV-527**：✅通过

### Commit 清单（4 个关注点）

| MED | SHA |
|---|---|
| M2 | `e94c681b2b9acd52097f476f1732577538233505` |
| M3 | `40090db3de33594fa75e93a1511a27155669e820` |
| M4 | `810b732ef2c5b7a98adec69d897f5fec50938249` |
| M7 | `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb` |

### Cursor 证据

1. 远端 tip 与四 commit 对象可达；diffstat 9 files +283/-14；未删 `legacy_proxy`。
2. 隔离 worktree `@ 0d7a67e`，无代理：
   - 定向 6 模块：**89 passed** in 0.79s
   - 回归 report/contracts：**23 passed** in 0.20s
3. Spot-check：provider 缺 `crawler_commit` 跳过；`ingest_at` datetime tie-break；`_summarize_social_context`；aggregator 0 行 → empty。

### 决定

**准予合入** tip `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`（线性包含上述 4 commit）。  
**不准予部署。** Gate 4 / T15b 仍未开。

请运维仅对该 tip 线性 FF。
