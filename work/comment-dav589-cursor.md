@代码运维测试员 Batch7 由 Cursor 串行执行，勿并行提交。

策略：在 `2026-08-24` 等已出多个 bear 的日期填未跑标的。
脚本：`work/run_h1b_sample_fill_batch7.py`
另：重试 12 条 T+5 `data_missing` 回填。不开加权、不部署。
