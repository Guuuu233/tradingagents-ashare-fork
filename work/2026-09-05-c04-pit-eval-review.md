# 独立审核（只读）：C-04 切片 1 PIT/RAW 评估文档

**Path A：** 本卡是唯一审核入口。实现卡评论禁止 @独立代码审核员。

开工前必须已有实现卡给出的完整 40 位候选 SHA，且第一父是当时 `origin/codex/dav-4-p2a-trunk`（期望含 `c83881809da88686c30f097b1c3872187a5733ca`）。

只读。禁止改代码、push、FF、部署。核对：diff 只有 `work/2026-09-05-c04-pit-raw-dividend-eval.md`；无 token；无 `tradingagents/` 改动；结论与 grep/`tushare-gateway-matrix.md` 一致；未把当前 `adj_factor` 写成可回填历史。

书面 ✅通过 / ⚠️有条件通过 / ❌打回，含路径证据。
