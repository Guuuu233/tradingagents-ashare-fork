## 污染源已定位：`tests/test_api_smoke.py`（必要条件），机制指向 `api/main.py` 的全局 executor

在 `b95a9b88` 只读检出上用增量删除（delta debugging）实测，解释器 `.venv310`，每次试验独立进程 + 240s 看门狗。

### 定位过程

| 步骤 | 试验 | 结果 |
|---|---|---|
| 复现 | game_theory 之前的 85 个文件 + `rt7` | **HANG** |
| 二分 | 前 42 个 + `rt7` | OK（`1 failed, 945 passed`，190s） |
| 二分 | 后 43 个 + `rt7` | OK（`7 failed, 916 passed`，48s） |
| → | 两半单独都不挂 → **需跨半组合触发** | |
| 补集法 | 8 块逐块删除 | 仅**块 0（文件 1-11）删掉后不挂**，块 1-7 删掉后仍挂 |
| 块内二分 | 逐步收敛 | 收敛到**单个文件** |

**结论：`tests/test_api_smoke.py` 是挂死的必要条件**——把它移出集合，`rt7` 就不再挂；保留它并配合后续足够多的文件即复现。其「搭档」不是某一个特定文件（后 74 个文件的两半单独都不挂），呈**累积效应**，符合资源泄漏而非单点冲突的特征。

### 机制（强证据，建议按此方向修）

`tests/test_api_smoke.py:471 test_lifespan_can_restart_on_same_event_loop` 用 `asyncio.run(...)` 在**同一 pytest 进程内进出 `api.main.lifespan` 两次**。而 lifespan 的关闭路径是：

```python
# api/main.py:441-443（lifespan 内 yield 之后）
_executor.shutdown(wait=True)                      # ← 关的是模块级全局
if new_default_executor is not None:
    new_default_executor.shutdown(wait=False)      # ← 不等待，线程可残留
```

`_executor` 并非 lifespan 内创建的对象，而是**模块级全局单例**：

```python
# api/main.py:607
_executor = ThreadPoolExecutor(max_workers=int(os.getenv("TA_MAX_WORKERS", "2")))
```

因此该测试一旦退出 lifespan，就把**整个 pytest 进程范围内**的 `api.main._executor` 永久关闭，后续任何模块再向它提交任务都无法正常执行。同时每次进入 lifespan 会新建一个 `max_workers=64`、`thread_name_prefix="ta-asyncio"` 的 executor 并 `loop.set_default_executor(...)`，绑定在 `asyncio.run` 的临时 loop 上；退出时 `shutdown(wait=False)` 不等待回收，而模块级 `_default_executor` 仍指向这些已关闭/已失效的对象（`api/main.py:4567-4569` 的 healthz 探针还会读它）。

这与此前采到的栈一致：主线程停在 `PyThread_acquire_lock_timed → _pthread_cond_wait`（等一个永远不会被执行的 future/锁），另有线程停在 `poll`/`internal_select`。

另外 `tests/test_api_smoke.py:59 _get_client()` 返回 `TestClient(app, raise_server_exceptions=False)`，**既未用 `with` 也未 `.close()`**，同样会累积未回收的 portal 线程。

### 建议修法（按优先级，需另开实施卡）

1. **让 lifespan 只关自己创建的资源**：`_executor` 不应在 lifespan 退出时被 `shutdown`，或改为每次 lifespan 创建独立实例；退出后把模块级引用复位为 `None`，避免留下已关闭的全局单例。
2. `new_default_executor.shutdown(wait=True)`，并在退出时 `loop.set_default_executor(None)`、`_default_executor = None`。
3. `test_lifespan_can_restart_on_same_event_loop` 增加 fixture，在用例结束后恢复 `api.main._executor` / `_default_executor` 的进程级状态（或标记为需独立进程运行）。
4. `_get_client()` 改为 `with TestClient(app) as client:` 形式的 fixture，确保关闭。

### 对门禁的影响

- 这解释了为什么 `rt7` **单独跑 0.18s 通过、全量里必挂**，也解释了 96% 处高 CPU 空转（同源的线程池/loop 失效很可能是同一类问题的另一表现）。
- 在修复前，**主干仍无法取得一次完整 RT-FULL 基线**；现行可用替代方案仍是分文件对照（两侧相同切分）。
- 本次定位全程只读，未改任何代码，未合入、未部署、未写生产库。

复现命令（240s 内必挂）：

```
cd <b95a9b88 只读检出>
env -u PYTHONPATH -u all_proxy PYTHONPATH=$PWD DATABASE_URL="sqlite:////private/tmp/x.db" \
  http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 no_proxy=127.0.0.1,localhost \
  .venv310/bin/python -m pytest -q -p no:randomly \
  $(ls tests/test_*.py | sort | head -85 | tr '\n' ' ') \
  tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation
```

移除 `tests/test_api_smoke.py` 后同一命令可正常结束。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
