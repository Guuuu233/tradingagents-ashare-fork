# TradingAgents Social Data Contract v1

> **Schema Version:** `social.raw_record.v1` / `social.sentiment_bundle.v1`  
> **Reference:** `docs/social_data/implementation_plan.md` §3, §4, §5, D-008, D-009, D-010  
> **Status:** Active Data Contract

---

## 1. Time Field Semantics (D-008 Alignment)

To prevent look-ahead bias and maintain reproducible historical backtesting, time fields are partitioned into five strictly defined layers:

| Layer | Field | Source | Semantic Meaning | Usage in Eligibility | Forbidden Usages |
|---|---|---|---|---|---|
| **Platform Source Time** | `published_at` | XHS `time`, DY `create_time` | When content was originally created on the platform | Gates content eligibility: `window_start <= published_at <= cutoff` | **Forbidden:** Using crawler `add_ts` or `last_modify_ts` as publication time. |
| **Platform Source Update** | `source_updated_at` | XHS `last_update_time` (null for DY / comments) | When note metadata was updated on the platform | Stored for audit; **does NOT participate in eligibility** until verification passed. | **Forbidden:** Treating as content modification time without verification. |
| **Crawler Ingestion** | `first_seen_at` | MediaCrawler `add_ts` | When MediaCrawler first inserted the row into its local SQLite DB | Gates anti-lookahead: `first_seen_at <= cutoff` | **Forbidden:** Treating as publication time. |
| **Crawler Snapshot** | `snapshot_at` | MediaCrawler `last_modify_ts` | When MediaCrawler last wrote this version of the record | Gates interaction metrics eligibility: `snapshot_at <= cutoff` | **Forbidden:** Treating as platform interaction occurrence time. |
| **Archive Ingest Clock** | `ingest_at` | TradingAgents archive write clock (UTC) | When the snapshot was committed into the append-only archive | **Audit only.** NEVER participates in eligibility or cutoff comparisons. | **Forbidden:** Using `ingest_at` in any filter or backfilling missing source times. |

### Anti-Lookahead Eligibility Invariants
1. **Candidate Snapshot Selection**:
   For a given `record_id`, select the snapshot row with `snapshot_at <= cutoff` having the largest `snapshot_at`. If no such snapshot exists, the record has no historical observation at `cutoff`.
2. **Content Qualification**:
   A post/comment qualifies for sentiment analysis iff:
   - `window_start <= published_at <= analysis_cutoff`
   - `first_seen_at <= analysis_cutoff`
   - Entity resolver matches the target symbol.
3. **Metric Qualification**:
   Interaction metrics (likes, shares, comments) qualify for weighted scoring iff:
   - `snapshot_at <= analysis_cutoff`
   - If content is qualified but `snapshot_at > analysis_cutoff`, content is retained with baseline weight (1.0) and metrics are treated as unobserved.

---

## 2. Raw Record Schema (`SocialRawRecordV1`)

```json
{
  "schema_version": "social.raw_record.v1",
  "record_id": "xhs:post:65abc123456",
  "snapshot_id": "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
  "record_type": "post",
  "platform": "xhs",
  "native_id": "65abc123456",
  "parent_record_id": null,
  "root_post_record_id": "xhs:post:65abc123456",
  "published_at": "2026-08-26T03:12:11Z",
  "source_updated_at": null,
  "first_seen_at": "2026-08-26T04:00:02Z",
  "snapshot_at": "2026-08-26T06:10:00Z",
  "ingest_at": "2026-08-26T06:15:00Z",
  "title": "寒武纪深度调研纪要",
  "text": "今天调研了寒武纪最新芯片出货节奏...",
  "canonical_url": "https://www.xiaohongshu.com/explore/65abc123456",
  "author_id_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "source_keyword": "寒武纪",
  "entities": ["688256"],
  "metrics": {
    "likes": 120,
    "comments": 45,
    "shares": null,
    "collects": 30,
    "views": null
  },
  "content_hash": "sha256:1b4f0e9851971998e732078544c96b36c3d01cedf7caa332359d6f1d83567014",
  "metrics_hash": "sha256:60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752",
  "ingest_run_id": "550e8400-e29b-41d4-a716-446655440000",
  "source_ref": {
    "provider": "mediacrawler",
    "crawler_commit": "d6f7c5bb906b6dac40ddf343ef9e26438a3de092",
    "source_table": "xhs_note",
    "source_row_id": "1001"
  }
}
```

