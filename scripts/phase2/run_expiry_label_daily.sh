#!/usr/bin/env bash
# DAV-2034 — daily expiry-label timer wrapper (launchd).
# DAV-2041 — env injection + token precondition (打回自 DAV-2038 🔴-1).
#
# Semantics (总控 10-13 令):
#   * runs `expiry_label_pipeline.py run` + `verify` for the fixed P4 account,
#     every CN trading day at 20:10 (after the 19:45 ledger seal);
#   * output dir is append-only: work/phase2-labels/ in the ledger worktree;
#   * reads the LIVE forward ledger + market data — never the snapshot DB;
#     this wrapper passes no --db at all and the pipeline never opens the
#     ledger DB (the ro/immutable URI convention only applies to snapshot
#     readers, which this timer is not);
#   * fail-close everywhere: trading-calendar unavailable / run non-zero /
#     verify non-zero / SW-leg credential missing -> write alert file +
#     non-zero exit, NO retry. Credential gating is double-layered since
#     DAV-2034 总控 10-14 令: this wrapper pre-probes TUSHARE_TOKEN before
#     run (exit 13, below), AND expiry_label_pipeline itself fail-closes
#     (exit 2, zero rows) on an online run without the token — so a manual
#     `run` that bypasses the wrapper cannot mint permanent gap rows either;
#   * launchd still fires on Mon-Fri holidays; a non-trading day exits 0 with
#     a log line only (calendar was loaded to decide that, so an unloaded
#     calendar still alerts rather than skipping silently).
#
# Credentials (DAV-2041 🔴-1): launchd hands us a sparse env — no
# TUSHARE_TOKEN means the SW industry leg silently degrades to
# membership_status="gap" on EVERY row while run/verify still exit 0, and
# the appended (report_id,horizon) keys are skipped forever. So this wrapper
# sources the single credential source ~/Documents/TradingAgents-AShare/.env
# (export-all around source; KEY=VALUE lines only — no secrets are written
# to the plist, the alert file, or the log) and then pre-probes the keys
# the pipeline actually consumes:
#   TUSHARE_TOKEN      — SW2021 membership + sw_daily bars (industry leg)
#   FUYAO_API_KEY      — trade-calendar fallback when akshare is down
#   INVESTODAY_API_KEY — vendor route credential (kept consistent with .env)
# Missing TUSHARE_TOKEN -> alert + exit 13 (BEFORE touching the ledger, so a
# degraded night cannot mint permanent gap labels). FUYAO/INVESTODAY missing
# is a warning in the log only — the calendar can still come from akshare
# and a missing fallback is not worth paging at 20:10.
#
# Install (template only — do NOT install before the 10-18 release card):
#   cp scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
#
# Exit codes: 0 ok/skip, 10 python missing, 11 pipeline missing,
#             12 ledger missing, 13 calendar unavailable OR required
#             credential missing, 14 run failed (incl. the pipeline's own
#             no-token fail-close, exit 2), 15 verify failed,
#             16 .env unreadable OR contains non KEY=VALUE lines,
#             17 post-run all-gap SW-leg guard.

set -u  # NOT -e: we must route every failure through the alert path ourselves.

# --- fixed paths (same layout as the installed 19:45 ledger timer) ---------
PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
ENV_FILE="/Users/davidliu/Documents/TradingAgents-AShare/.env"
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
    # NEVER log or alert credential values — names only.
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

# --- credential injection (DAV-2041) ---------------------------------------
# .env is the single credential source; values stay in the process env, they
# are never echoed. `set -a` exports every assignment `source` evaluates.
# DAV-2043 🟡: `source` would execute ANY malformed line as a command (a
# corrupted .env silently runs arbitrary code and still exits 0), so we
# validate first — every non-empty non-comment line must be a bare
# KEY=VALUE (optional `export` prefix) with NO whitespace anywhere except
# inside a quoted value, and the line must END right after the value.
# DAV-2058 🟡-1: the previous pattern only anchored the line START, so
# `KEY=value touch /tmp/x` passed the gate and `source` then ran `touch`.
# The value side is now a whitelist — a "safe bare value" contains only
# [A-Za-z0-9_+,.:/@%^?=!~*()[]-] (no space, ; | & ` $ < > { } ' " \),
# a "-quoted value may contain anything except " \ $ or `, and a
# '-quoted value anything except '. `FOO =bar`, `KEY=v;cmd`,
# `KEY=v>file`, `KEY=v`cmd``, `KEY=$(cmd)` and `KEY='x' y` are all
# rejected: grep may pass them but bash would run a command or
# redirect. Any other line makes the file untrusted: alert + exit,
# never source it.
# DAV-2072 🟡-1: the "-quoted alternative previously excluded only " and
# \, so `K="$(touch /tmp/PWNED)"` and K="`touch /tmp/PWNED`" passed the
# gate and `source` executed the substitution (sandbox-verified PWNED
# created); `K="${HOME}..."` also expands with side effects. $ and ` are
# now excluded too — a double-quoted value is literal text only.
if [ -f "${ENV_FILE}" ] && [ -r "${ENV_FILE}" ]; then
    # exit 0 = a bad line was FOUND (grep -vE prints lines that are NOT
    # KEY=VALUE/comment/blank); exit 1 = file is clean and safe to source.
    if /usr/bin/grep -vEq '^[[:space:]]*(#|$)|^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=("[^"\\$`]*"|'"'"'[^'"'"']*'"'"'|[]A-Za-z0-9_+,.:/@%^?=!~*()\[-]*)[[:space:]]*$' "${ENV_FILE}"; then
        alert "credential file ${ENV_FILE} contains non KEY=VALUE lines — refusing to source a possibly malformed file" 16
    fi
    set -a
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
    set +a
