# H-03a 分析入口接线（api/main + scheduled_service）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `0019d6c93ce1fd9d9bcc9b2bd7148bd62e76f51a`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** POST `/v1/analyze`、chat 建任务、定时/手动触发 把「字段未提供 vs 显式列表」完整传到 H-01 解析，中间层不得用 query 或 `user_intent.horizons` 伪造 explicit。  
**禁止：** frontend / `api.ts`（H-03b）；Portfolio（H-03c）；分析师/collector；缓存键；校准 hold_days；H-04+；C-04/C-09-3/Track B/H1b/PDF；`role_bindings`/`providers`；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-03a**。现网（合入后的 `0019d6c` / 当前主干后代）：

- `AnalyzeRequest` 已能区分未提供 vs 显式（H-01）。
- chat 已按未提供处理（H-01）。
- `_build_scheduled_analyze_request`（`api/main.py` 约 219–256）构造 `horizons=[horizon]` 且 `query="定时分析 {symbol}"`，并写入 `user_intent.horizons`。必须保证：定时 query **不能**把已选档扩成双档；DB 里的 `scheduled.horizon`（仅 `short`/`medium`，`scheduled_service._validate_horizon`）视为该入口的显式单档。
- 定时层目前**没有**双档存储。本卡不得把不支持的双档静默当 short；若某调用传入双档列表而 scheduled 模型只有单字段：显式拒绝（4xx/ValueError），不要丢档。

## 允许改

- `api/main.py`（analyze / chat 已有路径的接线缺口；`_build_scheduled_analyze_request` 及 batch/manual trigger）
- `api/services/scheduled_service.py`（仅当测试证明 create/update/serialize 会伪造或吞掉档位）
- `tests/test_horizon_entrypoints.py`（新建）
- 回归：`tests/test_watchlist_scheduled.py`、`tests/test_scheduled_queue.py`、`tests/test_horizon_profile_contract.py`

## 契约

1. HTTP 未提供 `horizons` → default short，`resolution_source=default`。
2. HTTP 显式 `["medium"]` → explicit medium；query 写「短中都看看」不得改 resolved。
3. chat 建 AnalyzeRequest 仍按未提供（H-01），不要把 LLM horizons 写回显式字段。
4. 定时：用任务上已存的 `horizon` 作为显式单档列表；query 文案不得改档；非法 horizon 保持现有校验失败。
5. 不改 UI；不改 H-02c 的 log/checkpointer 键。

## 测试

先写失败测。命令：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_entrypoints.py tests/test_horizon_profile_contract.py tests/test_watchlist_scheduled.py tests/test_scheduled_queue.py -q
```

覆盖：analyze 缺省/显式 medium/query 不扩档；scheduled 构造请求的 `horizons_resolution_source` 与 query 隔离；非法 scheduled horizon 仍失败。不要全仓。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、真实 pytest 计数。
