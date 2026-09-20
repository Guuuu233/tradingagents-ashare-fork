# P2-T8：DataCollector 市场采集之后读社交；拆 market_attention

## 目标

在 **已合入的** `SocialDataCollector`（主干 tip）之上，把社交读入接到 `DataCollector.collect()`：**先** `_fetch_all`（市场线程池），**再**独立短超时调用 social collector。从 `zt_pool` / `hot_stocks` 拆出 `market_data_context.market_attention`。`pool["news"]` 与 `social_data_context` 必须是独立键。

本卡**不**改 Graph / Propagator / `api/main.py` / analyst / prompts（Task 9+）。不删 `legacy_proxy`。不部署。默认 `TA_SOCIAL_MODE` 仍为 `disabled`。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`
- **新建**隔离分支，例如 `agent/dev2/p2-t8-data-collector-social`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 8 + §架构要点（社交在 `_fetch_all` **之后**、独立超时）
- D-008 / D-009 / D-010
- 已有：`tradingagents/dataflows/social/collector.py`、`contracts.create_default_social_data_context`

## 文件白名单

1. `tradingagents/graph/data_collector.py`（接线 + market_attention；**禁止**把社交塞进 `_fetch_all` 的 `tasks` / `ThreadPoolExecutor`）
2. `tests/test_data_collector.py`（既有回归仍绿；可追加与接线相关的最小断言）
3. `tests/test_data_collector_social_integration.py`（新建；本卡主测）

禁止：`trading_graph.py`、`propagation.py`、`agent_states.py`、`api/main.py`、analyst/prompts、改 `default_config` 默认 mode、删 legacy、改辩论轮次 / 开加权。

如需从 social 包 import，只允许 `from tradingagents.dataflows.social...` 既有公开符号；**不要**为了注入再改 `collector.py`（除非发现阻塞 T8 的明确 bug——若改，必须在交付里单列说明且仍不扩 Task 9）。

## 行为契约

### A. 注入

- `DataCollector.__init__` 增加可选 `social_collector=None`（或等价可测注入点）。
  - 默认：可用配置/`SocialDataCollector` 构造；测试可注入 stub/spy。
- **不要**在模块 import 时强制打开 archive。

### B. `collect()` 顺序（硬约束）

1. 现有路径：拿到锁 → 若未缓存则调用 `_fetch_all(...)` → 得到市场 `pool`。
2. **之后**（仍在同一 collect 路径、写入缓存前或写入时）：调用 social collector，独立超时（使用 `TA_SOCIAL_FETCH_TIMEOUT` / config 秒数；可用 `concurrent.futures.wait` 单 future，或 collector 自身超时——**不得**占用 `_fetch_all` 的 `FETCH_MAX_WORKERS` 池）。
3. 将结果写入 `pool["social_data_context"]`（contracts 形状）。
4. 社交失败 / 超时 / 异常：写 typed context（failed/timeout/… + ledger），**不得**抛穿导致市场 cache 丢失；市场字段仍按原逻辑缓存。
5. `disabled`：仍应写入明确的 `social_data_context`（`not_applicable` / mode=disabled 等，与 T7 契约一致），且 stub 证明未读 archive。

### C. 禁止事项（测试钉死）

- `_fetch_all` 的 `tasks` 字典**不得**出现 social / SocialDataCollector 相关 key。
- 对 `_fetch_all` 内 `ThreadPoolExecutor.submit` 的 spy：不得提交 social 调用。
- `pool["news"]` 继续只来自市场新闻拉取；不得被 social 覆盖或与 `social_data_context` 合并成同一键。

### D. `market_attention`

在 `_fetch_all` 组装 `market_data_context` 时（或紧随其后、同一函数返回前）增加：

```text
market_data_context["market_attention"] = {
  # 至少能审计：
  "zt_pool": { status / as_of / 摘要或保留原 payload 引用策略——择一并测钉死 },
  "hot_stocks": { 同上 },
}
```

要求：

- 保留可解析的 status / as_of（可复用既有 provenance / gap 语义；不要发明与 ledger 冲突的新成功伪装）。
- **不得**据此推断散户看多/看空分数（那是更后任务）。
- 缺失/失败源：显式 gap/status，禁止静默空 dict 当成功。

### E. 缓存语义

- 同一 `ticker+trade_date` 缓存命中时：返回的 deepcopy 须含当时写入的 `social_data_context` 与 `market_attention`（若第一次 collect 已写入）。
- 社交失败不得阻止市场 cache 写入。

## 测试（TDD）

离线；禁止实网；禁止 `@pytest.mark.asyncio`（无则 `asyncio.run`）。

`tests/test_data_collector_social_integration.py` 至少：

1. `disabled`：spy social collector / provider **未被调用**；pool 仍有 `social_data_context` 且 mode/status 正确。
2. 注入 stub 成功：`collect` 后 `pool["social_data_context"]` == stub 返回；`pool["news"]` 独立。
3. stub 抛异常 / 超时：市场键仍在；`social_data_context` 为 failed/timeout 类；不抛穿。
4. 静态或行为断言：社交不在 `_fetch_all` tasks / 不经市场 ThreadPoolExecutor。
5. `market_attention` 在成功/失败 zt_pool、hot_stocks 路径下有 status/as_of（可用 patch `_fetch_all` 子结果或浅层 stub）。
6. 回归：既有 `tests/test_data_collector.py` 全绿。

建议命令：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_data_collector_social_integration.py \
  tests/test_data_collector.py \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py
```

## 交付

1. 单 commit：`feat(data): collect social context after market fetch`
2. 推送隔离分支；评论写完整 40 位 SHA、`git diff --stat`、pytest 精确数字
3. 状态 `in_review`；**不要** @项目调度助手催工；**不要**自行 FF / 部署
4. Cursor 独立复审后才会「准予合入」

## 明确不做

- Task 9+（state/graph/api wiring、analyst 重写、删 legacy）
- 改 `TA_SOCIAL_MODE` 默认
- 部署 / 重启生产
