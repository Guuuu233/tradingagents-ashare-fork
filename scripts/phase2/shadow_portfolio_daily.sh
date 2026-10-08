#!/bin/bash
# DAV-1707 — M2-B shadow-portfolio daily wrapper (launchd template companion).
#
# TEMPLATE ONLY — not installed. The sibling launchd plist
# (scripts/phase2/com.davidliu.ta-shadow-portfolio.plist) fires this wrapper
# Mon-Fri 19:50, immediately AFTER the 19:45 P4 ledger seal
# (com.davidliu.ta-p4-ledger -> daily_snapshot_ledger.py run). Do NOT
# `launchctl load` / bootstrap it — install belongs to a later ops card
# after 总控 sign-off (D-033). This script itself never installs anything.
#
# What the daily shadow-portfolio run is *for*:
#   After the forward ledger seals the day's completed reports
#   (work/phase2-ledger/forward_ledger.jsonl), the M2-B 影子组合 recomputes
#   the three strategy ledgers (S1 direction / S2 risk_adjusted /
#   S3 high_confidence, tradingagents/eval/shadow_portfolio.py) over the
#   sealed set so the daily NAV / fill / blocked series stay a pure,
#   deterministic function of the sealed signal ledger + market bars.
#
# Behaviour contract (this wrapper):
#   * Disk-free guard — if the filesystem holding REPO_ROOT has < 100 GiB
#     free, SKIP this run and log one line explaining why (never start the
#     heavy computation on a nearly-full disk).
#   * Seal gate — run only after the 19:45 ledger seal has completed for
#     TODAY. The seal stamps <LEDGER_DIR>/ledger_state.json (last_run_at)
#     and snapshot_ledger.log. If the seal has not landed yet, log a
#     DEFERRED line and exit 2. The default plist template triggers once at
#     19:50; the ops card may add retry ticks (the production seal instance
#     runs 19:45 + 08:30 dual ticks, so a late seal is realistic) —
#     overlapping ticks are serialized by the per-day mkdir lock, so a
#     deferred day is retried by the next tick instead of skipped. (No silent
#     "run anyway" — the shadow must consume only sealed signals, never a
#     half-written day.)
#   * Idempotent / at-most-once — a same-calendar-day re-trigger must not
#     duplicate output. The run publishes to
#       work/shadow-portfolio/<UTC date>/...
#     and maintains a marker work/shadow-portfolio/<UTC date>/.done. If the
#     marker already exists for today the wrapper logs "already produced" and
#     exits 0 without recomputing. Overlapping ticks (back-to-back retry
#     triggers) are serialized by an atomic per-day mkdir lock
#     (work/shadow-portfolio/.lock.<UTC date>): a tick that finds the lock
#     held exits 0 without entering the runner, so at most one runner enters
#     per day even when ticks overlap. Results are persisted to disk (长跑结果
#     落盘) so a later re-read never re-runs the heavy step.
#   * Single process, peak RSS <= 4 GiB. On systems supporting RLIMIT_AS
#     (e.g. Linux), the wrapper sets an address-space cap (ulimit -v). On
#     Darwin (macOS) where RLIMIT_AS is unsupported by the kernel, the
#     wrapper logs a note and fails open; the runner contract mandates
#     self-monitoring via resource.getrusage(RUSAGE_SELF).ru_maxrss <= 4 GiB.
#   * Read-mostly: reads the ledger + market bars; writes ONLY under
#     work/shadow-portfolio/ (local scratch area; recommended to add to
#     .gitignore upon ops deployment). Never writes the production DB, never
#     calls a model/provider, never touches the network.
#
# Exit codes:
#   0  success (or already-done same day — idempotent no-op)
#   2  deferred — seal not landed yet / benign skip (disk guard logs then exits 0)
#   1  real failure (python step raised / nonzero)
#
# Config (env overrides for testing; the launchd plist pins the production
# values, notably LEDGER_DIR):
#   REPO_ROOT    default: repo root derived from this script's location
#   PYTHON_BIN   default: $REPO_ROOT/.venv310/bin/python  (locked interpreter)
#   LEDGER_DIR   default: $REPO_ROOT/work/phase2-ledger
#                PRODUCTION: /Users/davidliu/ta-p4-2e1e199f/work/phase2-ledger
#                (must match the seal job com.davidliu.ta-p4-ledger's
#                --ledger-dir; the plist template injects it explicitly —
#                a mismatch yields a permanent nightly DEFERRED).
#   OUT_DIR      default: $REPO_ROOT/work/shadow-portfolio
#   RUNNER       default: scripts/phase2/shadow_portfolio_run.py
#                (must be a Python script: it is executed as
#                "$PYTHON_BIN" "$RUNNER" ...).
#   MIN_FREE_GIB default: 100
#   RSS_CAP_KIB  default: 4194304   (4 GiB address-space cap)
#
# NOTE on the runner: scripts/phase2/shadow_portfolio_run.py is the thin
# deterministic driver that loads forward_ledger.jsonl, injects the trading
# calendar + get_bar market source, runs the three strategies, and writes the
# NAV / fills / summary JSON into OUT_DIR/<date>/. It is the payload this
# wrapper schedules; the scheduling, disk guard, seal gate, idempotence and
# RSS cap all live HERE (not in the runner) so the contract is enforced at
# the OS boundary regardless of how the runner is invoked.

