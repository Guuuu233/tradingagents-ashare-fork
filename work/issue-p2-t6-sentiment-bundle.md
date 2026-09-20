# P2-T6：分类、采样与确定性 SentimentBundle

## 目标

在 P2-T5 只读 provider 之上，实现 **classifier + aggregator**，产出确定性 `SentimentBundleV1`。本卡**不**接 DataCollector / Graph / API，**不**改 mode 开关默认值，**不**删 `legacy_proxy`，**不**部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `50ec109fe9992db11c00a98a3a07f3ad760bf3df`
- **新建**隔离分支，例如 `agent/dev2/p2-t6-sentiment-bundle`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 6 + §5.1 / §5.4
- D-008（资格已在 T5；本卡消费已合格记录，不得重新用 `ingest_at` 做资格）
- D-009 / D-010

## 文件白名单

1. `tradingagents/dataflows/social/classifier.py`（新建）
2. `tradingagents/dataflows/social/aggregator.py`（新建）
3. `tests/test_social_aggregator.py`（新建；可含 classifier 用例或同文件分区）
4. 必要时最小词典/常量文件：**仅**若放在 `tradingagents/dataflows/social/` 内且本卡必需（例如 `lexicon_v1.py`）；禁止新建顶层目录
5. `tradingagents/dataflows/social/__init__.py`（仅最小导出）
6. **允许**极小改动 `provider.py` **仅当** T5 残留必须修才能让 bundle 正确——优先不动；若动，单独说明且不得扩 scope

禁止：`data_collector.py`、analyst/prompts、`api/`、市场 `providers/`、Task 7+ 文件、改 `TA_SOCIAL_MODE` 默认。

## 行为契约

### A. Classifier（词典 v1）

- 词典名：`cn_equity_stance_lexicon.v1`（确定性；**不**调 LLM）
- 空正文：方向权重 0；可计入 attention 计数，**不计**方向样本
- 否定词在情绪词前 3 个汉字内翻转极性
- 无法识别 → `unknown`，**不当** `neutral`
- 输出每条：`stance` ∈ {bullish, bearish, neutral, mixed, unknown} + 可审计命中信息（至少词面或 rule id）

### B. Aggregator / Bundle（§5.4）

输入：同一 `as_of` 下已合格的 `SocialRawRecordV1` 列表（可直接调 `SocialArchiveProvider.fetch_records`，或测试注入 records）。

规则：

1. 文本 hash 去重；重复组最多一份权重
2. 每位作者最多 5 条（无 `author_id_hash` 的单独桶，仍须稳定）
3. 先近期帖，再评；默认上限 100 帖 / 300 评（可用参数覆盖）
4. 帖基础权重 1.5，评 1.0；空正文方向权重 0
5. 时间衰减半衰期 **3.5 天**，按 **`published_at`**（不是 `snapshot_at`）
6. 仅指标合格（记录已带合格 snapshot metrics）的 likes 可抬互动权重，系数 **≤ 1.5**
7. 两平台都有数据时单平台最终权重 ≤ 65%；仅一平台 → status 至少 `partial` 且 `direction_allowed=false`
8. 排序稳定 tie-breaker：`record_id`
9. 最低可判定：帖子 ≥3、已分类 ≥20、不同作者 ≥10；未达 → `score=null`、`label=insufficient`、`direction_allowed=false`，reason `social_insufficient_coverage`（禁止写成 `0.0/neutral`）
10. `bundle_id` **确定性**（同输入同输出；算法与字段顺序写进测试）
11. `content_as_of` / `metric_as_of` 按契约；**禁止**把 `ingest_at` 写入二者
12. `is_calibrated_probability=false`
13. Provider 层 `failed/refused/timeout/empty` 应映射为对应 bundle status（复用 `create_empty_sentiment_bundle`），不得伪装 available

### C. 质量债（若顺手且不扩文件）

T5 残留可不修。若本卡必须读 provider：不要新增硬编码 crawler_commit 回退。

## 测试（TDD）

离线 fixture；禁止实网；禁止 `@pytest.mark.asyncio`。

至少覆盖：

1. 空正文不计方向，仍可进 attention count
2. 半衰期按 `published_at`
3. likes 在 cutoff 后 snapshot 不得影响（若测端到端：依赖 T5 已选对 snapshot）
4. 未达最低覆盖 → insufficient + `direction_allowed=false` + score null（非 0.0）
5. 单平台 → partial + `direction_allowed=false`
6. 同输入两次 `bundle_id` 与 score 完全一致
7. 否定翻转极性
8. unknown ≠ neutral
9. 回归 T5 套件仍绿

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

## 交付

1. 一个 commit：`feat(social): build deterministic sentiment bundle`
2. 推隔离分支；评论：**完整 40 位 SHA**、父提交、`git diff --stat`、pytest 精确数字
3. **不要自行 FF**；等 Cursor「准予合入」
4. 不要 @调度助手催工；不要部署；不要开 Task 7

## 风险

- 词典边界导致 flaky 分类：用显式词表 + 固定用例，禁止依赖外部文件随机性
- 函数保持可拆；单函数避免再堆到 300+ 行（T5 教训）
