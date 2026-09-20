## Cursor 解阻 + 执行 Batch5

本地阻塞已解除：
- CLIProxyAPI `:8317` 已拉起
- API `:8000` healthz=200，commit=`31c32f0f877e86fc3c06eb58a34b4dc08a453044`，`executor_threads=1`，持久 3/1 完好
- 脚本：`work/run_h1b_sample_fill_batch5.py`（log：`work/h1b-sample-fill-batch5.log`）

**Cursor 正在宿主机串行跑 Batch5**，避免与运维重复提交。请运维：
1. **不要**另开并行 analyze 队列
2. 可只读盯 log / 门槛；若发现 API 挂了再重启
3. 跑完后 Cursor 会复跑 `verify_h1b_gates` 并在本卡汇总

仍禁止：部署、开 `credit_weighting_enabled`、改 3/1。
