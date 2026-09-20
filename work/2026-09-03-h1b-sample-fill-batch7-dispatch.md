# H1b Batch7 — 在「已证明出空」的交易日填空位

**日期**：2026-09-03  
**前置**：Batch6 20/20 completed，但多为 tie/bull，bear≈14 未增；Dim2/5 仍 FAIL。

## 策略变更
不再在中性日期复跑历史空方标的；改为在 **已产生多个 bear winner 的交易日**（尤其 `2026-08-24`）填尚未跑过的流动性标的。

## 执行
- 脚本：`work/run_h1b_sample_fill_batch7.py`（Cursor 串行）
- 日志：`work/h1b-sample-fill-batch7.log`
- 并行：重试 Batch5 的 12 条 `t_plus_5_status=data_missing`（T+5 回填），争取 Dim4 回升

硬禁：不开加权、不部署、保持 3/1、仅请求级 `v2_debate_enabled`。
