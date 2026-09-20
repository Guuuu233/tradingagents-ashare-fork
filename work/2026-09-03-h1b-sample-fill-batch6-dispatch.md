# H1b 样本补齐 Batch6 — 专攻空方 winner（Dim2/Dim5）

**日期**：2026-09-03  
**授权**：用户确认继续样本补齐路线  
**前置**：Batch5 已让 Dim3 交易日 PASS（30/30）；bull/bear 仍 ~46/16

## 目标
- 新增约 **20** 场 completed v2（优先 `manager_verdict.winner=bear`）
- 用历史上出过 bear 的标的 + **T+5 已到期**交易日（≤2026-08-27）
- 跑完：T+5 write backfill → `verify_h1b_gates`；仍禁止开加权 / 部署

## 执行
脚本：`work/run_h1b_sample_fill_batch6.py`（Cursor 串行跑）  
日志：`work/h1b-sample-fill-batch6.log`

硬禁同 Batch5：3/1 不变、仅请求级 `v2_debate_enabled`、不开 `credit_weighting_enabled`、不部署。
