## 候选 `896eaca3` 继续 HOLD：验收口径加严两条，两条均未满足

**本条是返修派工，不是存档。** 先说清定性：**这不是 DAV-1005 的漏审**——前轮打回只要求「启动期异常也恢复状态」「硬化失败 fail-closed」，候选按那两条确实做到了，DAV-1005 的 PASS 在当时口径下成立。是**验收口径在交付后被加严**，需要补齐。已修对的部分一律不要回退。

（注：主控的 steer 在本轮 run 结束后才发出，未送达，故加严要求未能进入上一轮实现。）

---

### 🔴 加严 1：启动期注入测试必须在同一 event loop 上重启 lifespan

现状 `tests/test_api_smoke.py::test_lifespan_startup_failure_cleans_up_global_state` 断言了三件事：timeout 已还原、`_default_executor is None`、无存活 `ta-asyncio` 线程。这些都对，但**测不到真正的残留**。

结构性盲区：

```python
asyncio.run(_failing_lifespan())     # ← 每次调用创建全新 loop，结束即关闭
```

`loop.set_default_executor(new_default_executor)` 把引用挂在 **loop** 上。`asyncio.run()` 用完即弃整个 loop，所以**即使把一个已 shutdown 的 executor 遗留为该 loop 的 default executor，这个测试也永远发现不了**——loop 已经不存在了。

而生产里 loop 是长期存活的。后续任何 `run_in_executor(None, ...)` 会命中那个已关闭的 executor，抛 `RuntimeError: cannot schedule new futures after shutdown`。

**要求**：改为**显式持有一个 loop**，在**同一个 loop** 上：

1. 先跑一次**失败**的 lifespan（注入启动期异常）
2. 再在**同一 loop** 上启动一次**正常** lifespan 并成功跑通
3. 通过 `loop.run_in_executor(None, ...)` 提交一次真实任务并拿到结果，证明该 loop 的 default executor 可用、不是已关闭的那个

注意上方已有的顺序 lifespan 测试走的是**成功路径**，不能替代——本条要覆盖的是**失败之后**的复用。

---

### 🔴 加严 2：fail-closed 测试必须断言 login 未被调用，且端到端而非注入

现状 `tests/test_offline_network_guardrail.py:399` 有两处不足：

**(a) 没有断言 `bs.login()` 根本没被调用。**
当前只断言 `_bs()` / `_session()` 抛 `NotImplementedError`。但「抛异常」不等于「没连过」——将来若有人把硬化检查挪到 login 之后，测试**照样绿**，而实际已经建立了未硬化连接，EOF 活锁的窗口重新打开。
**要求**：对 `bs.login` 下 spy/mock，断言 `call_count == 0`，锁住「拒绝发生在登录之前」。

**(b) 端到端链路是伪造的，不是跑通的。**

```python
hardening_err = NotImplementedError("baostock 硬化失败，...")        # ← 手工捏造的异常
with patch("tradingagents.dataflows.interface.route_to_vendor", side_effect=hardening_err):
    eval_date, return_pct, refusal = calculate_t1_return(...)
```

这里把 `route_to_vendor` 换成了一个**手写异常**，等于绕开了「provider 真实失败 → 异常如何向上传播 → `historical_cases` 如何归类」的**整段真实路径**。若真实路径上有任何一层对 `NotImplementedError` 做了不同处理（吞掉、改写、换 code），这条测试依旧全绿。这正是「只测分类函数」的变体。

**要求**：让**真实的 provider 硬化失败**（沿用 `monkeypatch.delattr(bssock, "SocketUtil")` 那套）沿真实调用链传播到 `calculate_t1_return` / 回填链路，再断言产出 `vendor_refuse` 且 `terminal is False`、案例仍在回填队列（`total_scanned > 0`）。**不得用 `side_effect=` 注入捏造异常替代。**

---

### 其余不变

- 新 SHA 须重跑 **RT-FULL-OFFLINE**（不带任何 `--deselect`/`-k`/路径限定，相对 `5a0320f` 基线 20 项零新增）+ **RT-NETWORK**
- `896eaca3` 与 DAV-1005 的 PASS 随本条失效；新 SHA 由**新开的复审卡**承接，不复用 DAV-1005
- 禁止对 `/tmp` 共享路径通配删除；清理进程按 PID 精确处理
- 主线 `5a0320f` 继续 HOLD，不合入、不部署

### 复审口径沉淀（供后续所有卡）

本轮两条的共性是**测试的结构本身决定了它能否发现目标缺陷**。今后写/审这类测试须自问：

1. 这个测试**有没有可能失败**？若把被测修复整个删掉，它会不会依然绿？
2. 是否用 `asyncio.run()` / 新建对象等方式，把「被测状态所依附的宿主」一并丢弃了？
3. 是否用 `side_effect=` 注入了本该由真实路径产生的异常？——那样测的是断言本身，不是链路。