set -euo pipefail

# --- locate repo + interpreter ---------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv310/bin/python}"
LEDGER_DIR="${LEDGER_DIR:-$REPO_ROOT/work/phase2-ledger}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/work/shadow-portfolio}"
RUNNER="${RUNNER:-$REPO_ROOT/scripts/phase2/shadow_portfolio_run.py}"
MIN_FREE_GIB="${MIN_FREE_GIB:-100}"
RSS_CAP_KIB="${RSS_CAP_KIB:-4194304}"

STATE_FILE="$LEDGER_DIR/ledger_state.json"
SEAL_LOG="$LEDGER_DIR/snapshot_ledger.log"
TODAY_UTC="$(date -u +%Y-%m-%d)"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_DIR="$OUT_DIR/$TODAY_UTC"
DONE_MARKER="$RUN_DIR/.done"
LOG_FILE="$OUT_DIR/shadow_portfolio_daily.log"

log() {
    # one UTC line to stdout (launchd StandardOutPath) + append to run log
    local line="$STAMP $1"
    printf '%s\n' "$line"
    mkdir -p "$OUT_DIR" 2>/dev/null || true
    printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
}

# Fail fast on a misconfigured interpreter so a missing python is reported
# as FAILED (exit 1) instead of being misdiagnosed as "seal not landed"
# (exit 2).
if [ ! -x "$PYTHON_BIN" ]; then
    log "FAILED: python interpreter not found or not executable: $PYTHON_BIN (check PYTHON_BIN)"
    exit 1
fi

# --- 1) disk-free guard (< MIN_FREE_GIB -> skip) -----------------------------
# df -Pk on the volume holding REPO_ROOT; field 4 = 1K-blocks available.
# Use safe capture so df failure does not trigger errexit without logging.
if ! avail_kib="$(df -Pk "$REPO_ROOT" 2>/dev/null | awk 'NR==2{print $4}')"; then
    avail_kib=""
fi
if [ -z "${avail_kib:-}" ]; then
    log "DEFERRED: cannot stat free space on $REPO_ROOT (df failed); skipping this run"
    exit 2
fi
avail_gib=$(( avail_kib / 1024 / 1024 ))
if [ "$avail_gib" -lt "$MIN_FREE_GIB" ]; then
    log "SKIP: free disk ${avail_gib} GiB < ${MIN_FREE_GIB} GiB on $REPO_ROOT; shadow-portfolio run skipped for $TODAY_UTC"
    exit 0
fi

# --- 2) seal gate (19:45 P4 seal must have landed for today) ----------------
seal_ok=0
if [ -f "$STATE_FILE" ]; then
    # ledger_state.json records last_run_at (UTC ISO string); the forward seal
    # for "today" has landed and saved state when last_run_at's date == TODAY_UTC.
    if "$PYTHON_BIN" - "$STATE_FILE" "$TODAY_UTC" <<'PY' 2>/dev/null
import json, sys
try:
    st = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
today = sys.argv[2]
last = str(st.get("last_run_at") or "")
sys.exit(0 if last[:10] == today else 1)
PY
    then
        seal_ok=1
    fi