else
    alert "credential file unreadable: ${ENV_FILE} — cannot inject TUSHARE_TOKEN/FUYAO_API_KEY/INVESTODAY_API_KEY" 16
fi

# --- preconditions ---------------------------------------------------------
[ -x "${PYTHON}" ]    || alert "python interpreter missing: ${PYTHON}" 10
[ -f "${PIPELINE}" ]  || alert "pipeline script missing: ${PIPELINE} (code worktree not upgraded to a trunk with DAV-1875 yet?)" 11
[ -f "${LEDGER}" ]    || alert "forward ledger missing: ${LEDGER} (19:45 seal timer has not produced it)" 12

# SW-leg credential probe: without TUSHARE_TOKEN every row lands as
# membership_status=gap AND (report_id,horizon) is permanently skipped —
# fail BEFORE the run mints irreversible gap labels.
if [ -z "${TUSHARE_TOKEN:-}" ]; then
    alert "TUSHARE_TOKEN missing after sourcing ${ENV_FILE} — SW industry leg would silently degrade to gap for every row" 13
fi
# Fallback/secondary credentials: warn-only (calendar still has akshare).
[ -n "${FUYAO_API_KEY:-}" ]      || log "WARN FUYAO_API_KEY missing — trade-calendar fallback unavailable (akshare only)"
[ -n "${INVESTODAY_API_KEY:-}" ] || log "WARN INVESTODAY_API_KEY missing — vendor investoday route unavailable"

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

# --- post-run SW-leg guard (DAV-2041 🟡-1) ----------------------------------
# run exits 0 even when every appended row is a gap (typed status, not a
# failure). Detect the all-gap regression here so the timer pages instead of
# reporting green; verify would also catch it, but alerting first keeps the
# exit code specific (17 = data degradation, not file corruption).
# DAV-2058 🟡-2: same sw_eligible denominator as verify — rows whose
# typed status short-circuits before SW resolution (_PRE_SW_TYPED_STATUSES
# in expiry_label_pipeline.py: the 6 pre-SW typed statuses) are
# structurally membership=gap and must not trip the all-gap alarm on a
# BSE/suspension-dense night. Mirrors verify's semantics so the two
# gates agree.
GAP_CHECK="$("${PYTHON}" - "${OUT_DIR}" <<'PY' 2>&1
import json, sys
from pathlib import Path
# keep in sync with expiry_label_pipeline._PRE_SW_TYPED_STATUSES
PRE_SW_TYPED = frozenset({
    "signal_date_not_in_calendar",
    "calendar_insufficient_coverage",
    "symbol_unmappable",
    "symbol_excluded_bse",
    "stock_bars_unavailable",
    "label_resolution_failed",
})
out = Path(sys.argv[1])
report = []
worst = 0
for name in ("labels_t10.jsonl", "labels_t40.jsonl"):
    p = out / name
    if not p.exists():
        continue
    eligible = gaps = 0
    for line in p.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(obj.get("outcome_status")) in PRE_SW_TYPED:
            continue
        eligible += 1
        sw = obj.get("sw") or {}
        if sw.get("membership_status") == "gap":
            gaps += 1
    if eligible:
        report.append(f"{name}: gap {gaps}/{eligible} sw-eligible")
        if gaps == eligible:
            worst = max(worst, 2)
        elif gaps / eligible >= 0.9:
            worst = max(worst, 1)
print(f"{worst} {'; '.join(report)}")
PY
)"
GAP_RC=$?
if [ "${GAP_RC}" -ne 0 ]; then
    alert "sw-gap post-check itself failed: ${GAP_CHECK}" 17
fi
GAP_LEVEL="${GAP_CHECK%% *}"
if [ "${GAP_LEVEL}" = "2" ]; then
    alert "SW industry leg fully degraded — ${GAP_CHECK#* } (labels are append-only; rows already written can never be re-labelled)" 17
fi
if [ "${GAP_LEVEL}" = "1" ]; then
    log "WARN ${GAP_CHECK#* } — partial SW gap >=90%, verify will flag"
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