### Field Constraints
- **Unknown Counts**: Missing, negative, or unparsed interaction metrics must be `null` (`None`), never defaulted to `0`. Literal `0` is recorded as integer `0`.
- **Anonymization**: `author_id_hash` stores a one-way SHA-256 hash. Nicknames, avatars, IP locations, and tracking tokens (`xsec_token`) are strictly discarded.
- **Canonical URLs**: Tracking parameters, UTM queries, and session query strings are stripped.
- **Empty Text**: Posts with empty text are valid for volume/attention statistics, but receive zero directional sentiment weight.

---

## 3. Archive SQLite Schema v1 (Append-Only)

```sql
CREATE TABLE IF NOT EXISTS social_archive_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_ingest_runs (
    run_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    platform TEXT NOT NULL,
    query_text TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    crawler_commit TEXT NOT NULL,
    source_schema_fingerprint TEXT NOT NULL,
    source_max_first_seen_at TEXT,
    rows_read INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS social_record_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    record_type TEXT NOT NULL CHECK(record_type IN ('post', 'comment')),
    platform TEXT NOT NULL CHECK(platform IN ('xhs', 'dy')),
    native_id TEXT NOT NULL,
    parent_record_id TEXT,
    root_post_record_id TEXT NOT NULL,
    published_at TEXT NOT NULL,
    source_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    ingest_at TEXT NOT NULL,
    title TEXT,
    text TEXT NOT NULL DEFAULT '',
    canonical_url TEXT,
    author_id_hash TEXT,
    source_keyword TEXT,
    metrics_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metrics_hash TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES social_ingest_runs(run_id),
    source_table TEXT NOT NULL,
    source_row_id TEXT NOT NULL,
    UNIQUE(record_id, content_hash, metrics_hash)
);

CREATE TABLE IF NOT EXISTS social_entity_mentions (
    snapshot_id TEXT NOT NULL REFERENCES social_record_snapshots(snapshot_id),
    symbol TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    match_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    resolver_version TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, symbol, resolver_version)
);

CREATE INDEX IF NOT EXISTS idx_social_snapshot_cutoff
    ON social_record_snapshots(platform, published_at, first_seen_at, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_social_record_history
    ON social_record_snapshots(record_id, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_social_entity_symbol
    ON social_entity_mentions(symbol, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_social_run_coverage
    ON social_ingest_runs(platform, query_text, completed_at, status);
```

---

## 4. Aggregated Sentiment Bundle Schema (`SentimentBundleV1`)

```json
{
  "schema_version": "social.sentiment_bundle.v1",
  "symbol": "688256",
  "symbol_name": "寒武纪",
  "as_of_date": "2026-08-26",
  "status": "available",
  "direction_allowed": true,
  "reason_codes": [],
  "cutoff_at": "2026-08-26T15:59:59Z",
  "content_as_of": "2026-08-26T03:12:11Z",
  "metric_as_of": "2026-08-26T06:10:00Z",
  "social_sentiment": {
    "score": 0.35,
    "label": "bullish",
    "positive_ratio": 0.65,
    "negative_ratio": 0.15,
    "neutral_ratio": 0.20,
    "classified_count": 85
  },
  "social_attention": {
    "total_posts": 42,
    "total_comments": 130,
    "unique_authors": 95,
    "total_likes": 1450,
    "total_shares": null
  },
  "coverage": {
    "platforms": ["xhs", "dy"],
    "lookback_days": 7,
    "sample_count": 172
  }
}
```

### Status & Direction Allowed Matrix
- `available`: Data meets minimum thresholds for posts, authors, and classification. `direction_allowed=true`.
- `partial` / `insufficient`: Partial platform or low coverage. `score=null`, label=`insufficient`, `direction_allowed=false`.
- `empty`: No matching posts within window. `score=null`, `direction_allowed=false`.
- `refused`: Future date or unproven time credentials. `direction_allowed=false`.
- `failed` / `timeout`: Archive read failure or lock timeout. `direction_allowed=false`.
