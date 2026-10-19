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
#
# Usage:
#   ta_secrets_install.sh                 # create/refresh copy, then verify
#   ta_secrets_install.sh --check         # verify only, exit 1 on any drift
#   ta_secrets_install.sh --src P --dest Q
#   Env overrides: TA_ENV_SRC, TA_ENV_FILE (same variable the wrapper reads).
#
# Exit codes: 0 ok; 2 usage; 3 source unusable / no TUSHARE_TOKEN;
#             4 destination unsafe (symlink, wrong location, non-absolute);
#             5 copy failed; 6 verification failed.
#
# REMINDER (card item 5): rotating a key means updating BOTH the source
# .env and this copy — see releases/launchd/TA_ENV_TCC.md.

set -u
umask 077

SRC="${TA_ENV_SRC:-/Users/davidliu/Documents/TradingAgents-AShare/.env}"
DEST="${TA_ENV_FILE:-/Users/davidliu/ta-secrets/ta.env}"
MODE="install"

usage() {
    sed -n '/^# Usage:/,/^# REMINDER/p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check|check) MODE="check"; shift ;;
        --src)   SRC="${2:-}"; shift 2 ;;
        --dest)  DEST="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit "${RC:-6}"; }

mode_file() { /usr/bin/stat -f '%Lp' "$1" 2>/dev/null || true; }

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
