@代码运维测试员 Batch6 由 Cursor 串行执行（勿并行提交 analyze）。

脚本：`work/run_h1b_sample_fill_batch6.py`
策略：历史 bear-winner 标的 × T+5 已到期日期（≤08-27）。
请只读盯 log / 服务存活；API 挂了再重启。不开加权、不部署。
