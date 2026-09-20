## ⚠️ RT-FULL 全量套件存在主干既有死锁，请按此处理后继续复审

你的全量回归很可能不是候选引起的失败，而是撞上了**目标主线 `b95a9b88` 既有的全量套件死锁**。已由运维侧独立定位，证据如下。

### 挂死点

```
tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields
```

进度约 38% 处停住，进程 `%CPU=0`、状态 `S`，日志不再增长，不会自行结束（超过 90 秒即可判定）。

### 已确认这不是候选引入

1. **单独运行该用例两边都通过**：纯主干 `b95a9b88` 与候选叠加树各跑一次，均 `1 passed`（约 3 秒）。
2. **纯主干全量在同一环境同一位置挂死**：在 `b95a9b88` 干净 detached worktree 上跑全量，同样停在 38% 的同一条用例，行为与候选树一致。
3. **调用栈证据**（`/usr/bin/sample`）：主线程阻塞在 `PyThread_acquire_lock_timed` → `_pthread_cond_wait`，另有一个线程停在 `poll` / `internal_select`。即线程锁等待 + socket 等待，属并发/IO 死锁，不是计算慢。

结论：**这是主线既有缺陷，不得计入任何候选的新增失败，也不得据此打回候选。**

### 请这样继续

在你原有的全量命令后追加 deselect，重跑一次并照常给出精确统计：

```
-p no:randomly --deselect "tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields"
```

并在报告中显式写明：

- 该用例被 deselect 及其原因（主干既有死锁，附本评论链接）；
- deselect 后的 collected / passed / failed / skipped / deselected 精确数字；
- 失败集合与目标主线基线的逐项对照结论（只判断有无**新增**失败）。

### 其他注意

- 固定解释器要求为 **Python 3.10.20**；若你的运行环境是 3.11/3.14，结论中必须注明，且发布门禁需要 3.10 下的复跑数字。
- 不要为了让全量跑完而修改被审候选、测试断言或配置；本卡仍是只读复审。
