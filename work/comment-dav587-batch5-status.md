## Batch5 进度（Cursor）

旧 runner 进程因轮询僵尸 job `a4a06105…` **HTTP 404** 超时退出（exit 1）——**不是**整批作废。

实质进度：
- 队列目标 `TARGET_NEW=12` 已在 ~03:07 达成；v2 约 52→64（门槛合格池现 **81**）
- **Dim3 交易日：PASS（30/30）** ← Batch5 主要收益
- Dim2/Dim5 仍 FAIL：bull/bear 仍 **46/16**（新样本几乎未增加空方 winner）
- Dim4 曾因新样本未回填掉到 ~85%；已对本地库执行 T+5 shadow **write backfill** 并复跑门槛（见后续评论）

CLIProxy 已重启；API `:8000` 仍健康。仍 **KEEP_FALSE** / 不部署。

### 门槛复跑（T+5 write 后）
- N PASS (81)
- Side FAIL 46/16
- Time **PASS 30/30**
- T+5 FAIL 90.1%（8 条行情缺失；已评估 73/81）
- Balance FAIL 74.2%
- 建议：KEEP_FALSE

下一步若继续：需真正增加 **bear winner**（仅铺交易日不够）；行情缺失 8 条可另查。
