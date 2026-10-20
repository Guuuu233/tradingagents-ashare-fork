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
# sources THE credential file named by TA_ENV_FILE (DAV-2284: the plist points
# it at ~/ta-secrets/ta.env, a 600 copy OUTSIDE ~/Documents; the old
# ~/Documents/TradingAgents-AShare/.env survives only as the fallback for a
# manual terminal run) — export-all around source; KEY=VALUE lines only — no
# secrets are written to the plist, the alert file, or the log — and then
# pre-probes the keys the pipeline actually consumes:
#   TUSHARE_TOKEN      — SW2021 membership + sw_daily bars (industry leg)
#   FUYAO_API_KEY      — trade-calendar fallback when akshare is down
#   INVESTODAY_API_KEY — vendor route credential (kept consistent with .env)
# Missing TUSHARE_TOKEN -> alert + exit 13 (BEFORE touching the ledger, so a
# degraded night cannot mint permanent gap labels). FUYAO/INVESTODAY missing
# is a warning in the log only — the calendar can still come from akshare
# and a missing fallback is not worth paging at 20:10.
#
# DAV-2284 (macOS TCC): launchd-spawned /bin/bash is denied *reads* under
# ~/Documents. Measured 2026-10-19: `grep: …/.env: Operation not permitted`
# and `. .env: Operation not permitted`, which fell through to "TUSHARE_TOKEN
# missing" — a real authorisation fault misreported as missing data. So:
#   * the credential file path is TA_ENV_FILE (plist-supplied), defaulting to
#     the old in-checkout .env only for manual terminal runs;
#   * the recommended target is ~/ta-secrets/ta.env: dir 700, file 600, made
#     by scripts/phase2/ta_secrets_install.sh (never by a bare `cp`);
#   * a read DENIED by the OS (grep rc >= 2) is its own alert, exit 18, and
#     NEVER falls through to `source` / "token missing";
#   * the interpreter is the independent venv ~/ta-venv/py310 (总控 10-17 令:
#     the in-checkout .venv310 is the audited single point AND sits under
#     ~/Documents, i.e. behind the same TCC wall), overridable via TA_PYTHON.
# Rationale + install/verification: releases/launchd/TA_ENV_TCC.md.
#
# Install (template only — do NOT install before the release card):
#   scripts/phase2/ta_secrets_install.sh          # 700 dir + 600 copy
#   scripts/phase2/ta_secrets_install.sh --check   # strict mode preflight
#   cp scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
#
# Exit codes: 0 ok/skip, 10 python missing, 11 pipeline missing,
#             12 ledger missing, 13 calendar unavailable OR required
#             credential missing, 14 run failed (incl. the pipeline's own
#             no-token fail-close, exit 2), 15 verify failed,
#             16 credential file unusable — four distinct causes:
#               16a path not absolute (launchd does not expand ~ / $VAR),
#               16b file missing / not readable,
#               16c file contains non KEY=VALUE lines,
#               16d file failed to source (syntax error etc.),
#             17 post-run all-gap SW-leg guard,
#             18 credential file READ denied by the OS (TCC/EACCES) —
#             fail-closed, no source attempt, alert names the denial.

set -u  # NOT -e: we must route every failure through the alert path ourselves.

# --- fixed paths (same layout as the installed 19:45 ledger timer) ---------
PYTHON="${TA_PYTHON:-/Users/davidliu/ta-venv/py310/bin/python}"
# DAV-2284: TA_ENV_FILE is what the plist sets. launchd does NOT expand `~`
# or `$VARS` in EnvironmentVariables, so the plist value must be absolute;
# a non-absolute value is rejected below rather than silently misread.
ENV_FILE="${TA_ENV_FILE:-/Users/davidliu/Documents/TradingAgents-AShare/.env}"
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

# The KEY=VALUE gate — ONE definition, used by this wrapper and (extracted at
# run time) by scripts/phase2/ta_secrets_install.sh, so the installer can
# never check less than the timer does (DAV-2286 🟢-4). Kept on its own line
# in the exact `NAME='pattern'` form the installer parses; do not reformat.
CRED_KEY_VALUE_GATE='^[[:space:]]*(#|$)|^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=("[^"\\$`]*"|'"'"'[^'"'"']*'"'"'|[]A-Za-z0-9_+,.:/@%^?=!~*()\[-]*)[[:space:]]*$'

