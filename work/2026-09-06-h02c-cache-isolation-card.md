# H-02c 派生结果键隔离（不裁原始池）

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `afd89503b9f93fc4b4a5b6b5a868bca686de030f`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 同股票同日、不同 `resolved` 档位 / `horizon_profile` **不得串用派生结果**。原始采集池继续按 ticker+date 共享。  
**禁止：** 改 `make_cache_key(ticker, trade_date)` 语义或按档位拆原始池；改 frontend；H-03+；校准 `DEFAULT_HOLD_DAYS` / `calibration_service._cache_key` 的 T+5 语义；`evaluation_eligible=true`；收益标签；C-04/C-09-3/Track B/H1b/PDF；`role_bindings`/`providers`；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-02c**。开卡前点名（`afd8950`）：

| 落点 | 现状 | 本卡 |
|---|---|---|
| `tradingagents/graph/data_collector.py::make_cache_key` | `f"{ticker}_{trade_date}"` | **禁止改键**；测换档仍共享未裁剪池 |
| `TradingAgentsGraph.log_states_dict` / `_log_state` / `full_states_log_{trade_date}.json` | 仅按 `trade_date` | 必须纳入本图 `horizon` 或整次 `resolved`，避免 short 被 medium 覆盖 |
| `propagate` 默认 checkpointer `thread_id=f"{company}_{trade_date}"`（`trading_graph.py` 约 364–368） | 无档位 | 同日换档不得复用同一 thread；API 双档已有 `f"{job_id}_{horizon}"`，不要破坏 |
| `report_service.get_latest_reports_by_symbols` | 每符号只取最新一行，无视档位 | 若测试证明会把 medium 报告当成 short（或反之）则最小修复：**读取侧**按 metadata 区分或拒绝混用，禁止新 DB 列 |

不要扩到 industry/macro TTL 缓存（不是分析裁决派生键）。

## 允许改

- `tradingagents/graph/trading_graph.py`（log / thread_id）
- 仅当测试钉死混档：`api/services/report_service.py` 的 **get_latest** 路径（不要重写 H-02b 的 persist/read metadata）
- `tests/test_horizon_run_metadata.py` 或新建 `tests/test_horizon_cache_isolation.py`
- 回归：`tests/test_data_collector.py`（`test_make_cache_key`）、`tests/test_trading_graph_multi_horizon.py`、`tests/test_report_social_context.py`

## 契约

1. 连续跑同一 ticker+date 的 short 与 medium：内存 `log_states_dict` 与落盘 log **都能分别取回**，后跑的档不得覆盖先跑的档。
2. checkpointer `thread_id` 在换档时不同；不要把原始数据 cache 拆成两份。
3. `collect(..., horizons=...)` 仍共用 `make_cache_key`；断言池不被按档裁剪。
4. 旧 log 无档位后缀：读取时标 unknown/legacy，不把内容解释成新 T+40 跑次。

## 测试

先写失败测再改键。命令：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_cache_isolation.py tests/test_horizon_run_metadata.py tests/test_data_collector.py tests/test_trading_graph_multi_horizon.py tests/test_report_social_context.py -q
```

若未新建 `test_horizon_cache_isolation.py`，把隔离测放进 `test_horizon_run_metadata.py` 并在评论写明。不要全仓。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、真实 pytest 计数。
