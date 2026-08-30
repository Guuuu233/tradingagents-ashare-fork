# TradingAgents Social Data Operations Runbook

> **Target:** MediaCrawler Bounded Ingestion, Verification, and Archive Maintenance Workflow  
> **Reference:** `docs/social_data/implementation_plan.md` Task 13, D-008, D-009, D-010  
> **Status:** Active Operational Runbook (Gate 0–3: Default `disabled` / Legacy Proxy; Gate 4+: Clean Native)

---

## 1. System Architecture & Boundaries

The social ingestion pipeline operates across strict process and storage boundaries:

```text
[ Xiaohongshu / Douyin ]
           │
           ▼
[ MediaCrawler Process ] (Python 3.11 + CDP / Chromium, independent virtualenv)
           │
           ▼
[ MediaCrawler Working SQLite DB ] (Update-in-place, mutable working store)
           │
           ▼  (scripts/import_mediacrawler_social.py / MediaCrawlerImporter)
[ TradingAgents Social Archive DB ] (Append-only SQLite, immutable snapshot history)
           │
           ▼  (file:<path>?mode=ro, PRAGMA query_only=ON)
[ TradingAgents Analysis Engine ] (DataCollector -> SocialDataProvider -> SentimentBundle)
```

### Core Invariants
1. **Update-in-Place vs Append-Only**:
   - **MediaCrawler Working DB**: Mutable operational workspace where rows are inserted or updated in-place (`update_content` / `store_content`). **Never** used as direct factual historical source by TradingAgents analysis graph.
   - **TradingAgents Social Archive DB**: Immutable, append-only SQLite database. Each row in `social_record_snapshots` is a distinct `(record_id, content_hash, metrics_hash)` revision. Existing snapshot rows are **never** updated or deleted.
2. **Loopback Network Isolation**:
   - MediaCrawler control services and local SQLite database paths are strictly constrained to `127.0.0.1` / `localhost`. External hosts are rejected.
3. **Fail-Closed Date & Eligibility Guards**:
   - Historical lookups only query the Social Archive DB with `snapshot_at <= cutoff`. `ingest_at` is purely for ingestion audit and never gates data eligibility.

---

## 2. Credentials & Cookie Management

To prevent account blocking and maintain reproducible testing without hardcoding secrets:

1. **Cookie Storage**:
   - Cookies must be stored outside the git repository (e.g. in `~/.mediacrawler/cookies/` or a local secure directory).
   - Pass cookie paths via `--cookie-path` or environment variable `MEDIACRAWLER_COOKIE_DIR`.
2. **Strict Credential Hygiene**:
   - **Never** commit cookie files, session tokens (`xsec_token`), phone numbers, or passwords into git.
   - **Never** log cookie contents or session tokens in scripts, stderr, stdout, test assertions, or Multica issue comments.
   - Ingestion logs and run summaries report counts and hashes only (`author_id_hash`, `content_hash`).

---

## 3. Running Controlled Ingestion (`scripts/run_social_ingestion.py`)

The runner script wraps MediaCrawler execution with strict safety guards and concurrency controls.

### Command Usage

```bash
env -u PYTHONPATH .venv/bin/python scripts/run_social_ingestion.py \
  --platform xhs \
  --query "寒武纪" \
  --source-db /path/to/mediacrawler_work.db \
  --archive-db /path/to/tradingagents_social_archive.db \
  --save-option sqlite \
  --crawler-host 127.0.0.1 \
  --crawler-commit d6f7c5bb906b6dac40ddf343ef9e26438a3de092 \
  --enable-comments \
  --no-enable-sub-comments \
  --auto-import
```

### Safety Flags and Defaults

| Flag | Default | Constraint / Requirement |
|---|---|---|
| `--save-option` | `sqlite` | **Mandatory `sqlite`**. Any non-sqlite option (`jsonl`, `csv`) causes immediate non-zero exit. |
| `--crawler-host` | `127.0.0.1` | **Mandatory loopback** (`127.0.0.1` or `localhost`). External IP/host causes non-zero exit. |
| `--crawler-commit`| `d6f7c5bb906b6dac40ddf343ef9e26438a3de092` | Pinned commit written to ingestion audit log. |
| `--enable-comments` | `True` | Primary comments enabled by default. |
| `--enable-sub-comments` | `False` | Secondary sub-comments disabled by default. |
| `--lock-file` | `/tmp/mediacrawler_ingestion.lock` | Mutex lock file. Concurrent execution of a second ingestion run is rejected. |
| `--auto-import` | `False` | If specified with `--archive-db`, automatically runs `MediaCrawlerImporter` after crawling. |

---

## 4. Running Archive Import (`scripts/import_mediacrawler_social.py`)

When importing from an existing MediaCrawler SQLite database directly into TradingAgents archive:

### Command Usage

```bash
env -u PYTHONPATH .venv/bin/python scripts/import_mediacrawler_social.py \
  --source-db /path/to/mediacrawler_work.db \
  --archive-db /path/to/tradingagents_social_archive.db \
  --platform xhs \
  --query "寒武纪" \
  --crawler-commit d6f7c5bb906b6dac40ddf343ef9e26438a3de092
```

### Required Arguments Checklist
All 5 arguments are strictly required; omitting any argument results in non-zero exit code:
1. `--source-db`: Valid path to existing SQLite database with required tables (`xhs_note`, `douyin_aweme`, etc.).
2. `--archive-db`: Path to TradingAgents append-only SQLite archive DB (initialized automatically if absent).
3. `--platform`: Target platform (`xhs`, `dy`, `xhs,dy`, or `all`).
4. `--query`: Target keyword/stock for ingest tracking.
5. `--crawler-commit`: Pinned MediaCrawler commit hash for audit.

---

## 5. Rollout Modes and Gate 4 Readiness

The TradingAgents social pipeline is governed by `TA_SOCIAL_MODE`:

| Mode | Gate | Behavior |
|---|---|---|
| `disabled` | Gate 0–3 | Default mode. Social analysis disabled or routed through `legacy_proxy` adapter layer. |
| `shadow` | Gate 2–3 | Computes `SentimentBundleV1` from social archive and logs trace data, but does not alter final analyst text or report conclusions. |
| `active` | Gate 3–4 | Fully enables social sentiment data in `social_media_analyst` prompt and downstream consensus. Fail-closed when data is insufficient or missing. |
| Gate 4 Clean-up | Gate 4 | `legacy_proxy` code is deleted; `disabled` mode strictly returns `not_applicable` with empty social context. |

---

## 6. Exit Codes & Troubleshooting

| Exit Code | Reason | Resolution |
|---|---|---|
| `0` | Success | Normal completion; ingest summary emitted. |
| `1` | Schema Mismatch | Source DB missing required columns (`add_ts`, `last_modify_ts`, `time`, `create_time`). Check MediaCrawler schema. |
| `1` | Concurrency Conflict | Lock file active. Wait for previous crawl to finish or clean up stale lock file if process crashed. |
| `1` | Invalid Storage Option | Non-sqlite `save_option` specified. Ensure MediaCrawler is configured with `save_option="sqlite"`. |
| `1` | Non-Loopback Host | Attempted connection to non-local host. Ensure host is `127.0.0.1`. |
| `2` | CLI Argument Missing | Check that all mandatory arguments are provided. |
