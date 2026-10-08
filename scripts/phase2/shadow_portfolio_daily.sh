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
#     TODAY. The seal stamps work/phase2-ledger/ledger_state.json
#     (last_run_at, snapshot_date=UTC today) and snapshot_ledger.log. If the
#     seal has not landed yet, log a DEFERRED line and exit 2 so launchd does
#     not retry-storm; the next tick picks it up. (No silent "run anyway" —
#     the shadow must consume only sealed signals, never a half-written day.)
#   * Idempotent — a same-calendar-day re-trigger must not duplicate output.
#     The run writes to
#       work/shadow-portfolio/<UTC date>/...
#     and maintains a marker work/shadow-portfolio/<UTC date>/.done. If the
#     marker already exists for today the wrapper logs "already produced" and
#     exits 0 without recomputing. Results are persisted to disk (长跑结果
#     落盘) so a later re-read never re-runs the heavy step.
#   * Single process, peak RSS <= 4 GiB. The wrapper sets an address-space
#     soft cap (ulimit -v) as a back-stop so a runaway computation is killed
#     by the OS rather than by luck; the python step additionally reports its
#     measured peak_rss_gb on the summary line. 4 GiB = 4194304 KiB.
#   * Read-mostly: reads the ledger + market bars; writes ONLY under
#     work/shadow-portfolio/ (gitignored scratch area). Never writes the
#     production DB, never calls a model/provider, never touches the network.
#
# Exit codes:
#   0  success (or already-done same day — idempotent no-op)
#   2  deferred — seal not landed yet / benign skip (disk guard logs then exits 0)
#   1  real failure (python step raised / nonzero)
#
# Config (env overrides for testing; launchd plist injects none by default):
#   REPO_ROOT    default: repo root derived from this script's location
#   PYTHON_BIN   default: $REPO_ROOT/.venv310/bin/python  (locked interpreter)
#   LEDGER_DIR   default: $REPO_ROOT/work/phase2-ledger
#   OUT_DIR      default: $REPO_ROOT/work/shadow-portfolio
#   RUNNER       default: scripts/phase2/shadow_portfolio_run.py
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

# --- 1) disk-free guard (< MIN_FREE_GIB -> skip) -----------------------------
# df -k on the volume holding REPO_ROOT; field 4 = 1K-blocks available.
avail_kib="$(df -k "$REPO_ROOT" 2>/dev/null | awk 'NR==2{print $4}')"
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
    # ledger_state.json records last_run_at (UTC iso) + snapshot_date; the
    # forward seal for "today" is present when snapshot_date == UTC today OR
    # last_run_at's date == today (seal always runs shortly after 19:45 local
    # = same UTC evening during the trading session window).
    if "$PYTHON_BIN" - "$STATE_FILE" "$TODAY_UTC" <<'PY' 2>/dev/null
import json, sys
try:
    st = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
today = sys.argv[2]
snap = str(st.get("snapshot_date") or "")
last = str(st.get("last_run_at") or "")
sys.exit(0 if (snap == today or last[:10] == today) else 1)
PY
    then
        seal_ok=1
    fi
fi
# Fallback: a same-day OK/append line in the seal log also proves the seal ran.
if [ "$seal_ok" -eq 0 ] && [ -f "$SEAL_LOG" ] && grep -q "$TODAY_UTC" "$SEAL_LOG" 2>/dev/null; then
    seal_ok=1
fi
if [ "$seal_ok" -eq 0 ]; then
    log "DEFERRED: P4 ledger seal has not completed for $TODAY_UTC yet (no ledger_state/snapshot_ledger stamp); will retry on next launchd tick"
    exit 2
fi

# --- 3) idempotence: already produced today -> no-op ------------------------
if [ -f "$DONE_MARKER" ]; then
    log "OK: shadow-portfolio output for $TODAY_UTC already exists ($RUN_DIR); same-day re-trigger skipped (idempotent)"
    exit 0
fi

# --- 4) single-process RSS back-stop + run ----------------------------------
# ulimit -v takes KiB; caps the *address space* of the python child so the
# 4 GiB peak-footprint contract is enforced by the kernel, not by goodwill.
ulimit -v "$RSS_CAP_KIB" 2>/dev/null || \
    log "WARNING: could not set address-space cap ${RSS_CAP_KIB} KiB; continuing uncapped"

mkdir -p "$RUN_DIR"
log "START: shadow-portfolio daily run for $TODAY_UTC (ledger=$LEDGER_DIR out=$RUN_DIR rss_cap=${RSS_CAP_KIB}KiB)"

if [ ! -f "$RUNNER" ]; then
    log "FAILED: runner not found: $RUNNER (payload script missing; nothing scheduled)"
    exit 1
fi

if env -u PYTHONPATH "$PYTHON_BIN" "$RUNNER" \
        --ledger-dir "$LEDGER_DIR" \
        --out-dir "$RUN_DIR" \
        --as-of "$TODAY_UTC" \
        >>"$LOG_FILE" 2>&1; then
    # mark done only on a clean runner exit so a crash never poisons the day
    printf '%s\n' "$STAMP" >"$DONE_MARKER"
    log "DONE: shadow-portfolio run for $TODAY_UTC wrote $RUN_DIR"
    exit 0
else
    rc=$?
    log "FAILED: runner exited rc=$rc for $TODAY_UTC (see $LOG_FILE); not marking done"
    exit 1
fi
