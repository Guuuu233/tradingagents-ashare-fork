#!/usr/bin/env bash
# DAV-2284 — install / verify the launchd-visible credential copy.
#
# Why this exists: macOS refuses launchd-spawned /bin/bash any *read* under
# ~/Documents (TCC). The 2026-10-19 run of com.davidliu.ta-p4-expiry-labels
# died with `grep: …/.env: Operation not permitted` and then misreported it
# as "TUSHARE_TOKEN missing" (exit 13). The fix is a copy of the credential
# file OUTSIDE ~/Documents, read via the wrapper's TA_ENV_FILE:
#
#   /Users/davidliu/ta-secrets          mode 700
#   /Users/davidliu/ta-secrets/ta.env   mode 600   <- TA_ENV_FILE
#
# This script is the only sanctioned way to create that copy: a bare `cp`
# under the default umask lands 644 in a 755 dir, i.e. the opposite of
# item 3 of the card. The wrapper itself only WARNS on a wrong mode (paging
# ops at 20:10 does not make the secret less exposed); THIS is the strict
# gate — `--check` exits non-zero so the install/verify step can fail closed.
#
# Discipline:
#   * never prints, logs or echoes a credential VALUE — only key names,
#     modes, line/byte counts and paths;
#   * never writes inside ~/Documents; never touches the source file;
#   * the copy is written to a sibling temp file (umask 077), chmod 600,
#     then renamed into place, so a partial copy can never be sourced.
#   * DAV-2292: this script refuses to be `source`d (see the guard below) —
#     sourcing it would execute the whole script against the REAL
#     credentials and the PRODUCTION label dir.
#
# Usage:
#   ta_secrets_install.sh                 # create/refresh copy, then verify
#   ta_secrets_install.sh --check         # verify only, exit non-zero on drift
#   ta_secrets_install.sh check           # same (the double-hyphen spelling
#                                         # cannot be written inside an XML
#                                         # comment; both forms work here)
#   ta_secrets_install.sh --src P --dest Q
#   Env overrides: TA_ENV_SRC, TA_ENV_FILE (same variable the wrapper reads),
#                  TA_ENV_WRAPPER (path to run_expiry_label_daily.sh; default
#                  = the copy sitting next to this script).
#
# Exit codes: 0 ok; 2 usage (incl. a flag used without its value — DAV-2286
#             🟡-1: the old `shift 2` spun forever at ~70% CPU with no
#             output when a value was missing);
#             3 source unusable / no TUSHARE_TOKEN / would fail the timer's
#             KEY=VALUE gate;
#             4 destination unsafe (symlink, wrong location, non-absolute);
#             5 copy failed; 6 verification failed;
#             7 the timer's KEY=VALUE gate could not be read from the wrapper
#             (refuse to install rather than check less);
#             64 REFUSED to be sourced (DAV-2292 guard: sourcing this script
#             would read the real credentials and write the real destination).
#
# REMINDER (card item 5): rotating a key means updating BOTH the source
# .env and this copy — see releases/launchd/TA_ENV_TCC.md.

set -u
umask 077

# --- DAV-2292 护栏：拒绝被 source -------------------------------------------
# 同 run_expiry_label_daily.sh：source 本脚本会**执行**整个脚本 → 读真实凭据、
# 写正式目录。被 source 时立刻拒绝。
if [ "${BASH_SOURCE[0]:-$0}" != "$0" ]; then
    printf 'REFUSED: %s is an executable script, not a sourceable library.\n' \
        "${BASH_SOURCE[0]}" >&2
    exit 64
fi

SRC="${TA_ENV_SRC:-/Users/davidliu/Documents/TradingAgents-AShare/.env}"
DEST="${TA_ENV_FILE:-/Users/davidliu/ta-secrets/ta.env}"
MODE="install"

usage() {
    sed -n '/^# Usage:/,/^# REMINDER/p' "$0" | sed 's/^# \{0,1\}//'
}

# NOTE: these helpers must be defined BEFORE the argument loop below — the
# loop calls fail() on a malformed flag (DAV-2286 🟡-1), and a function that
# is not defined yet fails with "command not found" and lets the script keep
# running into the very error it was meant to report.
say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit "${RC:-6}"; }