fi
# Fallback: when no new completed reports were present, daily_snapshot_ledger.py
# returns 0 without updating ledger_state.json, but writes an "OK: ..." line to
# snapshot_ledger.log. Match ONLY successful "OK:" lines for TODAY_UTC, filtering
# out any "DEFERRED: ..." or "FAILED: ..." lines so a postponed or aborted seal
# is never treated as sealed.
if [ "$seal_ok" -eq 0 ] && [ -f "$SEAL_LOG" ] && \
   grep -qE "^${TODAY_UTC}T[0-9:+-Zz]{6,} OK:" "$SEAL_LOG" 2>/dev/null; then
    seal_ok=1
fi
if [ "$seal_ok" -eq 0 ]; then
    log "DEFERRED: P4 ledger seal has not completed for $TODAY_UTC yet (ledger=$LEDGER_DIR; no ledger_state/snapshot_ledger stamp); will retry on next launchd tick"
    exit 2
fi

# --- 3) idempotence: already produced today -> no-op ------------------------
if [ -f "$DONE_MARKER" ]; then
    log "OK: shadow-portfolio output for $TODAY_UTC already exists ($RUN_DIR); same-day re-trigger skipped (idempotent)"
    exit 0
fi

# --- 4) single-process RSS back-stop ----------------------------------------
# ulimit -v sets address-space cap (RLIMIT_AS) in KiB.
# NOTE: Darwin (macOS) kernel does not support RLIMIT_AS (ulimit -v returns
# 'invalid argument'), so this OS-level limit is best-effort / advisory.
# On systems where RLIMIT_AS is unsupported, execution continues and the
# Python runner must self-monitor peak RSS (via resource.getrusage(RUSAGE_SELF).ru_maxrss
# <= 4 GiB) to satisfy the single-process footprint contract.
ulimit -v "$RSS_CAP_KIB" 2>/dev/null || \
    log "NOTE: address-space cap ${RSS_CAP_KIB} KiB not supported by OS (macOS/Darwin); runner must self-monitor RSS <= 4 GiB"

# --- 5) per-day mutual exclusion (at-most-once for overlapping ticks) ------
# .done is written only AFTER a successful run, so it cannot serialize two
# overlapping ticks. Claim the day with an atomic mkdir BEFORE touching
# $RUN_DIR; a second overlapping tick finds the lock held and exits 0
# without entering the runner. The lock is released (trap) on any failure
# path so a later tick can retry; on success it is kept alongside .done.
mkdir -p "$OUT_DIR" 2>/dev/null || true
if [ ! -d "$OUT_DIR" ]; then
    log "FAILED: cannot create output dir $OUT_DIR; not marking done"
    exit 1
fi
LOCK_DIR="$OUT_DIR/.lock.$TODAY_UTC"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "OK: shadow-portfolio run for $TODAY_UTC already in progress or finished (lock $LOCK_DIR held); overlapping tick skipped (at-most-once)"
    exit 0
fi
release_lock() {
    # Only the lock owner installs this trap (installed after mkdir won),
    # and only a day WITHOUT .done releases it — a mid-run host crash
    # leaves the lock behind, failing safe toward "no duplicate run".
    if [ ! -f "$DONE_MARKER" ]; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
}
trap release_lock EXIT

# --- 6) stage, run, verify fresh, publish atomically ------------------------
# Never judge "freshness" by counting files in a possibly-stale $RUN_DIR.
# The runner builds into a fresh per-attempt staging dir; only verified
# THIS-run artifacts are atomically renamed into place. Residue from a
# previous failed attempt can therefore never be mistaken for success.
STAGE_DIR="$OUT_DIR/.stage.$TODAY_UTC.$$"
rm -rf "$STAGE_DIR" 2>/dev/null || true
if ! mkdir -p "$STAGE_DIR" 2>/dev/null; then
    log "FAILED: cannot create staging dir $STAGE_DIR; not marking done"
    exit 1
fi
# Freshness reference with an ancient mtime: every artifact genuinely written
# by this run is newer; the check below is a secondary invariant (freshness
# is guaranteed primarily by the empty staging dir).
START_REF="$STAGE_DIR/.start-ref"
touch -t 200001010000 "$START_REF" 2>/dev/null || touch "$START_REF"

log "START: shadow-portfolio daily run for $TODAY_UTC (ledger=$LEDGER_DIR out=$RUN_DIR rss_cap=${RSS_CAP_KIB}KiB)"

if [ ! -f "$RUNNER" ]; then
    log "FAILED: runner not found: $RUNNER (payload script missing; nothing scheduled)"
    rm -rf "$STAGE_DIR" 2>/dev/null || true
    exit 1
