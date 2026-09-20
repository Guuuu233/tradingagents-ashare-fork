## 更正：此前对挂死用例的归因不成立，实际是三类现象

本卡原先记录的「挂死点 = `tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields`」**不准确**，据此给出的 `--deselect` 缓解方案**无效**。以下为实测更正。

### 实测经过

在 `b95a9b88`（基线）与「基线 + 三个 PASS 候选叠加」两棵树上并行执行全量，命令完全一致，均已带上原 deselect：

```
env -u PYTHONPATH -u all_proxy -u ALL_PROXY PYTHONPATH=<worktree> \
  DATABASE_URL="sqlite:////private/tmp/rtfull-<side>.db" \
  http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 no_proxy=127.0.0.1,localhost \
  .venv310/bin/python -m pytest -q -p no:randomly --deselect "<原用例>"
```

结果：**两侧都在 38% 处挂死**，`STAT=S`、`%CPU=0.0`、RSS≈370MB，超过 6 分钟无任何输出。

deselect 本身是生效的（`--collect-only` 显示 `4652/4656 tests collected (4 deselected)`），所以挂死的是**另一条用例**。

### 真正的挂死位置

用已输出的进度字符数精确定位：两侧均已完成 **1872** 条，即停在收集顺序第 **1873** 条：

```
tests/game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation
（准确路径：tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation）
```

前后文：1871 `test_rt5_persistence_and_readback_consistency`、1872 `test_rt6_deterministic_traceability_no_hallucination`、1874 `test_acceptance_fill_rate_measured_non_zero_and_final_state_not_none`。

### 关键：它单独跑并不挂

| 运行方式 | 结果 |
|---|---|
| 只跑 `::test_rt7_single_and_dual_horizon_isolation` | **1 passed in 0.18s** |
| 单独跑整个 `tests/test_game_theory_integration.py` | **1 failed, 17 passed in 2.73s**（2.73 秒内结束） |
| 全量套件上下文中跑到它 | **无限期挂死** |

因此这不是该用例自身的缺陷，而是**跨用例污染**：前面某个测试遗留了未释放的锁或未回收的线程，导致它在全量上下文里阻塞。这与此前采到的栈一致——主线程 `PyThread_acquire_lock_timed → _pthread_cond_wait`，另有线程停在 `poll`/`internal_select`。

### 另外还有第三种现象

`资深开发1` 在 DAV-946 的运行中（PID 97239，任务工作区 `.venv`、未加 `-p no:randomly`、未 deselect）观察到：跑到 **96%** 后停止输出 **51 分钟**，但 `%CPU≈171`、15 个线程持续占用 CPU。这与 38% 处 `%CPU=0` 的阻塞型挂死**不是同一种**，属高 CPU 空转。

### 汇总：本卡需要覆盖的是三类，不是一类

| # | 位置 | CPU | 形态 | 是否已定位 |
|---|---|---|---|---|
| 1 | 38% `game_theory::test_rt7...` | 0% | 锁阻塞，跨用例污染触发 | 已定位到用例，**污染源未定位** |
| 2 | 96% | ~171% | 高 CPU 空转 | 未定位 |
| 3 | 原记录的 fund_flow 用例 | — | **归因不成立，应撤销** | 已证伪 |

### 对门禁的影响（重要）

- **原 deselect 方案无效**，任何引用它来声称「已绕过死锁」的审查结论都不足以支撑 RT-FULL 通过。
- 在污染源定位之前，主干**无法取得一次完整的 RT-FULL 基线**。
- 当前替代方案：**分文件执行（`tests/test_*.py` 逐个独立进程，每个 120s 看门狗），基线与候选两侧使用完全相同的切分**，再逐项对照失败集合。切分一致时该对照仍然有效，且能把所有挂死文件单独标出。运维正在执行，完成后会把两侧结果附到本卡。
- 环境未安装 `pytest-timeout`；**不要**为此擅自向 `.venv310` 安装依赖（会动锁定环境），需要的话另行开卡评估。

### 建议的排查方向

1. 二分定位污染源：在 1873 之前按文件区间裁剪，找出使 rt7 阻塞的最小前置集合。
2. 重点看 1873 之前是否有测试起了后台线程 / `ThreadPoolExecutor` / DB 连接未关闭 / `atexit` 未注册清理。
3. 检查 `test_game_theory_integration.py` 是否依赖某个全局单例或共享 SQLite 连接。
4. 同步排查 96% 处的高 CPU 空转，方法同上。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
