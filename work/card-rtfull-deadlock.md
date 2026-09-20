# RT-FULL 全量套件在主干既有死锁（阻塞 D-012 §4b 门禁）

## 问题

在目标主线 `b95a9b88c81e87a4da121f9945aedbe844fa04e0` 上运行全量 `pytest -q -p no:randomly`，稳定挂死于：

```
tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields
```

约 38% 处停住后不再推进；进程 `%CPU=0`、状态 `S`，不会自行结束。

## 证据（运维侧只读定位，2026-09-15）

- 单独运行该用例：纯主干与候选叠加树均 `1 passed`（~3s）→ 用例自身没问题。
- 全量运行：纯主干 `b95a9b88` 与候选叠加树在**同一位置**挂死 → 与任何在审候选无关，是主干既有。
- `/usr/bin/sample` 调用栈：主线程 `PyThread_acquire_lock_timed` → `_pthread_cond_wait`；另有线程停在 `poll` / `internal_select`。属线程锁 + socket 等待的并发/IO 死锁。
- 环境：`.venv310`（Python 3.10.20），`env -u PYTHONPATH`，`DATABASE_URL` 指向隔离临时库，外连指向关闭端口（不走代理、不访问真实供应商）。生产库全程零写入。

## 影响

1. D-012 §4b 把 RT-FULL 定为改产品代码的不可省门禁，但该门禁本身在干净环境下不可靠——审核员会反复超时重试（实测 DAV-976 的审核运行因此空转 1.5 小时）。
2. 历史上多轮"全量被系统杀掉 / 中途异常退出 / 只拿到退出码"的现象，很可能是同一死锁，而非并发跑法问题。
3. 在修复前，全量证据必须显式 deselect 该用例并在报告中标注，否则无法取得可比的失败集合。

## 施工边界

- 先只读定位根因（线程/executor 与 socket 等待的交互；是否与 `api` 层线程池、DB 会话或 mock 未覆盖的出网调用有关），给出最小复现顺序（哪几个测试文件按序执行即可触发）。
- 修复应落在测试隔离或被测代码的超时/资源释放上；**不得**简单删除或 xfail 该用例来"消红"。
- 不得修改被审候选、生产库、部署或个人配置。
- 修复后须证明：全量在 3.10.20 下可完整跑完并给出稳定的失败集合基线，供后续 RT-FULL 对照。

## 临时缓解（已在各审查卡内告知）

全量命令追加：
`--deselect "tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields"`
