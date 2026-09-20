# P2-T7：SocialDataCollector 与配置 modes

## 目标

实现独立 `SocialDataCollector` + 配置/环境变量接线，产出 `SocialDataContext`（含 bundle）。**默认 `TA_SOCIAL_MODE=disabled`**。本卡**不**改 `data_collector.py` / Graph / API / analyst（Task 8+）。不删 `legacy_proxy`。不部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `bdefe4f85f61d5308280846e44ff69ca93bc3116`
- **新建**隔离分支，例如 `agent/dev2/p2-t7-social-collector`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 7 + §7 配置与运行模式
- D-008 / D-009 / D-010；`SocialDataContext` / `create_default_social_data_context` 已在 contracts

## 文件白名单

1. `tradingagents/dataflows/social/collector.py`（新建）
2. `tradingagents/default_config.py`（仅追加 social 配置块，不改辩论轮次/既有默认）
3. `.env.example`（仅追加 `TA_SOCIAL_*` 注释项；**默认保持 disabled / 空路径**）
4. `tests/test_social_data_collector.py`（新建）
5. `tradingagents/dataflows/social/__init__.py`（仅最小导出）

禁止：`data_collector.py`、`trading_graph.py`、`api/main.py`、analyst/prompts、删 legacy、改 `max_debate_rounds` / 开加权。

## 行为契约

### A. 配置（§7）

从 env / config 读取（名称与默认严格一致）：

| 键 | 默认 |
|---|---|
| `TA_SOCIAL_MODE` | `disabled` |
| `TA_SOCIAL_PROVIDER` | `archive_sqlite` |
| `TA_SOCIAL_ARCHIVE_DB` | 空 |
| `TA_SOCIAL_PLATFORMS` | `xhs,dy` |
| `TA_SOCIAL_LOOKBACK_DAYS` | `7` |
| `TA_SOCIAL_MAX_POSTS` | `100` |
| `TA_SOCIAL_MAX_COMMENTS` | `300` |
| `TA_SOCIAL_MIN_POSTS` | `3` |
| `TA_SOCIAL_MIN_CLASSIFIED` | `20` |
| `TA_SOCIAL_MIN_AUTHORS` | `10` |
| `TA_SOCIAL_EVIDENCE_LIMIT` | `20` |
| `TA_SOCIAL_CANARY_SYMBOLS` | 空 |
| `TA_SOCIAL_FETCH_TIMEOUT` | `5`（秒） |

`default_config.py` 增加对应键（建议嵌套 `social: {...}` 或扁平 `social_*`——与仓库现有风格一致即可，**必须可测**）。

### B. Collector API

建议：`SocialDataCollector.collect(symbol, as_of, *, now=None) -> SocialDataContext`（dict/TypedDict 与 contracts 对齐）。

内部：provider `fetch_records` → aggregator `aggregate_sentiment_bundle` → 组装 context（`mode`、`bundle`、`direction_allowed`、`reason_codes`、`source_provenance`、`data_failure_ledger` 按 §5.5）。

### C. Mode 行为（本卡可测，不接图）

1. **`disabled`**：返回 `status=not_applicable`（或契约等价），**不读 archive**；`direction_allowed=false`；reason `social_not_applicable`。不调用 provider（测 spy/mock）。
2. **`shadow` / `active`**：
   - `TA_SOCIAL_ARCHIVE_DB` 空或非绝对路径 → typed `failed`（`social_archive_missing`），不得静默成功
   - 绝对路径缺失文件 → 同 failed
   - 成功路径：返回真实 bundle；`mode` 字段反映请求 mode
3. **非 A 股 symbol**（无法规范为 A 股代码）：`not_applicable`，不读库
4. **canary**：`active` 且 `TA_SOCIAL_CANARY_SYMBOLS` 非空、symbol 未命中 → **不得 silently active**；本卡定义为回落 `disabled`/`shadow` 行为中的非 active 路径（实现选一并写测钉死；推荐：当作该次 `disabled`/`not_applicable` 或显式 shadow-only——**禁止**对未命中 canary 的标的走 active bundle 进方向）
5. **超时**：短超时读取；超时 → `timeout` + `social_archive_locked` 或等价 operational（与 provider 语义一致）；**不得**占用/假设市场线程池（本卡无市场线程池，测「collect 自身 timeout 配置被传入 provider」即可）
6. 日志：只打 symbol/日期/计数/状态/耗时；**不**打 Cookie、完整正文、用户标识；archive 路径可打 hash 而非明文（若打路径，测试勿断言绝对路径泄露到不该出现的地方）

### D. 明确不做

- 不改 `data_collector.collect()`（Task 8）
- 不实现 Gate 4 删 legacy
- 不把 mode 默认改成 shadow/active

## 测试（TDD）

离线；禁止实网；禁止 `@pytest.mark.asyncio`。

至少：

1. 默认/disabled：不打开 DB
2. shadow/active + 空路径 → failed missing
3. 相对路径拒绝（非绝对）
4. 非 A 股 → not_applicable
5. canary 未命中不得 active
6. 成功路径（tmp archive fixture + 足够样本或降阈值测）返回 context 含 bundle
7. timeout_ms/timeout 配置传到 provider（可用 stub）
8. 回归 T5/T6 套件仍绿

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

## 交付

1. 一个 commit：`feat(social): configure social collector modes`
2. 推隔离分支；评论完整 40 位 SHA + pytest 数字 + `git diff --stat`
3. **不要自行 FF**；等 Cursor「准予合入」
4. 不要部署；不要开 Task 8

## 风险

- 配置键命名与 §7 不一致会导致后续 Task 8 对接失败——以 §7 表为准
- 函数保持可拆；避免再堆 600+ 行单文件无分层