fi

if env -u PYTHONPATH "$PYTHON_BIN" "$RUNNER" \
        --ledger-dir "$LEDGER_DIR" \
        --out-dir "$STAGE_DIR" \
        --as-of "$TODAY_UTC" \
        >>"$LOG_FILE" 2>&1; then
    # Count with safe capture (same pattern as the df guard): a find failure
    # must degrade to FAILED-with-log, never to a silent errexit abort.
    if ! artifact_count="$(find "$STAGE_DIR" -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')"; then
        artifact_count="0"
    fi
    if ! fresh_count="$(find "$STAGE_DIR" -mindepth 1 -maxdepth 1 ! -name '.*' -newer "$START_REF" 2>/dev/null | wc -l | tr -d ' ')"; then
        fresh_count="0"
    fi
    if [ "${artifact_count:-0}" -eq 0 ]; then
        log "FAILED: runner exited 0 but produced no artifacts in staging $STAGE_DIR (any stale $RUN_DIR residue ignored); not marking done"
        rm -rf "$STAGE_DIR" 2>/dev/null || true
        exit 1
    fi
    if [ "${fresh_count:-0}" -ne "${artifact_count:-0}" ]; then
        log "FAILED: runner artifacts failed freshness check in staging $STAGE_DIR (fresh=${fresh_count:-0} total=${artifact_count:-0}); not marking done"
        rm -rf "$STAGE_DIR" 2>/dev/null || true
        exit 1
    fi
    # Completion artifact per the runner contract: summary.json must exist
    # and parse as JSON — a silent runner can no longer freeze the day.
    if [ ! -s "$STAGE_DIR/summary.json" ]; then
        log "FAILED: runner exited 0 but completion artifact summary.json missing/empty in staging $STAGE_DIR; not marking done"
        rm -rf "$STAGE_DIR" 2>/dev/null || true
        exit 1
    fi
    if ! "$PYTHON_BIN" -c 'import json,sys; json.load(open(sys.argv[1]))' "$STAGE_DIR/summary.json" 2>/dev/null; then
        log "FAILED: runner summary.json is not valid JSON in staging $STAGE_DIR; not marking done"
        rm -rf "$STAGE_DIR" 2>/dev/null || true
        exit 1
    fi
    # Drop the internal freshness reference before publishing so the
    # published date dir contains only runner artifacts + .done.
    rm -f "$STAGE_DIR/.start-ref" 2>/dev/null || true
    # Isolate any stale $RUN_DIR residue from a previous failed attempt,
    # then publish the verified staging dir atomically (same-filesystem
    # rename).
    if [ -e "$RUN_DIR" ]; then
        stale_backup="$OUT_DIR/.stale.${TODAY_UTC}.$(date +%H%M%S).$$"
        if mv -f "$RUN_DIR" "$stale_backup" 2>/dev/null; then
            log "NOTE: isolated stale output $RUN_DIR to $stale_backup before publishing fresh run"
        else
            log "FAILED: could not isolate stale $RUN_DIR; not marking done"
            rm -rf "$STAGE_DIR" 2>/dev/null || true
            exit 1
        fi
    fi
    if ! mv -f "$STAGE_DIR" "$RUN_DIR" 2>/dev/null; then
        log "FAILED: could not publish staging $STAGE_DIR to $RUN_DIR; not marking done"
        rm -rf "$STAGE_DIR" 2>/dev/null || true
        exit 1
    fi
    # Atomic write of .done marker via temporary file; mark done only on a
    # clean, verified run so a crash never poisons the day.
    tmp_done="${DONE_MARKER}.tmp.$$"
    if ! printf '%s\n' "$STAMP" >"$tmp_done" 2>/dev/null; then
        log "FAILED: could not write done marker for $TODAY_UTC; not marking done"
        exit 1
    fi
    mv -f "$tmp_done" "$DONE_MARKER"
    log "DONE: shadow-portfolio run for $TODAY_UTC wrote $RUN_DIR (${artifact_count} artifact(s), all fresh)"
    exit 0
else
    rc=$?
    log "FAILED: runner exited rc=$rc for $TODAY_UTC (see $LOG_FILE); not marking done"
    rm -rf "$STAGE_DIR" 2>/dev/null || true
    exit 1
fi
