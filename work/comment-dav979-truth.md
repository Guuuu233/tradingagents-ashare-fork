## 🔴 重大更正：根本不是死锁，是测试在打真实外网；我此前两次归因都错了

### 决定性证据

```
$ .venv310/bin/python -m pytest -q -p no:randomly \
    tests/test_api_smoke.py tests/test_fund_flow_scale_consumption.py
67 passed, 73 warnings in 1305.87s (0:21:45)
退出码 = 0
```

**它会跑完**，只是要 21 分 45 秒跑 67 条。此前所有「HANG」判定（我的 120s / 240s / 400s 看门狗，以及开发侧的 600s 超时）**全部是看门狗误判**，不是真死锁。

### 真正的机制

用 `PYTHONFAULTHANDLER=1` + `SIGABRT` 取到 Python 层栈（pytest 9 已无 `--faulthandler-timeout`，改用此法）：

**主线程**：
```
threading.py:324 in wait                     ← 带超时的等待分支
concurrent/futures/_base.py:453 in result
tradingagents/dataflows/interface.py:440 in route_to_vendor
tradingagents/knowledge/historical_cases.py:173 in calculate_t1_return
tradingagents/knowledge/historical_cases.py:364 in record_historical_case
api/services/report_service.py:1785 in create_report
tests/test_fund_flow_scale_consumption.py:205 in test_single_horizon_report_persists_and_reads_all_scale_fields
```

**某个 provider-call 工作线程**：
```
baostock/util/socketutil.py:65 in send_msg   ← 真实 TCP 发包，阻塞
tradingagents/dataflows/providers/cn_baostock_provider.py:66 in _session   （bs.login()）
tradingagents/dataflows/providers/cn_baostock_provider.py:87 in _fetch_hist_df
tradingagents/dataflows/providers/cn_baostock_provider.py:126 in get_stock_data
tradingagents/dataflows/interface.py:366 in <lambda>
```

即：测试实际在**连真实 baostock 服务器**。`interface.py:440` 的 `future.result(timeout=policy.timeout_seconds)` 是有超时的（`DEFAULT_PROVIDER_RESOURCE_POLICY.timeout_seconds=60.0`，且 `base.py:15` 强制为正），`max_retries=1`，`route_to_vendor` 还会沿 vendor 链逐个重试 —— 于是单次调用最坏 `60s × 2 次尝试 × 链上多个 vendor`，外加 `_submit_provider_call` 里 `semaphore.acquire(timeout=60)`，累积成几十分钟的「假死」。

**为什么 `http_proxy=127.0.0.1:9` 挡不住**：baostock 用**裸 TCP socket**（非 HTTP），不读 `http_proxy`/`https_proxy` 环境变量，因此我们一直以为已经断网的跑法，其实 baostock 那条路径始终在连真实外网。

### 为什么呈现「累积效应」和「必须有 api_smoke」

| 跑法 | 结果 |
|---|---|
| 单独跑 `test_single_horizon_report_persists_and_reads_all_scale_fields` | **1 passed in 1.58s** |
| 单独跑 `tests/test_api_smoke.py` | **56 passed in 29.53s** |
| `api_smoke` + `fund_flow_scale_consumption` | **67 passed in 1305.87s** |

同一条用例单独跑 1.58 秒、跟在 `api_smoke` 后面就要走真实 vendor 调用 —— 说明 `test_api_smoke.py`（其 lifespan 会 `init_db`、`_load_cn_trade_dates`、`_load_cn_stock_map`、构建 registry 等）**改变了进程内的 provider 路由/缓存状态**，使后续用例从「命中桩/缓存」变成「走真实 vendor 链」。这也解释了 delta debugging 得出的「`test_api_smoke.py` 是必要条件 + 其余文件呈累积效应」。

### 我此前两个错误结论，正式撤销

1. **撤销**「挂死用例是 `tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation`」。该结论来自用进度字符数反推收集序号，方法本身不可靠（subtests、warnings 等都会影响字符数）。faulthandler 栈显示真正的慢用例是 `tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields` —— **即本卡最初记录的那一条，原始归因是对的**，是我误证伪了它。
2. **撤销**「根因是 `api/main.py:441` 关闭模块级全局 `_executor`」。DAV-989 候选 `b5f63d4` 恰好按该方向修（移除 `_executor.shutdown`、`new_default_executor` 改 `wait=True` 并复位），实测复现命令**仍在同一位置卡住**（`STAT=S`、`%CPU=0.0`、进度冻结 90s+ 不动），证明该机制不是原因。

原 `--deselect` 方案无效的真实原因也随之清楚：deselect 掉一条慢用例，下一条走同样 vendor 链的用例照样慢，挂死点只是往后移。

### 结论与建议方向

**门禁问题的性质要重新定义**：不是「RT-FULL 会死锁、跑不完」，而是「RT-FULL 会真实访问外部行情供应商，因而极慢且结果依赖网络状况」。后者同时是**正确性与合规问题**：单元测试不应触达真实 vendor。

建议（需另开实施卡，不要在本卡直接改）：

1. **离线护栏（最高优先）**：测试会话默认禁止对外连接（如 autouse fixture 在 socket 层拦截非 localhost 连接，或统一以 fake/桩 provider 注入 registry）。需要真连的用例显式标记（项目已有 `addopts = -m 'not network'` 的 network 标记机制可复用）。
2. **定位 `test_api_smoke.py` 留下的全局状态**，使后续用例回落到真实 vendor 链；修掉该污染或在用例间复位 registry/缓存。
3. **`cn_baostock_provider` 增加 socket 级超时**，避免裸 TCP 无限期阻塞；并让它在无网络时快速失败。
4. 测试环境下调 `TA_PROVIDER_MAX_WORKERS` / `timeout_seconds`，把最坏耗时压到可接受范围。

### 对在途卡的处置建议

- **DAV-989 / DAV-991**：`b5f63d4` 修的是真实存在的资源泄漏（`new_default_executor` 不等待回收、`TestClient` 未关闭），**本身有价值**，但**不能宣称修复了 RT-FULL 挂死**。请审查卡据此调整验收口径，不要用「复现命令能跑完」作为它的通过条件。
- **DAV-992**（我创建）与 DAV-989 严重重叠，且其验收标准基于我已撤销的错误根因，**应作废**，改以本评论为准重新立卡。这是我的流程失误：建卡前未先检索 DAV-979 已有子卡。

### 对当前门禁口径的影响

- 分文件对照法**继续有效**（两侧相同切分、同样受网络影响），此前 5 次合入的「零新增失败」结论不受本更正影响。
- 但其中标记为 `HANG` 的文件（如 `tests/test_knowledge_rag.py`）应重新理解为「超过 120s 看门狗」，很可能同样是真实网络调用导致的慢，而非挂死。
- 运维已在主线 `28d1adc6` 上启动一次**不设看门狗的完整 RT-FULL**，用于首次取得真实基线，结果出来后附到本卡。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
