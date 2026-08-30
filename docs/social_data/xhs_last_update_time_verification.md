# Xiaohongshu `last_update_time` Reliability Verification Specification

> **Target:** Gate 1 Quality Gate & Time Semantics Verification  
> **Reference:** `docs/social_data/implementation_plan.md` §3.3, §3.4, §4.1, Task 13, D-008  
> **Current Status:** `untrusted` (`xhs_last_update_time_trusted = false`)

---

## 1. Problem Background & Risk Analysis

Under the pinned MediaCrawler commit (`d6f7c5bb906b6dac40ddf343ef9e26438a3de092`):
- `update_xhs_note` writes the platform's `last_update_time` field to the working SQLite database.
- However, MediaCrawler's `update_content` routine only modifies `last_modify_ts` and interaction counts (`liked_count`, `comment_count`, `share_count`, `collected_count`), and **does NOT update** `title` or `desc` text for existing rows.
- Consequently, `last_update_time` cannot be assumed to represent the modification time of the archived post body.

### Primary Risks
1. **False Eligibility Lookahead**: If `last_update_time` advances due to interaction metrics or platform internal re-indexing rather than text editing, treating it as `content_as_of` creates forward-looking bias.
2. **False Gap Exclusion**: If an author legitimately edits note text, but MediaCrawler retains the stale initial `desc` while recording a newer `last_update_time`, filtering by `last_update_time <= cutoff` would misclassify the stale content.

---

## 2. Experimental Verification Methodology

A controlled verification suite must execute the following four validation steps before `last_update_time` can participate in eligibility filtering:

### Step 1: Metric-Only Invariance Check
- **Objective**: Determine whether `last_update_time` mutates when only likes/comments change.
- **Method**: Select a controlled set of 50 active notes over a 48-hour period with increasing likes. Re-crawl hourly.
- **Assertion**: Record whether `last_update_time` changes when note body hash is unchanged. If `last_update_time` advances on metric changes, it is classified as a server metadata timestamp, not a content edit timestamp.

### Step 2: Content Edit Synchronization Check
- **Objective**: Determine whether MediaCrawler captures updated `desc` when a note body is edited.
- **Method**: Publish test notes, perform text revisions on Xiaohongshu, and trigger MediaCrawler refresh.
- **Assertion**: Verify whether MediaCrawler's SQLite row updates `desc` or whether it keeps the initial insert text.

### Step 3: Anomaly Frequency Analysis
- **Objective**: Quantify distribution of anomalous timestamps.
- **Metrics Tracked**:
  - Percentage of records with `last_update_time == 0` or missing.
  - Frequency of `last_update_time < published_at` (clock skew or epoch corruption).
  - Frequency of `last_update_time > last_modify_ts` (future platform timestamp).

### Step 4: Final Classification Verdict
The verification result must conclude with one of two explicit verdicts:
- **`untrusted`**: `last_update_time` does not reliably track archived content edits. The field is retained in archive for audit, but **ignored by all eligibility guards**.
- **`trusted_as_content_update`**: `last_update_time` strictly corresponds to content text revisions and MediaCrawler captures revised text. Only in this case may `source_updated_at <= cutoff` be enabled.

---

## 3. Current Fail-Closed Implementation

In accordance with D-008 and Gate 1 specifications:

1. `social_archive_meta` table is initialized with:
   ```sql
   INSERT INTO social_archive_meta (key, value) VALUES ('xhs_last_update_time_trusted', 'false');
   ```
2. `ArchiveSocialDataProvider` and `SocialAsOfGuard` check this configuration and **strictly ignore `source_updated_at`** during historical eligibility filtering.
3. Importer maps `last_update_time` into `source_updated_at` (converting zero/negative values to `null`), ensuring raw audit fidelity without violating historical fences.