mode_file() { /usr/bin/stat -f '%Lp' "$1" 2>/dev/null || true; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check|check) MODE="check"; shift ;;
        --src)
            # DAV-2286 🟡-1: `shift 2` with $#==1 fails AND leaves the
            # positional parameters untouched -> the while loop never
            # advances -> silent 100% CPU spin. A missing value must fail
            # loudly instead (this script is the release card's first step).
            # NOTE: the COUNT must be checked in a command that does NOT
            # mention "$2" at all. Under `set -u`, expanding an unset
            # positional parameter aborts the script by itself, so any
            # command that mentions "$2" needs a preceding, separate
            # count check. THE REAL RULE (DAV-2292 🟢-1): a command that
            # expands "$2" must be preceded by an independent count check.
            # (An earlier note here claimed that `a || b` cannot help
            # because "bash expands every word of the whole list" — that
            # was wrong and is retracted: `||`/`&&` DO short-circuit, so
            # when the count test succeeds the "$2" test never runs and
            # nothing aborts. The separate count check is what the code
            # below does, and it is what makes the rule obvious.)
            if [ "$#" -lt 2 ]; then
                RC=2 fail "--src needs a path"
            fi
            if [ -z "$2" ]; then
                RC=2 fail "--src needs a path"
            fi
            SRC="$2"; shift 2 ;;
        --dest)
            if [ "$#" -lt 2 ]; then
                RC=2 fail "--dest needs a path"
            fi
            if [ -z "$2" ]; then
                RC=2 fail "--dest needs a path"
            fi
            DEST="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

# ---- the KEY=VALUE gate (DAV-2286 🟢-4) -----------------------------------
# DAV-2286 🟢-4: the copy must be one the TIMER will accept, not merely one
# that exists. The authoritative pattern lives in the wrapper as
#   CRED_KEY_VALUE_GATE='<pattern>'
# — one definition, used by the wrapper itself and extracted here at run
# time, so the installer can never check less than the timer does.
#
# Extraction: the assignment line is read verbatim and handed to `eval`,
# which performs the shell's own unquoting — the pattern contains single
# quotes, spelled in the source as the four-character sequence '\'' , and
# `eval` resolves them exactly the way the wrapper does. No hand-rolled
# un-escaping, so no chance of the two sides disagreeing.
#
# The wrapper is looked up NEXT TO THIS SCRIPT (both live in scripts/phase2/
# of the same checkout); override with TA_ENV_WRAPPER if the two ever live
# apart.
WRAPPER="${TA_ENV_WRAPPER:-$(cd "$(/usr/bin/dirname "$0")" && pwd -P)/run_expiry_label_daily.sh}"
GATE_PATTERN=""
if [ -r "${WRAPPER}" ]; then
    GATE_LINE="$(grep -m1 '^CRED_KEY_VALUE_GATE=' "${WRAPPER}" || true)"
    if [ -n "${GATE_LINE}" ]; then
        # eval assigns the variable; print it (the assignment itself is silent)
        GATE_PATTERN="$(eval "${GATE_LINE}"; printf '%s' "${CRED_KEY_VALUE_GATE}")"
    fi
fi
if [ -z "${GATE_PATTERN}" ]; then
    RC=7 fail "cannot read the KEY=VALUE gate from ${WRAPPER} — refusing to install a copy the timer might reject (set TA_ENV_WRAPPER to the wrapper path)"
fi
# gate <file> -> 0 illegal line found, 1 clean, >=2 unreadable (grep semantics)
gate() {
    /usr/bin/grep -vEq "${GATE_PATTERN}" "$1" 2>/dev/null
    return $?
}

