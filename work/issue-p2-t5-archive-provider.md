# P2-T5：只读 archive provider 与资格护栏

## 质量优先决策（Cursor 总控）

- P1 已收口于 tip `ca4747c8653afe1de83f410b666537e511a26b0f`。
- 旧卡 **DAV-460** 基线停在 `de88de4`、两次 429 失败、**无可用交付** → **作废，不唤醒**。
- 本卡从**当前主干 tip** 重开 Task 5，范围收紧为只读 provider + as-of 护栏。
- **不做** classifier/aggregator/collector/graph/API；**不删** `legacy_proxy`；**不准予部署**；不得宣称社交接入完成。

## 权威

- `docs/social_data/implementation_plan.md` Task 5 + §5 时间资格 + D-008
- `work/2026-08-27-audit-decision-semantics-plan.md` §8 P2 第 1 条
- `DECISIONS.md` D-008 / D-009 / D-010

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `ca4747c8653afe1de83f410b666537e511a26b0f`
- **新建**隔离分支，例如 `agent/dev2/p2-t5-archive-provider`
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 文件白名单（只允许这些）

1. `tradingagents/dataflows/social/provider.py`（新建）
2. `tradingagents/dataflows/social/registry.py`（新建）
3. `tests/test_social_archive_provider.py`（新建）
4. `tests/test_social_as_of_guard.py`（新建）
5. `tradingagents/dataflows/social/__init__.py`（仅最小导出，可无则不动）
6. 必要时 `tests/social_fixtures.py`（只追加 provider 用的最小 helper，不改 importer 行为）

禁止改：`data_collector.py`、`social_media_analyst.py`、prompts、`evidence_verifier`、市场 `providers/`、`api/`、`frontend/`、importer/entity/contracts/archive_schema 的既有行为（除非发现必须修的编译级缺口——先停下来在评论说明，勿擅自扩 scope）。

## 行为契约

改原路径；禁止 `_v2` / `_new` / 平行函数。

### A. Protocol + registry

- Protocol **仅** `name` + `fetch_records(...)`。
- **禁止**继承 `BaseMarketDataProvider`；**禁止**注册进市场 `providers/registry.py`。
- 独立 `social` registry（本目录 `registry.py`）。

### B. 只读打开

- URI：`file:<path>?mode=ro`；连接后 `PRAGMA query_only=ON`；`PRAGMA busy_timeout=<TA_SOCIAL_FETCH_TIMEOUT_MS>`（缺省合理默认，可从 env 读）。
- **不得写** archive（无 INSERT/UPDATE/DELETE；测试断言 query_only / 无写路径）。
- 不启动爬虫；不碰 MediaCrawler 工作库。

### C. 失败语义（用既有 `SocialStatus` / reason codes）

| 条件 | status | reason（示例） |
|---|---|---|
| 库文件缺失 | `failed` 或契约规定的等价 | `social_archive_missing` |
| schema mismatch | `failed`/`refused` 按契约 | `social_schema_mismatch` |
| 锁/busy 超时 | `timeout` | `social_archive_locked` |
| as_of 非法 | `refused` | `social_invalid_as_of` |
| as_of 未来日 | `refused` | `social_future_as_of` |

禁止：解析失败回填 `now()`；未来日静默当成今天。

### D. 资格（D-008，硬钉）

时间比较必须先转 timezone-aware datetime；禁止字符串比日期。

历史日 cutoff = 上海时区该日 `23:59:59.999999`（序列化等价 UTC）。当前日 cutoff = 实际 now（不得预设到日末）。

同一 `record_id`：在 archive 中选 `snapshot_at <= cutoff` 的快照，取 **`snapshot_at` 最大** 的一条。

- **正文资格**：`window_start <= published_at <= cutoff` **且** `first_seen_at <= cutoff`
- **指标资格**：仅 `snapshot_at <= cutoff`（候选观测已蕴含；不得用 cutoff 后的 likes）
- **`ingest_at` 永不参与资格**。必测：`published_at`/`first_seen_at`/`snapshot_at` 均 ≤ cutoff，但 `ingest_at` > cutoff → **仍计入**
- 后补抓取：`published_at` 早但 `first_seen_at` > cutoff → **排除**
- `source_updated_at`：meta 未 trusted 时**不影响**资格（忽略即可）

本卡 `fetch_records` 返回合格快照记录列表（或带 status 的结果对象——与 contracts 对齐）；**不要**在本卡实现 SentimentBundle 聚合（Task 6）。

## 测试（TDD，先红后绿）

必须用离线临时 SQLite fixture（可复用/扩展 `tests/social_fixtures.py`），**禁止实网**。

至少覆盖：

1. 只读：对 archive 写操作失败或 provider 路径无写
2. 缺失库 → missing
3. schema mismatch → schema_mismatch
4. 非法 as_of / 未来 as_of → refused + 对应 reason
5. 正文：`first_seen_at` 晚于 cutoff → 排除（即使 `published_at` 早）
6. 指标：cutoff 后新 snapshot 的 likes 不得进入历史日；只用 ≤ cutoff 最大 snapshot
7. `ingest_at` > cutoff 仍计入
8. registry 能按 name 取到 archive provider

回归（不得破坏 Task 2–4）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

禁止 `@pytest.mark.asyncio`（仓内无 pytest-asyncio；若需异步用 `asyncio.run`）。

## 交付

1. 一个 commit：`feat(social): add read-only archive provider registry`
2. 推隔离分支；评论写：**完整 40 位 SHA**、父提交、`git diff --stat`（仅白名单）、pytest 精确数字
3. **不要自行 FF**；等 Cursor 独立复测后写「准予合入」
4. 不要 @项目调度助手催工；不要开部署卡；不要碰 DAV-460

## 风险点

- 资格函数若写在 provider 内，注意与 Task 6 aggregator 复用——本卡可把纯函数放在 `provider.py`（或极小 shared helper），**不要**提前建 `classifier.py`/`aggregator.py`
- 勿把社交 provider 挂进市场线程池或 `BaseMarketDataProvider`
