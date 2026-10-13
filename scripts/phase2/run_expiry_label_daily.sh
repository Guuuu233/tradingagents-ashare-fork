#!/usr/bin/env bash
# DAV-2034 — daily expiry-label timer wrapper (launchd).
#
# Semantics (总控 10-13 令):
#   * runs `expiry_label_pipeline.py run` + `verify` for the fixed P4 account,
#     every CN trading day at 20:10 (after the 19:45 ledger seal);
#   * output dir is append-only: work/phase2-labels/ in the ledger worktree;
#   * reads the LIVE forward ledger + market data — never the snapshot DB;
#     expiry_label_pipeline itself only touches SQLite through the ledger
#     writer's read-only URI (file:...?mode=ro&immutable=1) and this wrapper
#     passes no --db at all;
#   * fail-close everywhere: trading-calendar unavailable / run non-zero /
#     verify non-zero -> write alert file + non-zero exit, NO retry;
#   * launchd still fires on Mon-Fri holidays; a non-trading day exits 0 with
#     a log line only (calendar was loaded to decide that, so an unloaded
#     calendar still alerts rather than skipping silently).
#
# Install (template only — do NOT install before the 10-18 release card):
#   cp scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist

set -u  # NOT -e: we must route every failure through the alert path ourselves.

# --- fixed paths (same layout as the installed 19:45 ledger timer) ---------
PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
CODE_DIR="/Users/davidliu/ta-p4-ef8aa9d9"
PIPELINE="${CODE_DIR}/scripts/phase2/expiry_label_pipeline.py"
WORK_ROOT="/Users/davidliu/ta-p4-2e1e199f/work"
LEDGER="${WORK_ROOT}/phase2-ledger/forward_ledger.jsonl"
OUT_DIR="${WORK_ROOT}/phase2-labels"
ALERT_FILE="${OUT_DIR}/ALERT.launchd"
LOG_FILE="${OUT_DIR}/label_pipeline.log"

# --- env hygiene -----------------------------------------------------------
# launchd gives us a sparse env; make it deterministic:
#   * PYTHONPATH would otherwise leak host site-packages into the venv;
#   * the http(s)_proxy vars point at the local Clash port and HANG outbound
#     calls in this network — unset so vendor calendar/K-line requests fail
#     fast instead of stalling the timer (NO_PROXY still lets localhost work).
unset PYTHONPATH
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY="127.0.0.1,localhost,100.64.0.0/10"
export no_proxy="127.0.0.1,localhost,100.64.0.0/10"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$*" >>"${LOG_FILE}"
}

alert() {
    # Write/overwrite the alert file then exit non-zero. One file, not a
    # growing series — launchd surfaces the exit code and the file content is
    # the current failure, not a log of every failure since install.
    local msg="$1" code="$2"
    mkdir -p "${OUT_DIR}"
    {
        printf 'alert_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
        printf 'pipeline=expiry_label_pipeline.v1\n'
        printf 'exit_code=%s\n' "${code}"
        printf 'reason=%s\n' "${msg}"
        printf 'ledger=%s\n' "${LEDGER}"
        printf 'out_dir=%s\n' "${OUT_DIR}"
    } >"${ALERT_FILE}"
    log "ALERT ${msg} (exit ${code})"
    exit "${code}"
}

mkdir -p "${OUT_DIR}"
log "timer start label=com.davidliu.ta-p4-expiry-labels"

# --- preconditions ---------------------------------------------------------
[ -x "${PYTHON}" ]    || alert "python interpreter missing: ${PYTHON}" 10
[ -f "${PIPELINE}" ]  || alert "pipeline script missing: ${PIPELINE} (code worktree not upgraded to a trunk with DAV-1875 yet?)" 11
[ -f "${LEDGER}" ]    || alert "forward ledger missing: ${LEDGER} (19:45 seal timer has not produced it)" 12

# --- trading-day gate ------------------------------------------------------
# is_cn_trading_day() raises TradeCalendarUnavailableError when the calendar
# can't be loaded — that is exactly the "日历取不到" alert path, so we let the
# exception propagate as a non-zero and catch it below.
TODAY_CHECK="$("${PYTHON}" - <<'PY' 2>&1
import sys
sys.path.insert(0, "/Users/davidliu/ta-p4-ef8aa9d9")
from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day
today = cn_today_str()
print(f"{today} {is_cn_trading_day(today, allow_weekday_fallback=False)}")
PY
)"
CHECK_RC=$?
if [ "${CHECK_RC}" -ne 0 ]; then
    alert "trading calendar unavailable — cannot decide whether today is a trading day: ${TODAY_CHECK}" 13
fi
TODAY="${TODAY_CHECK%% *}"
IS_TRADING="${TODAY_CHECK##* }"
if [ "${IS_TRADING}" != "True" ]; then
    log "skip: ${TODAY} is not a CN trading day"
    exit 0
fi

# --- run -------------------------------------------------------------------
"${PYTHON}" "${PIPELINE}" run \
    --ledger "${LEDGER}" \
    --out-dir "${OUT_DIR}" >>"${LOG_FILE}" 2>&1
RUN_RC=$?
if [ "${RUN_RC}" -ne 0 ]; then
    alert "expiry_label_pipeline run exited ${RUN_RC} (see ${LOG_FILE} tail)" 14
fi

# --- verify ----------------------------------------------------------------
"${PYTHON}" "${PIPELINE}" verify \
    --out-dir "${OUT_DIR}" >>"${LOG_FILE}" 2>&1
VERIFY_RC=$?
if [ "${VERIFY_RC}" -ne 0 ]; then
    alert "expiry_label_pipeline verify exited ${VERIFY_RC} — label files may be inconsistent (see ${LOG_FILE} tail)" 15
fi

# Success: clear any stale alert file from a previous failure so a resolved
# incident does not keep alarming.
rm -f "${ALERT_FILE}"
log "timer done run_rc=0 verify_rc=0"
exit 0