# --- credential injection (DAV-2041) ---------------------------------------
# The file named by TA_ENV_FILE (DAV-2284: ~/ta-secrets/ta.env in production)
# is the single credential source; values stay in the process env, they are
# never echoed. `set -a` exports every assignment `source` evaluates.
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
# DAV-2284: an absolute path is mandatory — launchd does not expand `~` or
# `$VARS` in EnvironmentVariables, so a `~/ta-secrets/ta.env` value would be
# taken literally; a relative one would resolve against WorkingDirectory.
case "${ENV_FILE}" in
    /*) ;;
    *) alert "credential file path must be absolute, got '${ENV_FILE}' — launchd does not expand ~ or \$VARS in EnvironmentVariables (use /Users/davidliu/ta-secrets/ta.env)" 16 ;;
esac

if [ -f "${ENV_FILE}" ] && [ -r "${ENV_FILE}" ]; then
    # DAV-2284 item 3: the recommended copy is 600 in a 700 dir, created by
    # scripts/phase2/ta_secrets_install.sh. A wrong mode is a WARN here, not
    # a stop: a bare `cp` lands 644 under the default umask, and failing the
    # 20:10 label job does not make the secret less exposed. The strict gate
    # is `ta_secrets_install.sh --check`, run by the install/verify step.
    # Modes only — never contents.
    _file_mode="$(/usr/bin/stat -f '%Lp' "${ENV_FILE}" 2>/dev/null || true)"
    case "${_file_mode}" in
        ""|600|400) ;;
        *) log "WARN credential file mode ${_file_mode} (want 600) — chmod 600 '${ENV_FILE}'" ;;
    esac
    _dir_mode="$(/usr/bin/stat -f '%Lp' "$(/usr/bin/dirname "${ENV_FILE}")" 2>/dev/null || true)"
    case "${_dir_mode}" in
        ""|700|500) ;;
        *) log "WARN credential dir mode ${_dir_mode} (want 700) — chmod 700 '$(/usr/bin/dirname "${ENV_FILE}")'" ;;
    esac
    # exit 0 = a bad line was FOUND (grep -vE prints lines that are NOT
    # KEY=VALUE/comment/blank); exit 1 = file is clean and safe to source;
    # exit >= 2 = grep could not READ the file at all (EPERM under macOS TCC,
    # or EACCES) — DAV-2284 item 4: that is an environment/authorisation
    # fault, so it gets its own fail-closed alert (18) and must never be
    # downgraded to "TUSHARE_TOKEN missing" (which is what the 10-19 exit 13
    # actually was: grep+source both EPERM'd under ~/Documents).
    GREP_ERR="$(/usr/bin/grep -vEq "${CRED_KEY_VALUE_GATE}" "${ENV_FILE}" 2>&1 >/dev/null)"
    GREP_RC=$?
    case "${GREP_RC}" in
        0) alert "credential file ${ENV_FILE} contains non KEY=VALUE lines — refusing to source a possibly malformed file" 16 ;;
        1) : ;;
        *) log "grep could not read ${ENV_FILE}: rc=${GREP_RC} ${GREP_ERR:-<no stderr>}"
           alert "凭据文件读取被拒（疑似系统授权）：launchd 下的 /bin/bash 无权读取 ${ENV_FILE}（grep rc=${GREP_RC}${GREP_ERR:+: ${GREP_ERR}}）—— 未 source 任何内容，fail-closed。请把凭据副本放到 ~/Documents 之外（如 /Users/davidliu/ta-secrets/ta.env，目录 700 / 文件 600，用 scripts/phase2/ta_secrets_install.sh 生成），并用 plist 的 TA_ENV_FILE 指过去" 18 ;;
    esac
    set -a
    # DAV-2286 🟢-2: the KEY=VALUE gate already rejects every line that
    # could abort the script (a bare or double-quoted `$VAR` is outside the
    # value whitelist; a single-quoted one is literal text), so in practice
    # the source below cannot hit bash's "unbound variable" abort. The
    # `set +u` window is defence in depth against exactly that invisible
    # shape: under `set -u` bash would abort the WHOLE script (rc 127) and
    # write NO alert file; with the window the file is sourced leniently and
    # any genuine failure surfaces as a normal fail-closed alert (16)
    # below. `set -u` is restored immediately afterwards.
    set +u
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
    SOURCE_RC=$?
    set -u
    # A readable file that passes the gate can still fail to source (syntax
    # error, or a value that is not expandable). Say so instead of letting
    # the "token missing" probe 20 lines below take the blame.
    [ "${SOURCE_RC}" -eq 0 ] || alert "credential file ${ENV_FILE} specified but failed to source (rc=${SOURCE_RC}) — no values injected" 16
else
    alert "credential file missing or not readable: ${ENV_FILE} — cannot inject TUSHARE_TOKEN/FUYAO_API_KEY/INVESTODAY_API_KEY (TA_ENV_FILE must be an absolute path outside ~/Documents, e.g. /Users/davidliu/ta-secrets/ta.env)" 16
fi

# --- preconditions ---------------------------------------------------------
[ -x "${PYTHON}" ]    || alert "python interpreter missing: ${PYTHON}" 10
[ -f "${PIPELINE}" ]  || alert "pipeline script missing: ${PIPELINE} (code worktree not upgraded to a trunk with DAV-1875 yet?)" 11
[ -f "${LEDGER}" ]    || alert "forward ledger missing: ${LEDGER} (19:45 seal timer has not produced it)" 12

# SW-leg credential probe: without TUSHARE_TOKEN every row lands as
# membership_status=gap AND (report_id,horizon) is permanently skipped —
# fail BEFORE the run mints irreversible gap labels.
if [ -z "${TUSHARE_TOKEN:-}" ]; then
    alert "TUSHARE_TOKEN missing after sourcing ${ENV_FILE} (the file was readable and passed the KEY=VALUE gate, so this is a genuine content gap — not a TCC read denial, which exits 18 above) — SW industry leg would silently degrade to gap for every row" 13
fi
# Fallback/secondary credentials: warn-only (calendar still has akshare).
[ -n "${FUYAO_API_KEY:-}" ]      || log "WARN FUYAO_API_KEY missing — trade-calendar fallback unavailable (akshare only)"
[ -n "${INVESTODAY_API_KEY:-}" ] || log "WARN INVESTODAY_API_KEY missing — vendor investoday route unavailable"

# --- trading-day gate ------------------------------------------------------
# is_cn_trading_day() raises TradeCalendarUnavailableError when the calendar
# can't be loaded — that is exactly the "日历取不到" alert path, so we let the
# exception propagate as a non-zero and catch it below.
TODAY_CHECK="$("${PYTHON}" - "${CODE_DIR}" <<'PY' 2>&1
import sys
# CODE_DIR comes in as argv[1] — deriving it here instead of hardcoding keeps
# one copy of the install path (see the "Path coupling" note in the plist);
# the 10-19 installed worktree had to hand-edit both copies to stay in sync.
sys.path.insert(0, sys.argv[1])
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