# ---- destination shape (both modes) ---------------------------------------
case "${DEST}" in
    /*) ;;
    *) RC=4 fail "destination must be an absolute path, got '${DEST}' (launchd does not expand ~)" ;;
esac
DEST_DIR="$(/usr/bin/dirname "${DEST}")"
case "${DEST}" in
    "${HOME}/Documents/"*)
        RC=4 fail "destination ${DEST} is under ~/Documents — the whole point is to live outside it (TCC)" ;;
esac
# A symlinked directory would silently redirect the copy (and the "700"
# claim would then describe the link target, not what we chmod'ed).
if [ -L "${DEST_DIR}" ]; then
    RC=4 fail "destination dir ${DEST_DIR} is a symlink — refusing (resolve it first)"
fi
# Never copy a file onto itself (or through a symlink to itself).
if [ -e "${DEST}" ] && [ -e "${SRC}" ] && [ "${DEST}" -ef "${SRC}" ]; then
    RC=4 fail "source and destination are the same file: ${DEST}"
fi

# ---- verify-only ----------------------------------------------------------
verify() {
    local f_mode d_mode size rc=0
    if [ -L "${DEST}" ]; then
        say "FAIL destination is a symlink: ${DEST}"; rc=1
    fi
    if [ ! -f "${DEST}" ]; then
        say "FAIL missing copy: ${DEST}"; return 1
    fi
    f_mode="$(mode_file "${DEST}")"
    d_mode="$(mode_file "${DEST_DIR}")"
    # 600/400 file, 700/500 dir: never group/other bits.
    case "${f_mode}" in
        600|400) say "ok   file mode ${f_mode} ${DEST}" ;;
        *) say "FAIL file mode ${f_mode} (want 600): chmod 600 '${DEST}'"; rc=1 ;;
    esac
    case "${d_mode}" in
        700|500) say "ok   dir  mode ${d_mode} ${DEST_DIR}" ;;
        *) say "FAIL dir  mode ${d_mode} (want 700): chmod 700 '${DEST_DIR}'"; rc=1 ;;
    esac
    if [ ! -r "${DEST}" ]; then
        say "FAIL copy not readable: ${DEST}"; rc=1
    elif ! /usr/bin/grep -q '^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}TUSHARE_TOKEN=..*' "${DEST}"; then
        say "FAIL copy has no non-empty TUSHARE_TOKEN= line: ${DEST}"; rc=1
    else
        # key names only — never the value
        say "ok   keys present: TUSHARE_TOKEN (values not printed)"
    fi
    # DAV-2286 🟢-4: same gate the timer applies before sourcing.
    gate "${DEST}" >/dev/null 2>&1
    local gate_rc=$?
    case "${gate_rc}" in
        0) say "FAIL copy has non KEY=VALUE lines — the timer would refuse to source it: ${DEST}"; rc=1 ;;
        1) say "ok   KEY=VALUE gate: clean (same gate the timer applies)" ;;
        *) say "FAIL copy unreadable for the KEY=VALUE gate (rc=${gate_rc}): ${DEST}"; rc=1 ;;
    esac
    size="$(/usr/bin/wc -c <"${DEST}" 2>/dev/null | tr -d ' ')"
    say "info copy size ${size:-?} bytes, $(/usr/bin/wc -l <"${DEST}" 2>/dev/null | tr -d ' ') lines"
    return "${rc}"
}

if [ "${MODE}" = "check" ]; then
    verify || exit 6
    say "OK   credential copy passes the strict gate"
    exit 0
fi

# ---- install --------------------------------------------------------------
if [ ! -f "${SRC}" ]; then
    RC=3 fail "source credential file missing: ${SRC}"
fi
if [ ! -r "${SRC}" ]; then
    RC=3 fail "source credential file unreadable: ${SRC} (run this from an interactive shell — launchd is the thing that cannot read it)"
fi
if [ ! -s "${SRC}" ]; then
    RC=3 fail "source credential file is empty: ${SRC}"
fi
if ! /usr/bin/grep -q '^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}TUSHARE_TOKEN=..*' "${SRC}"; then
    RC=3 fail "source has no non-empty TUSHARE_TOKEN= line: ${SRC} — refusing to install a copy the timer would reject"
fi
# DAV-2286 🟢-4: reject a source the timer would refuse to source, at install
# time — not at 20:10.
gate "${SRC}" >/dev/null 2>&1
SRC_GATE_RC=$?
case "${SRC_GATE_RC}" in
    0) RC=3 fail "source contains non KEY=VALUE lines: ${SRC} — the timer would refuse to source it (exit 16)" ;;
    1) : ;;
    *) RC=3 fail "source unreadable for the KEY=VALUE gate (rc=${SRC_GATE_RC}): ${SRC}" ;;
esac

mkdir -p "${DEST_DIR}" || RC=5 fail "cannot create ${DEST_DIR}"
# Tighten even an existing dir: item 3 of the card is the dir being 700.
chmod 700 "${DEST_DIR}" || RC=5 fail "cannot chmod 700 ${DEST_DIR}"

TMP="${DEST}.tmp.$$"
trap 'rm -f "${TMP}"' EXIT INT TERM
command cp "${SRC}" "${TMP}" || RC=5 fail "copy ${SRC} -> ${TMP} failed"
chmod 600 "${TMP}" || RC=5 fail "cannot chmod 600 ${TMP}"
# rename(2) on the same filesystem — the timer never sees a half-written file
mv -f "${TMP}" "${DEST}" || RC=5 fail "cannot move ${TMP} into place"
trap - EXIT INT TERM

say "installed ${DEST} from ${SRC} (contents not shown; $(/usr/bin/wc -c <"${DEST}" | tr -d ' ') bytes)"
# Per-key presence without values: the wrapper warns on the two fallbacks.
for k in TUSHARE_TOKEN FUYAO_API_KEY INVESTODAY_API_KEY; do
    if /usr/bin/grep -q "^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}${k}=..*" "${DEST}"; then
        say "key ${k}: present"
    else
        say "key ${k}: MISSING"
    fi
done

verify || exit 6
say ""
say "NOTE 更换密钥时，两处都要改：${SRC} 与 ${DEST}（先改 .env，再跑本脚本刷新副本，然后 --check）。"
say "OK   credential copy installed and verified"
exit 0
