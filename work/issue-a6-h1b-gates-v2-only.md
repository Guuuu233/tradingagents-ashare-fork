# Track A6：门槛脚本只计 v2 样本

## 授权与基线

- 统一方案：`work/2026-08-27-unified-final-plan.md` A6
- 源方案：`work/2026-08-27-data-verdict-repair-plan.md` P6
- 主干：`aa41f44992674144eb2e320fc1962f7b0022a795`
- 隔离分支：从该 SHA 新建，例如 `agent/cursor/a6-h1b-gates-v2-only`

## 只做这件事

`scripts/verify_h1b_gates.py`（及相关测试若有）：

- 只计带 `v2_structured_disagreement` / v2 `manager_verdict.winner` 的 completed 报告
- 旧报告无 v2 winner 不得把分侧打成 0/0、行业 0 的假象（应排除出分母）
- 补齐/使用 industry 元数据（若报告已有则计入；没有不得硬编）
- TDD；一个 commit；推远程

## 禁止

- 开 `credit_weighting_enabled`
- 改 `data_collector.py` / prompts / evidence_verifier / frontend
- 改辩论轮次、snapshot refusal
- `git add .`

## 验收

定向 pytest（脚本相关测试）+ 跑一遍脚本输出说明口径变化。交付分支/SHA/原文。

[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff)
