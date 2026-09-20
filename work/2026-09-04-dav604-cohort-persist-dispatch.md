# DAV-604：分析完成时写入 H1b cohort 元数据

**父 tip：** `2a82b11d1157d61e7ecea7cac01225b8d020caca`  
**规格：** `work/2026-09-04-decision-model-version-isolation-design.md`（Cursor 冻结）

## 一个关注点

DAV-601 已让 gate 必须显式 `--cohort`。本卡只做：**新完成的分析在持久化时写入三元组 + SHA**。不改匹配逻辑、不补样本、不部署、不开加权。

写入 `ReportDB.result_data` 根字典（禁止平行表）：

- `decision_model_version`：新跑样本 `decision_model.v1`（confirmation lifecycle + 595 数值绑定之后）
- `evidence_contract_version`：`evidence_contract.v1`（595 合入后）
- `price_basis_version`：`price_basis.unspecified`（PIT 未落地，禁止标成 PIT_ADJUSTED）
- `generated_by_commit_sha`：40 字符；优先 `GIT_COMMIT_SHA`，否则 git rev-parse，失败则显式缺失不得填今天/随机值

旧 121 样本 **不得回填** 为 v1。

## 允许改

分析完成持久化原路径（查清后只改那一处）+ 对应测试。禁止改 `evidence_verifier.py`、禁止改 confirmation gate。`verify_h1b_gates.py` 仅在测试需要时读取这些字段，不要重做 601。

## 验收

RED：完成报告缺少四字段。GREEN：新完成报告带齐字段且 SHA 为 40 hex 或明确缺失标记。一个 commit。隔离 worktree。报告 exact SHA。

## 禁止

回填历史样本、删样本、缩短 T+5、编造 probability、自动 FF、部署、`credit_weighting_enabled=True`。
